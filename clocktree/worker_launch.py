"""Publish worker identity before Verdi can append any evidence (Linux)."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clocktree.runtime import alive, process_identity
from clocktree.storage import write_graph


def main():
    identity_path, owner_json = sys.argv[1:3]
    owner = json.loads(owner_json)
    if alive(owner) is not True:return 125
    write_graph(identity_path, process_identity())
    # If the supervisor disappeared during startup, leave the durable identity
    # and exit without ever touching the raw/checkpoint files.
    if alive(owner) is not True:return 125
    os.execvpe(sys.argv[3], sys.argv[3:], os.environ)


if __name__ == '__main__':
    raise SystemExit(main())
