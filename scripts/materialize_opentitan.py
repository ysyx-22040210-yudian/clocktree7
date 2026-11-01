"""Materialize symlinks when sharing the upstream checkout over VMware HGFS."""
import json
from pathlib import Path
import shutil
import sys

source, destination = map(Path, sys.argv[1:3])
records = []
for item in source.rglob("*"):
    if ".git" in item.relative_to(source).parts or not item.is_symlink():
        continue
    target = destination / item.relative_to(source)
    if target.exists():
        continue
    actual = item.resolve()
    if not actual.exists():
        records.append({"path": str(item.relative_to(source)), "status": "upstream_broken_symlink"})
        continue
    target.parent.mkdir(parents=True, exist_ok=True)
    if actual.is_dir():
        shutil.copytree(str(actual), str(target))
    else:
        shutil.copy2(str(actual), str(target))
    records.append({"path": str(item.relative_to(source)), "target": str(actual), "status": "materialized"})
(destination / ".clocktree_symlinks.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
print("Materialized symlinks:", len(records))
