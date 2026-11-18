import json
import re

import pytest

from clocktree.sv_macros import template_macros
from clocktree.sv_templates import prepare
from test_sv_templates import setup as make_branch_inputs


def rules(*pairs):
    return [{'name': name, 'text': text} for name, text in pairs]


def compiled(*pairs):
    return template_macros({'macros': rules(*pairs)})


def test_code_uses_longest_literal_match_and_preserves_sv_tokens():
    macros = compiled(('`ROOT', 'tb.dut'), ('BLOCK', 'tb.dut.crg'), ('ONE', "1'b1"))
    source = (
        '`ifdef ENABLE assign clk = tb.dut.crg.clk[3] & tb.dut.other & 1\'b1; `endif\n'
        'assign n = xtb.dut + tb.dut2 + tb.dut.crg_x;\n'
        'wire \\tb.dut.crg ; // tb.dut.crg\n'
        '/* tb.dut */ `include "tb.dut.svh"\n'
        '`define USER tb.dut \\\n  tb.dut.crg\n'
        'assign x = `USER + `BLOCK.clk;\n')
    assert macros.apply(source) == source.replace(
        "tb.dut.crg.clk[3] & tb.dut.other & 1'b1", "`BLOCK.clk[3] & `ROOT.other & `ONE"
    ).replace('tb.dut.crg_x;', '`ROOT.crg_x;')
    assert macros.rows()[0]['name'] == 'ROOT'


def test_quoted_fragments_compile_to_one_literal_and_helpers_are_private():
    macros = compiled(('CRG', 'tb.dut'), ('LABEL', '"READY"'))
    msg = r'"at tb.dut, url=http://tb.dut/a /*ok*/; %0d) \"TB\". tb.dutTAIL, `user, tb.duttb.dut"'
    source = '$display(' + msg + ', tb.dut.clk);\n$display(' + msg + ', "READY");'
    actual = macros.apply(source)
    helper = re.search(r'^`define (CT7_TEXT_\w+) ', actual).group(1)
    assert actual.count('`define ') == 1 and actual.count('`undef ') == 1
    assert actual.count('`' + helper) == 2 and actual.endswith('`undef ' + helper + '\n')
    assert 'url=http://`CRG/a /*ok*/; %0d)' in actual
    assert r'\042TB\042. `CRG``TAIL, \140user, `CRG`CRG' in actual
    assert ', `CRG.clk)' in actual and ', `LABEL)' in actual
    assert macros.apply(source) == actual
    assert macros.apply('$display("READY");') == '$display(`LABEL);'
    assert macros.apply(r'$display("xx\n\141");') == r'$display("xx\n\141");'
    # A user name colliding with a private helper still has its own definition.
    collision = compiled(('CRG', 'tb.dut'), ('LABEL', '"READY"'), (helper, 'unseen'))
    assert ('`define ' + helper + '_ ') in collision.apply(source)


@pytest.mark.parametrize('eol', ['\n', '\r\n'])
def test_continued_strings_match_across_physical_lines(eol):
    macros = compiled(('CRG', 'tb.dut'))
    source = '"tb.\\' + eol + 'dut\\' + eol + 'TAIL, literal=\\\\n"'
    assert r'`CRG``TAIL, literal=\\n' in macros.apply(source)


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
    assert text.count('`define DUT chip\n') == 1
    assert text.count('`define CT7_TEXT_') == text.count('`undef CT7_TEXT_') == 2
    assert text.count('// chip.trace') == 2 and report['macros'] == templates['macros']
    for row in plan.branches()[1]:
        resolved = plan.resolve(row, templates)
        original = 'chip.' + row['pin']
        assert resolved['ready'] and resolved['code'] in text
        assert resolved['values']['var_clkip'] == original
        assert any(b['clkip'] == original and b['values']['var_clkip'] == original for b in report['branches'])
        assert '`DUT.' + row['pin'] in text
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


def test_conflicting_template_does_not_block_the_other_mode(tmp_path):
    graph, reg = make_branch_inputs(tmp_path)
    plan = prepare(graph, reg, tmp_path/'plan', False)
    templates = dict(with_mux="`define DUT other\nwire w = $var_clkip;",
                     without_mux="wire w = $var_clkip;", macros=rules(('DUT', 'chip')))
    output = tmp_path/'some.sv'
    result = plan.export(templates, output)
    assert (result['blocks'], result['skipped']) == (1, 1)
    assert 'DUT' in result['issues'][0]
    assert '`DUT.clk_other' in output.read_text(encoding='utf-8')
    assert '`DUT.clk_mux' not in output.read_text(encoding='utf-8')
