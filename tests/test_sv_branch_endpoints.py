"""Terminal cardinality and real L1 origins, including one-device branches."""
import copy
import json

import pytest

from clocktree.register_trace import create_database
from clocktree.storage import write_graph,ResultStore,index_graph
from clocktree.sv_templates import prepare,architecture_signature,identity


ALL=' '.join('$'+name for name in ('var_pllsel','var_div','var_swdone','var_gt0','var_gt1','var_gt2',
    'var_pll0','var_pll1','var_pll2','var_pll3','var_clkip','var_rst'))


def node(name,kind='gate',layer=1,inputs=('i',),outputs=('o',)):
    return dict(id='chip.'+name,module='fixture_'+kind,component=0,kind=kind,layer=layer,inputs=list(inputs),
        output=outputs[0] if outputs and kind!='wire' else None,outputs=list(outputs) if kind=='wire' else [],
        aux_ports={},terminal=True,port_connections={p:{'status':'connected','coverage':'bit',
            'full_name':'chip.'+name+'_'+p} for p in inputs+outputs})


def graph(nodes,links=()):
    result={'tree_type':'clock','config':{'design':{'top':'chip','filelist':'/fixture.f'},
        'layers':[{'index':i,'hierarchy':'chip','reset_signal':'rst'+str(i),'modules':[]} for i in range(1,6)]},
        'nodes':nodes,'edges':[dict(source='chip.'+a,source_pin=out,target='chip.'+b,target_pin=pin) for a,out,b,pin in links],
        'summary':{'connectivity_complete':True}}
    used={e['source'] for e in result['edges']}
    by_id={n['id']:n for n in nodes}
    for i,e in enumerate(result['edges']):e.update(id='edge'+str(i),layer_delta=by_id[e['target']]['layer']-by_id[e['source']]['layer'])
    for n in nodes:
        if n['kind']!='wire':n['terminal']=n['id'] not in used
    return result


def registers(tmp_path,g,rows):
    path=tmp_path/'registers';path.mkdir()
    write_graph(path/'manifest.json',{'request':{'config':g['config']}})
    db=create_database(path/'registers.sqlite')
    for i,(inst,role,pin,signal) in enumerate(rows,1):
        db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(i,json.dumps({'instance':inst,'role':role,'pin':pin}),
            'complete',json.dumps({'bits':[inst+'.'+pin]})))
        db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(i,pin,signal,'D' if role=='sw_done' else 'Q',
            'direct','register','',None,'{}'))
    db.commit();db.close();return path


@pytest.mark.parametrize('kind',['gate','div','occ','mux'])
def test_one_l1_device_is_one_complete_branch_with_defaults(tmp_path,kind):
    n=node('single',kind,inputs=('i','j') if kind=='mux' else ('i',))
    g=graph([n]);original=copy.deepcopy(g)
    plan=prepare(g,None,tmp_path/'plan',False);count,rows=plan.branches()
    assert count==plan.source()['branches']==1
    result=plan.resolve(rows[0],dict(with_mux=ALL,without_mux=ALL))
    assert result['ready'],result
    values=result['values']
    assert result['mode']==('with_mux' if kind=='mux' else 'without_mux')
    assert values['var_pll0']=='chip.single_i' and values['var_clkip']=='chip.single_o'
    assert values['var_div']=='0' and values['var_rst']=='chip.rst1'
    for v in ('var_pllsel','var_swdone','var_gt0','var_gt1','var_gt2','var_pll2','var_pll3'):
        assert values[v]=="1'b1"
    assert values['var_pll1']==('chip.single_j' if kind=='mux' else "1'b1")
    assert result['pin']=='o' and result['branch']['single_l1_device']
    assert g==original  # UI/export semantics never rewrite proven connectivity.


def test_l1_subsystem_inputs_remain_independent_even_on_same_net(tmp_path):
    n=node('s','subsystem',inputs=('clk[2]','clk[3]'),outputs=())
    for b in n['port_connections'].values():b['full_name']='chip.same_clock'
    plan=prepare(graph([n]),None,tmp_path/'plan',False)
    assert plan.branches()[0]==2
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':ALL});assert value['ready'],value
        assert value['values']['var_clkip']==value['values']['var_pll0']=='chip.same_clock'
    result=plan.export({'without_mux':ALL},tmp_path/'all.sv')
    assert result['blocks']==2 and result['skipped']==0


