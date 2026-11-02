"""Generate a user-owned Linux shortcut for this exact installation directory."""
import argparse
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]


def desktop_entry(root):
    root = str(Path(root).resolve())
    if '\n' in root or '\r' in root:
        raise ValueError('Installation paths containing newlines are not supported')
    # Desktop Entry Exec quoting is not shell quoting. Escape its reserved
    # characters, and escape percent to prevent field-code substitution.
    command = root.replace('\\', '\\\\\\\\').replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
    value = root.replace('\\', '\\\\')
    return (ROOT / 'ClockTree7.desktop.in').read_text(encoding='utf-8').replace('@CLOCKTREE_ROOT@', command).replace('@CLOCKTREE_PATH@', value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--desktop', action='store_true', help='Also write the shortcut to ~/Desktop (or --desktop-dir)')
    parser.add_argument('--desktop-dir', help='Desktop directory, e.g. a localized desktop folder')
    args = parser.parse_args()
    if not sys.platform.startswith('linux'):
        parser.error('This installer is for Linux; use launch_gui.cmd on Windows')
    applications = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'applications'
    locations = [applications / 'clocktree7.desktop']
    if args.desktop or args.desktop_dir:
        locations.append(Path(args.desktop_dir).expanduser() / 'ClockTree7.desktop' if args.desktop_dir else Path.home() / 'Desktop/ClockTree7.desktop')
    entry = desktop_entry(ROOT)
    for path in locations:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry, encoding='utf-8')
        path.chmod(0o755)
        print(str(path))


if __name__ == '__main__':
    main()
