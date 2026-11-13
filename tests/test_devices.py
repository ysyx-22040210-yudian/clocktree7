import copy
import json
import tkinter
import xml.etree.ElementTree as ET

import pytest

from clocktree.config import validate_config, write_tcl_config
from clocktree.devices import symbol_parts, port_offsets, node_height, resolve_kind
from clocktree.graph import build_graph
from clocktree.viewmodel import GraphView


def config(kind='mux', inputs=None):
    rule={'module':'clock_cell','kind':kind,'clock_inputs':inputs or ['a','b'],'clock_output':'q'}
    return {'design':{'top':'top','elab':'/kdb'},'layers':[{'index':i,'hierarchy':'top','modules':[rule] if i==1 else []} for i in range(1,8)]}


def node(kind,inputs=None):
    return {'id':'top.u','path':'top.u','module':'clock_cell','layer':1,'component':0,
            'kind':kind,'inputs':inputs or (['a','b'] if kind=='mux' else ['a']),'output':'q'}


def test_single_input_contract():
    for kind in ['gate','sync_gate','gate_wrap','occ','occ_root','occ_branch','div']:
        validate_config(config(kind,['a']))
        with pytest.raises(ValueError,match='exactly one'):validate_config(config(kind,['a','b']))
    validate_config(config('mux',['a','b','c','d']))
    with pytest.raises(ValueError,match='kind must'):validate_config(config('buffer',['a']))
    legacy=config()
    del legacy['layers'][0]['modules'][0]['kind']
    validate_config(legacy)


def test_kind_survives_tcl_and_raw_event_graph_without_changing_connectivity(tmp_path):
    cfg=config('occ',['a'])
    path=tmp_path/'rules.tcl'
    write_tcl_config(cfg,path,'/raw.jsonl')
    tcl=tkinter.Tcl()
    tcl.eval(path.read_text())
    assert tcl.eval('lindex $ct_rules 0 5')=='occ'
    events=[dict(event='instance',**{k:v for k,v in node('occ').items() if k not in ('id','component')}),
            {'event':'trace','instance':'top.u','pin':'a','direction':'driver','results':[{'status':'input_boundary','endpoint':'top.clk'}]},
            {'event':'trace','instance':'top.u','pin':'q','direction':'load','results':[{'status':'output_boundary'}]},
            {'event':'complete'}]
    typed=build_graph(events,cfg)
    untyped_cfg=copy.deepcopy(cfg)
    del untyped_cfg['layers'][0]['modules'][0]['kind']
    untyped=copy.deepcopy(events)
    untyped[0].pop('kind')
    plain=build_graph(untyped,untyped_cfg)
    assert typed['nodes'][0]['kind']=='occ'
    assert typed['summary']==plain['summary'] and typed['edges']==plain['edges']


def test_symbols_have_distinct_geometry_and_real_wire_pin_anchors():
    sides={}
    for kind in ['mux','gate','occ','div']:
        n=node(kind)
        h=node_height(n)
        primitives=symbol_parts(n,242,h)
        polygon=next(p for p in primitives if p['op']=='polygon')
        sides[kind]=len(polygon['points'])
        inputs,output=port_offsets(n,242,h)
        wirepoints=[pt for p in primitives if p['op']=='polyline' for pt in p['points']]
        assert all(anchor in wirepoints for anchor in inputs+[output])
        graph={'nodes':[n],'edges':[]}
        view=GraphView(graph).project()
        xml=ET.fromstring(view.svg())
        assert xml.find('.//{http://www.w3.org/2000/svg}g').get('data-device-kind')==kind
    assert sides['mux']==4 and sides['occ']==8 and sides['gate']>20 and sides['div']==6


def test_mux_many_inputs_route_to_exact_same_anchors_as_exported_symbol():
    mux=node('mux',['i0','i1','i2','i3','i4','i5'])
    gate=dict(node('gate'),id='top.gate',path='top.gate',layer=2)
    edge={'id':'e','source':mux['id'],'source_pin':'q','target':gate['id'],'target_pin':'a','layer_delta':1}
    view=GraphView({'nodes':[mux,gate],'edges':[edge]}).project()
    sx,sy,sw,sh=view.positions[mux['id']]
    tx,ty,tw,th=view.positions[gate['id']]
    ins,out=port_offsets(mux,sw,sh)
    assert len(set(ins))==6
    target_ins,_=port_offsets(gate,tw,th)
    points=view.edge_points(edge)
    assert points[0]==(sx+out[0],sy+out[1])
    assert points[-1]==(tx+target_ins[0][0],ty+target_ins[0][1])


def test_old_graph_never_guesses_type_from_module_name_and_uses_explicit_scope():
    n=node(None)
    n['module']='some_clock_mux_occ_gate'
    assert resolve_kind(n,{}) is None
    view=GraphView({'nodes':[n],'edges':[]}).project()
    assert not view.nodes['top.u'].get('kind')
    cfg=config('mux')
    n['module']='clock_cell'
    n['inputs']=['a','b']
    n.pop('kind')
    assert GraphView({'nodes':[n],'edges':[],'config':cfg}).original['top.u']['kind']=='mux'
    cfg['layers'][0]['hierarchy']='top.other'
    assert GraphView({'nodes':[n],'edges':[],'config':cfg}).original['top.u'].get('kind') is None
