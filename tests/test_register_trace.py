import copy
import json
import sqlite3
import pytest

from clocktree.register_expr import Expressions, MappingError, sequential_proof
from clocktree.register_trace import Flow, create_database, trace_job, RegisterIndex


def net(name,left=None,right=None,kind='npiNet'):
    return dict(type=kind,full=name,name=name.rsplit('.',1)[-1],width=1 if left is None else abs(left-right)+1,
                signed=False,ranges=[] if left is None else [[str(left),str(right)]],text=name)


def select(parent,left,right=None):
    if right is None:return dict(type='npiNetBit',parent=parent,indices=[str(left)],width=1)
    return dict(type='npiPartSelect',parent=parent,left=str(left),right=str(right),width=abs(left-right)+1)


def constant(value,width=1):return dict(type='npiConstant',value=format(value,'0{}b'.format(width)),width=width,signed=False)


def op(name,*args,width=1):return dict(type='npiOperation',op=name,args=list(args),width=width,signed=False)


def assignment(lhs,rhs,sequential=False,ancestors=None):
    chain=ancestors if ancestors is not None else [dict(type='npiAlways',always='npiAlways',line=10,file='test.sv')]
    if sequential:chain=[dict(type='npiEventControl',condition=op('npiPosedgeOp',net('top.clk')))] + chain
    return dict(scope='top',statement=dict(type='npiAssignment',line=11,file='test.sv'),lhs=lhs,rhs=rhs,ancestors=chain)


def test_dft_zero_selects_only_functional_branch_and_exact_bits():
    expr=Expressions()
    good=select(net('top.D',15,0),5,2)
    bad={'type':'unsupported','width':4}
    value=op('npiConditionOp',net('top.dft_mode'),bad,good,width=4)
    assert [b.signals for b in expr.vector(value)]==[('top.D[{}]'.format(i),) for i in range(2,6)]
    assert expr.used=={'top.dft_mode'}
    inverted=op('npiConditionOp',op('npiNotOp',net('top.dft_mode')),good,bad,width=4)
    assert expr.vector(inverted)==expr.vector(value)


def test_dft_matching_is_exact_and_does_not_override_similarly_named_signals():
    expr=Expressions()
    assert expr.vector(net('top.dft_mode'))[0].value==0
    assert expr.vector(net('top.not_dft_mode'))[0].signals==('top.not_dft_mode',)
    expr=Expressions(['top.a.dft_mode'])
    assert expr.vector(net('top.b.dft_mode'))[0].signals==('top.b.dft_mode',)


def test_ascending_concat_reversal_replication_and_nonzero_ranges():
    expr=Expressions()
    assert expr.names(net('top.up',2,5))==['top.up[5]','top.up[4]','top.up[3]','top.up[2]']
    a,b=select(net('top.C',15,0),3,2),select(net('top.D',15,0),9,8)
    joined=op('npiConcatOp',a,b,width=4)
    assert [x.signals[0] for x in expr.vector(joined)]==['top.D[8]','top.D[9]','top.C[2]','top.C[3]']
    repeated=op('npiMultiConcatOp',constant(2,32),select(net('top.C',15,0),7),width=2)
    assert [x.signals[0] for x in expr.vector(repeated)]==['top.C[7]']*2


def test_dynamic_index_is_diagnostic_and_unknown_mux_is_not_unique_mapping():
    expr=Expressions()
    dynamic=dict(type='npiNetBit',parent=net('top.C',15,0),indices=[''],width=1)
    with pytest.raises(MappingError,match='dynamic'):expr.vector(dynamic)
    unknown=op('npiConditionOp',net('top.mode'),net('top.a'),net('top.b'))
    bit=expr.vector(unknown)[0]
    assert set(bit.signals)=={'top.mode','top.a','top.b'} and bit.relation=='conditional'


def test_mixed_signed_binary_context_zero_extends_and_signed_context_extends_sign():
    from clocktree.register_expr import number
    narrow=constant(15,4);narrow['signed']=True
    wide=constant(0,8)
    mixed=op('npiAddOp',narrow,wide,width=8)
    assert number(Expressions().vector(mixed))==15
    wide['signed']=True;mixed['signed']=True
    assert number(Expressions().vector(mixed))==255
    equal=op('npiEqOp',narrow,constant(255,8))
    assert number(Expressions().vector(equal))==0


def test_state_requires_an_always_edge_or_a_proven_hold_not_reg_declaration():
    row=assignment(net('top.q',kind='npiReg'),net('top.d'))
    assert sequential_proof(row) is None
    row=assignment(net('top.q'),net('top.d'),True)
    assert sequential_proof(row)['kind']=='flipflop'
    row['ancestors'][-1]['type']='npiInitial'
    assert sequential_proof(row) is None


