"""Small LAN/localhost application server; no third-party runtime packages."""
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import json
import mimetypes
import threading
import time
import uuid

from .config import validate_config
from .extract import extract
from .profiles import result_filename, tree_type
from .paths import default_runs_dir


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def serve(host, port, runs, result=None):
    base = Path(runs or default_runs_dir()).resolve()
    base.mkdir(parents=True, exist_ok=True)
    web = Path(__file__).resolve().parents[1] / "web"
    jobs = {}
    initial = Path(result).resolve() if result else None

    class Handler(BaseHTTPRequestHandler):
        def send(self, value, code=200, content_type="application/json; charset=utf-8"):
            data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def run_path(self, identifier):
            if not identifier or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in identifier):
                raise ValueError("Invalid run ID")
            return base / identifier

        def do_GET(self):
            route = urlparse(self.path)
            query = parse_qs(route.query)
            try:
                if route.path == "/api/runs":
                    records = []
                    for file in sorted(base.glob("*/summary.json"), key=lambda x: x.stat().st_mtime, reverse=True):
                        records.append({"id": file.parent.name, "summary": json.loads(file.read_text())})
                    self.send({"runs": records, "initial": bool(initial and initial.exists())})
                elif route.path == "/api/result":
                    identifier = query.get("run", [""])[0]
                    if identifier:
                        directory = self.run_path(identifier)
                        summary = json.loads((directory / 'summary.json').read_text()) if (directory / 'summary.json').is_file() else {}
                        path = directory / result_filename(summary)
                    else:
                        path = initial
                    if not path or not path.is_file():
                        self.send({"error": "No result loaded"}, 404)
                    else:
                        self.send(path.read_bytes())
                elif route.path == "/api/job":
                    identifier = query.get("id", [""])[0]
                    job = dict(jobs.get(identifier, {"state": "unknown"}))
                    log = self.run_path(identifier) / "verdi.log"
                    if log.exists():
                        with log.open("rb") as stream:
                            stream.seek(max(0, log.stat().st_size - 12000))
                            job["log"] = stream.read().decode("utf-8", "replace")
                    self.send(job)
                else:
                    relative = "index.html" if route.path == "/" else route.path.lstrip("/")
                    path = (web / relative).resolve()
                    if web not in path.parents or not path.is_file():
                        self.send({"error": "Not found"}, 404)
                    else:
                        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                        self.send(path.read_bytes(), content_type=content_type + "; charset=utf-8")
            except (ValueError, OSError) as error:
                self.send({"error": str(error)}, 400)

        def do_POST(self):
            if self.path != "/api/run":
                self.send({"error": "Not found"}, 404)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 2 * 1024 * 1024:
                    raise ValueError("Invalid configuration size")
                config = validate_config(json.loads(self.rfile.read(size).decode("utf-8")))
                if any(job.get("state") == "running" for job in jobs.values()):
                    self.send({"error": "An extraction is already running; wait for completion"}, 409)
                    return
                identifier = time.strftime(('rst_' if tree_type(config)=='reset' else 'run_') + "%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
                directory = self.run_path(identifier)
                directory.mkdir()
                path = directory / "config.json"
                path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
                jobs[identifier] = {"state": "running", "id": identifier}
                def worker():
                    try:
                        graph = extract(path, directory)
                        jobs[identifier] = {"state": "finished", "id": identifier, "summary": graph["summary"]}
                    except Exception as error:
                        jobs[identifier] = {"state": "failed", "id": identifier, "error": str(error)}
                    (directory / "job.json").write_text(json.dumps(jobs[identifier]), encoding="utf-8")
                threading.Thread(target=worker, daemon=True).start()
                self.send(jobs[identifier], 202)
            except (ValueError, KeyError, OSError) as error:
                self.send({"error": str(error)}, 400)

    print("ClockTree7: http://{}:{}".format(host, port), flush=True)
    Server((host, port), Handler).serve_forever()
