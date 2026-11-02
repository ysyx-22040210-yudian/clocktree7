import argparse
import json
from pathlib import Path

from .extract import extract


def main():
    parser = argparse.ArgumentParser(description="ClockTree7 full RTL clock network extraction")
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("extract")
    run.add_argument("--config", required=True)
    run.add_argument("--out", default=".", help="Output directory; defaults to the current directory and can be reused")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8767, type=int)
    serve.add_argument("--runs", default="output/runs")
    serve.add_argument("--result")
    gui = commands.add_parser("gui", help="Native Tk/ttk clock architecture desktop application")
    gui.add_argument("--runs", default=str(Path(__file__).resolve().parents[1] / "validation/runs"))
    gui.add_argument("--result")
    gui.add_argument("--config")
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
        from .gui import run_gui
        run_gui(args.runs, args.result, args.config)
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
