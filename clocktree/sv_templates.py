"""One user-authored SV template expansion per real terminal input endpoint.

The planner uses disk tables; templates are substitutions, never executed.
No unknown connectivity, bit ordering, or ambiguous register is invented.
"""
from collections import defaultdict,deque
from contextlib import closing,contextmanager
import hashlib
import copy
import json
import os
from pathlib import Path
import re
import signal
import sqlite3
import time
import uuid

from .register_expr import Expressions,MappingError
from .register_worker import RegisterWorker
from .storage import write_graph,IndexedGraph,ResultStore,ARRAY_KEYS
from .ui_state import design_key
from .layer_reset import branch_reset_binding,without_resets
from .sv_template_text import ROLES,CLOCK_VARS,variables,render,template_tokens
from .profiles import output_ports

SIGNAL=re.compile(r'[A-Za-z_$][A-Za-z0-9_$]*(?:\[-?\d+(?::-?\d+)?\])*(?:\.[A-Za-z_$][A-Za-z0-9_$]*(?:\[-?\d+(?::-?\d+)?\])*)*\Z')


def identity(*parts):return hashlib.sha256(json.dumps(parts,ensure_ascii=False).encode('utf-8')).hexdigest()[:24]


def architecture_signature(graph):
    if graph.get('summary',{}).get('l1_input_connections'):
        return identity('l1-input-terminal-output-v1',graph.get('config'),graph.get('metadata'),graph.get('summary'))
    wire=any(r.get('kind')=='wire' for layer in graph.get('config',{}).get('layers',[]) for r in layer.get('modules',[]))
    return identity('branch-origin-reset-wire-channels-v3' if wire else 'branch-origin-reset-d-input-v2',graph.get('config'),graph.get('metadata'),graph.get('summary'))


def graph_for_templates(graph,draft):
    """Use edited reset metadata without mutating or copying proven graph arrays."""
    from .session import same_config
    if not same_config(without_resets(graph['config']),without_resets(draft)):
        raise ValueError('当前设计或层次/module 规则与结果不一致，请先重新提取；仅修改复位信号可直接重新分析 SV 分支')
    result=IndexedGraph(graph.store,graph) if isinstance(graph,IndexedGraph) else dict(graph)
    result['config']=copy.deepcopy(graph['config'])
    for old,new in zip(result['config'].get('layers',[]),draft.get('layers',[])):
        old.pop('reset_signal',None)
        if new.get('reset_signal'):old['reset_signal']=new['reset_signal']
    return result


def write_source(path,graph):
    """Private worker input: reference an immutable index without copying it in Tk."""
    if isinstance(graph,IndexedGraph):
        write_graph(path,{'sv_graph_reference':1,'database':str(graph.store.path.resolve()),
                          'header':{k:v for k,v in graph.items() if k not in ARRAY_KEYS}})
    else:write_graph(path,graph)


def read_source(path):
    from .session import read_graph
    # Normal result files may be very large; only inspect our small descriptor.
    if Path(path).stat().st_size<1048576:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if value.get('sv_graph_reference')==1:
            graph=ResultStore(value['database']).graph();graph.update(value['header']);return graph
    return read_graph(path)


def vector_expression(lsb):
    if not lsb or any(not SIGNAL.fullmatch(v) for v in lsb):raise ValueError('寄存器路径不能安全表示为 SV 标识符')
    if len(lsb)==1:return lsb[0]
    parts=[re.fullmatch(r'(.+)\[(-?\d+)\]',v) for v in lsb]
    if all(parts) and len({p.group(1) for p in parts})==1:
        indices=[int(p.group(2)) for p in parts]
        steps={b-a for a,b in zip(indices,indices[1:])}
        if steps in ({1},{-1}):return '{}[{}:{}]'.format(parts[0].group(1),indices[-1],indices[0])
    return '{'+', '.join(reversed(lsb))+'}'


def branch_scope(db,instance,pin=None,table='walk',include_terminal=True):
    """SQLite 3.7 (CentOS 7) has no recursive CTE. Keep the queue on disk."""
    if table not in ('walk','ancestors'):raise ValueError('invalid internal queue')
    if db.execute("SELECT 1 FROM nodes WHERE kind='wire' LIMIT 1").fetchone():
        return wire_branch_scope(db,instance,pin,table,include_terminal)
    db.execute('CREATE TEMP TABLE IF NOT EXISTS '+table+'(seq INTEGER PRIMARY KEY,id TEXT UNIQUE)')
    db.execute('DELETE FROM '+table)
    db.execute('INSERT OR IGNORE INTO '+table+'(id) SELECT source FROM edges WHERE target=?'+(' AND pin=?' if pin is not None else ''),
               (instance,pin) if pin is not None else (instance,))
    cursor=0
    while True:
        row=db.execute('SELECT seq,id FROM '+table+' WHERE seq>? ORDER BY seq LIMIT 1',(cursor,)).fetchone()
        if row is None:break
        cursor=row['seq'];db.execute('INSERT OR IGNORE INTO '+table+'(id) SELECT source FROM edges WHERE target=?',(row['id'],))
    if include_terminal:db.execute('INSERT OR IGNORE INTO '+table+'(id) VALUES(?)',(instance,))


