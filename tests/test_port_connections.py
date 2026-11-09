import copy
import json
from pathlib import Path
import tkinter
import uuid
import xml.etree.ElementTree as ET

from clocktree.devices import connection_label, connection_details, node_height, symbol_parts
from clocktree.graph import build_graph
from clocktree.session import SessionStore, snapshot_graph, read_graph
from clocktree.storage import index_graph, new_store, write_graph
from clocktree.viewmodel import GraphView

ROOT = Path(__file__).resolve().parents[1]


def interpreter():
    tcl = tkinter.Tcl()
    tcl.eval((ROOT/'npi/extract.tcl').read_text(encoding='utf-8').split('if {[catch {ct_main}')[0])
    tcl.eval('''
        namespace eval ::npi_L1 {}
        set released {}; set decompiled {}; set ports {i o spare}; set fail 0
        proc npi_iterate {args} {global ports; set ::pending $ports; return iterator}
        proc npi_scan {args} {set p [lindex $::pending 0];set ::pending [lrange $::pending 1 end];return $p}
        proc npi_get_str {args} {
            set property [lindex $args 1];set obj [lindex $args 3]
            if {$property eq "npiName"} {return $obj}
            if {$property eq "npiFullName"} {return "top.alias_clk"}
            error "unexpected property $property"
        }
        proc npi_handle {args} {
            set port [lindex $args 3]
            if {$port eq "o"} {return ""}
            return "connection:$port"
        }
        proc ::npi_L1::npi_expr_decompile {hdl} {
            lappend ::decompiled $hdl
            if {$::fail} {error "unsupported expression"}
            return {alias_clk}
        }
        proc npi_release_handle {args} {lappend ::released [lindex $args 1]}
    ''')
    return tcl


def test_high_connection_is_not_a_trace_name_and_unrelated_handles_are_released():
    tcl = interpreter()
    result = json.loads(tcl.call('ct_port_connections', 'top.u', ('i', 'o', 'missing')))
    assert result['i']['expression'] == 'alias_clk'
    assert result['i']['full_name'] == 'top.alias_clk'
    assert result['i']['source'] == 'npiHighConn'
    assert result['o']['status'] == 'unconnected'
    assert result['missing']['status'] == 'unavailable'
    assert tcl.splitlist(tcl.getvar('decompiled')) == ('connection:i',)
    assert set(tcl.splitlist(tcl.getvar('released'))) == {'i', 'o', 'spare', 'connection:i'}


def test_connection_error_releases_handles_and_does_not_invent_a_net():
    tcl = interpreter(); tcl.setvar('fail', 1)
    result = json.loads(tcl.call('ct_port_connections', 'top.u', ('i',)))
    assert result['i']['status'] == 'unavailable' and result['i']['reason'] == 'unsupported expression'
    assert 'expression' not in result['i']
    assert 'connection:i' in tcl.splitlist(tcl.getvar('released'))


def test_selected_vector_bit_explicitly_reports_the_whole_formal_connection():
    tcl = interpreter()
    result = json.loads(tcl.call('ct_port_connections', 'top.u', ('i[3]',)))
    item = result['i[3]']
    assert item['coverage'] == 'whole_port' and item['formal_port'] == 'i'
    node = {'port_connections': result}
    assert connection_label(node, 'i[3]') == '整口: alias_clk'
    assert '未推断逐位映射' in connection_details(node, ['i[3]'])


def test_annotation_reads_actual_formal_direction_and_whole_bus_binding():
    tcl=interpreter()
    tcl.eval('''rename npi_get_str original_get_str
        proc npi_get_str {args} {
            if {[lindex $args 1] eq "npiDirection"} {
                return [expr {[lindex $args 3] eq "o" ? "npiOutput" : "npiInput"}]
            }
            return [original_get_str {*}$args]
        }
    ''')
    data=json.loads(tcl.call('ct_port_connections','top.u',('i','o'),('i','npiInput','o','npiOutput')))
    assert data['i']['direction']=='npiInput' and data['i']['expression']=='alias_clk'
    assert data['o']['direction']=='npiOutput' and data['o']['status']=='unconnected'
    assert set(tcl.splitlist(tcl.getvar('decompiled')))=={'connection:i'}


