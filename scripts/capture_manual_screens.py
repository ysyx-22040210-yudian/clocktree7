"""Capture real native Tk pages used in the GUI manual; Pillow/X11 are QA-only."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from clocktree.gui import ClockTreeApp, RuleDialog
from capture_x11 import x11_image


def settle(app,condition=lambda:True,timeout=30):
    started=time.monotonic()
    while not condition():
        app.update()
        if time.monotonic()-started>timeout:raise RuntimeError('GUI did not finish loading')
        time.sleep(.015)
    for _ in range(8):
        app.update()
        time.sleep(.025)


def capture(window,path):
    window.update()
    image=x11_image()
    x,y=window.winfo_rootx(),window.winfo_rooty()
    image.crop((x,y,x+window.winfo_width(),y+window.winfo_height())).save(str(path))


def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser()
    p.add_argument('--out',default=str(root/'docs/images'))
    args=p.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    errors=[]
    app=ClockTreeApp(result=root/'examples/device_models/clocktree.json',remember=False)
    app.geometry('1840x1000+20+20')
    app.report_callback_exception=lambda *e:errors.append(''.join(traceback.format_exception(*e)))
    try:
        settle(app,lambda:app.graph is not None and not app.busy)
        assert app.graph['summary']['instances']==16
        mux=next(n for n in app.diagram.model.original.values() if n['layer']==2 and n['module']=='ct_mux')
        app.diagram.select(mux)
        app.diagram.fit()
        settle(app)
        capture(app,out/'gui-overview.png')
        app.diagram.locate(mux)
        settle(app)
        app.connections.selection_set('0')
        app.connections.event_generate('<<TreeviewSelect>>')
        settle(app)
        assert 'npiNl' in app.evidence_text.get('1.0','end')
        capture(app,out/'connection-evidence.png')
        app.inspector_tabs.select(2)
        settle(app)
        capture(app,out/'diagnostics.png')
        app.left_tabs.select(1)
        settle(app)
        capture(app,out/'run-history.png')
        app.left_tabs.select(0)
        dialog=RuleDialog(app,2,{'module':'clock_gate','kind':'gate','clock_inputs':['clk0_i','clk1_i'],'clock_output':'clk_o'},lambda item:None)
        dialog.save()
        settle(app)
        assert '一个关注输入' in dialog.error.get()
        capture(dialog,out/'invalid-gate-inputs.png')
        dialog.destroy()
        assert not errors,errors
        print(json.dumps({'pass':True,'screenshots':5,'toolkit':app.tk.call('info','patchlevel'),'source':'actual_native_Tk_pixels'}))
    finally:app.destroy()


if __name__=='__main__':main()
