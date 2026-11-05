import copy
import json
from pathlib import Path
import tkinter

import pytest

from clocktree.config import hierarchy_contains, validate_config, write_tcl_config
from clocktree.graph import build_graph
from clocktree.profiles import empty_config


ROOT = Path(__file__).resolve().parents[1]


def config(mode='clock'):
    cfg = empty_config(mode)
    cfg.update(trace_scope='top.domain')
    cfg['design'] = {'top': 'top', 'elab': '/kdb'}
    cfg['layers'][0].update(hierarchy='top.domain', modules=[{'module': 'mux', 'kind': 'mux',
        ('reset_inputs' if mode == 'reset' else 'clock_inputs'): ['i'],
        ('reset_output' if mode == 'reset' else 'clock_output'): 'o'}])
    return cfg


def test_exact_root_validation_and_incomplete_workspace_drafts():
    for mode in ('clock', 'reset'):
        cfg = config(mode)
        validate_config(cfg)
        cfg['layers'][0]['hierarchy'] = ''
        validate_config(cfg)
        for invalid in ('top.domain_extra', 'top', 'other.domain'):
            cfg['layers'][0]['hierarchy'] = invalid
            with pytest.raises(ValueError, match='inside trace_scope'):validate_config(cfg)
            validate_config(cfg, allow_incomplete=True)
        cfg['layers'][0]['hierarchy'] = 'top.domain'
        for invalid in ('other', 'top.*', 'top.?'):
            cfg['trace_scope'] = invalid
            with pytest.raises(ValueError, match='trace_scope'):validate_config(cfg)
        cfg['trace_scope'] = None
        with pytest.raises(ValueError, match='trace_scope'):validate_config(cfg)
    assert hierarchy_contains('top.g[0].domain.u', 'top.g[0].domain')
    assert not hierarchy_contains('top.g[10].domain.u', 'top.g[1].domain')


def test_trace_root_survives_literal_tcl_export(tmp_path):
    cfg = config()
    cfg['trace_scope'] = 'top.g[0].domain'
    cfg['layers'][0]['hierarchy'] = 'top.g[0].domain'
    path = tmp_path / 'rules.tcl'
    write_tcl_config(validate_config(cfg), path, '/raw.jsonl')
    interp = tkinter.Tcl()
    interp.eval(path.read_text())
    assert interp.getvar('ct_trace_scope') == 'top.g[0].domain'
    cfg.pop('trace_scope')
    write_tcl_config(cfg, path, '/raw.jsonl')
    interp.eval(path.read_text())
    assert interp.getvar('ct_trace_scope') == ''


def test_scope_exit_is_not_a_terminal_and_later_layer_scope_entry_is_valid():
    cfg = config()
    name = 'top.domain.u'
    events = [dict(event='instance', path=name, module='mux', layer=2, inputs=['i'], output='o'),
              dict(event='trace', instance=name, pin='i', direction='driver', results=[
                  dict(status='scope_boundary', endpoint='top.domain.clk_i', scope='top.domain')]),
              dict(event='trace', instance=name, pin='o', direction='load', results=[
                  dict(status='scope_boundary', endpoint='top.domain.clk_o', scope='top.domain')]),
              dict(event='complete')]
    graph = build_graph(events, cfg)
    assert graph['summary']['connectivity_complete'] and graph['summary']['topology_valid']
    assert not graph['nodes'][0]['terminal'] and graph['nodes'][0]['scope_output_boundary']
    assert graph['nodes'][0]['scope_input_boundary']
    assert graph['summary']['scope_boundaries'] == 2 and graph['summary']['scope_output_instances'] == 1
    assert graph['clock_sources'][0]['id'] == 'top.domain.clk_i'
    assert graph['clock_sources'][0]['kind'] == 'scope_boundary'
    wrong = copy.deepcopy(events)
    wrong[0]['path'] = 'top.domain_extra.u'
    bad = build_graph(wrong, cfg)
    assert not bad['summary']['connectivity_complete']
    assert any(d['code'] == 'target_outside_trace_scope' for d in bad['diagnostics'])


