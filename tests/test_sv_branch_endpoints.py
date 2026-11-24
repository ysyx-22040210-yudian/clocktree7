"""Terminal cardinality and real L1 origins, including one-device branches."""
import copy
import json
from pathlib import Path

import pytest

from clocktree.register_trace import create_database
from clocktree.graph import build_graph
from clocktree.storage import write_graph,ResultStore,index_graph
from clocktree.sv_templates import prepare,architecture_signature,identity


ALL=' '.join('$'+name for name in ('var_pllsel','var_div','var_swdone','var_gt0','var_gt1','var_gt2',
    'var_pll0','var_pll1','var_pll2','var_pll3','var_clkip','var_rst'))


def node(name,kind='gate',layer=1,inputs=('i',),outputs=('o',)):
    return dict(id='chip.'+name,module='fixture_'+kind,component=0,kind=kind,layer=layer,inputs=list(inputs),
        output=outputs[0] if outputs and kind!='wire' else None,outputs=list(outputs) if kind=='wire' else [],
        aux_ports={},terminal=True,port_connections={p:{'status':'connected','coverage':'bit',
            'full_name':'chip.'+name+'_'+p} for p in inputs+outputs})


def sink(name='sink', layer=3, inputs=('clk_i',)):
    return node(name,'subsystem',layer,inputs,())


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


@pytest.mark.parametrize('mode', ['target', 'full'])
@pytest.mark.parametrize('legacy', [False, True])
def test_subsystem_timeout_keeps_earlier_input_and_reports_other_lanes(tmp_path, mode, legacy):
    sink = node('sink', kind='subsystem', inputs=('clk[2]', 'clk[1]', 'clk[0]'), outputs=())
    config = graph([])['config']
    events = [{'event':'metadata', 'extraction_mode':mode}, dict(sink, event='instance', path=sink['id'])]
    events.append({'event':'instance_trace_timeout', 'detail':{'instance':sink['id'], 'pin':'clk[1]', 'direction':'driver'}})
    for pin, status in zip(sink['inputs'], ('input_boundary', 'trace_timeout', 'trace_skipped_timeout')):
        events.append({'event':'trace','instance':sink['id'],'pin':pin,'direction':'driver',
                       'results':[{'status':status,'endpoint':'chip.clk'}]})
    result = build_graph(events+[{'event':'complete'}], config)
    assert not result['summary']['connectivity_complete']
    if legacy:
        result['nodes'][0]['terminal'] = False
    else:
        assert result['nodes'][0]['terminal']
    plan = prepare(result, None, tmp_path/'plan', False)
    rows = {b['pin']:plan.resolve(b, {'without_mux':'$var_clkip'}) for b in plan.branches()[1]}
    assert rows['clk[2]']['ready'], rows['clk[2]']['issues']
    assert any('端口超时' in issue for issue in rows['clk[1]']['issues'])
    assert any('随实例跳过' in issue for issue in rows['clk[0]']['issues'])


@pytest.mark.parametrize('broken', ['unproven_internal', 'unresolved_load', 'missing_load', 'unknown_load_target'])
def test_wire_full_mode_terminal_is_proven_per_output(tmp_path, broken):
    wire = node('w', kind='wire', inputs=('a', 'b'), outputs=('x', 'y'))
    config = graph([])['config']
    events = [dict(wire, event='instance', path=wire['id'])]

    def trace(port, direction, **result):
        events.append({'event':'trace','instance':wire['id'],'pin':port,'direction':direction,'results':[result]})

    for input_pin, output_pin in zip(wire['inputs'], wire['outputs']):
        trace(input_pin, 'driver', status='input_boundary', endpoint='chip.'+input_pin)
        if output_pin=='y' and broken=='unproven_internal':
            trace(output_pin, 'internal', status='unknown_cell')
        else:
            trace(output_pin, 'internal', status='target', instance=wire['id'], pin=input_pin)
        if output_pin=='y' and broken=='unknown_load_target':
            trace(output_pin, 'load', status='target', instance='chip.missing', pin='i')
        elif output_pin!='y' or broken!='missing_load':
            trace(output_pin, 'load', status='trace_limit' if output_pin=='y' and broken=='unresolved_load' else 'no_load')
    result = build_graph(events+[{'event':'complete'}], config)
    assert not result['summary']['connectivity_complete']
    assert result['nodes'][0]['terminal_outputs'] == ['x']
    plan = prepare(result, None, tmp_path/'plan', False)
    assert plan.branches() == (0, [])


