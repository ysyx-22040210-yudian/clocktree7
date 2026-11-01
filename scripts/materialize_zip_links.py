"""Preserve GitHub archive symlink contents on an HGFS/Windows shared tree."""
import argparse
import io
import json
import shutil
from pathlib import Path
import urllib.request
import zipfile

p=argparse.ArgumentParser()
p.add_argument("repository")
p.add_argument("revision")
p.add_argument("destination")
args=p.parse_args()
root=Path(args.destination).resolve()
url="https://codeload.github.com/{}/zip/{}".format(args.repository,args.revision)
data=urllib.request.urlopen(url,timeout=90).read()
links={}
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    prefix=archive.namelist()[0].split('/')[0]+'/'
    for member in archive.infolist():
        if member.external_attr>>16 & 0o170000 == 0o120000:
            links[member.filename[len(prefix):]]=archive.read(member).decode()
records=[]
done=set()
def materialize(name,active):
    if name in done:return
    if name in active:raise ValueError("Cyclic upstream symlink")
    link=(root/name).resolve()
    source=(link.parent/links[name]).resolve()
    if root not in source.parents or root not in link.parents:raise ValueError("Out-of-tree symlink")
    relative=source.relative_to(root).as_posix()
    if relative in links:materialize(relative,active|{name})
    if source.is_dir():
        if link.is_file():
            # This file is the archive's own short symlink placeholder.
            if link.read_text()!=links[name]:raise ValueError("Unexpected existing file")
            link.unlink()
        shutil.copytree(source,link,dirs_exist_ok=True)
    elif source.is_file():
        shutil.copy2(source,link)
    else:
        records.append({"path":name,"target":links[name],"status":"upstream_missing"});return
    done.add(name)
    records.append({"path":name,"target":links[name],"status":"materialized"})
for name in links:materialize(name,set())
(root/'.clocktree_zip_symlinks.json').write_text(json.dumps(records,indent=2))
print(json.dumps({"materialized":len(done),"links":len(links)}))
