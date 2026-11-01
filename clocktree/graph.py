"""Project all pin traces into one global graph, preserving unresolved components."""
from collections import Counter, defaultdict, deque
import hashlib
import json
from .devices import resolve_kind


UNRESOLVED = {"error", "unknown_cell", "missing_pin", "width_requires_bit", "trace_limit", "unknown_type"}


def build_graph(events, config):
    nodes = {}
    traces = []
    diagnostics = []
    metadata = {}
    completed = False
    for item in events:
        kind = item.get("event")
        if kind == "instance":
            key = item["path"]
            if key in nodes and nodes[key]["layer"] != item["layer"]:
                diagnostics.append({"code": "layer_conflict", "instance": key, "layers": [nodes[key]["layer"], item["layer"]]})
                continue
            nodes[key] = dict(item, id=key)
        elif kind == "trace":
            traces.append(item)
        elif kind == "diagnostic":
            diagnostics.append(item)
        elif kind == "metadata":
            metadata.update(item)
        elif kind == "complete":
            completed = True
            metadata["npi_counts"] = item
    for node in nodes.values():
        kind = resolve_kind(node, config)
        if kind:
            node['kind'], node['kind_source'] = kind, 'configured'
    edges = {}
    boundaries = []
    trace_keys = set()
    coverage = {}
    for trace_index, trace in enumerate(traces):
        sink_groups = {}
        key = (trace["instance"], trace["pin"], trace["direction"])
        trace_keys.add(key)
        coverage[key] = trace
        for result in trace.get("results", []):
            if result["status"] == "sink":
                # Register/RAM consumers are outside the configured structure
                # view. Keep full per-pin evidence in raw.jsonl; summarize it
                # here so the browser does not load tens of thousands of long,
                # duplicated hierarchy paths just to draw the clock structures.
                cell = result.get("cell", "")
                group = sink_groups.setdefault(cell, {"status": "sink_group", "cell": cell, "count": 0,
                    "instance": trace["instance"], "pin": trace["pin"], "direction": trace["direction"],
                    "examples": [], "evidence_ref": {"file": "raw.jsonl", "trace_index": trace_index}})
                group["count"] += 1
                if len(group["examples"]) < 5:
                    group["examples"].append(result.get("endpoint", ""))
                continue
            if result["status"] == "target":
                if trace["direction"] == "load":
                    src, sp, dst, dp = trace["instance"], trace["pin"], result["instance"], result["pin"]
                else:
                    src, sp, dst, dp = result["instance"], result["pin"], trace["instance"], trace["pin"]
                if src not in nodes or dst not in nodes:
                    diagnostics.append({"code": "unknown_target", "source": src, "target": dst})
                    continue
                edge_key = (src, sp, dst, dp)
                if edge_key not in edges:
                    edges[edge_key] = {"id": hashlib.sha256("\0".join(edge_key).encode()).hexdigest()[:20],
                                       "source": src, "source_pin": sp, "target": dst, "target_pin": dp,
                                       "evidence": {}, "layer_delta": nodes[dst]["layer"] - nodes[src]["layer"]}
                edges[edge_key]["evidence"][trace["direction"]] = result.get("path", [])
            else:
                boundaries.append(dict(result, instance=trace["instance"], pin=trace["pin"], direction=trace["direction"]))
        boundaries.extend(sink_groups.values())
    for edge in edges.values():
        if len(edge["evidence"]) != 2:
            diagnostics.append({"code": "trace_mismatch", "edge": edge["id"], "observed": sorted(edge["evidence"])})
        if edge["layer_delta"] < 0:
            diagnostics.append({"code": "reverse_layer", "edge": edge["id"]})
    # A physical external clock can feed many L1 gates; keep its identity and
    # join those branches in the same network without duplicating RTL instances.
    sources = {}
    source_neighbours = defaultdict(set)
    for boundary in boundaries:
        is_source_boundary = boundary["status"] == "input_boundary" or (
            boundary["status"] == "unselected_port_boundary" and boundary.get("pin_direction") == "npiNlOutput")
        if boundary["direction"] == "driver" and is_source_boundary:
            name = boundary.get("endpoint", "")
            if not name:
                continue
            source = sources.setdefault(name, {"id": name, "kind": boundary["status"], "loads": []})
            load = {"instance": boundary["instance"], "pin": boundary["pin"]}
            if load not in source["loads"]:
                source["loads"].append(load)
    for source in sources.values():
        members = sorted(set(load["instance"] for load in source["loads"]))
        for member in members[1:]:
            source_neighbours[members[0]].add(member)
            source_neighbours[member].add(members[0])
    outgoing, incoming = defaultdict(set), defaultdict(set)
    pin_drivers = defaultdict(set)
    for edge in edges.values():
        outgoing[edge["source"]].add(edge["target"])
        incoming[edge["target"]].add(edge["source"])
        pin_drivers[(edge["target"], edge["target_pin"])].add((edge["source"], edge["source_pin"]))
    for (instance, pin), drivers in pin_drivers.items():
        if len(drivers) > 1:
            diagnostics.append({"code": "multiple_drivers", "instance": instance, "pin": pin, "drivers": sorted(drivers)})
    expected_traces = set()
    for node in nodes.values():
        expected_traces.add((node["id"], node["output"], "load"))
        expected_traces.update((node["id"], pin, "driver") for pin in node["inputs"])
    missing = sorted(expected_traces - trace_keys)
    for node, pin, direction in missing:
        diagnostics.append({"code": "missing_trace", "instance": node, "pin": pin, "direction": direction})
    # Connected components are independent of root reachability: no node is filtered.
    component_id = {}
    components = []
    for node in sorted(nodes):
        if node in component_id:
            continue
        members = []
        queue = deque([node])
        component_id[node] = len(components)
        while queue:
            current = queue.popleft()
            members.append(current)
            for neighbour in sorted(outgoing[current] | incoming[current] | source_neighbours[current]):
                if neighbour not in component_id:
                    component_id[neighbour] = len(components)
                    queue.append(neighbour)
        components.append({"id": len(components), "nodes": sorted(members)})
    # Kahn identifies cycles without recursion limits on large SoCs.
    degree = {node: len(incoming[node]) for node in nodes}
    queue = deque(sorted(n for n in nodes if degree[n] == 0))
    visited = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for nxt in outgoing[current]:
            degree[nxt] -= 1
            if degree[nxt] == 0:
                queue.append(nxt)
    if len(visited) != len(nodes):
        diagnostics.append({"code": "cycle_or_cycle_downstream", "instances": sorted(n for n in nodes if degree[n])})
    unresolved = [b for b in boundaries if b["status"] in UNRESOLVED]
    unresolved_outputs = {b["instance"] for b in unresolved if b["direction"] == "load"}
    for node in nodes.values():
        node["component"] = component_id[node["id"]]
        node["root"] = not incoming[node["id"]]
        node["terminal"] = not outgoing[node["id"]] and node["id"] not in unresolved_outputs and (node["id"], node["output"], "load") in trace_keys
        node["has_unresolved_output"] = node["id"] in unresolved_outputs
        if node["root"] and node["layer"] != 1:
            diagnostics.append({"code": "root_outside_L1", "instance": node["id"]})
    fatal_codes = {"layer_conflict", "trace_mismatch", "missing_trace", "missing_module", "scope_not_found", "unknown_target", "pin_validation"}
    coverage_ok = completed and not missing and not unresolved and not any(d["code"] in fatal_codes for d in diagnostics)
    topology_errors = {"reverse_layer", "cycle_or_cycle_downstream", "multiple_drivers", "root_outside_L1", "layer_conflict"}
    return {"schema_version": 1, "tool_version": "0.1.0", "config": config, "metadata": metadata,
            "nodes": sorted(nodes.values(), key=lambda n: (n["layer"], n["id"])),
            "edges": sorted(edges.values(), key=lambda e: e["id"]), "boundaries": boundaries,
            "components": components, "clock_sources": sorted(sources.values(), key=lambda s: s["id"]), "diagnostics": diagnostics,
            "summary": {"instances": len(nodes), "edges": len(edges), "roots": sum(n["root"] for n in nodes.values()),
                        "terminals": sum(n["terminal"] for n in nodes.values()), "components": len(components), "clock_sources": len(sources),
                        "layers": dict(Counter(str(n["layer"]) for n in nodes.values())),
                        "terminal_layers": dict(Counter(str(n["layer"]) for n in nodes.values() if n["terminal"])),
                        "expected_pin_traces": len(expected_traces), "observed_pin_traces": len(trace_keys),
                        "unresolved": len(unresolved), "diagnostics": len(diagnostics),
                        "extraction_finished": completed, "connectivity_complete": coverage_ok,
                        "topology_valid": not any(d["code"] in topology_errors for d in diagnostics)}}


def read_events(path):
    with open(str(path), encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]
