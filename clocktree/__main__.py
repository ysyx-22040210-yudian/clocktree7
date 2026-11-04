import argparse
import json

from .extract import extract
from .memory import MemoryResumeError


def main():
    parser = argparse.ArgumentParser(description="ClockTree7 full RTL clock/reset network extraction")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("extract")
    run.add_argument("--config", required=True)
    run.add_argument("--out", default=".", help="Output directory; defaults to the current directory and can be reused")
    run.add_argument('--memory-limit-gib', type=float, help='Verdi + extraction RSS limit; default 60 GiB or config memory_limit_gib. 0 disables rotation.')
    recover = commands.add_parser('resume', help='Continue an interrupted job from its last committed checkpoint')
    recover.add_argument('--run', required=True, help='Output directory or .clocktree-runs/<run-id>')
    recover.add_argument('--memory-limit-gib', type=float, help='Override the memory limit while preserving the original extraction rules')
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8767, type=int)
    serve.add_argument("--runs")
    serve.add_argument("--result")
    gui = commands.add_parser("gui", help="Native Tk/ttk clock and reset architecture desktop application")
    gui.add_argument("--runs")
    gui.add_argument("--result")
    gui.add_argument("--config")
    gui.add_argument("--no-session",action="store_true",help="Start an isolated GUI without automatic session restore/save")
    doctor = commands.add_parser('doctor', help='Check this device and optionally run real EDA clock/reset acceptance')
    doctor.add_argument('--gui', action='store_true', help='Briefly open the native Tk workbench and check both rendered architectures')
    doctor.add_argument('--self-test', action='store_true', help='Run real clock/reset extraction and compare complete golden sets (no timeout)')
    doctor.add_argument('--out', help='Report/self-test directory; a new subdirectory is created for each run')
    doctor.add_argument('--json', action='store_true', help='Print the machine-readable report')
    doctor.add_argument('--viewer-only', action='store_true', help='Check result browsing without requiring EDA (Windows/Linux)')
    diagnosis=commands.add_parser('diagnose',help='Inspect durable stages after an interrupted run; does not launch EDA')
    where=diagnosis.add_mutually_exclusive_group(required=True)
    where.add_argument('--run',help='Output directory or .clocktree-runs/<run-id>')
    where.add_argument('--gui-latest',action='store_true',help='Inspect this user\'s latest and previous GUI sessions')
    diagnosis.add_argument('--json',action='store_true')
    args = parser.parse_args()
    if args.command == 'resume':
        from .recovery import resume, RecoveryError
        try:graph = resume(args.run, args.memory_limit_gib)
        except (RecoveryError, MemoryResumeError, ValueError, OSError) as error:
            parser.exit(1, 'ClockTree7: ' + str(error) + '\n')
        print(json.dumps(graph['summary'], indent=2))
        return 0 if graph['summary']['connectivity_complete'] else 2
    if args.command=='diagnose':
        from .runtime import diagnose, diagnose_gui, read_json
        from .paths import user_state_root
        if args.run:report=diagnose(args.run)
        else:
            base=user_state_root()/'gui-logs'
            latest=read_json(base/'latest.json',{})
            report=diagnose_gui(latest['journal_directory']) if latest else {'available':False}
            previous=read_json(base/'previous.json',{})
            if previous:report['previous_gui']=diagnose_gui(previous['journal_directory'])
        if args.json:print(json.dumps(report,ensure_ascii=False,indent=2))
        else:
            print('State: {} | Last stage: {} ({})'.format(report.get('state','unavailable'),report.get('label',''),report.get('stage','')))
            print('Current item: '+json.dumps(report.get('context',{}),ensure_ascii=False))
            print('Last completed: '+json.dumps(report.get('last_completed',{}),ensure_ascii=False))
            print('Checkpoint: '+json.dumps(report.get('checkpoint',{}),ensure_ascii=False))
            print('Logs: '+json.dumps(report.get('logs',report.get('log')),ensure_ascii=False))
            if report.get('last_failure'):print('Failure: '+json.dumps(report['last_failure'],ensure_ascii=False))
            if report.get('previous_gui'):print('Previous GUI: '+json.dumps(report['previous_gui'],ensure_ascii=False))
            print('An unfinished stage identifies where execution stopped; it does not prove OOM.')
        return 0 if report.get('available') else 1
    if args.command == "extract":
        from .recovery import RecoveryError
        try:
            graph = extract(args.config, args.out, args.memory_limit_gib)
        except (MemoryResumeError, RecoveryError) as error:
            parser.exit(1, 'ClockTree7: ' + str(error) + '\n')
        print(json.dumps(graph["summary"], indent=2))
        return 0 if graph["summary"]["connectivity_complete"] else 2
    if args.command == "serve":
        from .server import serve
        serve(args.host, args.port, args.runs, args.result)
        return 0
    if args.command == "gui":
        try:
            from .gui import run_gui
        except ImportError as error:
            parser.exit(1, 'ClockTree7: Tkinter is required for the GUI: {}\nRun doctor for environment details.\n'.format(error))
        import tkinter
        try:
            run_gui(args.runs, args.result, args.config,remember=not args.no_session)
        except tkinter.TclError as error:
            parser.exit(1, 'ClockTree7: GUI display/Tk initialization failed: {}\nUse a graphical session, or run extract/doctor without --gui.\n'.format(error))
        return 0
    if args.command == 'doctor':
        if args.self_test and args.viewer_only:
            parser.error('--self-test requires EDA; it cannot be combined with --viewer-only')
        from .doctor import run_doctor
        report = run_doctor(args.out, gui=args.gui, self_test=args.self_test, viewer_only=args.viewer_only)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            for check in report['checks']:
                print('[{}] {}: {}'.format(check['status'].upper(), check['name'], check['detail']))
            print('ClockTree7 {}. Report: {}'.format(report['status'].upper(), report.get('report_file', 'not saved')))
        return 0 if report['status'] == 'pass' else 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
