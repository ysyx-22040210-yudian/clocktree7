"""Verify automatic GUI restore across fresh Python processes on the VM."""
import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp, read_json, write_json
from capture_x11 import x11_image


def pump(app,predicate=lambda:True,timeout=180):
    started=time.monotonic()
    while not predicate():
        app.update()
        if time.monotonic()-started>timeout:raise AssertionError(app.status.get())
        time.sleep(.01)
    if not app._destroying:
        for _ in range(8):
            app.update()
            time.sleep(.02)


def capture(app,path):
    app.update()
    picture=x11_image()
    x,y=app.winfo_rootx(),app.winfo_rooty()
    picture.crop((x,y,x+app.winfo_width(),y+app.winfo_height())).save(str(path))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',default=str(ROOT/'validation/session-gui'))
    p.add_argument('--session-dir')
    p.add_argument('--phase',choices=['write','read','missing','large_write','large_read','migration','explicit','default_write','default_read'])
    args=p.parse_args()
    out=Path(args.out).resolve()
    out.mkdir(parents=True,exist_ok=True)
    if args.phase is None:
        session=Path(tempfile.mkdtemp(prefix='clocktree7-session-'))
        sources=out/'sources'
        sources.mkdir(exist_ok=True)
        shutil.copy2(str(ROOT/'examples/flexible_layers/clocktree.json'),str(sources/'clocktree.json'))
        shutil.copy2(str(ROOT/'examples/reset_tree/resettree.json'),str(sources/'resettree.json'))
        base=[sys.executable,str(Path(__file__).resolve()),'--out',str(out),'--session-dir',str(session)]
        def phase(name):subprocess.check_call(base+['--phase',name])
        phase('write')
        for name in ('clocktree.json','resettree.json'):
            file=sources/name
            moved=file.with_name(file.name+'.moved')
            if moved.exists():moved.unlink()
            file.rename(moved)
        phase('read')
        # Preserve, then remove only this test's reset snapshot. Its original
        # source is already moved, so the GUI must keep the draft without data.
        manifest=read_json(session/'session.json')
        snapshot=session/manifest['workspaces']['reset']['result']
        snapshot.rename(snapshot.with_suffix('.held'))
        phase('missing')
        phase('migration')
        phase('explicit')
        phase('default_write')
        phase('default_read')
        if (ROOT/'validation/runs/openc910-ordered7-01/clocktree.json').is_file():
            phase('large_write')
            phase('large_read')
        report={'pass':True,'fresh_process_reopen':True,'results_survive_source_move':True,
                'clock_and_reset_drafts':True,'type_edits_preserved':True,'missing_result_keeps_config':True,
                'history_migration':True,'explicit_file_priority':True,'default_user_cache':True,
                'large_graph':read_json(out/'large_read.json') if (out/'large_read.json').is_file() else None}
        write_json(out/'check.json',report)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        return

    phase=args.phase
    directory=Path(args.session_dir)
    run_folder='runs'
    kwargs={}
    if phase.startswith('large_'):
        directory=directory/'large'
        run_folder='large-runs'
    elif phase=='migration':
        directory=directory/'migration'
        run_folder='migration-runs'
        for name,source in [('clock','examples/flexible_layers/clocktree.json'),('reset','examples/reset_tree/resettree.json')]:
            target=out/run_folder/name
            target.mkdir(parents=True,exist_ok=True)
            shutil.copy2(str(ROOT/source),str(target/Path(source).name))
    elif phase=='explicit':
        kwargs['result']=ROOT/'examples/flexible_layers/clocktree.json'
    elif phase.startswith('default_'):
        run_folder='default-runs'
    if not phase.startswith('default_'):kwargs['session_dir']=directory
    errors=[]
    app=ClockTreeApp(runs=out/run_folder,**kwargs)
    app.geometry('1840x1000+20+20')
    app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error=lambda title,error:errors.append(title+': '+str(error))
    # Loading a session may use X11 screenshot tools, but must never start EDA.
    real_popen=subprocess.Popen
    eda=[]
    def observe(command,*a,**kw):
        text=' '.join(map(str,command)) if isinstance(command,(list,tuple)) else str(command)
        if ('clocktree extract' in text or Path(str(command[0])).name in ('verdi','.wrapper')):
            eda.append(text)
            raise AssertionError('Restoring a GUI must not start Verdi')
        return real_popen(command,*a,**kw)
    subprocess.Popen=observe
    try:
        pump(app,lambda:not app.busy)
        if phase=='write':
            assert app.graph is None and len(app.layers)==2
            app.load_result(out/'sources/clocktree.json')
            pump(app,lambda:not app.busy)
            app.select_layer(2)
            index=next(i for i,r in enumerate(app.layers[1]['modules']) if r.get('kind')=='gate')
            old=dict(app.layers[1]['modules'][index])
            new=dict(old,kind='div')
            app.layers[1]['modules'][index]=new
            assert app._apply_kind_change(old,new)
            app.top_var.set('unfinished_clock_draft')
            app.add_layer_button.invoke()
            app.hierarchy_var.set('clock.extra_domain')
            app.switch_tree_type('reset')
            app.load_result(out/'sources/resettree.json')
            pump(app,lambda:not app.busy)
            app.top_var.set('reset_draft_to_reopen')
            app.add_layer_button.invoke()
            app.add_layer_button.invoke()
            app.hierarchy_var.set('reset.extra_domain')
            capture(app,out/'before-close.png')
        elif phase=='read':
            assert app._active_tree_type=='reset' and len(app.layers)==4 and app.active_layer==4
            assert app.top_var.get()=='reset_draft_to_reopen' and app.hierarchy_var.get()=='reset.extra_domain'
            assert len(app.graph['nodes'])==12 and len(app.graph['edges'])==12 and app._config_dirty
            assert not app.graph_file.exists()
            capture(app,out/'after-reopen-reset.png')
            app.switch_tree_type('clock')
            assert app.top_var.get()=='unfinished_clock_draft' and len(app.layers)==3 and app.active_layer==3
            assert app.hierarchy_var.get()=='clock.extra_domain'
            assert len(app.graph['nodes'])==13 and Counter(n.get('kind') for n in app.graph['nodes'])['div']==8
            assert len(app.graph['edges'])==11 and app._config_dirty
            capture(app,out/'after-reopen-clock.png')
            app.switch_tree_type('reset')
        elif phase=='missing':
            assert app._active_tree_type=='reset' and app.top_var.get()=='reset_draft_to_reopen'
            assert len(app.layers)==4 and app.graph is None
            assert '配置已保留' in app.status.get()
            capture(app,out/'missing-result.png')
            app.switch_tree_type('clock')
            assert len(app.graph['nodes'])==13
            app.switch_tree_type('reset')
        elif phase=='migration':
            assert app.graph is not None and '最近历史运行' in app.status.get()
            assert set(app._workspaces)=={'clock','reset'}
        elif phase=='explicit':
            assert app._active_tree_type=='clock' and len(app.graph['nodes'])==13
            assert Counter(n.get('kind') for n in app.graph['nodes'])['div']==3
            assert app.top_var.get()=='flex_top' and '启动指定文件' in app.status.get()
        elif phase=='default_write':
            app.switch_tree_type('reset')
            app.top_var.set('default_cache_draft')
            app.mode_var.set('已有 elab / KDB')
            app.path_var.set('')
        elif phase=='default_read':
            assert app._active_tree_type=='reset' and app.top_var.get()=='default_cache_draft'
            assert app.mode_var.get()=='已有 elab / KDB' and not app.path_var.get()
            assert app._session_store.manifest.is_file()
        elif phase=='large_write':
            app.load_result(ROOT/'validation/runs/openc910-ordered7-01/clocktree.json')
            pump(app,lambda:not app.busy)
            assert len(app.graph['nodes'])==6984 and len(app.graph['edges'])==6983
        elif phase=='large_read':
            assert len(app.graph['nodes'])==6984 and len(app.graph['edges'])==6983
            assert '自动恢复上次' in app.status.get()
            capture(app,out/'large-restored.png')
        assert not errors,errors
        ticks=[0]
        def tick():
            ticks[0]+=1
            if not app._destroying:app.after(25,tick)
        app.after(25,tick)
        started=time.monotonic()
        app.close()
        pump(app,lambda:app._destroying)
        assert not errors and not eda,(errors,eda)
        write_json(out/(phase+'.json'),{'pass':True,'no_eda_process':True,'close_ui_ticks':ticks[0],
                   'close_seconds':round(time.monotonic()-started,3),'cache_directory':str(app._session_store.directory)})
    finally:
        subprocess.Popen=real_popen
        if not app._destroying:app.destroy()


if __name__=='__main__':main()
