"""Verify independent branch canvases against saved NPI results and large indexes."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.branch_view import BranchDialog
from clocktree.gui import ClockTreeApp
from clocktree.session import read_graph
from clocktree.storage import IndexedGraph, index_graph, new_store
from check_indexed_gui import capture, stress_index


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--clock',required=True,help='wb_top subsystem result from check_subsystem.py')
    parser.add_argument('--reset',required=True,help='wb_top subsystem reset result')
    parser.add_argument('--large',help='Optional existing 8,195-instance NPI fanout result')
    parser.add_argument('--cycles',type=int,default=5)
    args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    errors=[];reports={}
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False)
    app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    def pump(predicate=lambda:True,limit=120):
        started=time.monotonic()
        while True:
            app.update()
            if predicate() and not app.diagram.layout_pending and app.diagram._pending is None:break
            if time.monotonic()-started>limit:raise AssertionError('GUI wait: '+str(errors))
            time.sleep(.008)
        app.update()
    def ready(window):
        pump(lambda:window.graph is not None and not window.diagram.layout_pending and window.diagram._pending is None)
        assert not window.job.error,window.job.error
    def view_state(diagram):
        return (id(diagram.model.positions),diagram.mode,diagram.scope,diagram.component,diagram.scale,diagram.ox,diagram.oy)
    click_time=[100000]
    def double(diagram,node):
        diagram.locate(node);pump(lambda:not diagram.layout_pending and diagram._pending is None)
        actual=node.get('reference_of',node['id'])
        shown=next(n for n in diagram.model.nodes.values() if n['id']==node['id'] or n['id']==actual or n.get('reference_of')==actual)
        x,y,w,h=diagram.model.positions[shown['id']]
        x,y=map(int,diagram.point(x+w/2,y+h/2))
        assert diagram._hit(x,y)[0]=='node'
        before=view_state(diagram)
        windows={w for w in app.winfo_children() if isinstance(w,BranchDialog)}
        click_time[0]+=2000
        for i in range(2):
            diagram.canvas.event_generate('<ButtonPress-1>',x=x,y=y,time=click_time[0]+i*100)
            diagram.canvas.event_generate('<ButtonRelease-1>',x=x,y=y,time=click_time[0]+i*100+30)
        pump(lambda:len([w for w in app.winfo_children() if isinstance(w,BranchDialog) and w not in windows])==1)
        result=next(w for w in app.winfo_children() if isinstance(w,BranchDialog) and w not in windows)
        ready(result)
        assert view_state(diagram)==before,'Opening branch changed original viewport/layout'
        assert result.grab_current() is None,'Branch window must be non-modal'
        return result
    def ids(graph):return {edge['id'] for edge in graph['edges']}
    pump()
    for mode,path in (('clock',args.clock),('reset',args.reset)):
        original=read_graph(path)
        # Exercise the same disk-indexed path used by large commercial results.
        graph=original if isinstance(original,IndexedGraph) else index_graph(original,new_store(out/(mode+'-index')))
        app.show_graph(graph);pump()
        source=app.diagram.model.original['wb_top.g0']
        window=double(app.diagram,source)
        expected={edge['id'] for edge in graph['edges'] if edge['source']=='wb_top.g0'}
        assert ids(window.graph)==expected
        assert {n['id'] for n in window.graph['nodes']}=={'wb_top.g0','wb_top.s0','wb_top.s1'}
        assert window.diagram.tree_type==mode
        ref=next(n for n in window.diagram.model.nodes.values() if n.get('reference_of')=='wb_top.s1')
        child=double(window.diagram,ref)
        expected_child={e['id'] for e in graph['edges'] if e['source']=='wb_top.g0' and e['target']=='wb_top.s1'}
        assert ids(child.graph)==expected_child
        assert len(child.graph['nodes'])==2
        parent_state=view_state(app.diagram)
        child.diagram.zoom(1.2,300,220);child.diagram.ox+=20;child.diagram.schedule()
        pump(lambda:child.diagram._pending is None and not child.diagram._zoom_active)
        assert view_state(app.diagram)==parent_state
        child.search.set('wb_top.g1');child.locate_query();assert not child._matches
        child.search.set('wb_top.s1');child.locate_query();ready(child)
        child.expand_selected();ready(child)
        assert 'wb_top.s1' in child.diagram.model.expanded_buses
        child.jump_neighbour('upstream');ready(child)
        assert child.diagram.selected=='wb_top.g0'
        child.diagram.navigate_history();ready(child)
        child.inspect_edges([child.diagram.model.edges[0]])
        assert '输入反查' in child.details.get('1.0','end')
        child.diagram.model.svg()
        window.diagram.fit();window.lift();ready(window)
        capture(window,out/(mode+'-branch.png'))
        child.diagram.fit();child.lift();ready(child)
        capture(child,out/(mode+'-subsystem-input.png'))
        window.close();pump()
        # Another independent window must survive its parent's closure.
        app.show_graph(read_graph(ROOT/'examples/flexible_layers/clocktree.json'));pump()
        child.inspect_edges([child.diagram.model.edges[0]])
        child.search.set('wb_top.g0');child.locate_query();ready(child)
        child_path=child.graph.store.path
        child.close();pump();assert not child_path.exists()
        app.show_graph(graph);pump()
        assert len(app.diagram.model.original)==4 and len(app.diagram.model.original_edges)==len(graph['edges'])
        reports[mode]=dict(nodes=3,edges=len(expected),reference_nodes=2,reference_edges=len(expected_child),
                           original_unchanged=True,native_double_click=True,independent_navigation=True,main_result_switch=True)
    if args.large:
        graph=read_graph(args.large);app.show_graph(graph);pump()
        leaf=next(n for n in graph['nodes'] if n.get('terminal'))
        started=time.monotonic();window=app.open_branch(leaf);ready(window)
        expected=app.diagram.model.path_edges(leaf['id'],'upstream')
        if hasattr(expected,'sql'):
            expected={row[0] for row in app.diagram.model.store.connection().execute(expected.sql,expected.params)}
        assert ids(window.graph)==set(expected)
        reports['large_npi']=dict(source_instances=len(graph['nodes']),branch_instances=len(window.graph['nodes']),
                                  branch_edges=len(window.graph['edges']),open_seconds=time.monotonic()-started)
        capture(window,out/'large-npi-branch.png');window.close();pump()
    graph=stress_index(out/'stress',99999);app.show_graph(graph);pump()
    root=app.diagram.model.original['stress.domain.root']
    started=time.monotonic();window=app.open_branch(root);ready(window)
    assert len(window.graph['nodes'])==100000 and len(window.graph['edges'])==99999
    assert len(window.diagram.model.nodes)<=1600
    reports['stress']=dict(nodes=100000,edges=99999,boxes=len(window.diagram.model.nodes),open_seconds=time.monotonic()-started)
    window.search.set('stress.domain.g099998.u');window.locate_query();ready(window)
    assert window.diagram.selected=='stress.domain.g099998.u'
    window.diagram.reset_scope();ready(window)
    capture(window,out/'100k-branch.png')
    path=window.graph.store.path;window.close();pump();assert not path.exists()
    cancelled=app.open_branch(root);job=cancelled.job;cancelled.close()
    pump(lambda:job.done.is_set());assert job.take()==(None,None)
    reports['cancelled_preparation']=True
    # Repeated local windows should leave no private indexes or live workers.
    rss=[]
    leaf=app.diagram.model.original['stress.domain.g099998.u']
    for _ in range(args.cycles):
        window=app.open_branch(leaf);ready(window)
        assert len(window.graph['nodes'])==2 and len(window.graph['edges'])==1
        path=window.graph.store.path;window.close();pump();assert not path.exists()
        rss.append(int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:'))))
    assert rss[-1]-min(rss)<16*1024,rss
    reports['repeated_windows']=dict(count=args.cycles,rss_kib=rss,private_indexes_removed=True)
    assert not [w for w in app.winfo_children() if isinstance(w,BranchDialog)]
    assert not errors,errors
    reports.update(passed=True,callback_errors=errors,python=sys.version,tk=app.tk.call('info','patchlevel'))
    last=app.open_branch(leaf);ready(last);last_path=last.graph.store.path
    app.close()
    assert last.closed and not last_path.exists(),'Main exit must release remaining branch windows'
    reports['main_exit_released_branches']=True
    (out/'acceptance.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(reports,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
