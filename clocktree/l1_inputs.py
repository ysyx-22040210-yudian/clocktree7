"""Proven hierarchy inputs for the canvas and navigation."""
from collections import defaultdict
import hashlib
import json
from collections.abc import Sequence


INPUT_SOURCE_KINDS = ('l1_input', 'boundary_input')


def key(*parts):
    return 'l1:'+hashlib.sha256('\0'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:24]


def entries(node):
    values = dict(node.get('l1_inputs', {}))
    values.update(node.get('input_boundaries', {}))
    if not values:
        return {}
    pins = set(node.get('inputs', []))
    return {pin:value for pin,value in values.items()
            if pin in pins and value.get('status')=='verified' and value.get('signal')
            and value.get('scope') and value.get('port')}


def verified_table(conn, persist=False):
    """Older read-only indexes get the same lookup in their private connection."""
    name = 'ct7_verified_inputs'
    if conn.execute("SELECT 1 FROM main.sqlite_master WHERE name=?", (name,)).fetchone():
        return 'main.' + name
    schema = 'main' if persist else 'temp'
    table = schema + '.' + name
    if not persist and conn.execute("SELECT 1 FROM sqlite_temp_master WHERE name=?", (name,)).fetchone():
        return table
    conn.execute('SAVEPOINT index_verified_inputs')
    try:
        conn.execute('CREATE TABLE '+table+'(instance TEXT,pin TEXT,signal TEXT,scope TEXT,port TEXT,'
                     'layer INTEGER,kind TEXT,PRIMARY KEY(instance,pin))')
        nodes = conn.execute("""SELECT data FROM main.nodes WHERE instr(data,'"l1_inputs"')>0
                                OR instr(data,'"input_boundaries"')>0""")
        for data, in nodes:
            node = json.loads(data)
            conn.executemany('INSERT INTO '+table+' VALUES(?,?,?,?,?,?,?)', (
                (node['id'], pin, value['signal'], value['scope'], value['port'],
                 value.get('layer', 0), value.get('source_kind', 'port')) for pin,value in entries(node).items()))
        conn.execute('CREATE INDEX '+schema+'.ct7_verified_signal ON '+name+'(signal,instance,pin)')
    except BaseException:
        conn.execute('ROLLBACK TO index_verified_inputs')
        raise
    finally:
        conn.execute('RELEASE index_verified_inputs')
    return table


def aggregate_entries(model,node):
    """Keep a concrete signal visible when every load in a group shares it."""
    if hasattr(model,'store'):
        conn=model.store.connection()
        where,params=model.member_condition(node)
        return conn.execute('SELECT scope,layer,kind,COUNT(*),CASE WHEN MIN(signal)=MAX(signal) THEN MIN(signal) END,MIN(port) '
                            'FROM '+verified_table(conn)+' WHERE instance IN (SELECT id FROM nodes WHERE '+where+') '
                            'GROUP BY scope,layer,kind',params)
    grouped={}
    for instance in node['members']:
        for value in entries(model.original[instance]).values():
            group=(value['scope'],value.get('layer',0),value.get('source_kind','port'))
            count,signal,port=grouped.get(group,(0,value['signal'],value['port']))
            grouped[group]=(count+1,signal if signal==value['signal'] else None,min(port,value['port']))
    return [group + counts for group,counts in sorted(grouped.items())]


def group_signals(model, target, entry, search='', offset=0, limit=101):
    """One page of concrete signal names belonging to the clicked overview group."""
    group=(entry['scope'], entry.get('layer',0), entry.get('source_kind','port'))
    if hasattr(model,'store'):
        conn=model.store.connection()
        where,params=model.member_condition(target)
        return list(conn.execute('SELECT signal,MIN(port),COUNT(*) FROM '+verified_table(conn)+
            ' WHERE instance IN (SELECT id FROM nodes WHERE '+where+') AND scope=? AND layer=? AND kind=? '
            'AND instr(lower(signal),lower(?))>0 GROUP BY signal ORDER BY signal LIMIT ? OFFSET ?',
            params+group+(search,limit,offset)))
    counts={}
    for instance in target['members']:
        for value in entries(model.original[instance]).values():
            if (value['scope'],value.get('layer',0),value.get('source_kind','port'))==group and search.lower() in value['signal'].lower():
                signal=value['signal']
                port,count=counts.get(signal,(value['port'],0))
                counts[signal]=(min(port,value['port']),count+1)
    return [(sig,)+counts[sig] for sig in sorted(counts)[offset:offset+limit]]


def source_node(entry, component, identifier):
    explicit=bool(entry.get('layer'))
    port=entry['port']
    node=dict(id=identifier,path=entry['scope'],module='层次边界 · elab 已确认' if explicit else 'L1 层次输入 · elab 已确认',
              kind='boundary_input' if explicit else 'l1_input',virtual=True,layer=entry.get('layer') or 1,component=component,
              inputs=[],output=port,root=True,terminal=False,input_entry=entry,port_connections={port:{
                  'status':'connected','expression':port,'full_name':entry['signal'],'coverage':'port'}})
    if not explicit:node['l1_entry']=entry
    return node


