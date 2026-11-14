"""Project all pin traces into one global graph, preserving unresolved components."""
from collections import Counter, defaultdict, deque
import hashlib
import json
import sys
from .devices import resolve_kind
from .profiles import profile, tree_type, output_ports
from .config import hierarchy_contains


UNRESOLVED = {"error", "unknown_cell", "missing_pin", "width_requires_bit", "trace_limit", "unknown_type", "unsupported_expression",
              "trace_timeout", "trace_skipped_timeout", "wire_connection_unresolved", "unselected_wire_input"}
TOPOLOGY_ERRORS = {"reverse_layer", "cycle_or_cycle_downstream", "multiple_drivers", "multiple_l1_inputs", "layer_conflict"}


def build_graph(events, config, evidence_store=None):
    info = profile(config)
    nodes = {}
    diagnostics = []
    metadata = {}
    edges, boundaries, trace_keys = {}, [], set()
    active = {}
    trace_index = 0
    output_checks = {}
    timeout_instances = {}
    l1_traces = []

    def consume(trace, result):
        if trace['direction'] == 'internal':
            trace.setdefault('internal', []).append(dict(result))
            return
        if result['status'] == 'sink':
            cell = result.get('cell', '')
            group = trace['sinks'].setdefault(cell, {'status': 'sink_group', 'cell': cell, 'count': 0,
                'instance': trace['instance'], 'pin': trace['pin'], 'direction': trace['direction'],
                'examples': [], 'evidence_ref': {'file': 'raw.jsonl', 'trace_index': trace['index']}})
            if 'trace_id' in trace:
                group['evidence_ref']['trace_id'] = trace['trace_id']
            group['count'] += 1
            if len(group['examples']) < 5:
                group['examples'].append(result.get('endpoint', ''))
            return
        result = dict(result)
        if 'path_id' in result:
            result['path'] = evidence_store.trace_path(trace['trace_id'], result.pop('path_id'))
        elif evidence_store is not None and 'path' in result:
            result['path'] = evidence_store.add_path(result['path'])
        if trace['direction'] == 'driver':
            trace.setdefault('driver_results', []).append(result)
        if result['status'] == 'target':
            if trace['direction'] == 'load':
                src, sp, dst, dp = trace['instance'], trace['pin'], result['instance'], result['pin']
            else:
                src, sp, dst, dp = result['instance'], result['pin'], trace['instance'], trace['pin']
            edge_key = tuple(sys.intern(part) for part in (src, sp, dst, dp))
            if edge_key not in edges:
                edges[edge_key] = {'id': hashlib.sha256('\0'.join(edge_key).encode()).hexdigest()[:20],
                    'source': edge_key[0], 'source_pin': edge_key[1], 'target': edge_key[2], 'target_pin': edge_key[3],
                    'evidence': {}}
            edges[edge_key]['evidence'][trace['direction']] = result.get('path', [])
        else:
            boundaries.append(dict(result, instance=trace['instance'], pin=trace['pin'], direction=trace['direction']))

    def begin(item):
        nonlocal trace_index
        trace = {key: item[key] for key in ('instance', 'pin', 'direction')}
        trace.update(sinks={}, index=trace_index, count=0)
        if 'trace_id' in item:
            trace['trace_id'] = item['trace_id']
        trace_index += 1
        return trace

    def confirm_l1_input(trace):
        results = trace.get('driver_results', ())
        if not results:
            return
        entries, targets, known_sources = {}, set(), set()
        for result in results:
            status = result['status']
            if status in ('trace_timeout', 'trace_skipped_timeout', 'trace_limit'):
                return
            if status == 'target':
                targets.add((result['instance'], result['pin']))
            if result.get('l1_verified'):
                entries[result['endpoint']] = result
            if result.get('endpoint') and (status in ('input_boundary', 'scope_boundary', 'constant') or
                    status == 'unselected_port_boundary' and result.get('pin_direction') == 'npiNlOutput'):
                known_sources.add(result['endpoint'])
        passed = trace.get('passed_l1_entries', ())
        l1_signals = set(entries).union(entry['endpoint'] for entry in passed)
        if not targets and len(l1_signals) > 1:
            diagnostics.append({'code':'multiple_l1_inputs','instance':trace['instance'],'pin':trace['pin'],
                                'signals':sorted(l1_signals), 'message':'输入追到多个 L1 端口，不能唯一生成直达分支'})
            return
        if known_sources and len(targets) <= 1 and len(known_sources) + len(targets) > 1:
            diagnostics.append({'code':'multiple_drivers','instance':trace['instance'],'pin':trace['pin'],
                                'drivers':[list(target) for target in sorted(targets)] + sorted(known_sources)})
            return
        if targets or not l1_signals:
            return
        prefixes = defaultdict(list)
        for entry in passed:
            path = evidence_store.trace_path(trace['trace_id'], entry['path_id'])
            prefixes[entry['endpoint']].append(path)
            entries.setdefault(entry['endpoint'], dict(entry, path=path))
        signal, entry = next(iter(entries.items()))
        # An upstream problem cannot invalidate the proven segment on this side
        # of L1. Reject a competing path unless it crossed the same formal input.
        for result in results:
            if result.get('l1_verified'):
                continue
            path = result.get('path')
            if path is None or not any(path.starts_with(prefix) for prefix in prefixes[signal]):
                return
        l1_traces.append((trace['instance'], trace['pin'], {'scope':entry['l1_scope'],
            'port':entry['l1_port'], 'signal':signal, 'status':'verified', 'evidence':entry.get('path', [])}))

    def finish(trace):
        trace_keys.add((trace['instance'], trace['pin'], trace['direction']))
        boundaries.extend(trace['sinks'].values())
        if trace['direction'] == 'driver':
            confirm_l1_input(trace)
        if trace['direction'] == 'internal':
            owner, output = trace['instance'], trace['pin']
            results = trace.get('internal', [])
            targets = {r.get('pin') for r in results if r['status'] == 'target' and r.get('instance') == owner}
            good = bool(results) and len(targets) == 1 and all(r['status'] == 'target' and r.get('instance') == owner for r in results)
            if good and owner in nodes and targets.issubset(nodes[owner]['inputs']) and output in output_ports(nodes[owner]):
                result = results[0]
                path = result.get('path', [])
                if 'path_id' in result:path = evidence_store.trace_path(trace['trace_id'], result['path_id'])
                nodes[owner].setdefault('internal_connections', []).append({'input':next(iter(targets)), 'output':output,
                    'status':'verified', 'evidence':path})
            else:
                statuses = sorted(set(r['status'] for r in results))
                status = next((s for s in statuses if s in ('trace_timeout', 'trace_skipped_timeout')), 'wire_connection_unresolved')
                boundaries.append({'instance':owner,'pin':output,'direction':'internal','status':status,
                    'message':'穿线内部未证明唯一直接连接：'+', '.join(statuses or ['no_result']), 'candidates':sorted(targets)})

    trace_scope = config.get('trace_scope', '').strip().rstrip('./')
    completed = False
    for item in events:
        kind = item.get("event")
        if kind == "instance":
            key = sys.intern(item["path"])
            if trace_scope and not hierarchy_contains(key, trace_scope):
                diagnostics.append({'code': 'target_outside_trace_scope', 'instance': key, 'scope': trace_scope})
                continue
            if key in nodes and nodes[key]["layer"] != item["layer"]:
                diagnostics.append({"code": "layer_conflict", "instance": key, "layers": [nodes[key]["layer"], item["layer"]]})
                continue
            nodes[key] = dict(item, id=key)
            from .auxiliary import aux_diagnostics
            diagnostics.extend(aux_diagnostics(nodes[key]))
        elif kind == "trace":
            trace = begin(item)
            for result in item.get('results', []):
                consume(trace, result)
            finish(trace)
        elif kind == 'trace_begin':
            if evidence_store is None:
                from .storage import ResultStore
                import tempfile
                from pathlib import Path
                temporary = tempfile.TemporaryDirectory(prefix='ct7-evidence-')
                evidence_store = ResultStore(Path(temporary.name) / 'evidence.sqlite3', create=True)
                evidence_store.temporary = temporary
            if item['trace_id'] in active:
                raise ValueError('Duplicate trace_begin')
            active[item['trace_id']] = begin(item)
        elif kind == 'path_step':
            if item['trace_id'] not in active:
                raise ValueError('Path outside an active trace')
            evidence_store.trace_step(item['trace_id'], item['path_id'], item['parent_id'], item['evidence'])
        elif kind == 'trace_result':
            trace = active[item['trace_id']]
            consume(trace, item['result'])
            trace['count'] += 1
        elif kind == 'l1_entry':
            active[item['trace_id']].setdefault('passed_l1_entries', []).append(item['result'])
        elif kind == 'trace_end':
            trace = active.pop(item['trace_id'])
            if not trace['count'] or trace['count'] != item['result_count']:
                diagnostics.append({'code': 'trace_protocol', 'trace_id': item['trace_id'], 'message': 'Result count mismatch'})
            else:
                finish(trace)
            evidence_store.end_trace(item['trace_id'])
        elif kind == "diagnostic":
            diagnostics.append(item)
        elif kind == 'instance_trace_timeout':
            decision = item['detail']
            timeout_instances[decision['instance']] = decision
            diagnostics.append(dict(decision, code='instance_trace_timeout'))
        elif kind == 'output_check':
            output_checks[(item['instance'], item['pin'])] = item['status']
            if item['status'] != 'ok':
                diagnostics.append(dict(item, code='pin_validation'))
        elif kind == "metadata":
            metadata.update(item)
        elif kind == 'scoped_query':
            metadata.setdefault('scoped_queries', []).append({k: v for k, v in item.items() if k != 'event'})
        elif kind == 'module_checkpoint':
            checkpoint = item.get('checkpoint', {})
            index = metadata.get('module_checkpoints', 0) + 1
            if metadata.get('module_checkpoint_protocol') and checkpoint.get('index') != index:
                diagnostics.append({'code': 'module_checkpoint_sequence', 'expected': index, 'observed': checkpoint.get('index')})
            metadata['module_checkpoints'] = index
            metadata['last_module_checkpoint'] = checkpoint
        elif kind == "complete":
            completed = True
            metadata["npi_counts"] = item
    if metadata.get('module_checkpoint_protocol'):
        expected_modules = sum(len(layer.get('modules', [])) for layer in config.get('layers', []))
        metadata['expected_module_checkpoints'] = expected_modules
        if metadata.get('module_checkpoints', 0) != expected_modules or (completed and metadata.get('npi_counts', {}).get('module_rules') != expected_modules):
            diagnostics.append({'code': 'missing_module_checkpoint', 'expected': expected_modules, 'observed': metadata.get('module_checkpoints', 0)})
    for node in nodes.values():
        kind = resolve_kind(node, config)
        if kind:
            node['kind'], node['kind_source'] = kind, 'configured'
    for trace in active.values():
        boundaries.extend(trace['sinks'].values())
        diagnostics.append({'code': 'trace_protocol', 'trace_id': trace['trace_id'], 'message': 'Missing trace_end'})
    # Resolve topology after enumeration, allowing legacy interleaved events.
    for key in list(edges):
        edge = edges[key]
        if edge['source'] not in nodes or edge['target'] not in nodes:
            diagnostics.append({'code': 'unknown_target', 'source': edge['source'], 'target': edge['target']})
            del edges[key]
            continue
        edge['layer_delta'] = nodes[edge['target']]['layer'] - nodes[edge['source']]['layer']
    # Raw metadata defines evidence semantics; old saved results remain dual-direction.
    target_mode = metadata.get('extraction_mode') == 'target'
    if target_mode and any(direction not in ('driver', 'internal') for _, _, direction in trace_keys):
        diagnostics.append({'code': 'trace_protocol', 'message': 'Target mode contains an output load trace'})
    for edge in edges.values():
        required = {'driver'} if target_mode else {'driver', 'load'}
        if set(edge['evidence']) != required:
            diagnostics.append({"code": "trace_mismatch", "edge": edge["id"], "observed": sorted(edge["evidence"])})
        if edge["layer_delta"] < 0:
            diagnostics.append({"code": "reverse_layer", "edge": edge["id"]})
    # Keep the identity of a source that feeds several selected roots.
    sources = {}
    source_loads = defaultdict(set)
    for boundary in boundaries:
        is_source_boundary = boundary["status"] == "input_boundary" or (
            boundary["status"] == "unselected_port_boundary" and boundary.get("pin_direction") == "npiNlOutput") or boundary['status'] == 'scope_boundary'
        if boundary["direction"] == "driver" and is_source_boundary:
            name = boundary.get("endpoint", "")
            if not name:
                continue
            source = sources.setdefault(name, {"id": name, "kind": boundary["status"], "loads": []})
            load = {"instance": boundary["instance"], "pin": boundary["pin"]}
            load_key = (load['instance'], load['pin'])
            if load_key not in source_loads[name]:
                source["loads"].append(load)
                source_loads[name].add(load_key)
    del source_loads
    outgoing, incoming = defaultdict(set), defaultdict(set)
    pin_drivers = defaultdict(set)
    for edge in edges.values():
        outgoing[edge["source"]].add(edge["target"])
        incoming[edge["target"]].add(edge["source"])
        pin_drivers[(edge["target"], edge["target_pin"])].add((edge["source"], edge["source_pin"]))
    # Full verification can return a load-only edge even when the driver walk
    # stopped at a boundary. Do not display that pin as a proven L1 root as well.
    for instance, pin, entry in l1_traces:
        if instance in nodes and (instance, pin) not in pin_drivers:
            nodes[instance].setdefault('l1_inputs', {})[pin] = entry
    del l1_traces
    for (instance, pin), drivers in pin_drivers.items():
        if len(drivers) > 1:
            diagnostics.append({"code": "multiple_drivers", "instance": instance, "pin": pin, "drivers": sorted(drivers)})
    expected_count = 0
    missing = []
    for node in nodes.values():
        for pin in output_ports(node):
            if target_mode and (node['id'], pin) not in output_checks:
                diagnostics.append({'code': 'pin_validation', 'instance': node['id'], 'pin': pin, 'status': 'missing_output_check'})
        requests = ([] if target_mode else [(p, 'load') for p in output_ports(node)]) + [(pin, 'driver') for pin in node['inputs']]
        if node.get('kind') == 'wire':requests += [(p, 'internal') for p in output_ports(node)]
        for pin, direction in requests:
            expected_count += 1
            key = (node['id'], pin, direction)
            if key not in trace_keys:
                missing.append(key)
    missing.sort()
    for node, pin, direction in missing:
        diagnostics.append({"code": "missing_trace", "instance": node, "pin": pin, "direction": direction})
    from .networks import memberships
    components = memberships(nodes, edges.values(), sources.values())
    # Kahn identifies cycles without recursion limits on large SoCs.
    from .connectivity import topology, internal_pairs
    has_wire=any(n.get('kind')=='wire' for n in nodes.values())
    if has_wire:
        channel_out, channel_in = topology(nodes, edges.values())
        degree = {key:len(value) for key,value in channel_in.items()}
        del channel_in
    else:
        channel_out=outgoing
        degree={key:len(incoming.get(key,())) for key in nodes}
    queue = deque(n for n in degree if degree[n] == 0)
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for nxt in channel_out.get(current, ()):
            degree[nxt] -= 1
            if degree[nxt] == 0:
                queue.append(nxt)
    if visited != len(degree):
        diagnostics.append({"code": "cycle_or_cycle_downstream", "instances": sorted({key[0] if has_wire else key for key in degree if degree[key]})})
    del channel_out, degree
    unresolved_count = sum(b['status'] in UNRESOLVED for b in boundaries)
    undriven_count = len({(b['instance'], b['pin']) for b in boundaries
                          if b['status'] == 'undriven' and b['direction'] == 'driver'})
    unresolved_outputs = {b['instance'] for b in boundaries if b['status'] in UNRESOLVED and b['direction'] == 'load'}
    scope_output_pins = {(b['instance'], b['pin']) for b in boundaries
                         if b['status'] == 'scope_boundary' and b['direction'] == 'load'}
    scope_outputs = {instance for instance, _ in scope_output_pins}
    scope_inputs = {b['instance'] for b in boundaries if b['status'] == 'scope_boundary' and b['direction'] == 'driver'}
    uncertain_inputs = {b['instance'] for b in boundaries if b['status'] in UNRESOLVED and b['direction'] == 'driver'}
    uncertain_inputs.update(node for node, pin, direction in missing if direction == 'driver')
    timeout_pins = {(b['instance'], b['pin']): b['status'] for b in boundaries
                    if b['status'] in ('trace_timeout', 'trace_skipped_timeout')}
    for node in nodes.values():
        timed_out = node['id'] in timeout_instances
        node["root"] = node.get('kind') != 'subsystem' and not timed_out and not incoming.get(node['id']) and not (target_mode and node['id'] in uncertain_inputs)
        node["terminal"] = not timed_out and not outgoing.get(node['id']) and node['id'] not in unresolved_outputs and node['id'] not in scope_outputs and (node["id"], node["output"], "load") in trace_keys
        if timed_out:
            node['trace_timeout'] = timeout_instances[node['id']]
            node['port_trace_status'] = {pin: timeout_pins.get((node['id'], pin),
                'completed' if (node['id'], pin, direction) in trace_keys else
                ('not_requested' if target_mode and direction == 'load' else 'not_traced'))
                for pin, direction in [(p, 'driver') for p in node['inputs']] + [(p, 'internal' if node.get('kind') == 'wire' else 'load') for p in output_ports(node)]}
        if node.get('kind') == 'subsystem':
            node['terminal'] = not timed_out
            node['terminal_semantics'] = 'subsystem_input'
        node['scope_input_boundary'] = node['id'] in scope_inputs
        node['scope_output_boundary'] = node['id'] in scope_outputs
        node["has_unresolved_output"] = node["id"] in unresolved_outputs
        if tree_type(config) == 'reset' and node["root"] and node["layer"] != 1 and not node['scope_input_boundary'] and not node.get('l1_inputs'):
            diagnostics.append({"code": "root_outside_L1", "instance": node["id"]})
    fatal_codes = {"layer_conflict", "trace_mismatch", "missing_trace", "missing_module", "scope_not_found", "unknown_target", "pin_validation", 'target_outside_trace_scope', 'trace_protocol', 'fatal', 'module_checkpoint_sequence', 'missing_module_checkpoint', 'instance_trace_timeout'}
    coverage_ok = completed and not missing and not unresolved_count and not any(d["code"] in fatal_codes for d in diagnostics)
    if target_mode:
        # An unresolved input elsewhere could be a downstream branch of any
        # apparent terminal. Do not claim proven terminals on an incomplete graph.
        for node in nodes.values():
            node['terminal'] = (not node.get('trace_timeout')) if node.get('kind') == 'subsystem' else bool(coverage_ok and not outgoing.get(node['id']) and output_ports(node) and all(output_checks.get((node['id'], p)) == 'ok' for p in output_ports(node)))
            node['physical_loads_checked'] = False
            node['terminal_semantics'] = 'subsystem_input' if node.get('kind') == 'subsystem' else 'selected_structure'
    used_outputs={(e['source'],e['source_pin']) for e in edges.values() if nodes[e['source']].get('kind')=='wire'}
    for node in nodes.values():
        if node.get('kind') != 'wire':continue
        pairs = internal_pairs(node)
        node['internal_trace_status'] = {p:('verified' if any(b == p for a,b in pairs) else timeout_pins.get((node['id'],p), 'unresolved')) for p in output_ports(node)}
        terminal_pairs = [(a, b) for a, b in pairs if (node['id'], b) not in used_outputs and
                          (node['id'], b) not in scope_output_pins] if coverage_ok else []
        node['terminal_outputs'] = sorted({b for _, b in terminal_pairs})
        node['terminal_inputs'] = sorted({a for a, _ in terminal_pairs})
        node['terminal'] = bool(node['terminal_inputs'])
        node['has_unresolved_output'] |= any(v!='verified' for v in node['internal_trace_status'].values())
    topology_errors = TOPOLOGY_ERRORS | {'root_outside_L1'} if tree_type(config) == 'reset' else TOPOLOGY_ERRORS
    return {"schema_version": 1, "tool_version": "0.1.0", "tree_type": tree_type(config), "config": config, "metadata": metadata,
            "nodes": sorted(nodes.values(), key=lambda n: (n["layer"], n["id"])),
            "edges": sorted(edges.values(), key=lambda e: e["id"]), "boundaries": boundaries,
            "components": components, info['sources']: sorted(sources.values(), key=lambda s: s["id"]), "diagnostics": diagnostics,
            "summary": {"instances": len(nodes), "edges": len(edges), "roots": sum(n["root"] for n in nodes.values()),
                        "tree_type": tree_type(config),
                        'extraction_mode': 'target' if target_mode else 'full',
                        'physical_loads_checked': not target_mode and coverage_ok,
                        'terminal_semantics': 'selected_structure' if target_mode else 'load_trace',
                        'validated_outputs': sum(v == 'ok' for v in output_checks.values()),
                        "terminals": sum(n["terminal"] for n in nodes.values()), "components": len(components), info['sources']: len(sources),
                        "layers": dict(Counter(str(n["layer"]) for n in nodes.values())),
                        "terminal_layers": dict(Counter(str(n["layer"]) for n in nodes.values() if n["terminal"])),
                        "expected_pin_traces": expected_count, "observed_pin_traces": len(trace_keys),
                        "unresolved": unresolved_count, "diagnostics": len(diagnostics), 'undriven_inputs': undriven_count,
                        'l1_input_connections': sum(len(n.get('l1_inputs', {})) for n in nodes.values()),
                        'trace_timeout_instances': len(timeout_instances),
                        'trace_timeout_ports': sum(s == 'trace_timeout' for s in timeout_pins.values()),
                        'trace_skipped_ports': sum(s == 'trace_skipped_timeout' for s in timeout_pins.values()),
                        'trace_scope': trace_scope, 'scope_limited': bool(trace_scope),
                        'scope_boundaries': sum(b['status'] == 'scope_boundary' for b in boundaries),
                        'scope_output_instances': len(scope_outputs),
                        "extraction_finished": completed, "connectivity_complete": coverage_ok,
                        "topology_valid": not any(d["code"] in topology_errors for d in diagnostics)}}


def read_events(path, limit=None):
    """Read complete events, optionally stopping at a committed byte offset."""
    with open(str(path), 'rb') as stream:
        remaining = limit
        while remaining is None or remaining > 0:
            line = stream.readline() if remaining is None else stream.readline(remaining)
            if not line:break
            if remaining is not None:remaining -= len(line)
            if line.strip():yield json.loads(line.decode('utf-8'))
