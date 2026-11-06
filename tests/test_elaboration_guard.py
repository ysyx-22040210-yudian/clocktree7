from pathlib import Path
import tkinter
import json
import subprocess
import sys

import pytest


@pytest.fixture(scope='module')
def interpreter():
    # Match the native worker's isolated Tcl lifetime. Repeated Tcl interpreters
    # in one Windows Conda/pytest process can return empty error strings.
    script='''import json,sys,tkinter
from pathlib import Path
tcl=tkinter.Tcl()
tcl.eval(Path(sys.argv[1]).read_text(encoding='utf-8').split('if {[catch {ct_main}')[0])
status=tcl.call('catch',('ct_validate_compiler_log',sys.argv[2]),'message')
print(json.dumps([status,tcl.getvar('message')]))
'''
    source=Path(__file__).resolve().parents[1]/'npi/extract.tcl'
    return lambda path:json.loads(subprocess.check_output([sys.executable,'-c',script,str(source),str(path)],universal_newlines=True))


@pytest.mark.parametrize('content,error',[
    ('Total 0 error(s), 0 warning(s)',None),
    ('*Warning* unused signal\nTotal 0 error(s), 1 warning(s)',None),
    ('*Warning* Over Maximum Hierarchy layers(50). Skip elaboration!\nTotal 0 error(s), 1 warning(s)','truncated'),
    ('*Error* missing definition\nError-[SE] syntax','2 compiler errors'),
])
def test_compiler_truncation_is_fatal_even_when_vendor_calls_it_warning(tmp_path,content,error,interpreter):
    path=tmp_path/'compiler.log';path.write_text(content)
    status,message=interpreter(path)
    if error:
        assert status==1 and error in message
    else:assert status==0