@pytest.mark.parametrize('indexed',[False,True])
def test_terminals_at_different_layers_and_same_layer_chain_count_once(tmp_path,indexed):
    nodes=[node('a','occ'),node('b','occ'),node('c','occ',3),node('d','occ',5),node('alone','occ')]
    g=graph(nodes,[('a','o','b','i'),('b','o','c','i'),('b','o','d','i')])
    if indexed:
        store=ResultStore(tmp_path/'graph.sqlite',True);index_graph(g,store);g=store.graph()
    plan=prepare(g,None,tmp_path/'plan',False)
    assert plan.branches()[0]==3
    assert {r['instance'] for r in plan.branches()[1]}=={'chip.c','chip.d','chip.alone'}
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':'$var_pll0 $var_clkip $var_rst'})
        assert value['ready'],value
        origin='alone' if row['instance']=='chip.alone' else 'a'
        assert value['values']['var_pll0']=='chip.'+origin+'_i'
        assert value['values']['var_rst']=='chip.rst1'
        assert value['branch']['origins']==[{'instance':'chip.'+origin,'kind':'occ','layer':1}]
    if indexed:store.close()


def test_terminal_mux_merges_input_paths_and_classifies_by_actual_first_device(tmp_path):
    first=node('first','occ');end=node('end','mux',2,('a','b'))
    g=graph([first,end],[('first','o','end','a'),('first','o','end','b')])
    end['aux_ports']={'pll_sel':'sel'}
    reg=registers(tmp_path,g,[(end['id'],'pll_sel','sel','chip.Q[4]')])
    plan=prepare(g,reg,tmp_path/'plan',False)
    assert plan.branches()[0]==1
    row=plan.branches()[1][0];value=plan.resolve(row,{'without_mux':'$var_pll0 $var_pll1 $var_clkip $var_pllsel'})
    assert value['ready'],value
    assert value['mode']=='without_mux' and value['values']['var_pll0']=='chip.first_i'
    assert value['values']['var_pll1']=="1'b1" and value['values']['var_clkip']=='chip.end_o'
    assert plan.candidates(row,'pll_sel')[0]['expression']=='chip.Q[4]'


def test_single_l1_real_controls_override_defaults_and_unfinished_never_becomes_constant(tmp_path):
    n=node('d','div');n['aux_ports']={'div':'ratio','sw_done':'done'};g=graph([n])
    missing=prepare(g,None,tmp_path/'missing',False)
    assert not missing.resolve(missing.branches()[1][0],{'without_mux':ALL})['ready']
    reg=registers(tmp_path,g,[(n['id'],'div','ratio','chip.Q[7]'),(n['id'],'sw_done','done','chip.E[10]')])
    plan=prepare(g,reg,tmp_path/'plan',False)
    value=plan.resolve(plan.branches()[1][0],{'without_mux':ALL})
    assert value['ready'],value
    assert value['values']['var_div']=='chip.Q[7]' and value['values']['var_swdone']=='chip.E[10]'
    db=create_database(reg/'registers.sqlite')
    db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(2,'done','chip.E[11]','D','direct','register','',None,'{}'))
    db.commit();db.close();ambiguous=prepare(g,reg,tmp_path/'ambiguous',False)
    row=ambiguous.branches()[1][0];assert not ambiguous.resolve(row,{'without_mux':ALL})['ready']
    choices=ambiguous.candidates(row,'var_swdone');assert len(choices)==2
    assert ambiguous.resolve(row,{'without_mux':ALL},{'var_swdone':choices[1]['id']})['ready']
    assert ambiguous.branches()[0]==1


@pytest.mark.parametrize('problem', ['undriven', 'multiple_drivers', 'multiple_l1_inputs'])
def test_invalid_wire_input_skips_only_reachable_endpoints(tmp_path, problem):
    wire = node('w', 'wire', inputs=('a','b'), outputs=('x','y'))
    wire['terminal_inputs'] = ['a','b']
    wire['terminal_outputs'] = ['x','y']
    wire['internal_connections'] = [dict(input=a,output=b,status='verified') for a,b in [('a','y'),('b','x')]]
    right = node('right', layer=2)
    g = graph([wire,right], [('w','y','right','i')])
    if problem == 'undriven':
        g['boundaries'] = [dict(status=problem,instance=wire['id'],pin='a',direction='driver')]
    else:
        g['diagnostics'] = [dict(code=problem,instance=wire['id'],pin='a')]
    plan = prepare(g,None,tmp_path/'plan',False)
    templates = {'without_mux':'// $var_clkip'}
    resolved = {r['instance']:plan.resolve(r,templates) for r in plan.branches()[1]}
    assert not resolved[right['id']]['ready'], resolved
    assert resolved[wire['id']]['pin']=='x' and resolved[wire['id']]['ready'], resolved
    report = plan.export(templates,tmp_path/'out.sv')
    assert report['blocks']==1 and report['skipped']==1


