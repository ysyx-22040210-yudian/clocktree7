"""Windows QA: deny delete-sharing on one isolated HGFS status snapshot until done."""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if os.name!='nt':raise SystemExit('This QA lock requires Windows')
    directory=args.directory.resolve();path=directory/'status.json'
    started=time.monotonic();deadline=started+600
    while not path.exists():
        if time.monotonic()>deadline:raise RuntimeError('QA worker did not start')
        time.sleep(.02)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.CreateFileW(str(path),0x80000000,3,None,3,0,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    locked=time.monotonic();before=path.read_text(encoding='utf-8')
    print('WINDOWS_STATUS_LOCK_ACTIVE',flush=True)
    try:
        while not (directory/'register-trace.json').exists():
            if time.monotonic()>deadline:raise RuntimeError('QA trace did not finish while locked')
            time.sleep(.05)
        time.sleep(1)
        unchanged=path.read_text(encoding='utf-8')==before
    finally:kernel.CloseHandle(handle)
    result=json.loads((directory/'register-trace.json').read_text(encoding='utf-8'))
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines()]
    assert unchanged,'The OS did not preserve the requested lock'
    assert events[-1]['stage']=='finished' and result['summary']['complete']
    report=dict(passed=True,real_windows_delete_sharing_denied=True,snapshot_unchanged=True,
                latest_log_stage=events[-1]['stage'],locked_seconds=time.monotonic()-locked,summary=result['summary'])
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('WINDOWS_STATUS_LOCK_ACCEPTED',flush=True)


if __name__=='__main__':main()