def test_expression_is_json_data_even_when_it_contains_tcl_or_xml_characters():
    tcl = interpreter()
    expression = '(bus[2] & enable) | \\escaped"name ; [error injected]'
    tcl.setvar('expression', expression)
    tcl.eval('proc ::npi_L1::npi_expr_decompile {hdl} {return $::expression}')
    result = json.loads(tcl.call('ct_port_connections', 'top.u', ('i',)))
    assert result['i']['expression'] == expression


def sample():
    return json.loads((ROOT/'examples/flexible_layers/clocktree.json').read_text(encoding='utf-8'))


def test_legacy_results_never_fabricate_connections_from_driver_evidence():
    data = sample()
    for node in data['nodes']:node.pop('port_connections', None)
    original = copy.deepcopy(data)
    view = GraphView(data).project()
    for node in view.nodes.values():
        assert connection_label(node, node['output']) == '未采集'
    assert data == original


def test_graph_index_json_and_session_keep_each_instances_own_connections(tmp_path):
    records = []
    for index, signal in enumerate(['clock_a', 'clock_b']):
        records.append(dict(event='instance', path='top.u'+str(index), module='same_gate', kind='gate', layer=1,
            inputs=['i'], output='o', port_connections={
                'i': {'status': 'connected', 'expression': signal, 'source': 'npiHighConn'},
                'o': {'status': 'unconnected', 'expression': '', 'source': 'npiHighConn'}}))
    config = {'design': {'top':'top', 'elab':'/unused/kdb'}, 'layers': [
        {'index':1,'hierarchy':'top','modules':[{'module':'same_gate','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]}]}
    graph = build_graph(records + [{'event':'complete'}], config)
    indexed = index_graph(graph, new_store(tmp_path))
    path = tmp_path/'result.json';write_graph(path,indexed)
    restored = read_graph(path)
    assert [n['port_connections'] for n in restored['nodes']] == [r['port_connections'] for r in records]
    frozen = snapshot_graph(indexed)
    assert [n['port_connections'] for n in frozen['nodes']] == [r['port_connections'] for r in records]
    session = SessionStore(tmp_path/'runs',tmp_path/'session')
    session.save({'active':'clock','workspaces':{'clock':dict(config=config,graph=frozen,path=None,
        dirty=False,active_layer=1,graph_token=uuid.uuid4().hex)}})
    history = session.load()['workspaces']['clock']['graph']
    assert [n['port_connections'] for n in history['nodes']] == [r['port_connections'] for r in records]


def test_svg_port_labels_are_separate_from_pins_and_keep_full_escaped_text():
    node = dict(id='top.g.u',path='top.g.u',module='mux',kind='mux',layer=1,component=0,inputs=['a','b'],output='o')
    long_name = '(very_long_clock_signal_from_parent[3] & enable_signal)'
    node['port_connections'] = {
        'a': {'status':'connected','expression':long_name,'source':'npiHighConn'},
        'b': {'status':'connected','expression':"1'b0",'source':'npiHighConn'},
        'o': {'status':'unconnected','expression':'','source':'npiHighConn'}}
    view = GraphView({'nodes':[node],'edges':[]}).project()
    parts = symbol_parts(node,view.positions[node['id']][2],node_height(node))
    values = [p['text'] for p in parts if p['op']=='text']
    assert all(text in values for text in ('a','b','o',long_name,"1'b0",'未连接'))
    xml = ET.fromstring(view.svg())
    titles = [item.text for item in xml.findall('.//{http://www.w3.org/2000/svg}text/{http://www.w3.org/2000/svg}title')]
    assert any(long_name in title for title in titles)
    assert xml.find('./{http://www.w3.org/2000/svg}g').get('data-instance') == node['id']
