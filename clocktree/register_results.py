"""Discover completed register jobs without walking RTL or loading bit mappings."""
import json
from pathlib import Path

from .register_progress import read_progress
from .ui_state import design_key


def result_directory(path):
    path = Path(path).expanduser()
    return (path.parent if path.is_file() else path).resolve()


def register_result_stamp(path):
    """Small revision token: an in-place result update invalidates an old SV plan."""
    if not path or not str(path).strip():
        return None
    directory = result_directory(path)
    files = {}
    for name in ('manifest.json', 'register-trace.json', 'registers.sqlite'):
        try:
            stat = (directory / name).stat()
            files[name] = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            files[name] = None
    return {'directory': str(directory), 'files': files}


def latest_register_result(config, runs, known=()):
    """Newest finished result for this design/mode, in known task locations only.

    Normal GUI jobs are immediate children of runs/.register-trace. Also inspect
    saved/imported locations and their siblings, so external jobs remain usable.
    The summary's mtime is the result publication time, not the UUID/name order.
    Finished jobs with unresolved bits are valid: SV review handles those bits.
    """
    directories = set()
    roots = {Path(runs).resolve() / '.register-trace'}
    for value in known:
        if not value or not str(value).strip():
            continue
        try:
            directory = result_directory(value)
            directories.add(directory)
            roots.add(directory.parent)
        except (OSError, ValueError):
            continue
    for root in roots:
        try:
            directories.update(path for path in root.iterdir() if path.is_dir())
        except OSError:
            continue
    candidates = []
    for directory in directories:
        try:
            stamp = (directory / 'register-trace.json').stat()
            if not (directory / 'manifest.json').is_file():
                continue
            if (directory / 'registers.sqlite').stat().st_size == 0:
                continue
            candidates.append((stamp.st_mtime_ns, str(directory)))
        except OSError:
            continue
    key = design_key(config)
    for _, directory in sorted(candidates, reverse=True):
        try:
            result = json.loads((Path(directory) / 'register-trace.json').read_text(encoding='utf-8-sig'))
            if result.get('operation') != 'auxiliary-register-trace':
                continue
            if result.get('summary', {}).get('finished') is not True:
                continue
            if design_key(result['config']) != key:
                continue
            manifest = json.loads((Path(directory) / 'manifest.json').read_text(encoding='utf-8-sig'))
            if design_key(manifest['request']['config']) != key:
                continue
            with (Path(directory) / 'registers.sqlite').open('rb') as database:
                if database.read(16) != b'SQLite format 3\x00':
                    continue
            progress = read_progress(directory)
            if progress and progress.get('stage') != 'finished':
                continue
            return directory
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return ''
