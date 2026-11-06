import copy
import tkinter
import xml.etree.ElementTree as ET

import pytest

from clocktree.config import validate_config, write_tcl_config
from clocktree.viewmodel import GraphView


def config(count):
    return {'design': {'top': 'top', 'elab': '/kdb'}, 'layers': [
        {'index': i, 'hierarchy': 'top', 'modules': [
            {'module': 'divider', 'kind': 'div', 'clock_inputs': ['i'], 'clock_output': 'o'}] if i == count else []}
        for i in range(1, count + 1)]}


@pytest.mark.parametrize('count', [1, 2, 7, 10, 64])
def test_any_positive_consecutive_layer_count(count, tmp_path):
    cfg = validate_config(config(count))
    output = tmp_path / 'rules.tcl'
    write_tcl_config(cfg, output, '/raw.jsonl')
    tcl = tkinter.Tcl()
    tcl.eval(output.read_text())
    assert int(tcl.eval('lindex $ct_rules 0 0')) == count
    assert tcl.eval('lindex $ct_rules 0 5') == 'div'
    assert tcl.eval('set ct_max_steps') == '0'


@pytest.mark.parametrize('layers', [[], {}, None, [{'index': 2}], [{'index': 1}, {'index': 3}],
                                     [{'index': True}], ['invalid']])
def test_invalid_layer_sequence_rejected(layers):
    cfg = config(2)
    cfg['layers'] = layers
    with pytest.raises(ValueError, match='ordered layers'):
        validate_config(cfg)


def test_positions_and_svg_include_layers_beyond_seven_and_empty_columns():
    cfg = config(12)
    nodes = [{'id': 'top.u{}'.format(i), 'path': 'top.u{}'.format(i), 'layer': i, 'component': 0,
              'module': 'divider', 'kind': 'div', 'inputs': ['i'], 'output': 'o'} for i in (1, 8, 10)]
    edges = [{'id': str(i), 'source': a['id'], 'source_pin': 'o', 'target': b['id'], 'target_pin': 'i',
              'layer_delta': b['layer'] - a['layer']} for i, (a, b) in enumerate(zip(nodes, nodes[1:]))]
    graph = {'nodes': nodes, 'edges': edges, 'config': cfg}
    saved = copy.deepcopy(graph)
    view = GraphView(graph, layout_mode='layers').project()
    assert view.layer_count == 12 and len(view.positions) == 3
    assert view.positions['top.u10'][0] > view.positions['top.u8'][0]
    xml = ET.fromstring(view.svg())
    assert len(xml.findall('.//{http://www.w3.org/2000/svg}g[@data-device-kind="div"]')) == 3
    assert 'L12' in view.svg()
    assert len(xml.findall('.//{http://www.w3.org/2000/svg}polyline[@class="edge"]')) == 2
    assert graph == saved
    view.project(mode='hierarchy', depth=1)
    assert view.actual_count == 3 and len(view.positions) == 3


def test_aggregation_displays_all_four_device_counts():
    nodes = [{'id': 'top.u_' + kind, 'layer': 1, 'component': 0, 'module': kind,
              'kind': kind, 'inputs': ['i'], 'output': 'o'} for kind in ('mux', 'gate', 'occ', 'div')]
    view = GraphView({'nodes': nodes, 'edges': []}).project(mode='hierarchy', depth=1)
    aggregate = next(iter(view.nodes.values()))
    assert aggregate['kind_counts'] == {'mux': 1, 'gate': 1, 'occ': 1, 'div': 1}
    svg = view.svg()
    assert all(label in svg for label in ('MUX ×1', 'GATE ×1', 'OCC ×1', 'DIV ×1'))
