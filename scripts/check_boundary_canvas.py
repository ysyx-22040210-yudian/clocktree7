"""Exercise native boundary-signal double-clicks on saved results and a multi-channel fixture."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clocktree.branch_view import BoundarySignalDialog, BranchDialog
from clocktree.gui import ClockTreeApp
from clocktree.l1_inputs import entries
from clocktree.networks import memberships
from clocktree.session import read_graph
from clocktree.storage import index_graph, new_store


def proof(port, kind='port', scope='top.ifc'):
    signal = scope + '.' + port
    return dict(status='verified', scope=scope, port=port, signal=signal, layer=2,
                source_kind=kind, evidence=[dict(type='npiNetBit', name=signal)])


def fixture():
    specs = [
        ('r', 'gate', ['i'], 'o'), ('other', 'gate', ['i'], 'o'), ('solo', 'gate', ['i'], 'o'),
        ('m', 'mux', ['a', 'b', 'test'], 'o'), ('w', 'wire', ['i0', 'i1'], None),
        ('g', 'gate', ['i'], 'o'), ('end', 'subsystem', ['a','b','c','direct','foreign'], None)]
    nodes = {k:dict(id='top.'+k, path='top.'+k, kind=kind, module=kind, layer=2 if k=='end' else 1,
                    inputs=pins, output=out, root=k in ('r','other','solo','w'), terminal=k in ('solo','end'))
             for k,kind,pins,out in specs}
    nodes['w'].update(outputs=['x','y'], internal_connections=[
        dict(input='i0',output='x',status='verified'), dict(input='i1',output='y',status='verified'),
        dict(input='i0',output='y',status='unresolved')])
    main, other = proof('clk[3]'), proof('clk[4]')
    nodes['r']['input_boundaries'] = {'i':main}
    nodes['solo']['input_boundaries'] = {'i':main}
    nodes['other']['input_boundaries'] = {'i':other}
    nodes['w']['input_boundaries'] = {'i0':main,'i1':other}
    nodes['end']['input_boundaries'] = {'direct':main,'foreign':other}
    nodes['m']['input_boundaries'] = {'test':proof('test_clk','wire')}
    connections = [('r','o','m','a'),('other','o','m','b'),('m','o','g','i'),('g','o','end','c'),
                   ('w','x','end','a'),('w','y','end','b')]
    edges = [dict(id=str(i),source='top.'+s,target='top.'+t,source_pin=sp,target_pin=tp,
                  layer_delta=nodes[t]['layer']-nodes[s]['layer'],
                  evidence={'driver':[dict(type='port',name='top.'+s+'.'+sp)]})
             for i,(s,sp,t,tp) in enumerate(connections)]
    lookup = {node['id']:node for node in nodes.values()}
    components = memberships(lookup, edges, [])
    return dict(nodes=list(lookup.values()), edges=edges, components=components, boundaries=[],
                summary={'instances':len(nodes),'edges':len(edges),'connectivity_complete':True},
                tree_type='clock',config=dict(tree_type='clock',design={'top':'top'},
                    layers=[dict(index=i,hierarchy='top',modules=[]) for i in (1,2)]))


def grouped_fixture(total=105):
    nodes = []
    for i in range(total):
        node_id = 'top.grp.u{:03d}'.format(i)
        port = 'clk_{:03d}'.format(max(0,i-1))
        nodes.append(dict(id=node_id,path=node_id,module='gate',kind='gate',inputs=['i'],output='o',
                          root=True,terminal=True,layer=1,component=0,input_boundaries={'i':proof(port)}))
    return dict(nodes=nodes,edges=[],boundaries=[],components=[dict(id=0,nodes=[n['id'] for n in nodes])],
                config=dict(tree_type='clock',design={'top':'top'},layers=[dict(index=1,hierarchy='top',modules=[])]),
                summary={'instances':total,'edges':0,'connectivity_complete':True},tree_type='clock')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True)
    parser.add_argument('--result', action='append', default=[])
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False)
    app.geometry('1800x950+0+0')
    errors, cases = [], []
    app.report_callback_exception = lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title,error:errors.append(title+' '+str(error))
    tick = [10000]

    def wait(predicate):
        deadline = time.monotonic()+45
        while time.monotonic()<deadline:
            app.update()
            assert not errors, errors
            if predicate():return
            time.sleep(.01)
        raise AssertionError('Timed out waiting for Tk: '+repr(errors))

    def stable(diagram):
        return diagram.model is not None and not diagram.layout_pending and diagram._pending is None

    def ready(window):
        wait(lambda:window.load_error or (window.graph is not None and stable(window.diagram)))
        assert not window.load_error, window.details.get('1.0','end')

    def state(diagram):
        return (id(diagram.model.positions),diagram.scale,diagram.ox,diagram.oy,diagram.scope,diagram.component)

    def double(diagram, node, kind=BranchDialog):
        diagram.locate(node)
        wait(lambda:stable(diagram))
        before=state(diagram)
        x,y,w,h=diagram.model.positions[node['id']]
        x,y=map(int,diagram.point(x+w/2,y+h/2))
        old=set(app.winfo_children())
        tick[0]+=2000
        for i in (0,1):
            diagram.canvas.event_generate('<ButtonPress-1>',x=x,y=y,time=tick[0]+i*100)
            diagram.canvas.event_generate('<ButtonRelease-1>',x=x,y=y,time=tick[0]+i*100+30)
        wait(lambda:any(isinstance(w,kind) and w not in old for w in app.winfo_children()))
        child=next(w for w in app.winfo_children() if isinstance(w,kind) and w not in old)
        if kind is BranchDialog:ready(child)
        assert state(diagram)==before
        return child

    try:
        cases_to_open=[('memory', fixture()),('index',index_graph(fixture(),new_store(out/'fixture-index')))]
        cases_to_open.extend((p,read_graph(Path(p))) for p in args.result)
        for label,graph in cases_to_open:
            app.show_graph(graph)
            wait(lambda:stable(app.diagram))
            model=app.diagram.model
            all_signals={}
            for n in graph['nodes']:
                for pin,entry in entries(n).items():
                    all_signals.setdefault(entry['signal'],set()).add((n['id'],pin))
            markers={}
            for n in model.l1_nodes.values():
                if not n['input_entry'].get('aggregate_count'):
                    markers[n['input_entry']['signal']]=n
            assert markers and set(markers)==set(all_signals), (label,markers.keys(),all_signals.keys())
            for signal,node in sorted(markers.items()):
                w=double(app.diagram,node)
                def check_window():
                    shown=w.diagram.model
                    assert signal in w.title() and w.graph['branch_view']['direct_loads']==len(all_signals[signal])
                    assert w.selected_object['id']==node['id'] and len(shown.l1_nodes)==1
                    actual={(shown.nodes[e['target']].get('reference_of',e['target']),e['target_pin']) for e in shown.l1_edges.values()}
                    assert actual==all_signals[signal],(actual,all_signals[signal])
                    assert signal in shown.svg()
                check_window()
                w.retry_button.invoke()
                ready(w)
                check_window()
                if label in ('memory','index') and signal=='top.ifc.clk[3]':
                    assert {e['id'] for e in w.graph['edges']}=={'0','2','3','4'}
                    direct=next(e for e in w.diagram.model.l1_edges.values() if e['target_pin']=='direct')
                    w.diagram.locate_edge(direct,'downstream')
                    assert w.selected_object['reference_of']=='top.end' and w.selected_object['inputs']==['direct']
                    w.jump_neighbour('upstream')
                    assert w.selected_object['id']==node['id']
                    w.inspect_edges([direct])
                    detail=w.details.get('1.0','end')
                    assert signal in detail and 'top.end.direct' in detail
                    if label=='index':
                        (out/'boundary-example.svg').write_text(w.diagram.model.svg(),encoding='utf-8')
                    nested=double(w.diagram,w.diagram.model.l1_nodes[node['id']])
                    w.close()
                    nested.retry_button.invoke()
                    ready(nested)
                    w=nested
                    check_window()
                cases.append(dict(result=label,tree_type=model.tree_type,signal=signal,kind=node['kind'],
                                  source_kind=node['input_entry'].get('source_kind','port'),
                                  direct_inputs=len(all_signals[signal]),instances=len(w.graph['nodes']),
                                  device_edges=len(w.graph['edges'])))
                w.close()
        overview=index_graph(grouped_fixture(),new_store(out/'group-index'))
        app.show_graph(overview)
        wait(lambda:stable(app.diagram))
        app.diagram.mode='hierarchy'
        app.diagram.depth=1
        app.diagram.rebuild()
        wait(lambda:stable(app.diagram))
        bundle=next(n for n in app.diagram.model.l1_nodes.values() if n.get('boundary_group'))
        chooser=double(app.diagram,bundle,BoundarySignalDialog)
        assert len(chooser.rows)==100
        chooser.next.invoke()
        assert len(chooser.rows)==4
        chooser.search.set('clk_000')
        chooser.show(0)
        assert chooser.rows==[('top.ifc.clk_000','clk_000',2)]
        chooser.open_button.invoke()
        w=next(w for w in app.winfo_children() if isinstance(w,BranchDialog))
        ready(w)
        assert w.graph['branch_view']['direct_loads']==2 and len(w.graph['edges'])==0 and len(w.diagram.model.l1_nodes)==1
        w.close()
        report=dict(passed=True,cases=cases,native_double_click=True,reload=True,
                    nested_window=True,direct_port_roundtrip=True,group_pagination_and_search=True,
                    python=sys.version.split()[0],tk=str(app.tk.call('info','patchlevel')),errors=errors)
        (out/'acceptance.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        print('Verified {} signal canvases and one mixed overview group'.format(len(cases)),flush=True)
    finally:
        app.destroy()


if __name__=='__main__':
    main()
