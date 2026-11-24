import json
import re

import pytest

from clocktree.sv_macros import clean_template, template_macros
from clocktree.sv_templates import prepare
from test_sv_templates import setup as make_branch_inputs


def rules(*pairs):
    return [{'name': name, 'text': text} for name, text in pairs]


def compiled(*pairs):
    return template_macros({'macros': rules(*pairs)})


def test_code_uses_longest_literal_match_and_preserves_sv_tokens():
    source = (
        '`ifdef ENABLE assign clk = tb.dut.crg.clk[3] & tb.dut.other & 1\'b1; `endif\n'
        'assign n = xtb.dut + tb.dut2 + tb.dut.crg_x;\n'
        'wire \\tb.dut.crg ; // tb.dut.crg\n'
        '/* tb.dut */ `include "tb.dut.svh"\n'
        '`define USER tb.dut \\\n  tb.dut.crg\n'
        'assign x = `USER + `BLOCK.clk;\n')
    macros = template_macros({'macros': rules(('`ROOT', 'tb.dut'), ('BLOCK', 'tb.dut.crg'), ('ONE', "1'b1")),
                              'with_mux': source})
    assert macros.apply(clean_template(source)) == clean_template(source).replace(
        "tb.dut.crg.clk[3] & tb.dut.other & 1'b1", "`BLOCK.clk[3] & `ROOT.other & `ONE"
    ).replace('tb.dut.crg_x;', '`ROOT.crg_x;')
    assert macros.rows()[0]['name'] == 'ROOT'


def test_quoted_fragments_use_only_configured_header_names():
    macros = compiled(('CRG', 'tb.dut'), ('LABEL', '"READY"'))
    msg = r'"at tb.dut, url=http://tb.dut/a /*ok*/; %0d) \"TB\". tb.dutTAIL, `user, tb.duttb.dut"'
    source = '$display(' + msg + ', tb.dut.clk);\n$display(' + msg + ', "READY");'
    actual = macros.apply(source)
    assert re.findall(r'^`define (\w+)', macros.header(), re.M) == ['CRG', 'LABEL']
    assert '`define ' not in actual and '`undef' not in actual and 'CT7_TEXT_' not in actual
    assert actual.count('$display(`CRG(`"') == 2
    assert r'url=http:\057/`CRG()/a \057*ok*\057; %0d)' in actual
    assert r'\042TB\042. `CRG()\124AIL, \140user, `CRG()`CRG()' in actual
    assert ', `CRG().clk)' in actual and ', `LABEL)' in actual
    assert macros.apply(source) == actual
    assert macros.apply('$display("READY");') == '$display(`LABEL);'
    assert macros.apply(r'$display("xx\n\141");') == r'$display("xx\n\141");'


@pytest.mark.parametrize('eol', ['\n', '\r\n'])
def test_continued_strings_match_across_physical_lines(eol):
    macros = compiled(('CRG', 'tb.dut'))
    source = '"tb.\\' + eol + 'dut\\' + eol + 'TAIL, literal=\\\\n"'
    assert r'`CRG()\124AIL, literal=\\n' in macros.apply(source)


@pytest.mark.parametrize('rows', [
    None, {}, [None], [{'name': 'CRG'}], rules(('2BAD', 'chip')), rules(('define', 'x')),
    rules(('X', '')), rules(('X', 'a\nb')), rules(('X', 'a\\')),
    rules(('X', 'a'), ('`X', 'b')), rules(('X', 'a'), ('Y', 'a')),
    rules(('X', 'wire // truncated')), rules(('X', '"unfinished')), rules(('X', '`X')),
    rules(('X', '`Y'), ('Y', '`X')),
])
def test_invalid_macro_configuration_is_rejected(rows):
    with pytest.raises(ValueError):
        template_macros({'macros': rows})


