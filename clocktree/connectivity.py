"""Port-aware traversal through verified pass-through channels.

Ordinary clock devices combine their selected inputs; wires and subsystem
terminals never join unrelated ports just because they share an instance.
"""
from collections import deque
from .profiles import output_ports
from functools import lru_cache


def indexed_paths(model, instance, direction, seed_pins=None, cancel=None):
    """Disk-backed queue and result set; memory remains bounded on large trees."""
    import uuid
    from .indexed_view import SQLSelection
    conn=model.store.connection();name='ct7_wire_path_'+uuid.uuid4().hex
    conn.execute('CREATE TEMP TABLE '+name+'(id TEXT PRIMARY KEY)')
    conn.execute('CREATE TEMP TABLE '+name+'_queue(seq INTEGER PRIMARY KEY,id TEXT,pin TEXT,UNIQUE(id,pin))')
    conn.execute('INSERT INTO '+name+'_queue(id,pin) VALUES(?,?)',(instance,''))
    @lru_cache(maxsize=2048)
    def node(key):return model.original[key]
    match,other=('target','source') if direction=='upstream' else ('source','target')
    cursor=0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                from .branch import BranchCancelled
                raise BranchCancelled()
            row=conn.execute('SELECT seq,id,pin FROM '+name+'_queue WHERE seq>? ORDER BY seq LIMIT 1',(cursor,)).fetchone()
            if row is None:break
            cursor,current,arrival=row
            pins=allowed_pins(node(current),direction,arrival or None)
            if current==instance and not arrival and seed_pins is not None:pins=set(seed_pins)
            for key,peer,pin,local in conn.execute('SELECT id,'+other+','+other+'_pin,'+match+'_pin FROM edges WHERE '+match+'=?',(current,)):
                if pins is not None and local not in pins:continue
                conn.execute('INSERT OR IGNORE INTO '+name+' VALUES(?)',(key,))
                conn.execute('INSERT OR IGNORE INTO '+name+'_queue(id,pin) VALUES(?,?)',
                             (peer,pin if node(peer).get('kind') in ('wire','subsystem') else ''))
        return SQLSelection(model.store,'SELECT id FROM '+name,temporary=name)
    except BaseException:
        conn.execute('DROP TABLE '+name)
        raise
    finally:
        node.cache_clear()
        conn.execute('DROP TABLE '+name+'_queue')


def trim_wire(node, inputs, outputs, keep_all=False):
    if node.get('kind')!='wire' or keep_all:return dict(node)
    pairs=[c for c in node.get('internal_connections',[]) if c['input'] in inputs or c['output'] in outputs]
    inputs=set(inputs)|{c['input'] for c in pairs}
    outputs=set(outputs)|{c['output'] for c in pairs if c['output'] in outputs or not outputs}
    result=dict(node,inputs=[p for p in node['inputs'] if p in inputs],outputs=[p for p in output_ports(node) if p in outputs],
                internal_connections=[c for c in pairs if c['input'] in inputs and c['output'] in outputs])
    for field,pins in (('input_components',inputs),('output_components',outputs)):
        if field in node:result[field]={p:c for p,c in node[field].items() if p in pins}
    components=sorted(set(result.get('input_components',{}).values())|set(result.get('output_components',{}).values()))
    if components:result.update(components=components,component=components[0])
    return result


def internal_pairs(node):
    return [(c['input'], c['output']) for c in node.get('internal_connections', [])
            if c.get('status') == 'verified']


def allowed_pins(node, direction, arrival=None):
    if arrival is None:
        return None
    if node.get('kind') == 'wire':
        if direction == 'upstream':
            return {a for a, b in internal_pairs(node) if b == arrival}
        return {b for a, b in internal_pairs(node) if a == arrival}
    if node.get('kind') == 'subsystem':
        return set() if direction == 'downstream' else {arrival}
    return None


def focused_pins(node, direction):
    pin=node.get('_focus_pin')
    if node.get('kind')!='wire' or not pin:return None
    if direction=='upstream':
        return {pin} if pin in node['inputs'] else allowed_pins(node,direction,pin)
    return {pin} if pin in output_ports(node) else allowed_pins(node,direction,pin)


def walk_edges(model, instance, direction, seed_pins=None, cancel=None):
    """Yield exact physical edges, with (instance, arrival port) visited keys."""
    match, other = ('target', 'source') if direction == 'upstream' else ('source', 'target')
    adjacency = model.incoming if direction == 'upstream' else model.outgoing
    queue, seen, emitted = deque([(instance, None)]), set(), set()
    while queue:
        if cancel is not None and cancel.is_set():
            from .branch import BranchCancelled
            raise BranchCancelled()
        current, arrival = queue.popleft()
        key = (current, arrival)
        if key in seen:continue
        seen.add(key)
        node = model.original[current]
        pins = allowed_pins(node, direction, arrival)
        if current == instance and arrival is None and seed_pins is not None:
            pins = set(seed_pins)
        for edge in adjacency[current]:
            if pins is not None and edge[match+'_pin'] not in pins:continue
            if edge['id'] not in emitted:
                emitted.add(edge['id'])
                yield edge
            peer = model.original[edge[other]]
            pin = edge[other+'_pin'] if peer.get('kind') in ('wire', 'subsystem') else None
            queue.append((edge[other], pin))


def topology_key(node, pin):
    return (node['id'], pin if node.get('kind') in ('wire', 'subsystem') else None)


def topology(nodes, edges):
    """Directed terminal graph used for real cycles, not the drawing layout."""
    outgoing, incoming = {}, {}
    def add(a, b=None):
        outgoing.setdefault(a, set());incoming.setdefault(a, set())
        if b is not None:
            outgoing.setdefault(b, set());incoming.setdefault(b, set())
            outgoing[a].add(b);incoming[b].add(a)
    for node in nodes.values():
        for pin in node['inputs'] + output_ports(node):add(topology_key(node, pin))
        for a, b in internal_pairs(node):add(topology_key(node, a), topology_key(node, b))
    for edge in edges:
        add(topology_key(nodes[edge['source']], edge['source_pin']), topology_key(nodes[edge['target']], edge['target_pin']))
    return outgoing, incoming