@pytest.mark.parametrize('kind',['gate','sync_gate','gate_wrap','div','occ','occ_root','occ_branch','mux','wire'])
def test_ordinary_tip_is_visible_but_is_not_an_sv_endpoint(tmp_path,kind):
    n=node('single',kind,inputs=('i','j') if kind=='mux' else ('i',))
    g=graph([n]);original=copy.deepcopy(g)
    plan=prepare(g,None,tmp_path/'plan',False)
    assert plan.branches()==(0,[]) and plan.source()['branches']==0
    out=tmp_path/'existing.sv'
    out.write_text('module keep; endmodule')
    with pytest.raises(ValueError,match='子系统输入端口'):
        plan.export({'without_mux':ALL,'with_mux':ALL},out)
    assert out.read_text()=='module keep; endmodule'
    assert g==original


def test_l1_subsystem_inputs_remain_independent_even_on_same_net(tmp_path):
    n=node('s','subsystem',inputs=('clk[2]','clk[3]'),outputs=())
    for b in n['port_connections'].values():b['full_name']='chip.same_clock'
    plan=prepare(graph([n]),None,tmp_path/'plan',False)
    assert plan.branches()[0]==2
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':ALL});assert value['ready'],value
        assert value['values']['var_clkip']=='chip.s.'+row['pin']
        assert value['values']['var_pll0']=='chip.same_clock'
        assert value['values']['var_div']=='0' and value['values']['var_swdone']=="1'b1"
    result=plan.export({'without_mux':ALL},tmp_path/'all.sv')
    assert result['blocks']==2 and result['skipped']==0


@pytest.mark.parametrize('indexed',[False,True])
def test_terminals_at_different_layers_and_same_layer_chain_count_once(tmp_path,indexed):
    nodes=[node('a','occ'),node('b','occ'),sink('c',3,('i',)),sink('d',5,('i',)),node('ordinary_tip','occ'),sink('alone',1,('i',))]
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
        assert value['branch']['origins']==[{'instance':'chip.'+origin,'kind':'subsystem' if origin=='alone' else 'occ','layer':1}]
    if indexed:store.close()


def test_terminal_mux_merges_input_paths_and_classifies_by_actual_first_device(tmp_path):
    first=node('first','occ');end=node('end','mux',2,('a','b'))
    g=graph([first,end,sink()],[('first','o','end','a'),('first','o','end','b'),('end','o','sink','clk_i')])
    end['aux_ports']={'pll_sel':'sel'}
    reg=registers(tmp_path,g,[(end['id'],'pll_sel','sel','chip.Q[4]')])
    plan=prepare(g,reg,tmp_path/'plan',False)
    assert plan.branches()[0]==1
    row=plan.branches()[1][0];value=plan.resolve(row,{'without_mux':'$var_pll0 $var_pll1 $var_clkip $var_pllsel'})
    assert value['ready'],value
    assert value['mode']=='without_mux' and value['values']['var_pll0']=='chip.first_i'
    assert value['values']['var_pll1']=="1'b1" and value['values']['var_clkip']=='chip.sink.clk_i'
    assert plan.candidates(row,'pll_sel')[0]['expression']=='chip.Q[4]'


def test_single_l1_real_controls_override_defaults_and_unfinished_never_becomes_constant(tmp_path):
    n=node('d','div');n['aux_ports']={'div':'ratio','sw_done':'done'};g=graph([n,sink()],[('d','o','sink','clk_i')])
    n['input_boundaries']={'i':{'layer':1,'status':'verified','signal':'chip.pad'}}
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
    right = sink('right',2,('i',))
    left = sink('left',2,('i',))
    g = graph([wire,right,left], [('w','y','right','i'),('w','x','left','i')])
    if problem == 'undriven':
        g['boundaries'] = [dict(status=problem,instance=wire['id'],pin='a',direction='driver')]
    else:
        g['diagnostics'] = [dict(code=problem,instance=wire['id'],pin='a')]
    plan = prepare(g,None,tmp_path/'plan',False)
    templates = {'without_mux':'initial $display($var_clkip);'}
    resolved = {r['instance']:plan.resolve(r,templates) for r in plan.branches()[1]}
    assert not resolved[right['id']]['ready'], resolved
    assert resolved[left['id']]['clkip']=='chip.left.i' and resolved[left['id']]['ready'], resolved
    report = plan.export(templates,tmp_path/'out.sv')
    assert report['blocks']==1 and report['skipped']==1


