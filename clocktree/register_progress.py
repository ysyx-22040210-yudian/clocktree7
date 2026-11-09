"""Lossless register-stage log with an advisory, contention-tolerant UI snapshot."""
import json
from pathlib import Path
import time

from .runtime import process_identity,tail_text
from .storage import write_graph


def progress_reporter(directory,events):
    directory=Path(directory);last_attempt=[None];warned=[False]
    def report(stage,message,context):
        data={'time':time.time(),'stage':stage,'message':message,'context':context,'controller':process_identity()}
        # Logging/checkpoint failures are not display failures: never suppress them.
        events.write(json.dumps(data,ensure_ascii=False)+'\n');events.flush()
        now=time.monotonic()
        if stage in ('trace','query') and last_attempt[0] is not None and now-last_attempt[0]<.25:return
        last_attempt[0]=now
        try:write_graph(directory/'status.json',data)
        except PermissionError:
            # Windows readers can briefly prevent HGFS atomic replacement.
            # The event above remains authoritative, including the final stage.
            if not warned[0]:
                print('Register status snapshot temporarily unavailable; continuing. Full progress: '+str(directory/'events.jsonl'),flush=True)
                warned[0]=True
    return report


def read_progress(directory):
    """Prefer the last complete log event when a snapshot is stale/locked/missing."""
    directory=Path(directory)
    for line in reversed(tail_text(directory/'events.jsonl',65536).splitlines()):
        try:
            value=json.loads(line)
            if isinstance(value,dict) and value.get('stage'):return value
        except (ValueError,TypeError):pass
    try:
        value=json.loads((directory/'status.json').read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (ValueError,OSError):return {}
