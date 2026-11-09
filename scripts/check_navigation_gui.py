"""Native Tk regression for navigation to exact multi-input branch endpoints."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree import indexed_view
from clocktree.branch_view import BranchDialog
from clocktree.gui import ClockTreeApp
from clocktree.navigation import NeighbourDialog
from clocktree.networks import memberships
from clocktree.storage import index_graph, new_store
from check_indexed_gui import capture


def synthetic(count):
    nodes=[dict(id='n.g'+str(i),path='n.g'+str(i),module='gate',kind='gate',layer=1,
                inputs=['i'],output='o',root=True,terminal=False) for i in range(count)]
    nodes.append(dict(id='n.s',path='n.s',module='sub',kind='subsystem',layer=2,
                      inputs=['clk[{}]'.format(i) for i in range(count)],output=None,root=False,terminal=True))
    edges=[dict(id=str(i),source='n.g'+str(i),target='n.s',source_pin='o',target_pin='clk[{}]'.format(i),
                layer_delta=1,evidence={}) for i in range(count)]
    return dict(nodes=nodes,edges=edges,components=memberships({n['id']:n for n in nodes},edges,[]),
                boundaries=[],tree_type='clock',metadata=dict(synthetic=True),
                summary=dict(instances=count+1,edges=count,extraction_mode='target',connectivity_complete=False),
                config=dict(tree_type='clock',design=dict(top='n'),layers=[dict(index=i,hierarchy='n',modules=[]) for i in (1,2)]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    parser.add_argument('--clock',required=True)
    parser.add_argument('--reset',required=True)
    args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    errors=[];reports={}
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False)
    app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))

    def pump(window=app,predicate=lambda:True,limit=90):
        start=time.monotonic()
        while True:
            app.update()
            if predicate() and not window.diagram.layout_pending and window.diagram._pending is None:break
            if time.monotonic()-start>limit:raise AssertionError('GUI wait: '+str(errors))
            time.sleep(.008)
        app.update()
        assert not errors,errors

    def checked(window,edge,endpoint='target'):
        pump(window)
        d=window.diagram;chosen=d.model.nodes[d.selected]
        assert chosen.get('reference_of',chosen['id'])==edge[endpoint],chosen
        assert d.selection_target['pin']==edge[endpoint+'_pin'],d.selection_target
        assert d.selection_target['edge']==edge['id']
        assert d.highlights=={edge['id']},d.highlights
        if chosen.get('reference_of'):assert chosen['reference_source']==edge['source'],chosen
        assert any(e['id']==edge['id'] for e in d.model.edges)
        tag=d.scene.nodes[d.selected][0]
        assert any(d.canvas.type(i)=='oval' and d.canvas.itemcget(i,'outline')=='#78caff'
                   for i in d.canvas.find_withtag(tag)),'Exact port must have a visible blue ring'

    def chooser(window,direction='downstream'):
        before={w for w in window.winfo_children() if isinstance(w,NeighbourDialog)}
        window.jump_neighbour(direction);pump(window)
        created=[w for w in window.winfo_children() if isinstance(w,NeighbourDialog) and w not in before]
        assert len(created)==1,'Multiple endpoint ports must open a chooser'
        return created[0]

    def choose(dialog,edge):
        key=next(k for k,e in dialog.rows.items() if e['id']==edge['id'])
        dialog.table.selection_set(key);dialog.locate()

    pump()
    for mode,path in (('clock',args.clock),('reset',args.reset)):
        original=json.loads(Path(path).read_text(encoding='utf-8'))
        for indexed in (False,True):
            label=mode+('-indexed' if indexed else '-memory')
            graph=index_graph(original,new_store(out/(label+'-data'))) if indexed else copy.deepcopy(original)
            app.show_graph(graph);pump()
            model=app.diagram.model
            edge=next(e for e in model.original_edges.values() if e['source']=='wb_top.g1' and e['target']=='wb_top.s1' and e['target_pin']=='aux_clk')
            first=next(n for n in model.nodes.values() if n.get('reference_of')=='wb_top.s1')
            assert first['reference_source']=='wb_top.g0','Fixture must reproduce the old first-reference bug'
            app.diagram.locate(model.original['wb_top.g1']);pump()
            choose(chooser(app),edge);checked(app,edge)
            app.diagram.navigate_history();pump();assert app.diagram.selected=='wb_top.g1'
            app.diagram.navigate_history(True);checked(app,edge)
            app.expand_selected();checked(app,edge)
            app.expand_selected();checked(app,edge)
            # Restrict view to the wrong input branch. Exact navigation must leave it.
            app.diagram.component=0;app.diagram.rebuild();pump()
            assert not any(n.get('reference_source')=='wb_top.g1' for n in model.nodes.values())
            app.diagram.locate_edge(edge,'downstream');checked(app,edge)
            # Inspector connection list uses the same exact edge contract.
            app.diagram.locate(model.original['wb_top.g1']);pump()
            key=next(k for k,e in app.related_edges.items() if e['id']==edge['id'])
            app.connections.selection_set(key);app.locate_related();checked(app,edge)
            app.jump_neighbour('upstream');checked(app,edge,'source')
            # A branch containing all subsystem inputs still chooses the right reference.
            branch=app.open_branch(model.original['wb_top.s1'])
            pump(branch,lambda:branch.graph is not None)
            branch.diagram.locate(branch.diagram.model.original['wb_top.g1']);pump(branch)
            choose(chooser(branch),edge);checked(branch,edge)
            branch.diagram.navigate_history();pump(branch);assert branch.diagram.selected=='wb_top.g1'
            branch.diagram.navigate_history(True);checked(branch,edge)
            branch.diagram.fit();branch.lift();pump(branch);capture(branch,out/(label+'-branch.png'))
            branch.close();pump()
            app.diagram.locate_edge(edge,'downstream');checked(app,edge)
            app.lift();pump();capture(app,out/(label+'.png'))
            reports[label]=dict(exact_reference=True,exact_pin=True,connection_list=True,blue_port_marker=True,
                               history=True,bus_rebuild=True,cross_component=True,independent_branch=True)
            # Stale chooser cannot apply an edge to a newly loaded design.
            app.diagram.locate(model.original['wb_top.g1']);pump()
            stale=chooser(app);key=next(iter(stale.rows));stale.table.selection_set(key)
            app.show_graph(copy.deepcopy(original));pump();stale.locate();pump()
            assert app.diagram.selected is None
            reports[label]['stale_dialog_guard']=True
        # With one endpoint the button directly jumps, even if wrong reference is first.
        direct=copy.deepcopy(original)
        direct['edges']=[e for e in direct['edges'] if e['source']!='wb_top.g1' or e['id']==edge['id']]
        direct['summary']['edges']=len(direct['edges'])
        app.show_graph(direct);pump()
        app.diagram.locate(app.diagram.model.original['wb_top.g1']);pump()
        app.jump_neighbour('downstream');checked(app,edge)
        reports[mode+'-direct']=True

    # Plain MUX inputs also retain their exact port and traversed wire.
    mux=synthetic(2);mux['nodes'][-1].update(kind='mux',module='mux',inputs=['a','b'],output='y',aux_ports=dict(pll_sel='sel'))
    for edge,pin in zip(mux['edges'],('a','b')):edge['target_pin']=pin
    for node in mux['nodes']:
        for key in ('components','component','input_components'):node.pop(key,None)
    mux['components']=memberships({n['id']:n for n in mux['nodes']},mux['edges'],[])
    app.show_graph(mux);pump();app.diagram.locate(app.diagram.model.original['n.g1']);pump()
    app.jump_neighbour('downstream');checked(app,mux['edges'][1]);reports['mux_exact_input']=True
    app.locate_issue_object('n.s','sel');pump()
    assert app.diagram.selected=='n.s' and app.diagram.selection_target['pin']=='sel'
    reports['auxiliary_issue_location_preserved']=True
    app.diagram.issue_port=(None,None)

    # Force bounded focus on a small fixture so the last endpoint is outside
    # the first neighbourhood. This is a deterministic limit regression, not a pressure test.
    original_limit=indexed_view.DETAIL_LIMIT
    try:
        indexed_view.DETAIL_LIMIT=8
        graph=index_graph(synthetic(128),new_store(out/'bounded-focus'))
        app.show_graph(graph);pump()
        early=app.diagram.model.original_edges['0'];late=app.diagram.model.original_edges['127']
        app.diagram.locate_edge(early,'downstream');checked(app,early)
        app.diagram.locate_edge(late,'downstream');checked(app,late)
        assert app.diagram.model.focus_edge=='127'
        app.diagram.navigate_history();checked(app,early)
        app.diagram.navigate_history(True);checked(app,late)
        # Upstream chooser can page beyond 100 edges without changing target port.
        dialog=NeighbourDialog(app,app.diagram.model.original['n.s'],'upstream')
        assert len(dialog.rows)==100
        dialog.show(1);assert len(dialog.rows)==28
        choose(dialog,late);checked(app,late,'source')
        app.lift();pump();capture(app,out/'bounded-focus.png')
        reports['bounded_focus']=dict(instances=129,edges=128,test_detail_limit=8,required_edge_preserved=True,
                                     same_instance_cross_focus_history=True,chooser_second_page=True)
    finally:indexed_view.DETAIL_LIMIT=original_limit
    reports.update(passed=True,callback_errors=errors,python=sys.version,tk=app.tk.call('info','patchlevel'))
    app.close()
    (out/'acceptance.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(reports,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
