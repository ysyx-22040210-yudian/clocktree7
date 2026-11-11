import json
from pathlib import Path

import pytest

from clocktree.__main__ import main
from clocktree.sv_templates import prepare
from test_sv_templates import setup


TEMPLATES={'with_mux':'initial $display("MUX", $var_clkip);',
           'without_mux':'initial $display("OTHER", $var_clkip);'}


def test_sv_source_and_internal_diagnostics_have_separate_destinations(tmp_path):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    output=tmp_path/'deliverables/clock_branches';report=tmp_path/'internal/export.json'
    result=plan.export(TEMPLATES,output,report_path=report)
    target=output.with_suffix('.sv')
    assert result['sv_written'] and result['sv']==str(target)
    assert list(output.parent.iterdir())==[target]
    text=target.read_text();assert text.count('initial $display(')==2 and '$var_' not in text
    assert not text.lstrip().startswith('{') and text.count('// CT7 branch ')==2
    assert json.loads(report.read_text())['block_count']==2


@pytest.mark.parametrize('name',['branches.json','branches.sv.mapping.json','templates.txt'])
def test_code_export_refuses_json_or_config_filename_without_overwriting(tmp_path,name):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    target=tmp_path/name;target.write_text('existing config')
    with pytest.raises(ValueError,match='.sv'):plan.export(TEMPLATES,target)
    assert target.read_text()=='existing config'
    assert not list(tmp_path.glob('*.tmp'))


@pytest.mark.parametrize('overwrite_plan',[False,True])
def test_report_cannot_replace_the_source_or_database(tmp_path,overwrite_plan):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    target=tmp_path/'existing.sv';target.write_text('previous source')
    with pytest.raises(ValueError,match='不能覆盖'):
        plan.export(TEMPLATES,target,report_path=plan.path if overwrite_plan else target)
    assert target.read_text()=='previous source' and plan.branches()[0]==2


def command(monkeypatch,plan,settings,target,report):
    request=target.parent/'request.json';request.write_text(json.dumps(settings))
    monkeypatch.setattr('sys.argv',['clocktree','sv-export','--plan',str(plan.path),'--templates',str(request),
                                  '--out',str(target),'--report',str(report)])
    return main()


def test_zero_generated_is_unsuccessful_and_keeps_previous_sv(tmp_path,monkeypatch,capsys):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    target=tmp_path/'existing.sv';target.write_text('// previous valid source\n')
    code=command(monkeypatch,plan,dict(with_mux='$var_unknown',without_mux='$var_unknown'),target,tmp_path/'internal/result.json')
    result=json.loads(capsys.readouterr().out)
    assert code==2 and result['blocks']==0 and result['skipped']==2 and not result['sv_written']
    assert target.read_text()=='// previous valid source\n'
    assert not target.with_name(target.name+'.mapping.json').exists()
    assert 'var_unknown' in ' '.join(result['issues'])


def test_partial_export_is_real_sv_and_keeps_diagnostics(tmp_path,monkeypatch,capsys):
    graph,reg=setup(tmp_path,True);plan=prepare(graph,reg,tmp_path/'plan',False)
    target=tmp_path/'actual.sv';report=tmp_path/'internal/mapping.json'
    code=command(monkeypatch,plan,dict(with_mux='$var_swdone',without_mux='initial $display($var_clkip);'),target,report)
    result=json.loads(capsys.readouterr().out)
    assert code==0 and result['sv_written'] and result['blocks']==result['skipped']==1
    assert 'initial $display(chip.clk_other);' in target.read_text()
    assert json.loads(report.read_text())['skipped_count']==1