def project_sources(model):
    ports,wires={},[]
    branch=model.graph.get('branch_view',{})
    for target in list(model.nodes.values()):
        if target.get('aggregate'):
            values=[]
            for scope,layer,kind,count,signal,port in aggregate_entries(model,target):
                entry=dict(scope=scope,layer=layer,source_kind=kind,signal=signal or scope,port=port)
                if signal is None:
                    entry.update(aggregate_count=count,port='{} ×{}'.format('wire' if kind=='wire' else '输入端口',count))
                values.append(('输入',entry,count))
        else:values=((pin,entry,0) for pin,entry in entries(target).items())
        for pin,entry,count in values:
            aggregate=bool(entry.get('aggregate_count'))
            identifier=key(entry['signal'],target['component'],
                           (target['id'],entry['source_kind'],entry.get('layer',0)) if aggregate else '')
            if branch.get('signal')==entry['signal'] and branch.get('source_id'):
                identifier=branch['source_id']
            explicit = bool(entry.get('layer'))
            layer = entry.get('layer') or 1
            if identifier not in ports:
                ports[identifier]=source_node(entry,target['component'],identifier)
            if aggregate:ports[identifier]['boundary_group']=target['id']
            wires.append(dict(id=key(identifier,target['id'],pin),source=identifier,source_pin=entry['port'],
                target=target['id'],target_pin=pin,layer_delta=target['layer']-layer,input_boundary=True,
                l1_boundary=not explicit, boundary_layer=layer,
                aggregate_count=count,evidence={'driver':entry.get('evidence',[])}))
    model.l1_nodes,model.l1_edges=ports,{edge['id']:edge for edge in wires}
    model.nodes.update(ports);model.edges.extend(wires)


def lookup_node(model,identifier):
    shown=model.nodes.get(identifier)
    return shown if shown and (shown.get('virtual') or shown.get('aggregate') or shown.get('reference_of')) else model.original[identifier]


def source_title(node):
    if node['kind'] == 'l1_input':
        return 'L1 输入端口'
    title = 'wire 边界' if node['input_entry'].get('source_kind') == 'wire' else '输入边界'
    return 'L{} · {}'.format(node['layer'], title)


def source_basis(node):
    if node['kind'] == 'l1_input':
        return '输入反查确认信号经此端口进入 L1。'
    if node['input_entry'].get('source_kind') == 'wire':
        return '输入反查确认该输入连接到此层直接声明的 wire。'
    return '输入反查确认该输入连接到此层的 RTL 输入口。'


def describe(node):
    value=node['input_entry']
    return [('显示类型',source_title(node)), ('RTL 层次',value['scope']),
            ('连接信号',value['port']),('完整信号',value['signal']),
            ('连接依据',source_basis(node)+
             ('双击可选择该组中的具体信号。' if value.get('aggregate_count') else ''))]


class CombinedEdges(Sequence):
    """Append a small visible boundary list to a bounded SQL-backed sequence."""
    def __init__(self,base,extra):self.base,self.extra=base,extra
    def __len__(self):return len(self.base)+len(self.extra)
    def __getitem__(self,index):
        if isinstance(index,slice):
            start,stop,step=index.indices(len(self))
            return [self[i] for i in range(start,stop,step)]
        if index<0:index+=len(self)
        if not 0<=index<len(self):raise IndexError(index)
        count=len(self.base)
        return self.base[index] if index<count else self.extra[index-count]
    def __iter__(self):
        for item in self.base:yield item
        for item in self.extra:yield item


def neighbours(model,node,direction):
    from .connectivity import focused_pins
    side='target' if direction=='upstream' else 'source'
    pins=focused_pins(node,direction)
    return [e for e in getattr(model,'l1_edges',{}).values() if e[side]==node['id'] and
            (pins is None or e[side+'_pin'] in pins)]


def branch_highlights(model,upstream,seed):
    from .connectivity import allowed_pins,focused_pins
    if not getattr(model,'l1_edges',{}):return set()
    targets={e['target'] for e in model.l1_edges.values()}
    actual={model.nodes[k].get('reference_of',k) for k in targets}
    pins=defaultdict(set);all_inputs=set()
    # Only source ends of upstream paths are relevant. Downstream MUX inputs
    # from other branches must not accidentally join the highlight.
    if hasattr(upstream,'sql'):
        conn=model.store.connection()
        for key_ in actual:
            for pin, in conn.execute('SELECT source_pin FROM edges WHERE source=? AND id IN ('+upstream.sql+')',(key_,)+upstream.params):
                allowed=allowed_pins(model.original[key_],'upstream',pin)
                if allowed is None:all_inputs.add(key_)
                else:pins[key_].update(allowed)
    else:
        for identifier in upstream:
            edge=model.original_edges[identifier];key_=edge['source']
            if key_ not in actual:continue
            allowed=allowed_pins(model.original[key_],'upstream',edge['source_pin'])
            if allowed is None:all_inputs.add(key_)
            else:pins[key_].update(allowed)
    key_=seed.get('reference_of',seed.get('id'))
    chosen=set(seed['inputs']) if seed.get('kind')=='subsystem' else focused_pins(seed,'upstream')
    if chosen is None:all_inputs.add(key_)
    else:pins[key_].update(chosen)
    return {e['id'] for e in model.l1_edges.values() if
            model.nodes[e['target']].get('reference_of',e['target']) in all_inputs or
            e['target_pin'] in pins[model.nodes[e['target']].get('reference_of',e['target'])]}
