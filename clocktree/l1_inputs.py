"""Proven L1 interface entries. Display nodes never count as RTL instances."""
from collections import defaultdict
import hashlib
import json
from collections.abc import Sequence


def key(*parts):
    return 'l1:'+hashlib.sha256('\0'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:24]


def entries(node):
    return {pin:value for pin,value in node.get('l1_inputs',{}).items()
            if pin in node.get('inputs',[]) and value.get('status')=='verified'}


def aggregate_entries(model,node):
    """A wide overview uses one honest bundle per scope, expanded on demand."""
    if hasattr(model,'store'):
        conn=model.store.connection()
        if not conn.execute("SELECT 1 FROM sqlite_temp_master WHERE name='ct7_l1_entries'").fetchone():
            conn.execute('CREATE TEMP TABLE ct7_l1_entries(instance TEXT,scope TEXT,pin TEXT,PRIMARY KEY(instance,pin))')
            for data, in conn.execute("SELECT data FROM nodes WHERE instr(data,'\"l1_inputs\"')>0"):
                item=json.loads(data)
                conn.executemany('INSERT OR IGNORE INTO ct7_l1_entries VALUES(?,?,?)',
                    ((item['id'],value['scope'],pin) for pin,value in entries(item).items()))
        where,params=model.member_condition(node)
        return list(conn.execute('SELECT scope,COUNT(*) FROM ct7_l1_entries WHERE instance IN (SELECT id FROM nodes WHERE '+where+') GROUP BY scope',params))
    grouped=defaultdict(int)
    for instance in node['members']:
        for value in entries(model.original[instance]).values():grouped[value['scope']]+=1
    return sorted(grouped.items())


def project_sources(model):
    ports,wires={},[]
    for target in list(model.nodes.values()):
        if target.get('aggregate'):
            values=[('输入',{'scope':scope,'port':'输入端口 ×{}'.format(count),'signal':scope,
                    'aggregate_count':count}) for scope,count in aggregate_entries(model,target)]
        else:values=list(entries(target).items())
        for pin,entry in values:
            aggregate=entry.get('aggregate_count',0)
            identifier=key(entry['signal'],target['component'],target['id'] if aggregate else '')
            ports.setdefault(identifier,dict(id=identifier,path=entry['scope'],module='L1 层次输入 · elab 已确认',
                kind='l1_input',virtual=True,layer=1,component=target['component'],inputs=[],output=entry['port'],
                root=True,terminal=False,l1_entry=entry,port_connections={entry['port']:{'status':'connected',
                'expression':entry['port'],'full_name':entry['signal'],'coverage':'port'}}))
            wires.append(dict(id=key(identifier,target['id'],pin),source=identifier,source_pin=entry['port'],
                target=target['id'],target_pin=pin,layer_delta=target['layer']-1,l1_boundary=True,
                aggregate_count=aggregate,evidence={'driver':entry.get('evidence',[])}))
    model.l1_nodes,model.l1_edges=ports,{edge['id']:edge for edge in wires}
    model.nodes.update(ports);model.edges.extend(wires)


def lookup_node(model,identifier):
    shown=model.nodes.get(identifier)
    return shown if shown and (shown.get('virtual') or shown.get('aggregate')) else model.original[identifier]


def describe(node):
    value=node['l1_entry']
    return [('显示类型','L1 RTL 层次的真实 input 端口；不是新增器件实例'),
            ('L1 层次',value['scope']),('连接端口',value['port']),('完整信号',value['signal']),
            ('连接依据','目标器件输入 driver trace 已确认到达此 L1 input。'+
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