def tcl_walk():
    interp = tkinter.Tcl()
    source = (ROOT / 'npi/extract.tcl').read_text(encoding='utf-8').split('if {[catch {ct_main}')[0]
    interp.eval(source)
    interp.eval('''
        set ct_trace_scope top.domain
        set ct_top top
        set ct_selected {}
        set ct_instances {}
        set ct_owner_paths {}
        set ct_max_steps 0
        set visits {}
        set records {}
        proc ct_emit {record} {global records; lappend records $record}
        proc flush {args} {}
        set ct_stream unused
        proc ct_prop {hdl property} {
            if {$hdl eq ""} {return ""}
            switch $property {
                npiNlFullName {return $hdl}
                npiNlName {return [lindex [split $hdl .] end]}
                npiNlType {return [expr {[string match *.wire $hdl] ? "npiNlNet" : "npiNlInstPort"}]}
                npiNlCellType {return npiNlModuleCell}
                npiNlDirection {return [expr {[string match *.o $hdl] ? "npiNlOutput" : "npiNlInput"}]}
            }
            return ""
        }
        proc ct_inst {hdl} {return [join [lrange [split $hdl .] 0 end-1] .]}
        proc npi_nl_get {args} {return 0}
        proc npi_nl_handle {args} {return ""}
        proc npi_nl_release_handle {args} {}
        set iterators {};set next_iterator 0
        proc npi_nl_iterate {args} {
            global visits iterators next_iterator
            set hdl [lindex $args 3]
            lappend visits $hdl
            if {$hdl eq "top.domain.u.i"} {set next top.domain.wire} elseif {$hdl eq "top.domain.wire"} {
                set next top.domain_extra.wire
            } else {error "Traversal escaped the configured root"}
            set id [incr next_iterator];dict set iterators $id $next;return $id
        }
        proc npi_nl_scan {args} {
            global iterators
            set id [lindex $args 1];set value [dict get $iterators $id]
            dict set iterators $id {};return $value
        }
        namespace eval ::npi_L1 {}
        proc ::npi_L1::npi_nl_trace_driver_by_hdl2 {hdl result} {
            global visits
            lappend visits $hdl
            upvar $result next
            if {$hdl eq "top.domain.u.i"} {set next top.domain.wire} elseif {$hdl eq "top.domain.wire"} {
                set next top.domain_extra.wire
            } else {error "Traversal escaped the configured root"}
        }
    ''')
    return interp


def streamed_walk(interp, seed, direction, owner, pin):
    interp.call('ct_walk', seed, direction, owner, pin, 1)
    steps, results = {}, []
    for data in interp.splitlist(interp.getvar('records')):
        item = json.loads(str(data))
        if item['event'] == 'path_step':
            steps[item['path_id']] = (item['parent_id'], item['evidence'])
        elif item['event'] == 'trace_result':
            result = item['result']
            key, path = result.pop('path_id'), []
            while key:
                key, evidence = steps[key]
                path.append(evidence)
            result['path'] = list(reversed(path))
            results.append(result)
    return results


def test_tcl_never_expands_outside_handle_and_keeps_only_inside_evidence():
    interp = tcl_walk()
    records = streamed_walk(interp, 'top.domain.u.i', 'driver', 'top.domain.u', 'i')
    assert len(records) == 1 and records[0]['status'] == 'scope_boundary'
    assert records[0]['endpoint'] == 'top.domain.wire'
    assert [p['name'] for p in records[0]['path']] == ['top.domain.u.i', 'top.domain.wire']
    assert interp.splitlist(interp.getvar('visits')) == ('top.domain.u.i', 'top.domain.wire')
    interp.eval('proc ct_inst {hdl} {return top.domain}')
    assert interp.call('ct_inside_trace_scope', 'top.alias_outside.wire') == 0


def test_root_interface_is_a_boundary_even_when_it_is_the_seed():
    for pin, direction in [('i', 'driver'), ('o', 'load')]:
        interp = tcl_walk()
        record = streamed_walk(interp, 'top.domain.' + pin, direction, 'top.domain', pin)[0]
        assert record['status'] == 'scope_boundary' and len(record['path']) == 1
        assert interp.getvar('visits') == ''


def test_direct_query_never_descends_and_keeps_only_module_definition_matches():
    source=(ROOT/'npi/extract.tcl').read_text(encoding='utf-8').split('if {[catch {ct_main}')[0]
    interp=tkinter.Tcl();interp.eval(source)
    interp.eval('''
        set scanned {};set released {};set pending {}
        proc npi_iterate {args} {
            global scanned pending
            if {$args ne {-type npiInternalScope -refHandle top.domain}} {error "Unexpected recursive traversal: $args"}
            lappend scanned top.domain
            set pending {top.domain.u0 top.domain.u_other top.domain.g top.domain.arr[0] top.domain.arr[1]}
            return direct_iterator
        }
        proc npi_scan {args} {
            global pending
            set value [lindex $pending 0];set pending [lrange $pending 1 end];return $value
        }
        proc npi_get_str {args} {
            set prop [lindex $args 1];set object [lindex $args 3]
            if {$prop eq "npiType"} {return [expr {$object eq "top.domain.g" ? "npiGenScope" : "npiModule"}]}
            if {$prop eq "npiDefName"} {return [expr {$object eq "top.domain.u_other" ? "gate_other" : "gate"}]}
            error "Unexpected property: $prop"
        }
        proc npi_release_handle {args} {global released;lappend released [lindex $args 1]}
    ''')
    assert interp.call('ct_find_direct_instances','top.domain','gate','matches')==5
    assert interp.splitlist(interp.getvar('scanned'))==('top.domain',)
    assert interp.splitlist(interp.getvar('matches'))==('top.domain.u0','top.domain.arr[0]','top.domain.arr[1]')
    assert interp.splitlist(interp.getvar('released'))==('top.domain.u_other','top.domain.g')
    interp.call('ct_release_instances',interp.getvar('matches'))
    assert len(interp.splitlist(interp.getvar('released')))==5
