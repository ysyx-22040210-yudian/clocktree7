import copy
import json
import pytest
from clocktree.register_trace import create_database
from clocktree.storage import write_graph
from clocktree.sv_templates import prepare,render,vector_expression


def test_worker_uses_index_without_copy_and_preserves_header(tmp_path):
    from clocktree.storage import ResultStore,IndexedGraph
    from clocktree.sv_templates import write_source,read_source
    store=ResultStore(tmp_path/'source.sqlite3',True)
    store.finish({'config':{'design':{'top':'original'}},'summary':{},'array_fields':['nodes','edges']})
    graph=store.graph();graph['config']={'design':{'top':'current'}}
    graph['presentation']={'kind_overrides':[]}
    descriptor=tmp_path/'input.json';write_source(descriptor,graph)
    restored=read_source(descriptor)
    assert isinstance(restored,IndexedGraph) and restored.store.path==store.path
    assert restored['config']==graph['config'] and restored['presentation']==graph['presentation']
    assert len(restored['nodes'])==0 and descriptor.stat().st_size<4096
    restored.store.close();store.close()


def setup(tmp_path,multiple=False):
    config={'design':{'top':'chip','filelist':'/design.f'},'tree_type':'clock'}
    def node(name,kind,inputs,aux,terminal=False):
        return dict(id='chip.'+name,kind=kind,inputs=inputs,aux_ports=aux,terminal=terminal,
                    port_connections={pin:{'status':'connected','full_name':'chip.'+pin,'coverage':'port'} for pin in inputs})
    nodes=[node('mux','mux',['a','b'],{'pll_sel':'sel'}),node('div','div',['i'],{'div':'ratio','sw_done':'done'}),
           node('gate','gate',['i'],{'gt':'en'}),node('sink','subsystem',['clk_mux','clk_other'],{},True)]
    edges=[dict(source='chip.mux',target='chip.div',target_pin='i'),dict(source='chip.div',target='chip.sink',target_pin='clk_mux'),
           dict(source='chip.gate',target='chip.sink',target_pin='clk_other')]
    graph={'tree_type':'clock','config':config,'nodes':nodes,'edges':edges,'summary':{'connectivity_complete':True}}
    reg=tmp_path/'reg';reg.mkdir();write_graph(reg/'manifest.json',{'request':{'config':config,'dft_zero':['dft_mode']}})
    db=create_database(reg/'registers.sqlite')
    for job,(instance,role,pin,targets) in enumerate([('mux','pll_sel','sel',['chip.D[2]','chip.D[3]','chip.D[4]','chip.D[5]']),
            ('div','div','ratio',['chip.C[0]','chip.C[1]']),('div','sw_done','done',['chip.E[10]']),('gate','gt','en',['chip.C[7]'])],1):
        bits=[pin+'[{}]'.format(i) for i in range(len(targets))] if len(targets)>1 else [pin]
        seed={'instance':'chip.'+instance,'role':role,'pin':pin}
        db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(job,json.dumps(seed),'complete',json.dumps({'bits':['chip.'+instance+'.'+b for b in bits]})))
        for bit,target in zip(bits,targets):db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(job,bit,target,'D' if role=='sw_done' else 'Q','direct','register','',None,'{}'))
        if multiple and role=='sw_done':db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(job,'done','chip.E[11]','D','direct','register','',None,'{}'))
    db.commit();db.close();return graph,reg


def test_exact_template_names_only_and_no_recursive_or_python_execution():
    template='initial $display("%b", $var_gt); // $var_gt_suffix $$var_gt'
    with pytest.raises(ValueError,match='未知'):render(template,{'var_gt':'chip.C[7]'})
    assert render('initial $display("%b", $var_gt); // $$var_gt',{'var_gt':'chip.C[7]'})=='initial $display("%b", chip.C[7]); // $$var_gt'
    assert render('$var_gt',{'var_gt':'$var_clkip'})=='$var_clkip'


def test_shared_folder_status_contention_does_not_discard_branches(tmp_path,monkeypatch):
    import clocktree.sv_templates as module
    graph,reg=setup(tmp_path);actual=module.write_graph;failures=[1]
    def contended(path,value):
        if path.name=='status.json' and failures[0]:
            failures[0]-=1;raise PermissionError('Windows reader holds shared status')
        return actual(path,value)
    monkeypatch.setattr(module,'write_graph',contended)
    plan=prepare(graph,reg,tmp_path/'plan',False)
    assert plan.branches(limit=0)[0]==2 and failures[0]==0


def test_bus_order_noncontiguous_and_repeated_mapping():
    assert vector_expression(['top.D[2]','top.D[3]','top.D[4]','top.D[5]'])=='top.D[5:2]'
    assert vector_expression(['top.up[5]','top.up[4]','top.up[3]'])=='top.up[3:5]'
    assert vector_expression(['top.C[0]','top.D[8]'])=='{top.D[8], top.C[0]}'
    assert vector_expression(['top.C[7]','top.C[7]'])=='{top.C[7], top.C[7]}'


