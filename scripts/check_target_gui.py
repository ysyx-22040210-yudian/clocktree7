"""Native Tk mode selection, evidence, exports and fresh-process restoration."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.config import EXTRACTION_MODES
from clocktree.gui import ClockTreeApp
from clocktree.storage import IndexedGraph, write_graph
from check_flexible_gui import pump,capture


def phase(out, fixtures, large, stage):
    errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session')
    app.geometry('1840x1000+20+20')
    app.report_callback_exception=lambda *e:errors.append(str(e))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy)
    if stage=='write':
        app.load_result(fixtures/'clock-2/target/clocktree.json');pump(app,lambda:not app.busy)
        assert app.extraction_mode_var.get()==EXTRACTION_MODES['target']
        assert '目标结构连接已解析' in app.state.get()
        edge=next(iter(app.graph['edges']));app._edge_evidence(edge)
        text=app.evidence_text.get('1.0','end')
        assert '输入反查已找到此输出' in text and '输出全负载方向：未执行' in text
        assert '验证结论' in text and 'npiPort' not in text
        app.raw_evidence_var.set(True);app.toggle_raw_evidence()
        text=app.evidence_text.get('1.0','end')
        assert '来源端例化连接' in text and 'npiPort' in text
        app.raw_evidence_var.set(False);app.toggle_raw_evidence()
        app.inspector_tabs.select(1);pump(app)
        app.export_svg(out/'clock.svg');ET.parse(str(out/'clock.svg'))
        app.export_result(out/'clock.json')
        assert json.loads((out/'clock.json').read_text())['summary']['extraction_mode']=='target'
        capture(app,out/'target-workbench.png')
        app.extraction_mode_var.set(EXTRACTION_MODES['full']);pump(app)
        assert app.get_config()['extraction_mode']=='full'
        app.save_config(out/'full-mode-config.json')
        assert json.loads((out/'full-mode-config.json').read_text())['extraction_mode']=='full'
        app.switch_tree_type('reset');pump(app,lambda:not app.busy)
        app.load_result(fixtures/'reset-2/target/resettree.json');pump(app,lambda:not app.busy)
        assert app.extraction_mode_var.get()==EXTRACTION_MODES['target']
        assert len(app.graph['nodes'])==12
        app.export_svg(out/'reset.svg');ET.parse(str(out/'reset.svg'))
        capture(app,out/'reset-workbench.png')
    else:
        assert app._active_tree_type=='reset' and len(app.graph['nodes'])==12
        assert app.extraction_mode_var.get()==EXTRACTION_MODES['target']
        app.switch_tree_type('clock');pump(app,lambda:not app.busy)
        assert app.extraction_mode_var.get()==EXTRACTION_MODES['full']
        assert len(app.graph['nodes'])==13
        assert app.graph['summary']['extraction_mode']=='target'
        app.load_result(fixtures/'clock-2/full/clocktree.json');pump(app,lambda:not app.busy)
        assert app.extraction_mode_var.get()==EXTRACTION_MODES['full']
        app._edge_evidence(next(iter(app.graph['edges'])))
        assert '未执行输出全负载核验' not in app.evidence_text.get('1.0','end')
        if large:
            app.load_result(large);pump(app,lambda:not app.busy)
            assert isinstance(app.graph,IndexedGraph)
            assert len(app.graph['nodes'])==8195
            assert len(app.diagram.model.nodes)<len(app.graph['nodes'])
            node=app.diagram.model.original['fan_top.domain.g8191']
            app.diagram.locate(node);pump(app)
            assert node['id'] in app.prop_text.get('1.0','end')
            capture(app,out/'large-indexed-locate.png')
    assert not errors,errors
    write_graph(out/(stage+'.json'),{'passed':True,'native_tk':True,'tk':app.tk.call('info','patchlevel'),
                'mode_selection':True,'evidence_labels':True,'fresh_process':stage=='read','errors':errors})
    app.close()
    try:
        if not app._destroying:pump(app,lambda:app._destroying)
    except tk.TclError:
        if not app._destroying:raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True);parser.add_argument('--fixtures',required=True)
    parser.add_argument('--large');parser.add_argument('--phase',choices=['write','read'])
    args=parser.parse_args();out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    if args.phase:return phase(out,Path(args.fixtures).resolve(),Path(args.large).resolve() if args.large else None,args.phase)
    for stage in ('write','read'):
        command=[sys.executable,__file__,'--out',str(out),'--fixtures',args.fixtures,'--phase',stage]
        if args.large:command+=['--large',args.large]
        subprocess.check_call(command)
    write_graph(out/'acceptance.json',{'passed':True,'phases':['write','read'],'fresh_process':True})
    print('Target GUI acceptance passed')


if __name__=='__main__':main()
