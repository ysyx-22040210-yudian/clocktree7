"""Native Tk workbench acceptance, independent launches and 100k-node stress."""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp,RuleDialog
from clocktree.session import read_graph
from clocktree.storage import write_graph
from check_indexed_gui import stress_index,capture


def pump(app,predicate=lambda:True,limit=180):
    start=time.monotonic()
    while True:
        app.update()
        if predicate() and not app.diagram.layout_pending and app.diagram._pending is None:break
        if time.monotonic()-start>limit:raise AssertionError('GUI wait: '+app.status.get())
        time.sleep(.008)
    app.update()


def rss():
    return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:')))


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--result',required=True)
    p.add_argument('--phase',choices=['write','read','reset-write','reset-read','stress'])
    args=p.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if not args.phase:
        for phase in ('write','read','reset-write','reset-read','stress'):
            subprocess.check_call([sys.executable,__file__,'--out',str(out),'--result',args.result,'--phase',phase])
        write_graph(out/'acceptance.json',{'passed':True,'phases':{phase:json.loads((out/(phase+'.json')).read_text()) for phase in ('write','read','reset-write','reset-read','stress')}})
        return
    errors=[];report={}
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=args.phase!='stress')
    app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if args.phase in ('write','reset-write'):
        graph=read_graph(args.result)
        if args.phase=='reset-write':
            graph=json.loads(Path(args.result).read_text());graph['tree_type']='reset';graph['config']['tree_type']='reset'
            graph['reset_sources']=graph.pop('clock_sources',[])
            for layer in graph['config']['layers']:
                for rule in layer['modules']:
                    rule['reset_inputs']=rule.pop('clock_inputs')
                    if 'clock_output' in rule:rule['reset_output']=rule.pop('clock_output')
                    if rule['kind']=='gate':rule['kind']='rsy_sync'
            for node in graph['nodes']:
                if node.get('kind')=='gate':node['kind']='rsy_sync'
        app.show_graph(graph);pump(app)
        app.search_var.set('wb_top.g0');app.locate_query();pump(app)
        before=id(app.diagram.model.positions)
        app.search_var.set('wb_top.g1');app.locate_query();pump(app)
        assert id(app.diagram.model.positions)==before,'locate rebuilt an existing view'
        app.diagram.navigate_history();pump(app);assert app.diagram.selected=='wb_top.g0'
        app.diagram.navigate_history(True);pump(app);assert app.diagram.selected=='wb_top.g1'
        app.highlight_path('branch');pump(app);assert len(app.diagram.highlights)==3
        app.diagram.fit_highlight();pump(app)
        app._edge_evidence(app.graph['edges'][0]);assert '验证结论' in app.evidence_text.get('1.0','end')
        app.raw_evidence_var.set(True);app.toggle_raw_evidence();assert '原始证据' in app.evidence_text.get('1.0','end')
        app.raw_evidence_var.set(False);app.toggle_raw_evidence()
        app.jump_neighbour('downstream')
        dialogs=[w for w in app.winfo_children() if w.winfo_class()=='Toplevel']
        assert dialogs,'multiple neighbours should open chooser'
        chooser=dialogs[-1];chooser.search.set('s1');chooser.show(0,True);assert chooser.rows
        first=next(iter(chooser.rows));chooser.table.selection_set(first);chooser.locate();pump(app)
        assert app.selected_object['id']=='wb_top.s1'
        assert app.selected_object.get('output') is None
        app.expand_selected();pump(app)
        if args.phase=='write':
            saved=[];rule=app.layers[1]['modules'][0]
            editor=RuleDialog(app,2,rule,lambda r:saved.append(r));editor.save();assert editor.winfo_exists() and not saved
            editor.edit_inputs()
            from clocktree.input_view import InputPortDialog
            ports=next(w for w in editor.winfo_children() if isinstance(w,InputPortDialog))
            ports.start();ports.cancel()
            pump(app,lambda:ports.process.poll() is not None and str(ports.read_button['state'])=='normal')
            assert not ports.rows
            report['input_discovery_cancelled']=True
            ports.start();pump(app,lambda:ports.process.poll() is not None and bool(ports.rows))
            ports.expression.set('^no_match$');ports.filter();assert not ports.shown
            ports.expression.set('clk');ports.filter();ports.toggle_all(False)
            ports.selected={'clk_bus','aux_clk'};ports.refresh_checks();pump(app)
            capture(ports,out/'subsystem-preview.png')
            ports.confirm();editor.save()
            assert saved and saved[0]['clock_inputs']==['aux_clk','clk_bus'] and 'clock_output' not in saved[0]
            app.layers[1]['modules'][0]=saved[0]
            report['real_eda_editor_confirmed']=True
            app.diagram.fit();pump(app);capture(app,out/'compact-branch.png')
            app.toggle_focus();pump(app);app.diagram.fit();pump(app)
            capture(app,out/'compact-focus.png');app.toggle_focus();pump(app)
        app.layout_var.set('按层排列');app.change_layout();pump(app)
        app.search_var.set('explicit-'+args.phase);app.locate_query()  # unsuccessful explicit searches also persist
        app._save_session_now();pump(app)
        capture(app,out/(args.phase+'.png'))
        report.update(searches=app.ui_state.get(app.graph['config'])['searches'],physical_instances=len(app.graph['nodes']),display_boxes=len(app.diagram.model.nodes))
    elif args.phase in ('read','reset-read'):
        assert app.graph is not None
        expected='reset' if args.phase=='reset-read' else 'clock'
        assert app._active_tree_type==expected
        assert app.layout_var.get()=='按层排列'
        searches=list(app.search_entry['values']);assert ('explicit-reset-write' if expected=='reset' else 'explicit-write') in searches
        if expected=='reset':assert 'explicit-write' not in searches
        report.update(tree_type=expected,searches=searches,restored=True)
        capture(app,out/(args.phase+'.png'))
    else:
        graph=stress_index(out/'stress-data',99999)
        app.show_graph(graph);pump(app)
        assert len(graph['nodes'])==100000
        app.diagram.mode='instances';app.diagram.rebuild();app.diagram.mode='hierarchy';app.diagram.rebuild();pump(app)
        assert not app.diagram.layout_pending
        report['stale_layout_discarded']=True
        old_positions=app.diagram.model.positions
        app.diagram.layout_mode='layers';app.diagram.rebuild();app.diagram.cancel_layout();pump(app)
        assert app.diagram.model.positions is old_positions
        app.diagram.layout_mode='branch'
        report['layout_cancelled_and_view_restored']=True
        root=app.diagram.model.original['stress.domain.root']
        app.diagram.locate(root);pump(app)
        app.highlight_path('branch');pump(app);assert len(app.diagram.highlights)==99999
        app.search_var.set('stress.domain.g099998.u');app.locate_query();pump(app)
        assert app.selected_object['id']=='stress.domain.g099998.u'
        assert len(app.diagram.highlights)==99999
        counts=[];memory=[]
        for cycle in range(6):
            for step in range(24):
                app.diagram.zoom(1.03 if step<12 else 1/1.03,600,400)
                app.diagram.ox+=6 if step<12 else -6;app.diagram.schedule();pump(app)
            time.sleep(.12);pump(app)
            counts.append(len(app.diagram.canvas.find_all()));memory.append(rss())
        assert max(counts)-min(counts)<100
        assert memory[-1]-min(memory)<16*1024,(memory,counts)
        report.update(instances=100000,highlight_edges=len(app.diagram.highlights),items=counts,rss_kib=memory,
                      text_cache=len(getattr(app.diagram.canvas,'_text_cache',{})),font_cache=len(app.diagram._font_cache))
        capture(app,out/'stress.png')
    report['callback_errors']=errors
    write_graph(out/(args.phase+'.json'),report)
    assert not errors,errors
    app.close()
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:app.update()
        except Exception:break
        if app._destroying:break
        time.sleep(.01)
    print('GUI_ACCEPTED '+args.phase,flush=True)


if __name__=='__main__':main()