def test_subsystem_inputs_have_separate_upstreams_and_exactly_one_block_each(tmp_path):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    total,rows=plan.branches();assert total==2
    templates={'with_mux':'A($var_pllsel,$var_div,$var_swdone,$var_clkip);','without_mux':'B($var_gt,$var_clkip);'}
    for row in rows:
        r=plan.resolve(row,templates);assert r['ready'],r['issues']
        if row['pin']=='clk_mux':assert r['mode']=='with_mux' and r['values']['var_pllsel']=='chip.D[5:2]' and 'var_gt' not in r['values']
        else:assert r['mode']=='without_mux' and r['values']['var_gt']=='chip.C[7]' and not plan.candidates(row,'pll_sel')
    result=plan.export(templates,tmp_path/'all.sv');text=(tmp_path/'all.sv').read_text()
    assert result['blocks']==2 and text.count('// CT7 branch ')==2 and text.count('A(')==1 and text.count('B(')==1
    report=json.loads((tmp_path/'all.sv.mapping.json').read_text());assert report['block_count']==2 and report['source']['dft_zero']==['dft_mode']


def test_ambiguity_requires_choice_and_never_multiplies_branch_count(tmp_path):
    graph,reg=setup(tmp_path,True);plan=prepare(graph,reg,tmp_path/'plan',False)
    row=next(r for r in plan.branches()[1] if r['pin']=='clk_mux')
    templates={'with_mux':'A($var_swdone);','without_mux':'B($var_gt);'}
    assert not plan.resolve(row,templates)['ready']
    choices=plan.candidates(row,'sw_done');assert len(choices)==2
    selected={row['id']:{'var_swdone':choices[1]['id']}}
    result=plan.export(templates,tmp_path/'all.sv',selected);assert result['blocks']==2
    assert choices[1]['expression'] in (tmp_path/'all.sv').read_text()


def test_no_ready_branch_keeps_old_file_and_writes_feedback(tmp_path):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    target=tmp_path/'existing.sv';target.write_text('prior code')
    result=plan.export({'with_mux':'$var_bad','without_mux':'$var_bad'},target)
    assert result['blocks']==0 and result['skipped']==2 and not result['sv_written']
    assert target.read_text()=='prior code' and not list(tmp_path.glob('*.tmp*'))


def test_partial_output_lists_all_candidates_then_regenerates_without_duplicates(tmp_path):
    graph,reg=setup(tmp_path,True);plan=prepare(graph,reg,tmp_path/'plan',False)
    templates={'with_mux':'A($var_swdone);','without_mux':'B($var_gt0);'}
    output=tmp_path/'all.sv';result=plan.export(templates,output)
    assert result['blocks']==1 and result['skipped']==1 and 'B(chip.C[7])' in output.read_text()
    report=json.loads((tmp_path/'all.sv.mapping.json').read_text())
    skipped=next(r for r in report['branches'] if not r['ready'])
    assert {r['expression'] for r in skipped['available_choices']['var_swdone']}=={'chip.E[10]','chip.E[11]'}
    chosen=skipped['available_choices']['var_swdone'][0]['id']
    result=plan.export(templates,output,{skipped['id']:{'var_swdone':chosen}})
    assert result['blocks']==2 and result['skipped']==0 and output.read_text().count('// CT7 branch ')==2


def test_pll_input_order_and_missing_plls_and_gates_are_one(tmp_path):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    text='$var_pll0 $var_pll1 $var_pll2 $var_pll3 $var_gt0 $var_gt1 $var_gt2'
    for row in plan.branches()[1]:
        result=plan.resolve(row,dict(with_mux=text,without_mux=text));assert result['ready']
        if row['pin']=='clk_mux':assert result['code']=="chip.a chip.b 1'b1 1'b1 1'b1 1'b1 1'b1"
        else:assert result['code']=="chip.i 1'b1 1'b1 1'b1 chip.C[7] 1'b1 1'b1"


def test_vector_across_multiple_registers_needs_confirmation_without_losing_bits(tmp_path):
    graph,reg=setup(tmp_path);db=create_database(reg/'registers.sqlite')
    db.execute("UPDATE results SET endpoint='chip.D[8]' WHERE job=2 AND bit='ratio[1]'");db.commit();db.close()
    plan=prepare(graph,reg,tmp_path/'plan',False);row=next(r for r in plan.branches()[1] if r['pin']=='clk_mux')
    assert not plan.resolve(row,{'with_mux':'$var_div'})['ready']
    choices=plan.candidates(row,'var_div');assert len(choices)==1 and choices[0]['expression']=='{chip.D[8], chip.C[0]}'
    result=plan.resolve(row,{'with_mux':'$var_div'},{'var_div':choices[0]['id']})
    assert result['ready'] and result['code']=='{chip.D[8], chip.C[0]}'


