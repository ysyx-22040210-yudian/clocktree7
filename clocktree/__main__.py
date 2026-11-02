import argparse
import json

from .extract import extract


def main():
    parser = argparse.ArgumentParser(description="ClockTree7 full RTL clock/reset network extraction")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("extract")
    run.add_argument("--config", required=True)
    run.add_argument("--out", default=".", help="Output directory; defaults to the current directory and can be reused")
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
    args = parser.parse_args()
    if args.command == "extract":
        graph = extract(args.config, args.out)
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
