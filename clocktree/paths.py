"""Writable per-user storage for shared or read-only tool installations."""
import hashlib
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def user_state_root():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData/Local'))
    else:
        base = Path(os.environ.get('XDG_STATE_HOME') or (Path.home() / '.local/state'))
    return base / 'clocktree7'


def path_key(path):
    return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode('utf-8')).hexdigest()[:20]


def default_runs_dir(root=None):
    if os.environ.get('CLOCKTREE_RUNS'):
        return Path(os.environ['CLOCKTREE_RUNS']).expanduser().resolve()
    root = Path(root or ROOT).resolve()
    legacy = root / 'validation/runs'
    parent = legacy
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if parent.is_dir() and os.access(str(parent), os.W_OK | os.X_OK):
        return legacy
    return user_state_root() / 'projects' / path_key(root) / 'runs'