def test_incomplete_extra_register_candidate_is_reported_and_needs_choice(tmp_path):
    graph,reg=setup(tmp_path);db=create_database(reg/'registers.sqlite')
    db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(2,'ratio[0]','chip.X[8]','Q','direct','register','',None,'{}'));db.commit();db.close()
    plan=prepare(graph,reg,tmp_path/'plan',False);row=next(r for r in plan.branches()[1] if r['pin']=='clk_mux')
    result=plan.resolve(row,{'with_mux':'$var_div'})
    assert not result['ready'] and 'chip.X[8]' in ' '.join(result['issues'])
    choices=plan.candidates(row,'var_div');assert len(choices)==1 and choices[0]['expression']=='chip.C[1:0]'
    assert plan.resolve(row,{'with_mux':'$var_div'},{'var_div':choices[0]['id']})['ready']


def test_all_register_candidates_beyond_first_page_are_in_feedback(tmp_path):
    graph,reg=setup(tmp_path);db=create_database(reg/'registers.sqlite')
    for i in range(205):db.execute('INSERT OR IGNORE INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(3,'done','chip.E[{}]'.format(i),'D','direct','register','',None,'{}'))
    db.commit();db.close();plan=prepare(graph,reg,tmp_path/'plan',False)
    result=plan.export({'with_mux':'$var_swdone','without_mux':'$var_clkip'},tmp_path/'all.sv');assert result['skipped']==1
    report=json.loads((tmp_path/'all.sv.mapping.json').read_text());row=next(r for r in report['branches'] if r['issues'])
    assert len(row['available_choices']['var_swdone'])==205
    assert {c['expression'] for c in row['available_choices']['var_swdone']}=={'chip.E[{}]'.format(i) for i in range(205)}


def test_unreferenced_absent_variable_does_not_block_and_system_tasks_stay(tmp_path):
    graph,reg=setup(tmp_path);plan=prepare(graph,reg,tmp_path/'plan',False)
    templates=dict(with_mux='initial $display("$var_clkip");',without_mux='initial $display("$var_clkip");')
    assert plan.export(templates,tmp_path/'out.sv')['blocks']==2


def test_same_net_on_distinct_real_endpoints_is_not_deduplicated(tmp_path):
    graph,reg=setup(tmp_path)
    for value in graph['nodes'][-1]['port_connections'].values():value['full_name']='chip.shared_clk'
    plan=prepare(graph,reg,tmp_path/'plan',False)
    assert plan.export(dict(with_mux='$var_clkip',without_mux='$var_clkip'),tmp_path/'out.sv')['blocks']==2


def test_wrong_design_and_stale_pin_configuration_rejected(tmp_path):
    graph,reg=setup(tmp_path);bad=copy.deepcopy(graph);bad['config']['design']['top']='other'
    with pytest.raises(ValueError,match='不一致'):prepare(bad,reg,tmp_path/'bad',False)
    graph['nodes'][0]['aux_ports']['pll_sel']='different'
    plan=prepare(graph,reg,tmp_path/'plan',False);row=next(r for r in plan.branches()[1] if r['pin']=='clk_mux')
    assert any('辅助配置不一致' in s for s in plan.resolve(row,{'with_mux':'$var_pllsel'})['issues'])


def test_incomplete_tree_and_unknown_clock_connection_block_export(tmp_path):
    graph,reg=setup(tmp_path);graph['summary']['connectivity_complete']=False
    graph['nodes'][-1]['port_connections']['clk_mux']['coverage']='whole_port'
    plan=prepare(graph,reg,tmp_path/'plan',False)
    for row in plan.branches()[1]:assert not plan.resolve(row,dict(with_mux='$var_clkip',without_mux='$var_clkip'))['ready']


@pytest.mark.parametrize('count',[0,1,2,3,4])
def test_gate_order_follows_edges_and_missing_slots_only_are_one(tmp_path,count):
    graph,reg=setup(tmp_path)
    graph['edges']=[e for e in graph['edges'] if e['target_pin']!='clk_other']
    previous=None
    db=create_database(reg/'registers.sqlite')
    for i in range(count):
        name='chip.'+['gate','a_later','b_last','extra'][i]
        if i:
            node=copy.deepcopy(graph['nodes'][2]);node['id']=name;graph['nodes'].append(node)
            seed={'instance':name,'role':'gt','pin':'en'};job=4+i
            db.execute('INSERT INTO jobs VALUES(?,?,?,?)',(job,json.dumps(seed),'complete',json.dumps({'bits':[name+'.en']})))
            db.execute('INSERT INTO results VALUES(NULL,?,?,?,?,?,?,?,?,?)',(job,'en','chip.C[{}]'.format(7-i),'Q','direct','register','',None,'{}'))
        if previous:graph['edges'].append(dict(source=previous,target=name,target_pin='i'))
        previous=name
    if previous:graph['edges'].append(dict(source=previous,target='chip.sink',target_pin='clk_other'))
    db.commit();db.close();plan=prepare(graph,reg,tmp_path/'plan',False)
    row=next(r for r in plan.branches()[1] if r['pin']=='clk_other')
    result=plan.resolve(row,{'without_mux':'$var_gt0 $var_gt1 $var_gt2'})
    if count>3:assert not result['ready'] and any('超过' in p for p in result['issues'])
    else:
        assert result['ready'],result['issues']
        assert result['code']==' '.join('chip.C[{}]'.format(7-i) if i<count else "1'b1" for i in range(3))
