"""Exercise actual Tk save dialogs, not the export(path) test shortcut."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp
from clocktree.session import read_graph
from clocktree.sv_view import SVDialog
from clocktree.storage import write_graph
from check_workbench_gui import pump
from check_indexed_gui import capture
from check_sv_branch_endpoints import TEMPLATES


def native_save(app,action,destination):
    """Choose a filename in Tk's real file chooser, including its type filter."""
    info={};deadline=time.monotonic()+20
    def visit(path):
        result=[path]
        for child in app.tk.splitlist(app.tk.call('winfo','children',path)):result.extend(visit(child))
        return result
    def accept():
        try:
            windows=[w for w in visit('.')
                if app.tk.call('winfo','class',w)=='TkFDialog']
            if not windows:
                if time.monotonic()<deadline:app.after(50,accept);return
                info['error']='save dialog did not open';app.destroy();return
            widgets=visit(windows[0]);entries=[];buttons=[]
            for w in widgets:
                cls=app.tk.call('winfo','class',w)
                if cls in ('Entry','TEntry'):entries.append((w,str(app.tk.call(w,'get'))))
                if cls in ('Button','TButton'):buttons.append((w,str(app.tk.call(w,'cget','-text'))))
            info.update(entries=entries,buttons=buttons)
            field=next(w for w,value in entries if value.endswith(('.sv','.json')))
            save=next(w for w,value in buttons if value in ('Save','保存'))
            info['initial_name']=str(app.tk.call(field,'get'))
            app.tk.call(field,'delete',0,'end');app.tk.call(field,'insert',0,str(destination))
            app.tk.call(save,'invoke');info['accepted']=True
        except Exception:
            info['error']=traceback.format_exc()
            # Never leave a failed test blocked inside a modal file dialog.
            for w in visit('.'):
                if app.tk.call('winfo','class',w)=='TkFDialog':app.tk.call('destroy',w)
    app.after(150,accept);action()
    assert info.get('accepted'),info
    return info


def main(out,source,baseline):
    out.mkdir(parents=True,exist_ok=True);exports=out/'exports';exports.mkdir();errors=[]
    app=ClockTreeApp(runs=out/'runs',session_dir=out/'session',remember=False);app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *args:errors.append(''.join(traceback.format_exception(*args)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    pump(app,lambda:not app.busy);app.show_graph(read_graph(source/'tree/clocktree.json'));pump(app)
    app.ui_state.update(app.graph['config'],register_trace_directory=str(source/'registers'))
    dialog=SVDialog(app);pump(app)
    for key,editor in dialog.editors.items():editor.delete('1.0','end');editor.insert('1.0',TEMPLATES[key])
    dialog.analyze_button.invoke();pump(app,lambda:dialog.plan is not None and dialog.process.poll() is not None and dialog.analyze_button.instate(['!disabled']))
    if not baseline:
        wrong_backup=exports/'template-disguised.sv'
        native_save(app,dialog.save_templates_button.invoke,wrong_backup)
        assert not wrong_backup.exists() and '模板备份必须使用' in dialog.status.get()
        wrong_code=exports/'code-disguised.json';before=dialog.process
        native_save(app,dialog.export_button.invoke,wrong_code)
        assert dialog.process is before and not wrong_code.exists() and 'SV 代码文件必须使用' in dialog.status.get()
    saved=out/'templates.json';template_dialog=native_save(app,dialog.save_templates_button.invoke if hasattr(dialog,'save_templates_button') else dialog.save_templates,saved)
    assert json.loads(saved.read_text())==TEMPLATES
    target=exports/'actual.sv';save_dialog=native_save(app,dialog.export_button.invoke,target)
    pump(app,lambda:dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
    result=json.loads((dialog.directory/'export.log').read_text());content=target.read_text()
    assert result['sv_written'] and result['blocks']==15 and content.count('// CT7 branch ')==15
    assert '$var_' not in content and 'initial $display(' in content and not content.lstrip().startswith('{')
    if not baseline:
        assert sorted(p.name for p in exports.iterdir())==['actual.sv']
        assert dialog.tabs.select()==str(dialog.output_tab)
        assert dialog.output_code.get('1.0','end-1c')==content
        assert dialog.export_result['sv']==str(target)
        assert Path(result['mapping']).parent==dialog.directory
        capture(dialog,out/'generated-sv.png')
        dialog.close();dialog=SVDialog(app);pump(app)
        assert dialog.plan and dialog.export_result['sv']==str(target)
        assert dialog.output_code.get('1.0','end-1c')==content
    else:capture(dialog,out/'baseline-export.png')
    blocked=exports/'blocked.sv'
    for editor in dialog.editors.values():editor.delete('1.0','end');editor.insert('1.0','$var_unknown')
    blocked_dialog=native_save(app,dialog.export_button.invoke,blocked)
    pump(app,lambda:dialog.process.poll() is not None and dialog.export_button.instate(['!disabled']))
    rejected=json.loads((dialog.directory/'export.log').read_text())
    assert not rejected['sv_written'] and rejected['blocks']==0 and not blocked.exists()
    if not baseline:
        assert dialog.process.returncode==2
        assert '未生成 SV' in dialog.status.get() and 'var_unknown' in dialog.output_code.get('1.0','end')
        assert not (exports/'blocked.sv.mapping.json').exists()
        capture(dialog,out/'blocked-export.png')
    if not baseline:
        build=out/'compile';build.mkdir()
        checks=out/'checks.sv';checks.write_text('module template_checks;\n'+content+'\nendmodule\n')
        with (build/'compile.log').open('w') as log:
            subprocess.check_call(['vcs','-full64','-sverilog','-kdb=only','-lca','-debug_access+all','-top','sv_ends','-top','template_checks',
                str(source/'fixture/rtl.sv'),str(checks)],cwd=str(build),stdout=log,stderr=subprocess.STDOUT)
    assert not errors,errors
    write_graph(out/'acceptance.json',dict(passed=True,baseline=baseline,actual_sv=True,blocks=15,template_dialog=template_dialog,
        save_dialog=save_dialog,blocked_dialog=blocked_dialog,blocked_exit_code=dialog.process.returncode,blocked_status=dialog.status.get(),
        sv_vcs_elaborated=not baseline,files=sorted(p.name for p in exports.iterdir()),errors=errors))
    dialog.close();app.close();print('SV_EXPORT_FILES_PASS',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--baseline',action='store_true');a=p.parse_args();main(a.out.resolve(),a.source.resolve(),a.baseline)
