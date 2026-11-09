import copy
import json
import tkinter
import uuid
import pytest

from clocktree.auxiliary import aux_entries, aux_diagnostics
from clocktree.config import validate_config, write_tcl_config
from clocktree.devices import node_height, port_offsets, symbol_parts, auxiliary_details
from clocktree.graph import build_graph
from clocktree.storage import index_graph, new_store, write_graph
from clocktree.session import SessionStore, snapshot_graph, read_graph
from clocktree.viewmodel import GraphView
from test_workbench import fixture, assert_geometry


def config(kind='div', aux=None):
    rule={'module':'cell','kind':kind,'clock_inputs':['i'],'clock_output':'o'}
    if aux is not None:rule['aux_ports']=aux
    return {'design':{'top':'top','elab':'/test/kdb'},'layers':[{'index':1,'hierarchy':'top','modules':[rule]}]}


@pytest.mark.parametrize('kind,ports,expected',[
    ('mux',{'pll_sel':'mode_i'},(('pll_sel','mode_i','npiInput'),)),
    ('gate',{'gt':'enable_i'},(('gt','enable_i','npiInput'),)),
    ('div',{'div':'ratio_i','sw_done':'done_o'},(('div','ratio_i','npiInput'),('sw_done','done_o','npiOutput'))),
    ('div',{'div':''},()),
])
def test_explicit_auxiliary_names_have_separate_tcl_metadata(tmp_path,kind,ports,expected):
    cfg=validate_config(config(kind,ports));original=copy.deepcopy(cfg)
    path=tmp_path/'config.tcl';write_tcl_config(cfg,path,tmp_path/'raw.jsonl')
    tcl=tkinter.Tcl();tcl.eval(path.read_text())
    assert tcl.eval('lindex $ct_rules 0 3')=='i' and tcl.eval('lindex $ct_rules 0 4')=='o'
    observed=tcl.splitlist(tcl.eval('lindex $ct_rules 0 6'))
    assert tuple(tcl.splitlist(row) for row in observed)==expected
    assert cfg==original


@pytest.mark.parametrize('kind,ports',[
    ('gate',{'pll_sel':'s'}),('occ',{'gt':'en'}),('div',{'div':4}),('mux',['sel']),
    ('div',{'div':'i'}),('div',{'sw_done':'o'}),('div',{'div':'sel','sw_done':'sel'}),
    ('div',{'div':'x[1]','sw_done':'x[0]'}),
])
def test_invalid_auxiliary_mapping_is_rejected(kind,ports):
    with pytest.raises(ValueError):validate_config(config(kind,ports))


def node():
    return dict(id='top.div0',path='top.div0',module='cell',kind='div',layer=1,component=0,
                inputs=['i'],output='o',aux_ports={'div':'ratio_i','sw_done':'done_o'},
                port_connections={'ratio_i':{'status':'connected','expression':'config_ratio[7:4]','direction':'npiInput','source':'npiHighConn'},
                                  'done_o':{'status':'connected','expression':'switch_done[0]','direction':'npiOutput','source':'npiHighConn'}})


def test_auxiliary_labels_never_add_wire_anchors_or_clock_marks():
    extra=node();plain=copy.deepcopy(extra);plain.pop('aux_ports')
    small,big=node_height(plain),node_height(extra)
    assert big>small and port_offsets(extra,382,big)==port_offsets(plain,382,small)
    before=symbol_parts(plain,382,small);after=symbol_parts(extra,382,big)
    assert [p for p in before if p['op'] in ('circle','polyline','polygon')]==[p for p in after if p['op'] in ('circle','polyline','polygon')]
    text=[p['text'] for p in after if p['op']=='text']
    assert 'config_ratio[7:4]' in text and 'switch_done[0]' in text
    extra.update(trace_timeout={'pin':'i'},port_trace_status={'i':'trace_timeout'})
    assert 'trace 超时' not in auxiliary_details(extra) and '不追踪' in auxiliary_details(extra)


def test_auxiliary_metadata_survives_index_session_and_svg(tmp_path):
    graph=fixture();selected=graph['nodes'][1];selected.update(kind='div',aux_ports=node()['aux_ports'],port_connections=node()['port_connections'])
    graph['config']['layers'][selected['layer']-1]['modules']=[{'module':selected['module'],'kind':'div','clock_inputs':['i'],'clock_output':'o','aux_ports':node()['aux_ports']}]
    for layout in ('branch','layers'):
        view=GraphView(graph,layout_mode=layout).project();assert_geometry(view)
        assert {e['id'] for e in graph['edges']}=={e['id'] for e in view.edges}
        svg=view.svg();assert 'ratio_i' in svg and 'switch_done[0]' in svg
    indexed=index_graph(graph,new_store(tmp_path));path=tmp_path/'result.json';write_graph(path,indexed)
    restored=read_graph(path)
    assert next(n for n in restored['nodes'] if n['id']==selected['id'])['aux_ports']==selected['aux_ports']
    session=SessionStore(tmp_path/'runs',tmp_path/'session')
    session.save({'active':'clock','workspaces':{'clock':dict(config=graph['config'],graph=snapshot_graph(indexed),path=None,dirty=False,active_layer=1,graph_token=uuid.uuid4().hex)}})
    saved=session.load()['workspaces']['clock']['graph']
    actual=next(n for n in saved['nodes'] if n['id']==selected['id'])
    assert actual['port_connections']==selected['port_connections'] and actual['aux_ports']==selected['aux_ports']


def test_auxiliary_problems_are_diagnostics_without_trace_seeds_or_edges():
    extra=node();extra['port_connections']['ratio_i']={'status':'unavailable','reason':'port_not_found'}
    extra['port_connections']['done_o']['direction']='npiInput'
    events=[dict(extra,event='instance'),dict(event='trace',instance=extra['id'],pin='i',direction='driver',results=[{'status':'input_boundary'}]),
            dict(event='trace',instance=extra['id'],pin='o',direction='load',results=[{'status':'output_boundary'}]),dict(event='complete')]
    graph=build_graph(events,dict(config('div',extra['aux_ports']),extraction_mode='full'))
    assert len(graph['edges'])==0
    assert graph['summary']['expected_pin_traces']==graph['summary']['observed_pin_traces']==2
    assert graph['summary']['connectivity_complete']
    assert {d['code'] for d in graph['diagnostics']}>={'aux_port_missing','aux_port_direction'}
    extra['port_connections']['done_o']={'status':'unconnected','direction':'npiOutput'}
    assert any(d['code']=='aux_port_unconnected' for d in aux_diagnostics(extra))
