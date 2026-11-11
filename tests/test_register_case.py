import copy
import pytest

from clocktree.register_expr import Expressions, MappingError
from clocktree.register_trace import Flow
from test_register_trace import Worker, assignment, net, op, constant


def case(selector, labels, selected=0, kind='npiCaseExact'):
    return dict(type='npiCase',condition=selector,case_type=kind,
                case_items=[dict(selected=i==selected,expressions=values) for i,values in enumerate(labels)])


def row(*guards,sequential=True):
    rhs=op('npiConcatOp',net('top.B',3,0),net('top.C',1,0),net('top.D',1,0),width=8)
    return assignment(net('top.A',7,0,kind='npiReg'),rhs,sequential,
                      [*guards,dict(type='npiAlways',line=1,file='case.sv')])


@pytest.mark.parametrize('default', [False,True])
def test_runtime_case_and_default_keep_direct_data_input_and_exact_register_bits(default):
    guard=case(net('top.state',1,0),[[constant(1,2),constant(2,2)],[]],int(default))
    statement=row(dict(type='npiCaseItem'),guard)
    flow=Flow(Worker({}),'top',[])
    for i in range(4):
        action=list(flow.row_actions('top.B[{}]'.format(i),'load',statement,None))[0]
        assert action['status']=='register' and action['relation']=='direct'
        assert action['data_input']=='top.B[{}]'.format(i)
        assert action['capture_target']=='top.A[{}]'.format(i+4)
        assert action['proof']['basis']=='edge_triggered_always'


def test_nested_case_and_if_capture_guard_do_not_turn_data_into_conditional_mapping():
    outer=case(net('top.state',1,0),[[constant(1,2)],[]])
    inner=case(net('top.other'),[[constant(0)],[]],1)
    statement=row(dict(type='npiCaseItem'),inner,dict(type='npiIf',branch='true',condition=net('top.enable')),
                  dict(type='npiCaseItem'),outer)
    action=list(Flow(Worker({}),'top',[]).row_actions('top.B[2]','load',statement,None))[0]
    assert action['data_input']=='top.B[2]' and action['capture_target']=='top.A[6]' and action['relation']=='direct'


@pytest.mark.parametrize('selected,expected',[(0,True),(1,False),(2,False)])
def test_dft_case_prunes_excluded_items_and_default(selected,expected):
    guard=case(net('top.dft_mode'),[[constant(0)],[constant(1)],[]],selected)
    expr=Expressions(['dft_mode']);assert expr.case_enabled(guard) is expected
    action=list(Flow(Worker({}),'top',['dft_mode']).row_actions('top.B[0]','load',row(guard),None))[0]
    assert action.get('status')==('register' if expected else 'pruned')
    assert expr.used=={'top.dft_mode'}


def test_dft_alias_is_resolved_and_default_can_precede_explicit_items():
    alias=assignment(net('top.alias'),net('top.dft_mode'),ancestors=[])
    flow=Flow(Worker({('top.alias','driver'):[alias]}),'top',['dft_mode'])
    guard=case(net('top.alias'),[[],[constant(1)]])
    assert flow.expr.case_enabled(guard) is True
    assert 'top.dft_mode' in flow.expr.used
    guard['case_items'][1]['expressions']=[constant(0)]
    assert flow.expr.case_enabled(guard) is False


@pytest.mark.parametrize('kind,wildcard',[('npiCaseX','x'),('npiCaseZ','z')])
def test_wildcard_case_first_matching_item_prunes_later_items(kind,wildcard):
    wild=dict(type='npiConstant',value='0'+wildcard,width=2,signed=False)
    selector=op('npiConcatOp',constant(0),net('top.dft_mode'),width=2)
    labels=[[wild],[constant(0,2)],[]]
    for selected,expected in enumerate((True,False,False)):
        assert Expressions(['dft_mode']).case_enabled(case(selector,labels,selected,kind)) is expected
    assert Expressions([]).case_enabled(case(net('top.runtime',1,0),labels,0,kind)) is None


def test_exact_xz_width_signedness_and_known_mismatch_with_unknown_lane():
    x=dict(type='npiConstant',value='x',width=1,signed=False)
    z=dict(type='npiConstant',value='z',width=1,signed=False)
    assert Expressions([]).case_enabled(case(x,[[x],[z]],0)) is True
    assert Expressions([]).case_enabled(case(x,[[x],[z]],1)) is False
    negative=constant(15,4);negative['signed']=True
    wide=constant(255,8);wide['signed']=True
    assert Expressions([]).case_enabled(case(negative,[[wide]])) is True
    wide['signed']=False
    assert Expressions([]).case_enabled(case(negative,[[wide]])) is False
    selector=op('npiConcatOp',constant(1),net('top.dynamic'),width=2)
    assert Expressions([]).case_enabled(case(selector,[[constant(0,2)]])) is False


@pytest.mark.parametrize('process',['npiAlways','npiInitial'])
def test_case_in_combination_or_initial_process_is_not_sequential_storage(process):
    statement=row(case(net('top.state'),[[constant(0)],[]]),sequential=False)
    statement['ancestors'][-1]['type']=process
    actions=list(Flow(Worker({('top.B[0]','load'):[statement]}),'top',[]).actions('top.B[0]','load'))
    assert not any(a.get('status')=='register' for a in actions)
    assert any(a.get('status')=='unresolved' for a in actions)


def test_selector_load_is_not_a_data_input_but_does_not_hide_real_data_load():
    control=dict(scope='top',statement={'type':'npiCase'},ancestors=[])
    data=row(case(net('top.B',3,0),[[constant(1,4)],[]]))
    flow=Flow(Worker({('top.B[0]','load'):[control,data]}),'top',[])
    actions=list(flow.actions('top.B[0]','load'))
    assert len(actions)==1 and actions[0]['data_input']=='top.B[0]'


def test_missing_case_metadata_and_unhandled_delay_remain_explicit():
    for guard in (dict(type='npiCase'),dict(type='npiCaseItem'),dict(type='npiDelayControl')):
        actions=list(Flow(Worker({('top.B[0]','load'):[row(guard)]}),'top',[]).actions('top.B[0]','load'))
        assert len(actions)==1 and actions[0]['status']=='unresolved'


def test_legacy_npi_ambiguous_one_bit_literal_does_not_invent_case_match():
    literal=constant(1);literal['text']="'b1"  # Both '1 and 1'b1 on Verdi 2018.
    for selector in (constant(1,4),constant(15,4)):
        with pytest.raises(MappingError,match='ambiguous_case_literal_extension'):
            Expressions([]).case_enabled(case(selector,[[literal],[]]))
    literal['text']="'1"  # If a newer API preserves the fill literal, use it.
    assert Expressions([]).case_enabled(case(constant(15,4),[[literal],[]])) is True
    assert Expressions([]).case_enabled(case(constant(1,4),[[literal],[]])) is False