def wire_branch_scope(db,instance,pin,table,include_terminal):
    from functools import lru_cache
    from .connectivity import allowed_pins
    db.execute('CREATE TEMP TABLE IF NOT EXISTS '+table+'(seq INTEGER PRIMARY KEY,id TEXT UNIQUE)')
    db.execute('CREATE TEMP TABLE IF NOT EXISTS '+table+'_inputs(id TEXT,pin TEXT,PRIMARY KEY(id,pin))')
    db.execute('CREATE TEMP TABLE IF NOT EXISTS '+table+'_queue(seq INTEGER PRIMARY KEY,id TEXT,pin TEXT,UNIQUE(id,pin))')
    for suffix in ('','_inputs','_queue'):db.execute('DELETE FROM '+table+suffix)
    @lru_cache(maxsize=2048)
    def node(key):return json.loads(db.execute('SELECT data FROM nodes WHERE id=?',(key,)).fetchone()[0])
    def expand(key,pins):
        if node(key)['kind']=='wire':
            db.executemany('INSERT OR IGNORE INTO '+table+'_inputs VALUES(?,?)',((key,p) for p in (pins if pins is not None else node(key)['inputs'])))
        for row in db.execute('SELECT source,source_pin,pin FROM edges WHERE target=?',(key,)):
            if pins is not None and row['pin'] not in pins:continue
            db.execute('INSERT OR IGNORE INTO '+table+'_queue(id,pin) VALUES(?,?)',(row['source'],row['source_pin'] if node(row['source'])['kind']=='wire' else ''))
    expand(instance,{pin} if pin is not None else None)
    cursor=0
    while True:
        row=db.execute('SELECT * FROM '+table+'_queue WHERE seq>? ORDER BY seq LIMIT 1',(cursor,)).fetchone()
        if row is None:break
        cursor=row['seq'];key=row['id']
        db.execute('INSERT OR IGNORE INTO '+table+'(id) VALUES(?)',(key,))
        expand(key,allowed_pins(node(key),'upstream',row['pin'] or None))
    if include_terminal:db.execute('INSERT OR IGNORE INTO '+table+'(id) VALUES(?)',(instance,))


def database(path):
    conn=sqlite3.connect(str(path));conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA cache_size=-4096');conn.execute('PRAGMA temp_store=FILE');return conn


def l1_control_bindings(db,has_wire,bindings,issues):
    """Defaults cover absent control ports, never failed/ambiguous real jobs."""
    devices=defaultdict(list)
    for row in db.execute('SELECT n.* FROM walk JOIN nodes n ON n.id=walk.id ORDER BY n.position'):
        devices[row['kind']].append(dict(row))
    for kind,maximum in [('mux',1),('div',1),('gate',3)]:
        if len(devices[kind])>maximum:issues.append('分支 {} 数量 {} 超过约定 {}'.format(kind,len(devices[kind]),maximum))
    if has_wire and len(devices['gate'])<=3:
        ancestors={}
        for gate in devices['gate']:
            branch_scope(db,gate['id'],table='ancestors',include_terminal=False)
            ancestors[gate['id']]={r[0] for r in db.execute("SELECT n.id FROM ancestors a JOIN nodes n ON n.id=a.id WHERE n.kind='gate'")}
        devices['gate'].sort(key=lambda g:len(ancestors[g['id']]))
    for upstream,downstream in zip(devices['gate'],devices['gate'][1:]):
        branch_scope(db,downstream['id'],table='ancestors',include_terminal=False)
        if not db.execute('SELECT 1 FROM ancestors WHERE id=?',(upstream['id'],)).fetchone():
            issues.append('门控存在并行关系，不能确定唯一从前到后顺序')
    for i,gate in enumerate(devices['gate'][:3]):
        if (json.loads(gate['data']).get('aux_ports') or {}).get('gt'):
            bindings['var_gt'+str(i)]={'instance':gate['id']}
    bindings['var_gt']=bindings['var_gt0']
    for variable,kind,role in [('var_pllsel','mux','pll_sel'),('var_div','div','div'),('var_swdone','div','sw_done')]:
        if len(devices[kind])==1 and (json.loads(devices[kind][0]['data']).get('aux_ports') or {}).get(role):
            bindings[variable]={'instance':devices[kind][0]['id']}


