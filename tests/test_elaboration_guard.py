from pathlib import Path
import tkinter

import pytest


@pytest.fixture(scope='module')
def interpreter():
    code=(Path(__file__).resolve().parents[1]/'npi/extract.tcl').read_text(encoding='utf-8')
    tcl=tkinter.Tcl();tcl.eval(code.split('if {[catch {ct_main}')[0])
    return tcl


@pytest.mark.parametrize('content,error',[
    ('Total 0 error(s), 0 warning(s)',None),
    ('*Warning* unused signal\nTotal 0 error(s), 1 warning(s)',None),
    ('*Warning* Over Maximum Hierarchy layers(50). Skip elaboration!\nTotal 0 error(s), 1 warning(s)','truncated'),
    ('*Error* missing definition\nError-[SE] syntax','2 compiler errors'),
])
def test_compiler_truncation_is_fatal_even_when_vendor_calls_it_warning(tmp_path,content,error,interpreter):
    path=tmp_path/'compiler.log';path.write_text(content)
    if error:
        with pytest.raises(tkinter.TclError,match=error):interpreter.call('ct_validate_compiler_log',str(path))
    else:interpreter.call('ct_validate_compiler_log',str(path))
