import copy
import importlib
import json
import tkinter
import xml.etree.ElementTree as ET

import pytest

from clocktree.config import validate_config, write_tcl_config
from clocktree.devices import kind_label, type_labels, symbol_parts, port_offsets, node_height
from clocktree.graph import build_graph
from clocktree.profiles import empty_config, profile, result_filename
from clocktree.viewmodel import GraphView


def config(kind='rsy_sync', inputs=None):
    cfg=empty_config('reset')
    cfg['design']={'top':'top','elab':'/kdb'}
    cfg['layers'][0].update(hierarchy='top',modules=[
        {'module':'reset_cell','kind':kind,'reset_inputs':inputs or ['rst_ni'],'reset_output':'rst_no'}])
    return cfg


def events():
    return [dict(event='instance',path='top.sync',layer=1,module='reset_cell',kind='rsy_sync',inputs=['rst_ni'],output='rst_no'),
            dict(event='trace',instance='top.sync',pin='rst_ni',direction='driver',results=[dict(status='input_boundary',endpoint='top.rst_ni')]),
            dict(event='trace',instance='top.sync',pin='rst_no',direction='load',results=[dict(status='output_boundary')]),
            dict(event='complete')]


def test_reset_defaults_profiles_and_port_schema():
    assert len(empty_config('reset')['layers'])==2
    assert type_labels(config())==['MUX','RSY_SYNC','子系统 SUBSYSTEM']
    assert type_labels(empty_config('clock'))==['选择器','门控','OCC','DIV 分频器','子系统 SUBSYSTEM']
    assert result_filename(config())=='resettree.json'
    validate_config(config())
    validate_config(config('mux',['rst_a','rst_b','rst_c']))
    with pytest.raises(ValueError,match='exactly one'):validate_config(config(inputs=['rst_a','rst_b']))


@pytest.mark.parametrize('kind',['gate','occ','div',None,'RSY_SYNC'])
def test_reset_rejects_clock_only_and_unclassified_types(kind):
    with pytest.raises(ValueError,match='kind must be'):validate_config(config(kind))


def test_modes_reject_mixed_port_fields_and_foreign_types():
    mixed=config()
    mixed['layers'][0]['modules'][0]['clock_inputs']=['clk_i']
    with pytest.raises(ValueError,match='Use reset_inputs'):validate_config(mixed)
    clock=config()
    clock.pop('tree_type')
    rule=clock['layers'][0]['modules'][0]
    rule['clock_inputs']=rule.pop('reset_inputs')
    rule['clock_output']=rule.pop('reset_output')
    with pytest.raises(ValueError,match='kind must be'):validate_config(clock)
    clock['tree_type']='unknown'
    with pytest.raises(ValueError,match='tree_type'):validate_config(clock)


def test_reset_tcl_contains_only_declared_reset_ports_and_mode(tmp_path):
    path=tmp_path/'rules.tcl'
    write_tcl_config(validate_config(config()),path,'/raw.jsonl')
    interpreter=tkinter.Tcl()
    interpreter.eval(path.read_text())
    assert interpreter.eval('set ct_tree_type')=='reset'
    assert interpreter.eval('lindex $ct_rules 0 3')=='rst_ni'
    assert interpreter.eval('lindex $ct_rules 0 4')=='rst_no'
    assert interpreter.eval('lindex $ct_rules 0 5')=='rsy_sync'
    assert 'clk_i' not in path.read_text()


def test_reset_graph_source_identity_type_and_svg():
    graph=build_graph(events(),config())
    assert graph['tree_type']==graph['summary']['tree_type']=='reset'
    assert graph['reset_sources'][0]['id']=='top.rst_ni'
    assert graph['summary']['reset_sources']==1 and 'clock_sources' not in graph
    assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid']
    view=GraphView(graph).project()
    node=view.nodes['top.sync']
    assert node['kind']=='rsy_sync'
    primitives=symbol_parts(node,242,node_height(node))
    pins,out=port_offsets(node,242,node_height(node))
    wires=[p for part in primitives if part['op']=='polyline' for p in part['points']]
    assert all(anchor in wires for anchor in pins+[out])
    assert len([p for p in primitives if p['op']=='circle' and p['r']==3])==2
    assert any(p.get('text')=='RSY_SYNC' for p in primitives)
    svg=ET.fromstring(view.svg())
    assert svg.get('data-tree-type')=='reset'
    assert svg.find('.//{http://www.w3.org/2000/svg}g').get('data-device-kind')=='rsy_sync'


def test_reset_aggregation_and_high_layers_keep_every_instance():
    cfg=config()
    cfg['layers']=[dict(index=i,hierarchy='top',modules=[]) for i in range(1,11)]
    nodes=[dict(id='top.u'+str(i),path='top.u'+str(i),module='reset_sync',kind='rsy_sync',
                layer=10,component=0,inputs=['rst_ni'],output='rst_no') for i in range(8)]
    view=GraphView({'tree_type':'reset','config':cfg,'nodes':nodes,'edges':[]}).project(mode='hierarchy',depth=1)
    group=next(iter(view.nodes.values()))
    assert group['kind_counts']=={'rsy_sync':8}
    assert len(group['members'])==8 and view.layer_count==10
    assert 'RSY_SYNC ×8' in view.svg()


def test_clock_and_reset_reuse_directory_archive_the_correct_result(tmp_path, monkeypatch):
    module=importlib.import_module('clocktree.extract')
    import sys
    monkeypatch.setattr(module,'resolve_verdi',lambda *args:(sys.executable,'/verdi'))
    out=tmp_path/'run'
    current=[events()]
    def runner(command,work,environment,raw,log,output,config):
        raw.write_text('\n'.join(json.dumps(e) for e in current[0]))
        return 0,work,{'state':'finished'}
    monkeypatch.setattr(module,'run_verdi',runner)
    reset=config()
    path=tmp_path/'config.json'
    path.write_text(json.dumps(reset))
    module.extract(path,out)
    assert (out/'resettree.json').is_file() and not (out/'clocktree.json').exists()
    clock=copy.deepcopy(reset)
    clock['tree_type']='clock'
    rule=clock['layers'][0]['modules'][0]
    rule['kind']='gate'
    rule['clock_inputs']=rule.pop('reset_inputs')
    rule['clock_output']=rule.pop('reset_output')
    current[0][0]['kind']='gate'
    path.write_text(json.dumps(clock))
    module.extract(path,out)
    assert (out/'clocktree.json').is_file() and not (out/'resettree.json').exists()
    saved=list((out/'.clocktree-history').glob('*/resettree.json'))
    assert len(saved)==1 and json.loads(saved[0].read_text())['tree_type']=='reset'