class Worker:
    def __init__(self,queries,ports=None):self.queries=queries;self.ports=ports or {}
    def query(self,operation,*args):
        if operation=='port':return iter([self.ports[args]])
        return iter(copy.deepcopy(self.queries.get(args,[])))
    def check(self):pass


def test_simple_comb_reg_is_passed_and_latch_is_proven_from_all_writers():
    conditional=dict(type='npiIf',condition=net('top.en'),branch='true',file='x.sv',line=3)
    q=net('top.q',kind='npiReg');d=net('top.d')
    row=assignment(q,d,ancestors=[conditional,dict(type='npiAlways',line=2,file='x.sv')])
    worker=Worker({('top.q','driver'):[row]});flow=Flow(worker,'top',[])
    assert flow.latch_proof('top.q')['basis']=='all_writers_share_conditional_hold'
    assert list(flow.actions('top.q','driver'))[0]['status']=='register'
    worker.queries[('top.q','driver')]=[assignment(q,d)]
    assert flow.latch_proof('top.q') is None
    assert list(flow.actions('top.q','driver'))[0]['next']=='top.d'


def test_unsupported_load_does_not_hide_other_register_sinks():
    q=select(net('top.E',15,0),10)
    rows=[dict(scope='top',statement={'type':'npiTaskCall'},ancestors=[]),assignment(q,net('top.done'),True)]
    flow=Flow(Worker({('top.done','load'):rows}),'top',[])
    results=list(flow.actions('top.done','load'))
    assert any(a.get('status')=='unresolved' for a in results)
    assert any(a.get('status')=='register' and a['endpoint']=='top.E[10]' and a['side']=='D' for a in results)


def test_dft_prunes_load_only_branch_and_keeps_all_functional_fanout():
    lhs=net('top.E',15,0)
    rhs=op('npiConditionOp',net('top.dft_mode'),net('top.done'),constant(0))
    rows=[assignment(select(lhs,9),rhs,True),assignment(select(lhs,10),net('top.done'),True),assignment(select(lhs,11),net('top.done'),True)]
    flow=Flow(Worker({('top.done','load'):rows}),'top',['dft_mode'])
    found=[a['endpoint'] for a in flow.actions('top.done','load') if a.get('status')=='register']
    assert found==['top.E[10]','top.E[11]']


def test_condition_alias_can_resolve_dft_zero_without_tracing_its_clock():
    queries={('top.alias','driver'):[assignment(net('top.alias'),net('top.dft_mode'),ancestors=[])]}
    flow=Flow(Worker(queries),'top',['dft_mode'])
    bit=flow.expr.vector(op('npiConditionOp',net('top.alias'),net('top.bad'),net('top.good')))[0]
    assert bit.signals==('top.good',) and flow.expr.used=={'top.dft_mode'}


def test_sqlite_trace_preserves_bit_mapping_proof_and_full_export(tmp_path):
    low=net('top.u.sel',3,0);high=select(net('top.D',15,0),5,2)
    port=dict(event='port',direction='npiInput',low=low,high=high)
    queries={}
    for i in range(4):
        queries[('top.u.sel[{}]'.format(i),'driver')]=[dict(scope='top.u',statement={'type':'npiPort'},direction='npiInput',low=low,high=high,ancestors=[])]
        queries[('top.D[{}]'.format(i+2),'driver')]=[assignment(net('top.D',15,0,kind='npiReg'),net('top.data',15,0),True)]
    worker=Worker(queries,{('top.u','sel'):port});flow=Flow(worker,'top',['dft_mode'])
    seed=dict(instance='top.u',role='pll_sel',pin='sel',direction='driver')
    db=create_database(tmp_path/'registers.sqlite');db.execute('INSERT INTO jobs VALUES (1,?,?,?)',(json.dumps(seed),'pending','{}'));db.commit()
    trace_job(db,1,seed,flow,lambda *args:None);db.close()
    index=RegisterIndex(tmp_path);total,rows=index.query()
    assert total==4 and [r[5] for r in rows]==['top.D[2]','top.D[3]','top.D[4]','top.D[5]']
    detail=index.detail(rows[0][0]);assert detail['proof']['basis']=='edge_triggered_always'
    assert detail['steps'][0]['signal']=='top.u.sel[0]'
    assert index.export(tmp_path/'map.csv')==4
    (tmp_path/'manifest.json').write_text(json.dumps({'request':{'seeds':[seed]}}))
    index.export_json(tmp_path/'map.json')
    report=json.loads((tmp_path/'map.json').read_text())
    assert len(report['results'])==4 and report['paths'] and not report.get('sv_generated')
