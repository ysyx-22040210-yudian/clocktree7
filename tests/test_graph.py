import copy
import tkinter
import pytest

from clocktree.graph import build_graph
from clocktree.config import tcl_string, validate_config


def node(path, layer, inputs=("i",)):
    return {"event": "instance", "path": path, "module": "gate", "layer": layer, "inputs": list(inputs), "output": "o"}


def trace(path, pin, direction, results):
    return {"event": "trace", "instance": path, "pin": pin, "direction": direction, "results": results}


def target(path, pin):
    return {"status": "target", "instance": path, "pin": pin, "path": []}


def test_all_trees_fanout_merge_same_layer_and_early_leaf():
    events = [node("top.a", 1), node("top.b", 2), node("top.c", 2), node("top.mux", 5, ("a", "b")), node("top.other", 1)]
    events += [trace("top.a", "i", "driver", [{"status": "input_boundary", "endpoint": "top.clk0"}]),
               trace("top.other", "i", "driver", [{"status": "input_boundary", "endpoint": "top.clk1"}]),
               trace("top.other", "o", "load", [{"status": "output_boundary"}]),
               trace("top.a", "o", "load", [target("top.b", "i"), target("top.c", "i")]),
               trace("top.b", "i", "driver", [target("top.a", "o")]),
               trace("top.c", "i", "driver", [target("top.a", "o")]),
               trace("top.b", "o", "load", [target("top.mux", "a")]),
               trace("top.c", "o", "load", [target("top.mux", "b")]),
               trace("top.mux", "a", "driver", [target("top.b", "o")]),
               trace("top.mux", "b", "driver", [target("top.c", "o")]),
               trace("top.mux", "o", "load", [{"status": "output_boundary"}]), {"event": "complete"}]
    graph = build_graph(events, {})
    assert graph["summary"]["instances"] == 5
    assert graph["summary"]["edges"] == 4
    assert graph["summary"]["components"] == 2
    assert graph["summary"]["terminal_layers"] == {"1": 1, "5": 1}
    assert graph["summary"]["connectivity_complete"]
    assert len(graph["clock_sources"]) == 2


def test_shared_external_clock_joins_all_branches():
    events = []
    for name in ("a", "b", "c"):
        events.extend([node(name, 1), trace(name, "i", "driver", [{"status": "input_boundary", "endpoint": "top.clk"}]),
                       trace(name, "o", "load", [{"status": "output_boundary"}])])
    graph = build_graph(events + [{"event": "complete"}], {})
    assert graph["summary"]["components"] == 1
    assert len(graph["clock_sources"][0]["loads"]) == 3
    assert graph["summary"]["terminals"] == 3


def test_unselected_output_is_an_explicit_source_boundary():
    events=[node("a",1), trace("a","i","driver",[{"status":"unselected_port_boundary","endpoint":"clockgen.per_clk","pin_direction":"npiNlOutput"}]),
            trace("a","o","load",[{"status":"output_boundary"}]),{"event":"complete"}]
    graph=build_graph(events,{})
    assert graph["clock_sources"][0]["id"]=="clockgen.per_clk"
    assert graph["clock_sources"][0]["kind"]=="unselected_port_boundary"
    assert graph["summary"]["connectivity_complete"]


def test_unknown_branch_is_retained_and_never_certified_as_terminal():
    events = [node("a", 1), trace("a", "i", "driver", [{"status": "input_boundary", "endpoint": "clk"}]),
              trace("a", "o", "load", [{"status": "unknown_cell", "cell": "blackbox"}]), {"event": "complete"}]
    graph = build_graph(events, {})
    assert len(graph["nodes"]) == 1
    assert not graph["nodes"][0]["terminal"]
    assert not graph["summary"]["connectivity_complete"]


def test_missing_reverse_trace_and_reverse_layer_are_reported_without_dropping_edge():
    graph = build_graph([node("a", 5), node("b", 2), trace("a", "o", "load", [target("b", "i")]), {"event": "complete"}], {})
    assert len(graph["edges"]) == 1
    assert {d["code"] for d in graph["diagnostics"]} >= {"trace_mismatch", "reverse_layer", "missing_trace"}
    assert not graph["summary"]["connectivity_complete"]


@pytest.mark.parametrize("value", ["top.g[3].u", 'x;set injected 1', '[error injected]', '$secret', 'a\\b"c\nd', '{unbalanced'])
def test_tcl_configuration_literals_never_execute(value):
    interpreter = tkinter.Tcl()
    interpreter.eval("set value " + tcl_string(value))
    assert interpreter.getvar("value") == value
    assert interpreter.eval("info exists injected") == "0"
