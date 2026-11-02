"""Build/verify an offline source bundle without EDA, databases or user history."""
import argparse
import datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('clocktree', 'npi', 'scripts', 'tests', 'examples', 'docs', 'web')
ROOT_FILES = ('README.md', 'clocktree.sh', 'clocktree.cmd', 'launch.sh', 'launch_gui.sh',
              'launch_gui.cmd', 'launch_web.sh', 'ClockTree7.desktop.in', 'pytest.ini')


def files(root):
    result = [root / name for name in ROOT_FILES]
    for name in DIRECTORIES:
        result.extend(p for p in (root / name).rglob('*') if p.is_file()
                      and not any(part in ('__pycache__', '.pytest_cache') for part in p.relative_to(root).parts)
                      and not p.name.startswith('.env')
                      and p.suffix not in ('.pyc', '.log', '.pid', '.xwd'))
    for path in result:
        if not path.is_file() or path.is_symlink():
            raise ValueError('Missing/linked release file: ' + str(path))
    return sorted(result)


def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'MANIFEST.json').read_text(encoding='utf-8'))
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        path.relative_to(root)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError('Checksum mismatch: ' + name)
    return {'pass': True, 'files': len(manifest['files']), 'root': str(root)}


def build(output, name, root=ROOT):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    payloads = {path.relative_to(root).as_posix(): path.read_bytes() for path in files(root)}
    # Copying from Windows must not introduce CRLF into Bash launchers.
    payloads = {path: data.replace(b'\r\n', b'\n') if path.endswith(('.sh', '.py', '.tcl')) else data
                for path, data in payloads.items()}
    try:
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], stderr=subprocess.DEVNULL).decode().strip()
        dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], stderr=subprocess.DEVNULL).strip())
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    manifest = {'schema_version': 1, 'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'source_commit': commit, 'source_dirty': dirty,
                'requirements': 'Python >=3.6; Tk for GUI; Linux Verdi/NPI and site license for extraction',
                'files': {path: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for path, data in payloads.items()}}
    payloads['MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    archives = [output / (name + '.zip'), output / (name + '.tar.gz')]
    for path in archives:
        if path.exists():
            raise ValueError('Release archive already exists: ' + str(path))
    with zipfile.ZipFile(str(archives[0]), 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, data in payloads.items():
            entry = zipfile.ZipInfo(name + '/' + path)
            entry.external_attr = ((0o100755 if path.endswith('.sh') else 0o100644) << 16)
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, data)
    with tarfile.open(str(archives[1]), 'w:gz') as archive:
        for path, data in payloads.items():
            entry = tarfile.TarInfo(name + '/' + path)
            entry.size = len(data)
            entry.mode = 0o755 if path.endswith('.sh') else 0o644
            archive.addfile(entry, io.BytesIO(data))
    return {'files': len(payloads), 'archives': [str(p) for p in archives],
            'source_commit': commit, 'source_dirty': dirty,
            'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in archives}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default=str(ROOT / 'validation/releases'))
    parser.add_argument('--name', default='clocktree7-portable-' + datetime.date.today().strftime('%Y%m%d'))
    parser.add_argument('--verify', help='Verify an already unpacked bundle against its manifest')
    args = parser.parse_args()
    if not args.name or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.name):
        parser.error('Use letters, digits, underscore and hyphen for --name')
    print(json.dumps(verify(args.verify) if args.verify else build(args.out, args.name), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
