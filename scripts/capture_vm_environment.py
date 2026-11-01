"""Record the VM and hashes of the source actually read through the shared mount."""
import datetime
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
files = [root / name for name in ['launch.sh', 'launch_gui.sh', 'launch_gui.cmd', 'launch_web.sh', 'ClockTree7.desktop', 'docs/clocktree7.gif', 'docs/clocktree7.svg']]
for directory, pattern in [('clocktree', '*.py'), ('npi', '*.tcl'), ('web', '*')]:
    files.extend(p for p in (root / directory).glob(pattern) if p.is_file())
mount = subprocess.check_output(['findmnt', '-n', '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS', '-T', str(root)], universal_newlines=True)
mount_fields = mount.strip().split(None, 3)
record = {'captured_utc': datetime.datetime.utcnow().isoformat() + 'Z',
          'workspace': str(root), 'hostname': platform.node(), 'platform': platform.platform(),
          'python': sys.version, 'shared_mount': dict(zip(['target', 'source', 'fstype', 'options'], mount_fields)),
          'source_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
          'elab_databases': {name: Path('/home/clocktree7-work/kdb', name, 'simv.daidir/kdb.elab++').is_dir()
                             for name in ['pulp_soc', 'openc910', 'opentitan_clkmgr']}}
path = root / 'validation/vm_environment.json'
path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps(record, indent=2))