@contextmanager
def connection(path,existing=None):
    if existing is not None:yield existing
    else:
        with closing(database(path)) as db:yield db


def add_candidates(db,registers):
    """Bit order comes from the completed formal-port job, never string sort."""
    with closing(database(Path(registers)/'registers.sqlite')) as source:
        for job in source.execute('SELECT * FROM jobs ORDER BY id'):
            seed=json.loads(job['seed']);summary=json.loads(job['summary'])
            instance,pin,role=seed['instance'],seed['pin'],seed['role']
            bit_names=[v[len(instance)+1:] for v in summary.get('bits',[])]
            choices=defaultdict(set);relations=set();bad=[];data_inputs=defaultdict(set)
            for row in source.execute('SELECT * FROM results WHERE job=?',(job['id'],)):
                if row['status']!='register':bad.append(row['status']+': '+row['reason']);continue
                choices[row['bit']].add(row['endpoint']);relations.add(row['relation'])
                if role=='sw_done' and row['side']=='D':
                    data_inputs[(row['bit'],row['endpoint'])].add(json.loads(row['detail']).get('data_input') or row['endpoint'])
            vectors=[];incomplete=[]
            if job['state']!='complete' or not bit_names or any(not choices[b] for b in bit_names):bad.append('端口尚无完整逐位寄存器映射')
            elif all(len(choices[b])==1 for b in bit_names):vectors=[[next(iter(choices[b])) for b in bit_names]]
            elif len(bit_names)==1:vectors=[[name] for name in sorted(choices[bit_names[0]])]
            else:
                # Group fanout vectors only when each destination register has
                # exactly one proven bit for every formal bit. Do not build a
                # Cartesian product that invents a register bus.
                groups=defaultdict(lambda:defaultdict(set))
                for bit in bit_names:
                    for endpoint in choices[bit]:groups[re.sub(r'\[-?\d+\]$','',endpoint)][bit].add(endpoint)
                for group in groups.values():
                    if all(len(group[b])==1 for b in bit_names):vectors.append([next(iter(group[b])) for b in bit_names])
                    else:incomplete.extend(sorted({v for values in group.values() for v in values}))
                if not vectors:bad.append('多位多目的映射无法归并为完整寄存器向量；已追到：'+', '.join(incomplete))
            if bad:
                db.execute('INSERT INTO problems VALUES(?,?,?,?)',(instance,role,pin,'; '.join(sorted(set(bad)))));continue
            if incomplete:
                db.execute('INSERT INTO problems VALUES(?,?,?,?)',(instance,role,pin,'另有未覆盖全部端口 bit 的寄存器候选：'+', '.join(incomplete)))
            for vector in vectors:
                mapped=vector;capture=''
                if role=='sw_done':
                    # Group by the real destination first: two registers fed
                    # by the same D net remain two choices, never one success.
                    inputs=[data_inputs.get((bit,target),{target}) for bit,target in zip(bit_names,vector)]
                    if any(len(values)!=1 for values in inputs):
                        db.execute('INSERT INTO problems VALUES(?,?,?,?)',(instance,role,pin,'同一接收位存在多个时序数据输入：'+', '.join(sorted({v for values in inputs for v in values}))));continue
                    mapped=[next(iter(values)) for values in inputs]
                try:
                    expression=vector_expression(mapped)
                    if role=='sw_done':capture=vector_expression(vector)
                except ValueError as error:
                    db.execute('INSERT INTO problems VALUES(?,?,?,?)',(instance,role,pin,str(error)));continue
                certainty='direct' if relations=={'direct'} else ','.join(sorted(relations))
                if len({re.sub(r'\[-?\d+\]$','',v) for v in vector})>1:
                    certainty+=';multiple_registers'
                key=identity(instance,role,pin,vector,mapped,certainty)
                db.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?,?,?,?,?,?)',
                           (key,instance,role,pin,expression,certainty,json.dumps(mapped),capture))


