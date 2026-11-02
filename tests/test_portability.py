import copy
import importlib
import json
from pathlib import Path
import runpy
import zipfile

import pytest

from clocktree.config import load_config
from clocktree.doctor import compare_golden, run_doctor
from clocktree.paths import default_runs_dir, user_state_root


ROOT = Path(__file__).resolve().parents[1]


def test_project_base_resolves_relative_to_config_not_installation(tmp_path, monkeypatch):
    project = tmp_path / 'project with spaces'
    project.mkdir()
    source = json.loads((ROOT / 'examples/flexible_layers/config.template.json').read_text())
    source['design'].update(base_dir='.', filelist='$CLOCKTREE_TEST_RTL/rtl.f')
    path = project / 'config.json'
    path.write_text(json.dumps(source))
    monkeypatch.setenv('CLOCKTREE_TEST_RTL', 'rtl')
    monkeypatch.chdir(tmp_path)
    config = load_config(path)
    assert config['design']['base_dir'] == str(project)
    assert config['design']['filelist'] == str(project / 'rtl/rtl.f')
    # Moving the generated job config must not change the original base.
    job = tmp_path / 'job.json'
    job.write_text(json.dumps(config))
    assert load_config(job) == config


def test_legacy_relative_input_keeps_launch_directory_semantics(tmp_path, monkeypatch):
    source = json.loads((ROOT / 'examples/flexible_layers/config.template.json').read_text())
    path = tmp_path / 'legacy.json'
    path.write_text(json.dumps(source))
    monkeypatch.chdir(tmp_path)
    assert load_config(path)['design']['filelist'] == str(tmp_path / 'examples/flexible_layers/rtl.f')


def test_read_only_install_uses_user_owned_runs(tmp_path, monkeypatch):
    module = importlib.import_module('clocktree.paths')
    monkeypatch.delenv('CLOCKTREE_RUNS', raising=False)
    monkeypatch.setattr(module.os, 'access', lambda *args: False)
    result = default_runs_dir(tmp_path)
    assert user_state_root() in result.parents and result.name == 'runs'
    monkeypatch.setenv('CLOCKTREE_RUNS', str(tmp_path / 'explicit'))
    assert default_runs_dir(tmp_path) == tmp_path / 'explicit'


def test_writable_install_retains_existing_history_location(tmp_path, monkeypatch):
    monkeypatch.delenv('CLOCKTREE_RUNS', raising=False)
    assert default_runs_dir(tmp_path) == tmp_path / 'validation/runs'


def test_doctor_viewer_uses_isolated_session_and_does_not_require_eda(tmp_path, monkeypatch):
    module = importlib.import_module('clocktree.doctor')
    def forbidden(*args):raise AssertionError('Unexpected EDA execution')
    monkeypatch.setattr(module, 'resolve_verdi', forbidden)
    monkeypatch.setattr(module, 'extract', forbidden)
    report = run_doctor(tmp_path, viewer_only=True)
    assert report['status'] == 'pass' and not report['eda_verified']
    assert Path(report['report_file']).is_file()
    assert next(c for c in report['checks'] if c['name'] == 'session_storage')['status'] == 'pass'


def test_doctor_blocks_self_test_when_eda_is_missing(tmp_path, monkeypatch):
    module = importlib.import_module('clocktree.doctor')
    def missing():raise ValueError('verdi unavailable')
    monkeypatch.setattr(module, 'resolve_verdi', missing)
    def forbidden(*args):raise AssertionError('Must not run EDA when prerequisites failed')
    monkeypatch.setattr(module, 'extract', forbidden)
    report = run_doctor(tmp_path, self_test=True)
    assert report['status'] == 'fail' and not report['eda_verified']
    assert any(c['name'] == 'verdi_npi' and c['status'] == 'fail' for c in report['checks'])


def test_golden_checks_all_nodes_edges_and_terminals():
    for folder, result in [('flexible_layers', 'clocktree.json'), ('reset_tree', 'resettree.json')]:
        graph = json.loads((ROOT / 'examples' / folder / result).read_text())
        golden = json.loads((ROOT / 'examples' / folder / 'golden.json').read_text())
        assert compare_golden(graph, golden)['nodes'] == len(graph['nodes'])
        for field in ('nodes', 'edges', 'terminals'):
            wrong = copy.deepcopy(golden)
            wrong[field].pop()
            with pytest.raises(ValueError, match=field):compare_golden(graph, wrong)


def test_portable_bundle_has_no_site_data_and_manifest_detects_tampering(tmp_path):
    module = runpy.run_path(str(ROOT / 'scripts/build_portable.py'))
    result = module['build'](tmp_path / 'archives', 'test-package')
    with zipfile.ZipFile(result['archives'][0]) as archive:
        names = archive.namelist()
        assert not any('/validation/' in name or '/tools/' in name or '/.git/' in name for name in names)
        assert 'test-package/clocktree.sh' in names and 'test-package/ClockTree7.desktop.in' in names
        archive.extractall(str(tmp_path / 'unpacked'))
    package = tmp_path / 'unpacked/test-package'
    assert module['verify'](package)['pass']
    (package / 'clocktree/__main__.py').write_text('modified')
    with pytest.raises(ValueError, match='Checksum mismatch'):module['verify'](package)


def test_shortcut_uses_current_installation_and_escapes_exec_fields(tmp_path):
    module = runpy.run_path(str(ROOT / 'scripts/install_desktop.py'))
    root = tmp_path / 'tool with spaces $VAR 100%'
    text = module['desktop_entry'](root)
    assert '@CLOCKTREE' not in text and '/mnt/hgfs/' not in text
    assert '100%%' in text and '\\\\$VAR' in text
    assert 'Exec=bash "' in text and 'Path=' + str(root).replace('\\', '\\\\') in text


def test_vendor_legacy_path_policy_rejects_spaces_and_unicode():
    module = importlib.import_module('clocktree.extract')
    assert module.vendor_safe_path('/tmp/user/project-1/run_2')
    for path in ('/tmp/tool with spaces', '/tmp/中文', '/tmp/a;command', '/tmp/$HOME'):
        assert not module.vendor_safe_path(path)
