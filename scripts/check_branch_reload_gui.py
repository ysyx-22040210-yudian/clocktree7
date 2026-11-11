"""Native Tk double-click, private-index lifetime and branch retry acceptance."""
import argparse
import gc
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree import branch, layout_job
from clocktree.branch_view import BranchDialog
from clocktree.gui import ClockTreeApp
from clocktree.indexed_view import IndexedGraphView
from clocktree.networks import memberships
from clocktree.session import read_graph
from clocktree.storage import IndexedGraph, index_graph, new_store, ResultStore
from clocktree.viewmodel import GraphView
from check_indexed_gui import capture


def fixture():
    nodes=[dict(id='top.'+key,path='top.'+key,module=kind,kind=kind,layer=layer,inputs=pins,
                output=output,root=layer==1,terminal=kind=='subsystem')
           for key,kind,layer,pins,output in [('a','gate',1,['i'],'o'),('b','gate',1,['i'],'o'),
                    ('w','wire',2,['a','b'],None),('s','subsystem',3,['clk[0]','clk[1]'],None)]]
    nodes[2].update(outputs=['x','y'],internal_connections=[dict(input=a,output=b,status='verified')
                                                              for a,b in [('a','x'),('b','y')]])
    edges=[dict(id=str(i),source='top.'+s,target='top.'+t,source_pin=a,target_pin=b,layer_delta=1,
                evidence={'driver':[dict(name='top.'+s+'.'+a,type='port'),dict(name='top.'+t+'.'+b,type='port')]})
           for i,(s,t,a,b) in enumerate([('a','w','o','a'),('b','w','o','b'),('w','s','x','clk[0]'),('w','s','y','clk[1]')])]
    components=memberships({n['id']:n for n in nodes},edges,[])
    return dict(nodes=nodes,edges=edges,components=components,boundaries=[],summary={'instances':4,'edges':4,'connectivity_complete':True},
                tree_type='clock',config=dict(tree_type='clock',design={'top':'top'},layers=[
                    dict(index=i,hierarchy='top',modules=[]) for i in (1,2,3)]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--result',action='append',default=[])
    parser.add_argument('--cycles',type=int,default=20)
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    errors=[];report={'cases':[],'faults':[]};clock=[100000]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False);app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    def pump(predicate=lambda:True):
        until=time.monotonic()+60
        while time.monotonic()<until:
            app.update()
            assert not errors,errors
            if predicate() and not app.diagram.layout_pending and app.diagram._pending is None:return
            time.sleep(.008)
        raise AssertionError('GUI wait timeout')
    def ready(window):
        pump(lambda:window.load_error or (window.graph is not None and not window.diagram.layout_pending and window.diagram._pending is None))
        assert not window.load_error,window.details.get('1.0','end')
    def state(diagram):return (id(diagram.model.positions),diagram.scale,diagram.ox,diagram.oy,diagram.scope,diagram.component)
    def double(diagram,node):
        diagram.locate(node);pump(lambda:not diagram.layout_pending and diagram._pending is None)
        from clocktree.navigation_target import display_node,location_target
        shown=display_node(diagram.model.nodes,location_target(node));assert shown,node
        x,y,w,h=diagram.model.positions[shown['id']];x,y=map(int,diagram.point(x+w/2,y+h/2))
        before=state(diagram);old=set(app.winfo_children());clock[0]+=2000
        for i in (0,1):
            diagram.canvas.event_generate('<ButtonPress-1>',x=x,y=y,time=clock[0]+100*i)
            diagram.canvas.event_generate('<ButtonRelease-1>',x=x,y=y,time=clock[0]+100*i+30)
        pump(lambda:any(isinstance(w,BranchDialog) and w not in old for w in app.winfo_children()))
        window=next(w for w in app.winfo_children() if isinstance(w,BranchDialog) and w not in old)
        ready(window);assert state(diagram)==before
        return window
    def edge_ids(graph):return {e['id'] for e in graph['edges']}
    def verify(window,model,node):
        expected=branch.build_branch(model,node)
        try:
            assert edge_ids(window.graph)==edge_ids(expected)
            assert {n['id'] for n in window.graph['nodes']}=={n['id'] for n in expected['nodes']}
        finally:branch.close_branch(expected)
        assert window.diagram.model.positions
        window.diagram.model.svg()
    def fd_count():
        try:return len(os.listdir('/proc/self/fd'))
        except OSError:return None
    pump()
    sources=[('synthetic-memory',fixture()),('synthetic-index',fixture())]
    sources.extend(('native-'+str(i),read_graph(p)) for i,p in enumerate(args.result))
    for label,raw in sources:
        if label=='synthetic-memory':graph=raw
        elif isinstance(raw,IndexedGraph):graph=raw
        else:
            built=index_graph(raw,new_store(out/(label+'-index')));path=built.store.path;built.store.close()
            graph=ResultStore(path).graph()  # Reopened saved results use read-only connections.
        app.show_graph(graph);pump()
        oracle=GraphView(raw) if not isinstance(raw,IndexedGraph) else IndexedGraphView(raw)
        for key in list(app.diagram.model.original):
            node=app.diagram.model.original[key];started=time.monotonic();window=double(app.diagram,node)
            verify(window,oracle,node)
            expected=edge_ids(window.graph);private=window.graph.store.path if isinstance(window.graph,IndexedGraph) else None
            window.retry_button.invoke();ready(window)
            assert edge_ids(window.graph)==expected
            if private:assert not private.exists()
            row=dict(case=label,instance=key,kind=node['kind'],nodes=len(window.graph['nodes']),edges=len(window.graph['edges']),
                     native_double_click=True,reload=True,seconds=round(time.monotonic()-started,3))
            report['cases'].append(row);print(json.dumps(row),flush=True)
            window.close();pump()
    # Failure at every asynchronous/synchronous boundary must be visible and retryable.
    source=index_graph(fixture(),new_store(out/'retry-index'));app.show_graph(source);pump()
    node=app.diagram.model.original['top.a']
    for phase in ('prepare','model','layout','render'):
        window=double(app.diagram,node);before=state(app.diagram)
        if phase=='prepare':owner,name=branch,'build_branch'
        elif phase=='model':owner,name=window.diagram,'load'
        elif phase=='layout':owner,name=layout_job.LayoutJob,'result'
        else:owner,name=window.diagram.scene,'paint'
        original=getattr(owner,name)
        def fail(*a,**kw):raise RuntimeError('acceptance injected '+phase+' failure')
        setattr(owner,name,fail)
        try:
            window.retry_button.invoke();pump(lambda:bool(window.load_error))
            pump(lambda:window.diagram._pending is None)
            assert any(window.diagram.canvas.type(item)=='text' and window.diagram.canvas.itemcget(item,'text')=='分支加载失败'
                       for item in window.diagram.canvas.find_all())
            detail=window.details.get('1.0','end')
            assert 'Traceback' in detail and 'acceptance injected '+phase in detail
            assert window.retry_button.instate(['!disabled'])
            assert 'acceptance injected '+phase in window.journal.event_file.read_text(encoding='utf-8')
            window.set_status('canvas hover');assert '失败' in window.status.get()
            capture(window,out/('failure-'+phase+'.png'))
        finally:setattr(owner,name,original)
        window.retry_button.invoke();ready(window);verify(window,GraphView(fixture()),node)
        assert state(app.diagram)==before
        report['faults'].append(dict(phase=phase,logged=True,retry_passed=True,main_unchanged=True))
        window.close();pump()
    # Keep another main-thread SQLite cursor alive, then exercise the public builder.
    cursor=source.store.connection().execute('SELECT id FROM nodes');assert cursor.fetchone()
    snapshot=branch.build_branch(app.diagram.model,node);branch.close_branch(snapshot);cursor.close()
    report['active_cursor']=True
    # Repeated opening/reloading must not leave worker connections or private files behind.
    gc.collect();gc.disable();fds=[fd_count()];rss=[]
    try:
        for i in range(args.cycles):
            window=app.open_branch(node);ready(window);private=window.graph.store.path
            window.retry_button.invoke();ready(window);assert not private.exists()
            private=window.graph.store.path;window.close();pump();assert not private.exists()
            fds.append(fd_count())
            try:rss.append(int(next(s for s in Path('/proc/self/status').read_text().splitlines() if s.startswith('VmRSS:')).split()[1]))
            except (OSError,StopIteration):pass
        if fds[0] is not None:assert max(fds)-fds[0]<=4,fds
    finally:gc.enable();gc.collect()
    report.update(cycles=args.cycles,fd_counts=fds,rss_kib=rss,errors=errors,python=sys.version,sqlite=sqlite3.sqlite_version,
                  tk=str(app.tk.call('info','patchlevel')))
    window=double(app.diagram,app.diagram.model.original['top.w']);capture(window,out/'branch-reloaded.png')
    # Child window and main result switches do not invalidate its immutable source.
    child=double(window.diagram,next(n for n in window.diagram.model.nodes.values() if n.get('reference_of')=='top.s'))
    window.close();pump();app.show_graph(fixture());pump();child.retry_button.invoke();ready(child)
    child.diagram.model.svg();child.close();pump();report['nested_after_main_switch']=True
    app.destroy()
    (out/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'cases':len(report['cases']),'faults':report['faults'],'fd_counts':fds,'errors':errors}),flush=True)


if __name__=='__main__':main()