def prepare(graph,registers,directory,resolve_connections=True):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'plan.sqlite'
    if path.exists():raise ValueError('SV 分支目录已存在，请使用新目录')
    if graph.get('tree_type',graph['config'].get('tree_type','clock'))!='clock':raise ValueError('SV 分支模板当前用于时钟树')
    manifest=json.loads((Path(registers)/'manifest.json').read_text(encoding='utf-8')) if registers else {'request':{'config':graph['config']}}
    if design_key(manifest['request']['config'])!=design_key(graph['config']):raise ValueError('寄存器结果与当前架构的设计入口不一致')
    worker=None
    previous=signal.getsignal(signal.SIGTERM)
    def cancel(signum,frame):raise KeyboardInterrupt('SV 分支分析已取消')
    signal.signal(signal.SIGTERM,cancel)
    last_report=[None,0.];status_warning=[False]
    def report(stage,message,context):
        now=time.monotonic()
        if stage!=last_report[0] or now-last_report[1]>=.25:
            try:write_graph(directory/'status.json',{'stage':stage,'message':message,'context':context})
            except PermissionError:
                # HGFS can briefly deny replacement while Windows reads this
                # advisory status file. Never discard branch data for a UI tick.
                if not status_warning[0]:
                    print('SV status refresh temporarily denied; continuing analysis. See plan.sqlite / process log.',flush=True)
                    status_warning[0]=True
                return
            last_report[:]=[stage,now]
    resolved={}
    def connection(node,pin):
        nonlocal worker
        inst=node['id'];binding=(node.get('port_connections') or {}).get(pin,{})
        name=binding.get('full_name','')
        if binding.get('status')=='connected' and binding.get('coverage')!='whole_port' and SIGNAL.fullmatch(name):return {'value':name}
        if not resolve_connections:return {'error':'缺少精确例化连接名，需要 elab 端口读取：'+inst+'.'+pin}
        if worker is None:
            work=directory/'connection-worker';work.mkdir()
            worker=RegisterWorker(graph['config'],work,float(graph['config'].get('memory_limit_gib',60)),report)
        base=re.sub(r'\[-?\d+\]$','',pin);key=(inst,base)
        if key not in resolved:
            if len(resolved)>=1024:resolved.clear()
            try:
                port=list(worker.query('port',inst,base))[0];expr=Expressions(())
                expected='npiOutput' if pin in output_ports(node) else 'npiInput'
                if port.get('direction')!=expected:raise MappingError('端口方向不符合 '+expected)
                low=expr.names(port['low']);high=expr.names(port['high'])
                if len(low)!=len(high):raise MappingError('端口位宽不一致')
                resolved[key]=dict(zip(low,high))
            except (ValueError,IndexError) as error:resolved[key]={'error':str(error)}
        lookup=resolved[key]
        if inst+'.'+pin in lookup:name=lookup[inst+'.'+pin]
        elif len(lookup)==1 and 'error' not in lookup:name=next(iter(lookup.values()))
        else:return {'error':inst+'.'+pin+' 无法唯一确定输入信号：'+lookup.get('error','请检查逐位连接')}
        return {'value':name} if SIGNAL.fullmatch(name) else {'error':'不能安全表示为 SV 信号路径：'+name}
    try:
        with closing(database(path)) as db:
            db.executescript('''
             CREATE TABLE nodes(id TEXT PRIMARY KEY,kind TEXT,terminal INTEGER,position INTEGER,data TEXT);
             CREATE TABLE edges(source TEXT,target TEXT,pin TEXT,source_pin TEXT);
             CREATE INDEX node_kind ON nodes(kind);
             CREATE INDEX edge_target ON edges(target,pin);
             CREATE INDEX edge_source ON edges(source);
             CREATE TABLE candidates(id TEXT PRIMARY KEY,instance TEXT,role TEXT,pin TEXT,expression TEXT,certainty TEXT,bits TEXT,capture_expression TEXT);
             CREATE INDEX candidate_instance ON candidates(instance,role);
             CREATE TABLE problems(instance TEXT,role TEXT,pin TEXT,reason TEXT);
             CREATE INDEX problem_instance ON problems(instance,role);
             CREATE TABLE branches(id TEXT PRIMARY KEY,seq INTEGER,instance TEXT,pin TEXT,clkip TEXT,mode TEXT,issues TEXT,bindings TEXT);
             CREATE INDEX branch_sequence ON branches(seq);
             CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            ''')
            outgoing=defaultdict(list);degree={}
            for n in graph['nodes']:
                small={k:n.get(k) for k in ('id','kind','layer','inputs','output','port_connections','aux_ports','outputs','internal_connections','terminal_inputs')}
                small['l1_inputs']={p:{k:v for k,v in value.items() if k!='evidence'} for p,value in n.get('l1_inputs',{}).items()}
                if n.get('kind')=='wire':
                    small['internal_connections']=[{k:c[k] for k in ('input','output','status')} for c in n.get('internal_connections',[])]
                db.execute('INSERT INTO nodes VALUES(?,?,?,?,?)',(n['id'],n['kind'],int(bool(n.get('terminal'))),0,json.dumps(small)))
                degree[n['id']]=0
            for edge in graph['edges']:
                db.execute('INSERT INTO edges VALUES(?,?,?,?)',(edge['source'],edge['target'],edge['target_pin'],edge.get('source_pin','')))
                outgoing[edge['source']].append(edge['target']);degree[edge['target']]=degree.get(edge['target'],0)+1
            pending=deque(n for n,d in degree.items() if not d)
            position=0
            while pending:
                node=pending.popleft()
                position+=1;db.execute('UPDATE nodes SET position=? WHERE id=?',(position,node))
                for target in outgoing.pop(node,[]):
                    degree[target]-=1
                    if not degree[target]:pending.append(target)
            cyclic={n for n,d in degree.items() if d};del degree,outgoing,pending
            has_wire=db.execute("SELECT 1 FROM nodes WHERE kind='wire' LIMIT 1").fetchone() is not None
            if has_wire:cyclic={key for d in graph.get('diagnostics',[]) if d.get('code')=='cycle_or_cycle_downstream' for key in d.get('instances',[])}
            report('registers','读取逐位寄存器映射',{})
            if registers:add_candidates(db,registers)
            complete=bool(graph.get('summary',{}).get('connectivity_complete'))
            seq=0
            for row in db.execute('SELECT * FROM nodes WHERE terminal=1 ORDER BY id'):
                node=json.loads(row['data']);inst=node['id']
                for pin in dict.fromkeys((node.get('terminal_inputs') if node['kind']=='wire' else node.get('inputs')) or []):
                    seq+=1;issues=[];bindings={'var_clkip':connection(node,pin)}
                    clkip=bindings['var_clkip'].get('value','')
                    if not clkip:issues.append('$var_clkip 未解析：'+bindings['var_clkip'].get('error',''))
                    if not complete:issues.append('当前架构连接不完整，不能保证覆盖全部分支')
                    if inst in cyclic:issues.append('上游包含组合环路')
                    scope_pin=None if graph.get('summary',{}).get('l1_input_connections') and node['kind'] not in ('subsystem','wire') and len(node.get('inputs') or [])>1 else pin
                    branch_scope(db,inst,scope_pin)
                    roots=[dict(r) for r in db.execute('''SELECT n.* FROM walk JOIN nodes n ON n.id=walk.id
                        WHERE n.id!=? AND NOT EXISTS(SELECT 1 FROM edges WHERE target=n.id)''',(inst,))]
                    if has_wire:
                        roots=[r for r in roots if r['kind']!='wire']
                        roots.extend(dict(r) for r in db.execute('''SELECT DISTINCT n.* FROM walk_inputs p JOIN nodes n ON n.id=p.id
                            WHERE NOT EXISTS(SELECT 1 FROM edges e WHERE e.target=p.id AND e.pin=p.pin)'''))
                    if not roots:roots=[dict(row)] if not db.execute('SELECT 1 FROM edges WHERE target=? AND pin=?',(inst,pin)).fetchone() else []
                    bindings['var_rst']=branch_reset_binding([json.loads(root['data']) for root in roots],graph['config'])
                    # L1 devices retain the existing controlled-tree semantics.
                    # A later-layer entry with no configured upstream device is
                    # the new bypass case, proven at the real L1 input interface.
                    direct=[];root_pins=[]
                    for source_row in db.execute('SELECT n.data FROM walk w JOIN nodes n ON n.id=w.id'):
                        start=json.loads(source_row['data'])
                        pins=[scope_pin] if start['id']==inst and scope_pin is not None else start.get('inputs') or []
                        if has_wire and start['kind']=='wire':
                            pins=[r[0] for r in db.execute('SELECT pin FROM walk_inputs WHERE id=?',(start['id'],))]
                        pins=[p for p in pins if not db.execute('SELECT 1 FROM edges WHERE target=? AND pin=?',(start['id'],p)).fetchone()]
                        root_pins.extend((start,p) for p in pins)
                        direct.extend(start.get('l1_inputs',{}).get(p) for p in pins if (start.get('layer') or 1)>1 and start.get('l1_inputs',{}).get(p))
                    if direct:
                        signals={value['signal'] for value in direct}
                        bindings['_l1']={'signals':sorted(signals),'status':'verified_boundary','terminal_rule':'output_if_present_else_input'}
                        if len(signals)!=1 or len(direct)!=len(root_pins):
                            issues.append('L1 直达来源不唯一或与普通器件起点混合：'+', '.join(sorted(signals)))
                        for var in ('var_pllsel','var_swdone','var_gt','var_gt0','var_gt1','var_gt2','var_pll1','var_pll2','var_pll3'):
                            bindings[var]={'value':"1'b1",'origin':'l1_input_default'}
                        bindings['var_div']={'value':'0','origin':'l1_input_default'}
                        l1_control_bindings(db,has_wire,bindings,issues)
                        bindings['var_pll0']={'value':next(iter(signals)),'origin':'l1_input'} if len(signals)==1 else {'error':'L1 输入来源不唯一'}
                        terminals=output_ports(node)
                        if node['kind']=='wire':
                            terminals=[c['output'] for c in node.get('internal_connections',[]) if c['input']==pin and c['status']=='verified' and
                                       not db.execute('SELECT 1 FROM edges WHERE source=? AND source_pin=?',(inst,c['output'])).fetchone()]
                        terminals=terminals or [pin]
                        for end_pin in terminals:
                            final=dict(bindings,var_clkip=connection(node,end_pin))
                            final_issues=[value for value in issues if not value.startswith('$var_clkip 未解析')]
                            value=final['var_clkip'].get('value','')
                            if not value:final_issues.append('$var_clkip 未解析：'+final['var_clkip'].get('error',''))
                            identifier=identity(inst,'l1',end_pin)
                            previous_branch=db.execute('SELECT bindings,issues FROM branches WHERE id=?',(identifier,)).fetchone()
                            if previous_branch:
                                seq-=1
                                if json.loads(previous_branch['bindings']).get('var_pll0')!=final['var_pll0']:
                                    old=json.loads(previous_branch['issues']);old.append('同一末端输出追到多个 L1 输入，不能选择唯一 $var_pll0')
                                    db.execute('UPDATE branches SET issues=? WHERE id=?',(json.dumps(old,ensure_ascii=False),identifier))
                            else:
                                db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?,?,?)',(identifier,seq,inst,end_pin,value,'without_mux',json.dumps(final_issues,ensure_ascii=False),json.dumps(final,ensure_ascii=False)))
                            if end_pin!=terminals[-1]:seq+=1
                        report('branches','分析 L1 直达末端',{'branch':seq,'instance':inst,'pin':pin})
                        continue
                    modes={r['kind']=='mux' for r in roots}
                    mode='with_mux' if modes=={True} else 'without_mux' if modes=={False} else 'mixed'
                    devices=defaultdict(list)
                    for device in db.execute('SELECT n.* FROM walk JOIN nodes n ON n.id=walk.id ORDER BY n.position'):devices[device['kind']].append(dict(device))
                    if has_wire and len(devices['gate'])<=3:
                        gate_ancestors={}
                        for gate in devices['gate']:
                            branch_scope(db,gate['id'],table='ancestors',include_terminal=False)
                            gate_ancestors[gate['id']]={r[0] for r in db.execute("SELECT n.id FROM ancestors a JOIN nodes n ON n.id=a.id WHERE n.kind='gate'")}
                        devices['gate'].sort(key=lambda g:len(gate_ancestors[g['id']]))
                    for kind,maximum in [('mux',1),('div',1),('gate',3)]:
                        if len(devices[kind])>maximum:issues.append('分支 {} 数量 {} 超过约定 {}'.format(kind,len(devices[kind]),maximum))
                    for upstream,downstream in zip(devices['gate'],devices['gate'][1:]):
                        branch_scope(db,downstream['id'],table='ancestors',include_terminal=False)
                        if not db.execute('SELECT 1 FROM ancestors WHERE id=?',(upstream['id'],)).fetchone():issues.append('门控存在并行关系，不能确定唯一从前到后顺序')
                    for i in range(3):bindings['var_gt'+str(i)]={'instance':devices['gate'][i]['id']} if i<len(devices['gate']) else {'value':"1'b1"}
                    bindings['var_gt']=bindings['var_gt0']
                    for var,kind in [('var_pllsel','mux'),('var_div','div'),('var_swdone','div')]:
                        bindings[var]={'instance':devices[kind][0]['id']} if len(devices[kind])==1 else {'error':'分支没有唯一的 '+kind+' 器件'}
                    for i in range(4):bindings['var_pll'+str(i)]={'value':"1'b1"}
                    if len(devices['mux'])==1:
                        mux=json.loads(devices['mux'][0]['data']);inputs=mux.get('inputs') or []
                        if len(inputs)>4:issues.append('选择器关注输入超过 4 个')
                        for i,mux_pin in enumerate(inputs[:4]):bindings['var_pll'+str(i)]=connection(mux,mux_pin)
                    elif not devices['mux']:
                        starts=[]
                        for root in roots:
                            start=json.loads(root['data']);start_pins=[pin] if start['id']==inst else start.get('inputs') or []
                            if start['kind']=='wire':start_pins=[r[0] for r in db.execute('SELECT pin FROM walk_inputs WHERE id=? AND NOT EXISTS(SELECT 1 FROM edges WHERE target=? AND pin=walk_inputs.pin)',(start['id'],start['id']))]
                            starts.extend(connection(start,p) for p in start_pins)
                        unique={r['value'] for r in starts if r.get('value')}
                        bindings['var_pll0']={'value':next(iter(unique))} if len(unique)==1 and all(r.get('value') for r in starts) else {'error':'无选择器分支没有唯一且已解析的起始输入信号'}
                    db.execute('INSERT INTO branches VALUES(?,?,?,?,?,?,?,?)',(identity(inst,pin),seq,inst,pin,clkip,mode,json.dumps(issues,ensure_ascii=False),json.dumps(bindings,ensure_ascii=False)))
                    report('branches','分析末端分支',{'branch':seq,'instance':inst,'pin':pin})
            db.execute('INSERT INTO meta VALUES(?,?)',('source',json.dumps({'architecture_signature':architecture_signature(graph),'config':graph['config'],'registers':str(Path(registers).resolve()) if registers else '', 'register_fingerprint':manifest.get('fingerprint'), 'dft_zero':manifest['request'].get('dft_zero',[]),'branches':seq},ensure_ascii=False)))
            db.commit()
            report('finished','分支分析完成',{'branches':seq})
        return SVPlan(path)
    finally:
        if worker:worker.close()
        signal.signal(signal.SIGTERM,previous)


