import pytest

from clocktree.sv_template_text import variables,render,template_tokens,identifier_fragment,VARIABLE_BUTTONS
from clocktree.sv_editor import syntax_spans


def test_identifier_suffix_and_signal_expression_have_distinct_bindings():
    template='check_$var_clkip_sva: assert property (@(posedge $var_clkip) disable iff (!$var_rst) $var_gt0);'
    values={'var_clkip':'top.u.clk','var_rst':'top.rst_n','var_gt0':'top.C[7]'}
    assert variables(template)==set(values)
    assert render(template,values)=='check_top_u_clk_sva: assert property (@(posedge top.u.clk) disable iff (!top.rst_n) top.C[7]);'


@pytest.mark.parametrize('template,expected',[
    ('$var_clkip_sva','top_u_clk_3_sva'),
    ('check_$var_clkip','check_top_u_clk_3'),
    ('check_${var_clkip}_sva','check_top_u_clk_3_sva'),
    ('$var_clkip[0]','top.u.clk[3][0]'),
    ('$var_clkip','top.u.clk[3]'),
    ('$var_gt0_$var_gt1','top_C_7_top_C_6'),
    ('$var_gt0$var_gt1','top_C_7top_C_6'),
    ('$display("check_$var_clkip_sva"); // $$var_clkip','\u0024display("check_top_u_clk_3_sva"); // $$var_clkip'),
])
def test_token_boundaries_braces_and_escaped_literals(template,expected):
    assert render(template,{'var_clkip':'top.u.clk[3]','var_gt0':'top.C[7]','var_gt1':'top.C[6]'})==expected


@pytest.mark.parametrize('value',('$var_pll10','$var_gt00','$var_clkipx','$var_bad','${var_clkip_sva}','${var_clkip'))
def test_mistyped_or_unclosed_variables_are_not_silently_shortened(value):
    with pytest.raises(ValueError):variables(value)
    assert any(t.error for t in template_tokens(value))


def test_replacements_are_literal_never_evaluated_or_recursively_substituted():
    assert render('$var_clkip',{'var_clkip':'$var_gt0'})=='$var_gt0'
    assert render('$display("${var_clkip}");',{'var_clkip':'top.clk'})=='$display("top.clk");'
    with pytest.raises(ValueError,match='缺少'):render('check_$var_clkip_sva',{})
    assert variables('$$var_clkip $time $display $rose')==set()
    assert identifier_fragment("1'b1")=='_1_b1'
    assert identifier_fragment('{top.A[3], top.B[0]}')=='top_A_3_top_B_0'


def test_palette_has_all_twelve_current_variables_and_preserves_longest_name():
    assert len(VARIABLE_BUTTONS)==12
    for name in VARIABLE_BUTTONS:
        assert variables('$'+name)=={name}
    assert variables('$var_gt0_suffix $var_gt_suffix $var_pll0_suffix $var_pllsel_suffix')=={'var_gt0','var_gt','var_pll0','var_pllsel'}


def test_systemverilog_lexer_respects_comments_strings_and_literals():
    source='''/* always_ff\nlogic 8'hff */
logic [7:0] value = 8'hff;
always_ff @(posedge clk) begin
  $display("// not a comment \\\"quoted\\\"", value); // endmodule
  value <= 1'b1;
end
'''
    spans=[(kind,source[start:end]) for kind,start,end in syntax_spans(source)]
    assert ('comment',"/* always_ff\nlogic 8'hff */") in spans
    assert ('type','logic') in spans and ('keyword','always_ff') in spans
    assert ('system','$display') in spans and ('number',"8'hff") in spans
    assert ('comment','// endmodule') in spans and ('keyword','endmodule') not in spans
    assert sum(kind=='string' for kind,_ in spans)==1