def test_l1_wire_feeds_separate_subsystem_inputs(tmp_path):
    n=node('w','wire',inputs=('a','b'),outputs=('x','y','z'))
    n['terminal_inputs']=['a','b']
    n['internal_connections']=[dict(input=a,output=b,status='verified') for a,b in [('a','y'),('a','z'),('b','x')]]
    target=sink(inputs=('x','y','z'))
    links=[('w',p,'sink',p) for p in target['inputs']]
    plan=prepare(graph([n,target],links),None,tmp_path/'plan',False)
    assert plan.branches()[0]==plan.source()['branches']==3
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':'$var_pll0 $var_clkip'});assert value['ready'],value
        pin='b' if row['pin']=='x' else 'a'
        assert value['values']['var_pll0']=='chip.w_'+pin
        assert value['values']['var_clkip']=='chip.sink.'+row['pin']


@pytest.mark.parametrize('kind', ['occ_root', 'mux', 'gate_wrap'])
def test_single_later_layer_device_uses_actual_origin_and_output(tmp_path, kind):
    start = node('start', kind, 3, ('i', 'j') if kind == 'mux' else ('i',))
    if kind == 'mux':
        start['l1_inputs'] = {pin: {'signal': 'chip.pad_'+pin, 'status': 'verified'} for pin in start['inputs']}
    end = sink(layer=4)
    plan = prepare(graph([start,end],[('start','o','sink','clk_i')]), None, tmp_path/'plan', False)
    mode = 'with_mux' if kind == 'mux' else 'without_mux'
    value = plan.resolve(plan.branches()[1][0], {mode: '$var_clkip $var_rst $var_pll0'})
    assert value['ready'], value
    assert value['branch']['origins'] == [{'instance':'chip.start', 'layer':3, 'kind':kind}]
    assert not value['branch']['single_device'] and value['pin'] == 'clk_i'
    assert value['values']['var_pll0'] == 'chip.start_i'
    assert value['values']['var_clkip'] == 'chip.sink.clk_i' and value['values']['var_rst'] == 'chip.rst3'


def test_later_layer_chain_orders_both_gate_types_by_connectivity(tmp_path):
    start = node('s','occ_root',2)
    first = node('a','sync_gate',3)
    second = node('b','gate_wrap',3)
    end = node('t','occ_branch',5)
    for gate in (first,second):gate['aux_ports'] = {'gt':'en'}
    g = graph([second,start,end,first,sink(layer=5)],[('s','o','a','i'),('a','o','b','i'),('b','o','t','i'),('t','o','sink','clk_i')])
    reg = registers(tmp_path,g,[(first['id'],'gt','en','chip.Q[7]'),(second['id'],'gt','en','chip.Q[5]')])
    plan = prepare(g,reg,tmp_path/'plan',False)
    assert plan.branches()[0] == 1
    result = plan.resolve(plan.branches()[1][0], {'without_mux':'$var_gt0 $var_gt1 $var_gt2 $var_pll0 $var_rst $var_clkip'})
    assert result['ready'], result
    assert result['values'] == {'var_gt0':'chip.Q[7]', 'var_gt1':'chip.Q[5]', 'var_gt2':"1'b1",
                                'var_pll0':'chip.s_i', 'var_clkip':'chip.sink.clk_i', 'var_rst':'chip.rst2'}


def test_wire_fanout_at_later_terminal_layer_counts_each_proven_output(tmp_path):
    source=node('source','occ');n=node('w','wire',2,('a',),('x','y'))
    n['terminal_inputs']=['a']
    n['internal_connections']=[dict(input='a',output=p,status='verified') for p in ('x','y')]
    end=sink(inputs=('x','y'))
    links=[('source','o','w','a')]+[('w',p,'sink',p) for p in end['inputs']]
    plan=prepare(graph([source,n,end],links),None,tmp_path/'plan',False)
    assert plan.branches()[0]==2
    for row in plan.branches()[1]:
        value=plan.resolve(row,{'without_mux':'$var_pll0 $var_clkip'})
        assert value['ready'] and value['values']['var_pll0']=='chip.source_i'
        assert value['values']['var_clkip']=='chip.sink.'+row['pin']