class SVPlan:
    def __init__(self,path):
        self.path=Path(path)
        if not self.path.is_file():raise ValueError('分支计划不存在：'+str(path))
        self._candidate_search=None
    def source(self):
        with closing(database(self.path)) as db:return json.loads(db.execute("SELECT value FROM meta WHERE key='source'").fetchone()[0])
    def branches(self,search='',offset=0,limit=100,_db=None):
        with connection(self.path,_db) as db:
            clause=' WHERE instr(lower(instance||pin||clkip||mode),lower(?))>0'
            count=db.execute('SELECT count(*) FROM branches'+clause,(search,)).fetchone()[0]
            return count,[dict(r) for r in db.execute('SELECT * FROM branches'+clause+' ORDER BY seq LIMIT ? OFFSET ?',(search,limit,offset))]
    def candidates(self,branch,role,search='',offset=0,limit=100,_db=None):
        var=role;role=ROLES.get(var,role);instance=json.loads(branch.get('bindings','{}')).get(var,{}).get('instance')
        if var in ROLES and not instance:return []
        with connection(self.path,_db) as db:
            if not instance:branch_scope(db,branch['instance'],branch['pin'])
            if self._candidate_search is None:
                columns={r['name'] for r in db.execute('PRAGMA table_info(candidates)')}
                self._candidate_search='c.instance||c.expression'+("||coalesce(c.capture_expression,'')" if 'capture_expression' in columns else '')
            sql=('SELECT c.* FROM candidates c' if instance else 'SELECT c.* FROM walk JOIN candidates c ON c.instance=walk.id')+' WHERE c.role=? AND instr(lower('+self._candidate_search+'),lower(?))>0'
            if instance:sql+=' AND c.instance=?'
            return [dict(r) for r in db.execute(sql+' ORDER BY c.instance,c.pin,c.expression LIMIT ? OFFSET ?',(role,search)+((instance,) if instance else ())+(limit,offset))]
    def resolve(self,branch,templates,selection=None,_db=None):
        selection=selection or {};issues=json.loads(branch['issues']);mode=selection.get('mode',branch['mode'])
        template=templates.get(mode,'')
        if mode not in ('with_mux','without_mux'):issues.append('多个起点类型混合，请明确选择模板类型')
        if not template.strip():issues.append('该分支的模板为空')
        try:needed=variables(template)
        except ValueError as error:needed=set();issues.append(str(error))
        bindings=json.loads(branch.get('bindings','{}'));values={'var_clkip':branch['clkip']};chosen={}
        with connection(self.path,_db) as db:
            for var in sorted(needed):
                binding=bindings.get(var,{})
                if var=='var_rst' and binding.get('basis')!='branch_origin':
                    issues.append('$var_rst：历史计划未包含起始层复位配置，请填写最初器件所属层复位信号后重新分析');continue
                if 'value' in binding:values[var]=binding['value'];continue
                if binding.get('error'):issues.append('$'+var+'：'+binding['error']);continue
                if var not in ROLES:issues.append('$'+var+' 未解析');continue
                role=ROLES[var];selected=selection.get(var)
                instance=binding.get('instance','')
                rows=list(db.execute('SELECT * FROM candidates WHERE role=? AND instance=?'+(' AND id=?' if selected else '')+' LIMIT 2',(role,instance)+((selected,) if selected else ())))
                problems=list(db.execute('SELECT * FROM problems WHERE role=? AND instance=?',(role,instance)))
                if len(rows)!=1:
                    reasons='; '.join(p['reason'] for p in problems[:3])
                    issues.append('$'+var+(' 所选映射不属于当前分支' if selected else ' 存在多组映射，请选择' if len(rows)>1 else ' 缺少寄存器映射')+('：'+reasons if reasons else ''));continue
                row=rows[0]
                if not selected and (row['certainty']!='direct' or problems):
                    issues.append('$'+var+' 含非直接映射、跨寄存器或其他未解析候选，请核对并明确选择'+('：'+'; '.join(p['reason'] for p in problems) if problems else ''));continue
                # Stale architecture rules must not pick an old auxiliary pin.
                node=db.execute('SELECT data FROM nodes WHERE id=?',(row['instance'],)).fetchone()
                actual=json.loads(node['data']).get('aux_ports') or {}
                if actual.get(role)!=row['pin']:issues.append('$'+var+' 与当前器件辅助配置不一致');continue
                values[var]=row['expression'];chosen[var]=dict(row)
        result={'id':branch['id'],'instance':branch['instance'],'pin':branch['pin'],'clkip':branch['clkip'],
                'mode':mode,'values':values,'selected':chosen,'issues':issues,'ready':not issues,'reset':bindings.get('var_rst',{}),
                'l1_input':bindings.get('_l1')}
        result['code']=render(template,values) if not issues else ''
        return result
    def export(self,templates,path,selections=None):
        if not isinstance(templates,dict) or any(not isinstance(templates.get(k,''),str) for k in ('with_mux','without_mux')):
            raise ValueError('模板必须是包含 with_mux / without_mux 文本的 JSON 对象')
        if selections is not None and (not isinstance(selections,dict) or any(not isinstance(v,dict) for v in selections.values())):
            raise ValueError('selections 必须按分支 ID 保存变量选择对象')
        selections=selections or {};path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True)
        if path==self.path.resolve():raise ValueError('输出路径不能覆盖计划数据库')
        temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');report_path=path.with_name(path.name+'.mapping.json')
        temp_report=temporary.with_name(temporary.name+'.json');count=0;generated=0;skipped=0;failed=[]
        previous=signal.getsignal(signal.SIGTERM)
        def cancel(signum,frame):raise KeyboardInterrupt('SV 导出已取消')
        signal.signal(signal.SIGTERM,cancel)
        try:
            with temporary.open('w',encoding='utf-8',newline='\n') as stream,temp_report.open('w',encoding='utf-8') as evidence,closing(database(self.path)) as db:
                stream.write('// Generated by ClockTree7 from saved elaborated connectivity and register evidence.\n')
                source=json.loads(db.execute("SELECT value FROM meta WHERE key='source'").fetchone()[0])
                stream.write('// DFT zero assumptions: '+', '.join(source.get('dft_zero',[])).replace('\n',' ').replace('\r',' ')+'\n')
                evidence.write('{"schema_version":1,"source":');json.dump(source,evidence,ensure_ascii=False);evidence.write(',"branches":[')
                offset=0
                while True:
                    total,branches=self.branches(offset=offset,limit=100,_db=db)
                    for branch in branches:
                        result=self.resolve(branch,templates,selections.get(branch['id']),_db=db)
                        if count:evidence.write(',')
                        record={k:v for k,v in result.items() if k!='code'}
                        encoded=json.dumps(record,ensure_ascii=False)
                        evidence.write(encoded[:-1]+',"available_choices":{')
                        first_var=True
                        if result['issues']:
                            for var in sorted({t.name for t in template_tokens(templates.get(result['mode'],''))} & set(ROLES)):
                                if not first_var:evidence.write(',')
                                first_var=False;evidence.write(json.dumps(var)+':[');choice_offset=0;first_choice=True
                                while True:
                                    choices=self.candidates(branch,var,offset=choice_offset,limit=100,_db=db)
                                    for choice in choices:
                                        if not first_choice:evidence.write(',')
                                        first_choice=False;json.dump(choice,evidence,ensure_ascii=False)
                                    choice_offset+=len(choices)
                                    if len(choices)<100:break
                                evidence.write(']')
                        evidence.write('}}')
                        count+=1
                        if result['issues']:
                            skipped+=1
                            if len(failed)<20:failed.append(result['instance']+'.'+result['pin']+'：'+'; '.join(result['issues']))
                        else:
                            generated+=1
                            stream.write('\n// CT7 branch {} | {}.{} | {}\n'.format(branch['id'],branch['instance'],branch['pin'],branch['clkip']))
                            stream.write(result['code']);stream.write('\n')
                    offset+=len(branches)
                    if offset>=total:break
                evidence.write('],"block_count":'+str(generated)+',"skipped_count":'+str(skipped)+',"terminal_count":'+str(count)+'}')
            if not count:raise ValueError('没有已确认的时钟树末端输入')
            if generated:os.replace(str(temporary),str(path))
            os.replace(str(temp_report),str(report_path))
            return {'blocks':generated,'skipped':skipped,'terminals':count,'sv':str(path),'sv_written':bool(generated),'mapping':str(report_path),'issues':failed}
        finally:
            signal.signal(signal.SIGTERM,previous)
            for leftover in (temporary,temp_report):
                if leftover.exists():leftover.unlink()