def test_single_l1_wire_counts_terminal_outputs_and_preserves_channel_input(tmp_path):
    n=node('w','wire',inputs=('a','b'),outputs=('x','y','z'))
    n['terminal_inputs']=['a','b']
    n['internal_connections']=[dict(input=a,output=b,status='verified') for a,b in [('a','y'),('a','z'),('b','x')]]
    plan=prepare(graph([n]),None,tmp_path/'plan',False)
    assert plan.branches()[0]==plan.source()['branches']==3
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':ALL});assert value['ready'],value
        pin='b' if row['pin']=='x' else 'a'
        assert value['values']['var_pll0']=='chip.w_'+pin
        assert value['values']['var_clkip']=='chip.w_'+row['pin']


@pytest.mark.parametrize('kind', ['occ_root', 'mux', 'gate_wrap'])
def test_single_later_layer_device_uses_actual_origin_and_output(tmp_path, kind):
    start = node('start', kind, 3, ('i', 'j') if kind == 'mux' else ('i',))
    if kind == 'mux':
        start['l1_inputs'] = {pin: {'signal': 'chip.pad_'+pin, 'status': 'verified'} for pin in start['inputs']}
    plan = prepare(graph([start]), None, tmp_path/'plan', False)
    mode = 'with_mux' if kind == 'mux' else 'without_mux'
    value = plan.resolve(plan.branches()[1][0], {mode: ALL})
    assert value['ready'], value
    assert value['branch']['origins'] == [{'instance':'chip.start', 'layer':3, 'kind':kind}]
    assert value['branch']['single_device'] and value['pin'] == 'o'
    assert value['values']['var_pll0'] == 'chip.start_i'
    assert value['values']['var_clkip'] == 'chip.start_o' and value['values']['var_rst'] == 'chip.rst3'
    assert value['values']['var_div'] == '0'


def test_later_layer_chain_orders_both_gate_types_by_connectivity(tmp_path):
    start = node('s','occ_root',2)
    first = node('a','sync_gate',3)
    second = node('b','gate_wrap',3)
    end = node('t','occ_branch',5)
    for gate in (first,second):gate['aux_ports'] = {'gt':'en'}
    g = graph([second,start,end,first],[('s','o','a','i'),('a','o','b','i'),('b','o','t','i')])
    reg = registers(tmp_path,g,[(first['id'],'gt','en','chip.Q[7]'),(second['id'],'gt','en','chip.Q[5]')])
    plan = prepare(g,reg,tmp_path/'plan',False)
    assert plan.branches()[0] == 1
    result = plan.resolve(plan.branches()[1][0], {'without_mux':'$var_gt0 $var_gt1 $var_gt2 $var_pll0 $var_rst $var_clkip'})
    assert result['ready'], result
    assert result['values'] == {'var_gt0':'chip.Q[7]', 'var_gt1':'chip.Q[5]', 'var_gt2':"1'b1",
                                'var_pll0':'chip.s_i', 'var_clkip':'chip.t_i', 'var_rst':'chip.rst2'}


def test_wire_fanout_at_later_terminal_layer_counts_each_proven_output(tmp_path):
    source=node('source','occ');n=node('w','wire',2,('a',),('x','y'))
    n['terminal_inputs']=['a']
    n['internal_connections']=[dict(input='a',output=p,status='verified') for p in ('x','y')]
    plan=prepare(graph([source,n],[('source','o','w','a')]),None,tmp_path/'plan',False)
    assert plan.branches()[0]==2
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':'$var_pll0 $var_clkip'})
        assert value['ready'] and value['values']['var_pll0']=='chip.source_i'
        assert value['values']['var_clkip']=='chip.w_'+row['pin']


def test_previous_single_l1_plan_signature_is_invalidated():
    g=graph([node('one')])
    old=identity('branch-origin-reset-d-input-v2',g.get('config'),g.get('metadata'),g.get('summary'))
    assert architecture_signature(g)!=old