def test_both_modes_share_one_header_and_keep_original_mapping(tmp_path):
    graph, reg = make_branch_inputs(tmp_path)
    plan = prepare(graph, reg, tmp_path/'plan', False)
    source = 'initial $display("end=$var_clkip, data=%b", $var_clkip);\n// chip.trace'
    templates = dict(with_mux=source, without_mux=source, macros=rules(('DUT', 'chip')))
    output = tmp_path/'clocks.sv'
    exported = plan.export(templates, output)
    text = output.read_text(encoding='utf-8')
    report = json.loads((tmp_path/'clocks.sv.mapping.json').read_text(encoding='utf-8'))
    assert (exported['blocks'], exported['skipped']) == (2, 0)
    assert text.startswith('`define DUT(ct7_value=chip) ct7_value\n\n')
    assert text.count('`define ') == 1 and '`undef' not in text and 'CT7_TEXT_' not in text
    assert '//' not in text and report['macros'] == templates['macros']
    for row in plan.branches()[1]:
        resolved = plan.resolve(row, templates)
        original = 'chip.sink.' + row['pin']
        assert resolved['ready'] and resolved['code'] in text
        assert resolved['values']['var_clkip'] == original
        assert any(b['clkip'] == original and b['values']['var_clkip'] == original for b in report['branches'])
        assert '`DUT().sink.' + row['pin'] in text
    plan.export(templates, output)
    assert output.read_text(encoding='utf-8') == text
    with pytest.raises(ValueError):
        plan.export(dict(templates, macros=rules(('bad name', 'chip'))), output)
    assert output.read_text(encoding='utf-8') == text
    legacy = dict(with_mux=source, without_mux=source)
    plan.export(legacy, output)
    previous = output.read_bytes()
    plan.export(dict(legacy, macros=[]), output)
    assert output.read_bytes() == previous and b'`define' not in previous


def test_handwritten_macro_is_removed_before_either_branch_is_checked(tmp_path):
    graph, reg = make_branch_inputs(tmp_path)
    plan = prepare(graph, reg, tmp_path/'plan', False)
    templates = dict(with_mux="`define DUT other\nwire w = $var_clkip;",
                     without_mux="wire w = $var_clkip;", macros=rules(('DUT', 'chip')))
    output = tmp_path/'some.sv'
    result = plan.export(templates, output)
    assert (result['blocks'], result['skipped']) == (2, 0)
    assert output.read_text(encoding='utf-8').splitlines() == [
        '`define DUT chip', '', 'wire w = `DUT.sink.clk_mux;', '', 'wire w = `DUT.sink.clk_other;']


def test_header_aliases_and_manual_calls_use_the_same_configured_name():
    source = '`ifdef ROOT\nwire o = `ALIAS | `ROOT.net;\n`endif\ninitial $display("at tb.dut.net");'
    macros = template_macros(dict(with_mux=source, macros=rules(('ROOT','tb.dut'),('ALIAS','`ROOT.q'))))
    assert macros.header().splitlines() == ['`define ROOT(ct7_value=tb.dut) ct7_value', '`define ALIAS `ROOT().q']
    code=macros.apply(source)
    assert '`ifdef ROOT\nwire o = `ALIAS | `ROOT().net;' in code
    assert '`ROOT(`"at `ROOT().net`")' in code
    assert macros.apply('`LOG(`"at `ROOT.net`")') == '`LOG(`"at `ROOT().net`")'


def test_exact_template_string_rule_does_not_hide_expanded_signal_fragments(tmp_path):
    graph,reg=make_branch_inputs(tmp_path)
    plan=prepare(graph,reg,tmp_path/'plan',False)
    source='initial $display("$var_clkip");'
    templates=dict(with_mux=source,without_mux=source,macros=rules(('DUT','chip'),('OLD','"$var_clkip"')))
    result=plan.export(templates,tmp_path/'clocks.sv')
    assert result['blocks']==2 and result['skipped']==0
    assert '`DUT().sink.clk_mux' in (tmp_path/'clocks.sv').read_text(encoding='utf-8')


@pytest.mark.parametrize('value', [',',')','u.x,y','ct7_prefix,ct7_suffix'])
def test_punctuation_rules_do_not_introduce_any_other_macro(value):
    source = 'initial $display("text='+value+'");'
    macros = template_macros(dict(with_mux=source,macros=rules(('PUNCT',value))))
    assert re.findall(r'^`define (\w+)', macros.header(), re.M)==['PUNCT']
    assert '`PUNCT()' in macros.apply(source)
    assert '`define' not in macros.apply(source) and '`undef' not in macros.apply(source)
