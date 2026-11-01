"""Download the exact public GitHub revisions in PULPissimo's Bender.lock."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import io
import json
import shutil
import sys
import time
import urllib.request
import zipfile
import yaml

project = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
destination.mkdir(parents=True, exist_ok=True)
lock = yaml.safe_load((project / "Bender.lock").read_text())

def fetch(item):
    name, package = item
    source = package["source"].get("Git")
    if not source:
        return {"name": name, "path": package["source"].get("Path"), "local": True}
    revision = package["revision"]
    target = destination / name
    if (target / ".clocktree_revision").exists():
        return {"name": name, "revision": revision, "cached": True}
    repository = source.split("github.com/")[1].removesuffix(".git")
    url = "https://codeload.github.com/{}/zip/{}".format(repository, revision)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                data = response.read()
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                prefix = archive.namelist()[0].split("/")[0] + "/"
                for member in archive.infolist():
                    relative = member.filename[len(prefix):]
                    if not relative or member.is_dir(): continue
                    path = (target / relative).resolve()
                    if target.resolve() not in path.parents: raise ValueError("Unsafe archive path")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(member))
            (target / ".clocktree_revision").write_text(revision)
            return {"name": name, "revision": revision, "bytes": len(data), "url": source}
        except Exception as error:
            if attempt == 2: return {"name": name, "error": str(error)}
            time.sleep(1)

records = []
with ThreadPoolExecutor(max_workers=8) as pool:
    for result in pool.map(fetch, lock["packages"].items()):
        records.append(result)
        print(json.dumps(result), flush=True)
(destination / "manifest.json").write_text(json.dumps(records, indent=2))
raise SystemExit(1 if any("error" in r for r in records) else 0)
