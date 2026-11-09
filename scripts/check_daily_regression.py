"""Run a recorded, serial EDA/Tk regression without changing user sessions.

Python 3.6 compatible. Each case has its own directory and log. This harness
adds no product timeout and owns only the isolated Xvfb it starts.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--fanout', type=int, default=25000)
    p.add_argument('--terminals', type=int, default=20000)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--portable-smoke', action='store_true',help='Self-contained fixtures; omit shared historical SoC/performance data')
    p.add_argument('--only',nargs='+',help='Run only named cases (include their dependencies)')
    a = p.parse_args()
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)
    records = []
    status_path = out / 'suite.json'
    if a.resume and status_path.exists():
        records = json.loads(status_path.read_text(encoding='utf-8')).get('cases', [])
    env = dict(os.environ, PYTHONNOUSERSITE='1', PYTHONUNBUFFERED='1')
    env.pop('PYTHONPATH', None)
    display = next(i for i in range(190, 240)
                   if not Path('/tmp/.X11-unix/X'+str(i)).exists()
                   and not Path('/tmp/.X'+str(i)+'-lock').exists())
    xlog = (out/'xvfb.log').open('a')
    xvfb = subprocess.Popen(['/usr/bin/Xvfb', ':'+str(display), '-screen', '0',
                            '1920x1080x24', '-nolisten', 'tcp', '-ac', '-noreset'],
                            stdout=xlog, stderr=subprocess.STDOUT)
    env['DISPLAY'] = ':'+str(display)
    for _ in range(100):
        if xvfb.poll() is not None: raise RuntimeError('Isolated Xvfb exited')
        if Path('/tmp/.X11-unix/X'+str(display)).exists(): break
        time.sleep(.05)
    else: raise RuntimeError('Isolated display failed to start')
    metadata = dict(started_at=datetime.datetime.now().isoformat(), python=sys.version,
                    source=str(ROOT), display=env['DISPLAY'], pid=os.getpid(),
                    fanout=a.fanout, terminals=a.terminals,
                    verdi=shutil.which('verdi'), vcs=shutil.which('vcs'))
    save(out/'environment.json', metadata)
    state = dict(metadata, cases=records, completed=False, passed=False)
    py = sys.executable
    def script(name, *args):
        return [py, str(ROOT/'scripts'/name)] + [str(v) for v in args]
    def run(name, command, dependencies=()):
        if a.only and name not in a.only:return
        existing = next((r for r in records if r['name']==name and r.get('passed')), None)
        if existing and a.resume: return
        failed = [d for d in dependencies if not any(r['name']==d and r.get('passed') for r in records)]
        if failed:
            records.append(dict(name=name, passed=False, skipped_dependencies=failed))
            save(status_path,state); return
        attempt=1+sum(r['name']==name for r in records)
        logfile=out/(name+('' if attempt==1 else '-attempt'+str(attempt))+'.log')
        rec = dict(name=name, attempt=attempt, command=command, started_at=datetime.datetime.now().isoformat(),
                   passed=False, log=str(logfile))
        records.append(rec)
        state['active']=name; save(status_path,state)
        print('BEGIN '+name, flush=True)
        begin=time.monotonic()
        with logfile.open('w', encoding='utf-8') as log:
            process=subprocess.Popen(command,cwd=str(ROOT),env=env,stdout=log,stderr=subprocess.STDOUT)
            rec['pid']=process.pid;save(status_path,state)
            while process.poll() is None:
                rec['elapsed_seconds']=round(time.monotonic()-begin,3);save(status_path,state)
                time.sleep(2)
            rec.update(exit_code=process.returncode, passed=process.returncode==0,
                       seconds=round(time.monotonic()-begin,3))
        save(status_path,state)
        print(('PASS ' if rec['passed'] else 'FAIL ')+name+' '+str(rec['seconds'])+' s',flush=True)
    try:
        run('doctor',[py,'-m','clocktree','doctor','--self-test','--gui','--out',str(out/'doctor')])
        run('auxiliary-native',script('check_auxiliary_ports.py','--out',out/'auxiliary'))
        for phase in ('gui-write','gui-read'):
            run('auxiliary-'+phase,script('check_auxiliary_ports.py','--out',out/'auxiliary','--phase',phase),('auxiliary-native',))
        run('subsystems',script('check_subsystem.py','--out',out/'subsystems'))
        run('subsystem-edges',script('check_subsystem_edges.py','--base',out/'subsystems','--out',out/'subsystem-edges'),('subsystems',))
        run('input-selection',script('check_input_selection_gui.py','--out',out/'input-selection','--request',out/'subsystems/request.json'),('subsystems',))
        clock=out/'subsystems/target/clocktree.json'
        reset=out/'subsystem-edges/reset-target/resettree.json'
        large=ROOT/'validation/resilience/scales/fanout/run/clocktree.json'
        run('workbench',script('check_workbench_gui.py','--out',out/'workbench','--result',clock),('subsystems',))
        run('navigation',script('check_navigation_gui.py','--out',out/'navigation','--clock',clock,'--reset',reset,'--fan-in',1024),('subsystem-edges',))
        branch_args=['--out',out/'branch','--clock',clock,'--reset',reset,'--cycles',50]
        if not a.portable_smoke:branch_args+=['--large',large]
        run('branch',script('check_branch_gui.py',*branch_args),('subsystem-edges',))
        if not a.portable_smoke:
            run('canvas-performance',script('check_canvas_performance.py','--result',large,'--out',out/'canvas-performance.json','--frames',240))
        run('register-1k',script('check_register_trace.py','--out',out/'register-1k','--fanout',1000))
        for phase in ('gui-write','gui-read'):
            run('register-'+phase,script('check_register_trace.py','--out',out/'register-1k','--phase',phase),('register-1k',))
        run('register-recovery',script('check_register_recovery.py','--request',out/'register-1k/request.json','--out',out/'register-recovery'),('register-1k',))
        run('register-large',script('check_register_trace.py','--out',out/'register-large','--fanout',a.fanout))
        run('register-bit-matrix',script('check_register_bit_matrix.py','--out',out/'register-bit-matrix'))
        run('register-progress-gui',script('check_register_progress_gui.py','--base',out/'register-bit-matrix','--out',out/'register-progress-gui'),('register-bit-matrix',))
        run('sv-native',script('check_sv_templates.py','--out',out/'sv-native'))
        for phase in ('gui-write','gui-read'):
            run('sv-'+phase,script('check_sv_templates.py','--out',out/'sv-native','--phase',phase),('sv-native',))
        run('sv-scale',script('check_sv_templates.py','--out',out/'sv-scale','--phase','scale','--native-source',out/'sv-native','--terminals',a.terminals),('sv-native',))
        run('sv-oracle',script('check_sv_scale_oracle.py','--source',out/'sv-scale','--out',out/'sv-oracle','--terminals',a.terminals),('sv-scale',))
        if not a.portable_smoke:
            run('real-memory',script('check_real_memory_threshold.py','--out',out/'real-memory','--gui'))
            run('soc-regression',script('check_target_soc.py','--out',out/'soc-regression'))
        latest={r['name']:r for r in records}
        state.update(completed=True, active=None, passed=all(r.get('passed') for r in latest.values()),
                     finished_at=datetime.datetime.now().isoformat())
        save(status_path,state)
    finally:
        xvfb.terminate()
        try:xvfb.wait(timeout=5)
        except subprocess.TimeoutExpired:xvfb.kill();xvfb.wait()
        xlog.close()
    return 0 if state['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
