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
    return {pin:value for pin,value in values.items()
            if pin in node.get('inputs',[]) and value.get('status')=='verified'}


def aggregate_entries(model,node):
    """A wide overview uses one honest bundle per scope, expanded on demand."""
    if hasattr(model,'store'):
        conn=model.store.connection()
        if not conn.execute("SELECT 1 FROM sqlite_temp_master WHERE name='ct7_l1_entries'").fetchone():
            conn.execute('CREATE TEMP TABLE ct7_l1_entries(instance TEXT,scope TEXT,pin TEXT,layer INTEGER,kind TEXT,PRIMARY KEY(instance,pin))')
            for data, in conn.execute("""SELECT data FROM nodes WHERE instr(data,'"l1_inputs"')>0
                                       OR instr(data,'"input_boundaries"')>0"""):
                item=json.loads(data)
                conn.executemany('INSERT OR IGNORE INTO ct7_l1_entries VALUES(?,?,?,?,?)',
                    ((item['id'],value['scope'],pin,value.get('layer',0),value.get('source_kind','port'))
                     for pin,value in entries(item).items()))
        where,params=model.member_condition(node)
        return list(conn.execute('SELECT scope,layer,kind,COUNT(*) FROM ct7_l1_entries WHERE instance IN (SELECT id FROM nodes WHERE '+where+') GROUP BY scope,layer,kind',params))
    grouped=defaultdict(int)
    for instance in node['members']:
        for value in entries(model.original[instance]).values():
            grouped[(value['scope'], value.get('layer', 0), value.get('source_kind','port'))] += 1
    return [(*entry, count) for entry, count in sorted(grouped.items())]


def project_sources(model):
    ports,wires={},[]
    for target in list(model.nodes.values()):
        if target.get('aggregate'):
            values=[('输入',{'scope':scope,
                    'port':'{} ×{}'.format('wire' if kind=='wire' else '输入端口',count),'signal':scope,
                    'aggregate_count':count,'layer':layer,'source_kind':kind})
                    for scope,layer,kind,count in aggregate_entries(model,target)]
        else:values=list(entries(target).items())
        for pin,entry in values:
            aggregate=entry.get('aggregate_count',0)
            identifier=key(entry['signal'],target['component'],
                           (target['id'],entry['source_kind']) if aggregate else '')
            explicit = bool(entry.get('layer'))
            layer = entry.get('layer') or 1
            source = ports.setdefault(identifier,dict(id=identifier,path=entry['scope'],
                module='层次边界 · elab 已确认' if explicit else 'L1 层次输入 · elab 已确认',
                kind='boundary_input' if explicit else 'l1_input',virtual=True,layer=layer,component=target['component'],
                inputs=[],output=entry['port'],root=True,terminal=False,input_entry=entry,port_connections={entry['port']:{'status':'connected',
                'expression':entry['port'],'full_name':entry['signal'],'coverage':'port'}}))
            if not explicit:
                source.setdefault('l1_entry', entry)
            wires.append(dict(id=key(identifier,target['id'],pin),source=identifier,source_pin=entry['port'],
                target=target['id'],target_pin=pin,layer_delta=target['layer']-layer,input_boundary=True,
                l1_boundary=not explicit, boundary_layer=layer,
                aggregate_count=aggregate,evidence={'driver':entry.get('evidence',[])}))
    model.l1_nodes,model.l1_edges=ports,{edge['id']:edge for edge in wires}
    model.nodes.update(ports);model.edges.extend(wires)


def lookup_node(model,identifier):
    shown=model.nodes.get(identifier)
    return shown if shown and (shown.get('virtual') or shown.get('aggregate')) else model.original[identifier]


def source_title(node):
    if node['kind'] == 'l1_input':
        return 'L1 输入端口'
    title = 'wire 边界' if node['input_entry'].get('source_kind') == 'wire' else '输入边界'
    return 'L{} · {}'.format(node['layer'], title)


def source_basis(node):
    if node['kind'] == 'l1_input':
        return '输入反查确认信号经此端口进入 L1。'
    if node['input_entry'].get('source_kind') == 'wire':
        return '输入反查到达该层直接声明的 wire 信号并停止。'
    return '输入反查到达配置的 RTL 输入口并停止。'


def describe(node):
    value=node['input_entry']
    return [('显示类型',source_title(node)), ('RTL 层次',value['scope']),
            ('连接信号',value['port']),('完整信号',value['signal']),
            ('连接依据',source_basis(node)+
             ('当前为聚合显示，展开目标 RTL 范围后查看各端口。' if value.get('aggregate_count') else ''))]


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
