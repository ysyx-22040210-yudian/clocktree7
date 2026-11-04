import importlib
import json
from pathlib import Path
import shutil

import pytest

from clocktree.environment import resolve_verdi


extract_module = importlib.import_module('clocktree.extract')


def installation(path):
    (path / 'bin').mkdir(parents=True)
    executable = path / 'bin' / 'verdi'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    npi = path / 'share/NPI/L1/TCL/npi_L1.tcl'
    npi.parent.mkdir(parents=True)
    npi.write_text('# NPI fixture')
    return executable


def test_active_verdi_command_overrides_saved_host_and_environment(tmp_path, monkeypatch):
    executable = installation(tmp_path / 'active')
    monkeypatch.setattr(shutil, 'which', lambda command, **kw: str(executable) if command == 'verdi' else None)
    command, home = resolve_verdi({'verdi_home': '/old-host'}, {'PATH': str(executable.parent), 'VERDI_HOME': '/wrong'})
    assert command == str(executable) and home == str(executable.parent.parent)


@pytest.mark.skipif(__import__('os').name == 'nt', reason='Linux Verdi wrapper and symlinks')
def test_verdi_wrapper_basename_preserved_and_home_resolved(tmp_path):
    executable = installation(tmp_path / 'installation')
    wrapper = executable.with_name('.wrapper')
    executable.rename(wrapper)
    executable.symlink_to(wrapper.name)
    command, home = resolve_verdi(environment={'PATH': str(executable.parent)})
    assert Path(command).name == 'verdi'
    assert home == str(executable.parent.parent)


def test_missing_npi_and_missing_command_have_clear_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda *args, **kw: None)
    with pytest.raises(ValueError, match='verdi'):
        resolve_verdi(environment={'PATH': ''})
    executable = tmp_path / 'verdi'
    executable.write_text('not a complete installation')
    monkeypatch.setattr(shutil, 'which', lambda *args, **kw: str(executable))
    with pytest.raises(ValueError, match='npi_L1.tcl'):
        resolve_verdi(environment={'PATH': str(tmp_path)})


def test_unlimited_wait_repeated_directory_and_failed_attempt_never_reuses_evidence(tmp_path, monkeypatch):
    cfg = {'design': {'top': 'top', 'elab': '/kdb'}, 'timeout_seconds': 1,
           'layers': [{'index': 1, 'hierarchy': 'top', 'modules': [
               {'module': 'divider', 'kind': 'div', 'clock_inputs': ['i'], 'clock_output': 'o'}]},
                      {'index': 2, 'hierarchy': 'top', 'modules': []}]}
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(cfg))
    out = tmp_path / 'run'
    calls = []
    emit = [True]
    import sys
    monkeypatch.setattr(extract_module, 'resolve_verdi', lambda *a: (sys.executable, '/tools'))
    def runner(command, work, env, raw, log, output, config):
            calls.append(str(work))
            assert env['VERDI_HOME'] == '/tools'
            log.write_text('this attempt only\n')
            if emit[0]:
                events = [dict(event='instance', path='top.div', module='divider', layer=1,
                               inputs=['i'], output='o', kind='div'),
                          dict(event='trace', instance='top.div', pin='i', direction='driver',
                               results=[dict(status='input_boundary', endpoint='top.clk')]),
                          dict(event='trace', instance='top.div', pin='o', direction='load',
                               results=[dict(status='output_boundary')]), dict(event='complete')]
                raw.write_text('\n'.join(json.dumps(e) for e in events))
            return 0,work,{'state':'finished'}
    monkeypatch.setattr(extract_module, 'run_verdi', runner)
    for _ in range(3):
        graph = extract_module.extract(path, out)
        assert graph['summary']['connectivity_complete']
    histories = sorted((out / '.clocktree-history').iterdir())
    assert len(histories) == 2
    assert all((h / 'clocktree.json').is_file() and (h / 'raw.jsonl').is_file() for h in histories)
    # An unrelated input file in the working/output directory is never touched.
    untouched = out / 'design.sv'
    untouched.write_text('source RTL')
    emit[0] = False
    with pytest.raises(RuntimeError, match='no raw evidence'):
        extract_module.extract(path, out)
    assert not (out / 'clocktree.json').exists() and not (out / 'summary.json').exists()
    assert untouched.read_text() == 'source RTL'
    emit[0] = True
    assert extract_module.extract(path, out)['summary']['connectivity_complete']
    assert len(set(calls)) == 5