@pytest.mark.parametrize('indexed',[False,True])
def test_formal_names_through_concat_and_shared_external_signal(tmp_path,indexed):
    names=('clocks[3]','clocks[2]','strap_clk')
    ip=sink('gen_ip[1].u_gpu',4,names)
    ip['inputs'].append('clocks[2]')
    other=sink('u_dma',2,('clk_i',))
    ref=dict(other,id='chip.ref',reference_of=other['id'])
    extra=node('ordinary','gate_wrap')
    graph_=graph([ip,other,ref,extra])
    for pin in names:
        ip['port_connections'][pin]={'direction':'npiInput','status':'connected',
            'coverage':'whole_port','expression':'{crg_clk, crg_clk}'}
    other['port_connections']['clk_i']['full_name']='chip.crg_clk'
    if indexed:
        db=ResultStore(tmp_path/'arch.sqlite',True);index_graph(graph_,db);graph_=db.graph()
    try:
        plan=prepare(graph_,None,tmp_path/'plan',False)
        rows=plan.branches()[1]
        paths={ip['id']+'.'+pin for pin in names}|{'chip.u_dma.clk_i'}
        assert len(rows)==len(paths) and {r['clkip'] for r in rows}==paths
        sv=tmp_path/'all.sv'
        text='wire check_$clkip_sva = $var_clkip;\ninitial $display("$clkip=%b", ${clkip});'
        result=plan.export({'without_mux':text},sv)
        assert result['blocks']==4 and result['skipped']==0
        contents=sv.read_text()
        for path in paths:assert ' = '+path+';' in contents and '"'+path+'=%b", '+path in contents
        assert 'check_chip_gen_ip_1_u_gpu_clocks_3_sva' in contents and 'crg_clk' not in contents
    finally:
        if indexed:db.close()


def test_historical_untyped_instances_use_only_a_matching_saved_rule(tmp_path):
    ip=sink('u_gpu',1); other=sink('u_untyped',1); other['module']='unknown_ip'
    gate=node('g','gate')
    g=graph([ip,other,gate]);g['config']['layers'][0]['modules']=[
        dict(module=ip['module'],kind='subsystem',clock_inputs=['clk_i'])]
    for item in g['nodes']:item.pop('kind')
    plan=prepare(g,None,tmp_path/'plan',False)
    count,rows=plan.branches()
    assert count==1 and rows[0]['clkip']=='chip.u_gpu.clk_i'
    assert plan.resolve(rows[0],{'without_mux':'$clkip'})['ready']
    assert all('kind' not in item for item in g['nodes'])


def test_invalid_subsystem_formals_and_trace_are_isolated(tmp_path):
    end=sink(inputs=('clk_i','status_o','io','open_i','absent','late','skipped'))
    con=end['port_connections']
    con['status_o']['direction']='npiOutput';con['io']['direction']='npiInout'
    con['open_i']['status']='unconnected';con['absent'].update(status='unavailable',reason='port_not_found')
    end['port_trace_status']={'clk_i':'completed','late':'trace_timeout','skipped':'trace_skipped_timeout'}
    g=graph([end]);end['terminal']=False
    plan=prepare(g,None,tmp_path/'plan',False)
    result=plan.export({'without_mux':'wire w = $clkip;'},tmp_path/'g.sv')
    assert (result['blocks'],result['skipped'])==(1,6)
    assert (tmp_path/'g.sv').read_text().strip()=='wire w = chip.sink.clk_i;'
    records=json.loads(Path(result['mapping']).read_text(encoding='utf-8'))['branches']
    assert {r['pin'] for r in records if r['issues']}==set(end['inputs'])-{'clk_i'}


def test_legacy_plan_cannot_preview_or_overwrite_sv(tmp_path):
    from clocktree.sv_templates import SVPlan, database
    from contextlib import closing
    g=graph([sink()])
    plan=prepare(g,None,tmp_path/'plan',False)
    source=dict(plan.source())
    source.pop('endpoint_policy')
    source['architecture_signature']=architecture_signature(g,'sv-plan-v10')
    with closing(database(plan.path)) as db:
        db.execute("UPDATE meta SET value=? WHERE key='source'",(json.dumps(source),))
        db.commit()
    legacy=SVPlan(plan.path)
    out=tmp_path/'old.sv';out.write_text('module original; endmodule')
    for action in (legacy.branches,lambda:legacy.export({'without_mux':'$clkip'},out)):
        with pytest.raises(ValueError,match='重新运行'):action()
    assert out.read_text()=='module original; endmodule'
    assert not out.with_name('old.sv.mapping.json').exists()
    assert architecture_signature(g)!=source['architecture_signature']


def test_previous_single_l1_plan_signature_is_invalidated():
    g=graph([node('one')])
    old=identity('branch-origin-reset-d-input-v2',g.get('config'),g.get('metadata'),g.get('summary'))
    assert architecture_signature(g)!=old
