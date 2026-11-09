"""Auxiliary ports to proven sequential endpoints, independent of tree edges."""
import copy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import time
from contextlib import closing

from .auxiliary import aux_entries
from .config import resolve_design_paths, validate_config
from .register_expr import Expressions, MappingError, number, sequential_proof, resize
from .register_worker import RegisterWorker, WorkerFault, MemoryFault, PortDeadline, QueryError
from .memory import GIB
from .recovery import RunLease, input_snapshot
from .register_progress import progress_reporter
from .storage import write_graph


SCHEMA=1
LABELS={'register':'已确认寄存器','constant':'常量终点','unresolved':'未解析','boundary':'范围边界',
        'cycle':'组合环路','pruned':'条件分支已排除','timeout':'端口超时','skipped':'当前例化已跳过'}


def seeds_from_graph(graph):
    for node in graph.get('nodes',[]):
        for role,pin,direction in aux_entries(node):
            yield {'instance':node['id'],'module':node['module'],'kind':node['kind'],
                   'layer':node.get('layer'),'role':role,'pin':pin,'direction':'load' if direction=='output' else 'driver'}


def contains(root,name):return name==root or name.startswith(root+'.') or name.startswith(root+'[')


def relation(first,second):
    if 'conditional' in (first,second):return 'conditional'
    if 'logic' in (first,second):return 'logic'
    if second=='constant':return first
    return 'inverted' if (first=='inverted')!=(second=='inverted') else 'direct'


def assignments(row):
    kind=row.get('statement',{}).get('type')
    if kind=='npiPort':
        if row.get('direction')=='npiInput':return row.get('low'),row.get('high')
        if row.get('direction')=='npiOutput':return row.get('high'),row.get('low')
        raise MappingError('unsupported_inout_port')
    if kind in ('npiAssignment','npiContAssign'):return row.get('lhs'),row.get('rhs')
    raise MappingError('unsupported_statement:'+str(kind))


class Flow:
    def __init__(self,worker,root,assumptions):
        self.worker=worker;self.root=root;self.constants={};self.active_constants=set()
        self.expr=Expressions(assumptions,self.constant)

    def constant(self,name):
        if name in self.constants:return self.constants[name]
        if name in self.active_constants or len(self.active_constants)>64 or not contains(self.root,name):return None
        self.active_constants.add(name)
        try:
            result=[]
            for row in self.worker.query('query',name,'driver'):
                if sequential_proof(row):return None
                if not self.enabled(row)[0]:continue
                lhs,rhs=assignments(row);names=self.expr.names(lhs)
                if name not in names:return None
                bits=self.expr.vector(rhs,len(names))
                result.append(bits[names.index(name)].value)
            value=result[0] if result and result[0] is not None and all(v==result[0] for v in result) else None
            # Bound the in-memory convenience cache. The authoritative NPI
            # replies remain on disk and can be read again without native work.
            if len(self.constants)>=4096:self.constants.clear()
            self.constants[name]=value
            return value
        except (MappingError,QueryError):return None
        finally:self.active_constants.remove(name)

    def enabled(self,row):
        conditional=False
        for parent in row.get('ancestors',[]):
            kind=parent['type']
            if kind in ('npiIf','npiIfElse'):
                branch=parent.get('branch')
                if branch not in ('true','false'):raise MappingError('unknown_procedural_branch')
                value=self.expr.condition(parent.get('condition')).value
                if value is not None and bool(value)!=(branch=='true'):return False,conditional
                conditional=conditional or value is None
            elif kind in ('npiCase','npiCaseItem','npiFor','npiWhile','npiRepeat','npiForeachStmt','npiWait','npiDelayControl'):
                raise MappingError('unsupported_procedural_control:'+kind)
            elif kind=='npiInitial':raise MappingError('initial_is_not_sequential_storage')
        return True,conditional

    def latch_proof(self,name):
        """Prove a hold path using all writers, not a reg/logic declaration.

        Common incomplete if guards across every writer prove retained state.
        Case-based/complex latches remain explicit unresolved boundaries.
        """
        common=None;process=None;count=0
        try:
            for row in self.worker.query('query',name,'driver'):
                if row['statement']['type']!='npiAssignment' or sequential_proof(row):return None
                chain=row.get('ancestors',[])
                procs=[p for p in chain if p['type'] in ('npiAlways','npiInitial')]
                if not procs or procs[-1]['type']!='npiAlways':return None
                if any(p['type'] in ('npiCase','npiCaseItem','npiFor','npiWhile','npiRepeat','npiDelayControl') for p in chain):return None
                key=json.dumps(procs[-1],sort_keys=True)
                if process is not None and key!=process:return None
                process=key;count+=1
                guards=set()
                for parent in chain:
                    if parent['type'] in ('npiIf','npiIfElse') and parent.get('branch') in ('true','false'):
                        # Keep only runtime gates; a constant true condition
                        # does not prove a hold state.
                        condition=parent.get('condition') or {}
                        try:known=Expressions(()).condition(condition).value
                        except MappingError:known=None
                        if known is None:
                            guards.add(json.dumps(parent,sort_keys=True))
                common=guards if common is None else common & guards
            if count and common:
                return {'kind':'latch','basis':'all_writers_share_conditional_hold','process':json.loads(process),
                        'hold_guards':[json.loads(g) for g in sorted(common)]}
        except (MappingError,QueryError):return None
        return None

    def actions(self,name,direction):
        emitted=False;pruned=False;writers=0
        try:
            latch=self.latch_proof(name) if direction=='driver' else None
            for row in self.worker.query('query',name,direction):
                self.worker.check()
                try:
                    active=False
                    for action in self.row_actions(name,direction,row,latch):
                        if action.get('status')=='pruned':pruned=True;continue
                        emitted=active=True;yield action
                    if active and direction=='driver' and row['statement']['type'] in ('npiAssignment','npiContAssign') and not (sequential_proof(row) or latch):writers+=1
                except (MappingError,QueryError) as error:
                    # An unsupported fanout must not suppress valid siblings.
                    emitted=True;yield {'status':'unresolved','endpoint':name,'reason':str(error),'evidence':row}
            if writers>1:yield {'status':'unresolved','endpoint':name,'reason':'multiple_active_combinational_writers'}
            if not emitted:yield {'status':'pruned' if pruned else 'unresolved','endpoint':name,
                                   'reason':'no_active_data_path' if pruned else 'no_driver_or_load'}
        except (MappingError,QueryError) as error:
            yield {'status':'unresolved','endpoint':name,'reason':str(error)}

    def row_actions(self,name,direction,row,latch):
        if not contains(self.root,row.get('scope','')):
            yield {'status':'boundary','reason':'query_scope_outside_root','endpoint':name,'evidence':row};return
        kind=row['statement']['type'];proof=sequential_proof(row)
        if direction=='load' and kind=='npiEventControl':
            # Clock/sensitivity-list references are not register data inputs.
            yield {'status':'pruned'};return
        if kind=='npiAssignment' and direction=='driver' and (proof or latch):
            if name not in self.expr.names(row.get('lhs')):raise MappingError('driver_lhs_bit_mismatch')
            yield {'status':'register','endpoint':name,'side':'Q','proof':proof or latch,'evidence':row};return
        if kind=='npiPrimTerm':
            primitive=row.get('primitive')
            if primitive=='npiSeqPrim' and direction=='driver':
                yield {'status':'register','endpoint':name,'side':'Q','proof':{'kind':'sequential_udp','basis':'npiSeqPrim','statement':row['statement']},'evidence':row};return
            if primitive in ('npiBufPrim','npiNotPrim'):
                for expr in row.get('signals',[]):
                    for target in self.expr.names(expr):yield {'next':target,'relation':'inverted' if primitive=='npiNotPrim' else 'direct','evidence':row}
                return
        enabled,conditional=self.enabled(row)
        if not enabled:yield {'status':'pruned'};return
        lhs,rhs=assignments(row)
        if not rhs:
            yield {'status':'boundary','reason':'unconnected_or_top_port','endpoint':name,'evidence':row};return
        names=self.expr.names(lhs);bits=self.expr.vector(rhs,len(names))
        if direction=='driver':
            if name not in names:raise MappingError('driver_lhs_bit_mismatch')
            bit=bits[names.index(name)]
            if not bit.signals:
                yield {'status':'constant' if bit.value is not None else 'unresolved',
                       'reason':str(bit.value) if bit.value is not None else 'unknown_constant','endpoint':'','evidence':row}
            for target in bit.signals:yield {'next':target,'relation':'conditional' if conditional else bit.relation,'evidence':row}
        else:
            found=False
            for target,bit in zip(names,bits):
                if name not in bit.signals:continue
                found=True;target_proof=proof
                if kind=='npiAssignment' and not target_proof:target_proof=self.latch_proof(target)
                # Enable/reset guards affect capture, not the identity of D.
                mode=bit.relation if target_proof else ('conditional' if conditional else bit.relation)
                if target_proof:
                    yield {'status':'register','endpoint':target,'side':'D','relation':mode,'proof':target_proof,
                           'data_expression':rhs.get('text',''),'evidence':row}
                else:yield {'next':target,'relation':mode,'evidence':row}
            if not found:yield {'status':'pruned'}


def create_database(path):
    db=sqlite3.connect(str(path))
    db.execute('CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, seed TEXT, state TEXT, summary TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS paths (id INTEGER PRIMARY KEY, job INTEGER, parent INTEGER, signal TEXT, evidence TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY, job INTEGER, bit TEXT, signal TEXT, relation TEXT, path INTEGER, UNIQUE(job,bit,signal,relation))')
    db.execute('CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY, job INTEGER, bit TEXT, endpoint TEXT, side TEXT, relation TEXT, status TEXT, reason TEXT, path INTEGER, detail TEXT, UNIQUE(job,bit,endpoint,side,relation,status,reason))')
    db.execute('CREATE INDEX IF NOT EXISTS results_job ON results(job)')
    db.execute('CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state,id)')
    db.commit();return db


def add_result(db,job,bit,mode,path,action):
    db.execute('INSERT OR IGNORE INTO results VALUES (NULL,?,?,?,?,?,?,?,?,?)',
               (job,bit,action.get('endpoint',''),action.get('side',''),mode,action['status'],action.get('reason',''),path,
                json.dumps({k:v for k,v in action.items() if k!='evidence'},ensure_ascii=False)))


def path_contains(db,path,name):
    while path:
        row=db.execute('SELECT parent,signal FROM paths WHERE id=?',(path,)).fetchone()
        if not row:return False
        if row[1]==name:return True
        path=row[0]
    return False


def trace_job(db,job,seed,flow,report):
    base=seed['pin'];selected=None
    match=re.fullmatch(r'(.+)\[(-?\d+)\]',base)
    if match:base,selected=match.group(1),int(match.group(2))
    rows=list(flow.worker.query('port',seed['instance'],base))
    if len(rows)!=1:raise QueryError('端口查询没有返回唯一结果')
    port=rows[0]
    expected='npiInput' if seed['direction']=='driver' else 'npiOutput'
    if port.get('direction')!=expected:raise QueryError('辅助端口方向不符：'+str(port.get('direction')))
    names=flow.expr.names(port.get('low'))
    if selected is not None:names=[name for name in names if name.endswith('[{}]'.format(selected))]
    if not names:raise QueryError('辅助端口 bit 不存在')
    for name in names:
        bit=name[len(seed['instance'])+1:]
        path=db.execute('INSERT INTO paths VALUES(NULL,?,NULL,?,?)',(job,name,'{}')).lastrowid
        db.execute('INSERT OR IGNORE INTO queue VALUES(NULL,?,?,?,?,?)',(job,bit,name,'direct',path))
    cursor=0
    while True:
        row=db.execute('SELECT id,bit,signal,relation,path FROM queue WHERE job=? AND id>? ORDER BY id LIMIT 1',(job,cursor)).fetchone()
        if row is None:break
        cursor,bit,name,mode,path=row
        flow.worker.check()
        if not contains(flow.root,name):
            add_result(db,job,bit,mode,path,{'status':'boundary','endpoint':name,'reason':'outside_trace_root'});continue
        report('trace','逐位寄存器追踪',{'job':job,'instance':seed['instance'],'pin':seed['pin'],'bit':bit,'signal':name,'direction':seed['direction']})
        for action in flow.actions(name,seed['direction']):
            next_mode=relation(mode,action.get('relation','direct'))
            newpath=db.execute('INSERT INTO paths VALUES(NULL,?,?,?,?)',(job,path,action.get('next',action.get('endpoint',name)),
                                      json.dumps(action.get('evidence',{}),ensure_ascii=False))).lastrowid
            if 'next' in action:
                target=action['next']
                if path_contains(db,path,target):
                    add_result(db,job,bit,next_mode,newpath,{'status':'cycle','endpoint':target,'reason':'combinational_cycle'})
                else:db.execute('INSERT OR IGNORE INTO queue VALUES(NULL,?,?,?,?,?)',(job,bit,target,next_mode,newpath))
            else:add_result(db,job,bit,next_mode,newpath,action)
    for name in names:
        bit=name[len(seed['instance'])+1:]
        if not db.execute('SELECT 1 FROM results WHERE job=? AND bit=?',(job,bit)).fetchone():
            add_result(db,job,bit,'direct',None,{'status':'unresolved','reason':'no_sequential_endpoint','endpoint':name})
    counts=dict(db.execute('SELECT status,count(*) FROM results WHERE job=? GROUP BY status',(job,)))
    summary={'bits':names,'counts':counts,'assumptions_applied':sorted(flow.expr.used)}
    db.execute('DELETE FROM queue WHERE job=?',(job,))
    db.execute('UPDATE jobs SET state=?,summary=? WHERE id=?',('complete',json.dumps(summary,ensure_ascii=False),job))
    db.commit()


def fingerprint(request):
    root=Path(__file__).resolve().parents[1]
    digest=hashlib.sha256()
    for name in ('clocktree/register_trace.py','clocktree/register_expr.py','clocktree/register_worker.py','clocktree/register_progress.py','npi/register_worker.tcl','npi/extract.tcl'):
        digest.update((root/name).read_bytes())
    return {'engine':digest.hexdigest(),'request':hashlib.sha256(json.dumps(request,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest(),
            'inputs':input_snapshot(request['config'])}


def run(request_path,output,resume=False):
    request_path=Path(request_path).resolve();directory=Path(output).resolve()
    request=json.loads(request_path.read_text(encoding='utf-8-sig'))
    request['config']=resolve_design_paths(copy.deepcopy(request['config']),request_path)
    validate_config(request['config'])
    config=request['config'];root=request.get('trace_root') or config.get('trace_scope') or config['design']['top']
    if not contains(config['design']['top'],root):raise ValueError('寄存器追踪根必须位于设计顶层内')
    assumptions=request.get('dft_zero',['dft_mode'])
    if not isinstance(assumptions,list) or any(not isinstance(v,str) or not v.strip() for v in assumptions):raise ValueError('dft_zero 需要实际信号名列表')
    memory=float(request.get('memory_limit_gib',config.get('memory_limit_gib',60)))
    timeout=float(request.get('port_trace_timeout_s',config.get('port_trace_timeout_s',0)))
    if not all(math.isfinite(v) and v>=0 for v in (memory,timeout)):raise ValueError('内存/端口超时必须为非负有限值')
    request.update(trace_root=root,dft_zero=assumptions,memory_limit_gib=memory,port_trace_timeout_s=timeout)
    seeds=request.get('seeds',[])
    if not seeds:raise ValueError('当前结果没有配置辅助端口')
    for seed in seeds:
        if seed.get('role') not in ('pll_sel','div','gt','sw_done') or seed.get('direction')!=('load' if seed.get('role')=='sw_done' else 'driver'):
            raise ValueError('无效辅助端口角色或追踪方向')
        if not contains(root,seed['instance']):raise ValueError('追踪根未包含全部辅助实例：'+seed['instance'])
    with RunLease(directory):
        signature=fingerprint(request);manifest=directory/'manifest.json'
        if manifest.exists():
            saved=json.loads(manifest.read_text(encoding='utf-8'))
            if not resume:raise ValueError('运行目录已有任务，请选择新目录或使用 --resume')
            if not signature['inputs'].get('valid'):raise ValueError('无法校验设计输入变更，不能复用断点；请新建追踪')
            if saved.get('fingerprint')!=signature:raise ValueError('输入、规则或实现已变化，不能复用旧断点；请新建追踪')
        elif resume:raise ValueError('目录中不存在可恢复的寄存器任务')
        else:write_graph(manifest,{'schema_version':SCHEMA,'request':request,'fingerprint':signature})
        db=create_database(directory/'registers.sqlite')
        if not db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():
            db.executemany('INSERT INTO jobs VALUES (?, ?, ?, ?)',((i+1,json.dumps(seed,ensure_ascii=False),'pending','{}') for i,seed in enumerate(seeds)));db.commit()
        events=(directory/'events.jsonl').open('a',encoding='utf-8')
        report=progress_reporter(directory,events)
        metrics_path=directory/'metrics.json'
        metrics=json.loads(metrics_path.read_text(encoding='utf-8')) if metrics_path.exists() else {}
        worker=None;restarts=metrics.get('verdi_restarts',0);timeouts=metrics.get('timeout_instances',0)
        previous=signal.getsignal(signal.SIGTERM)
        def cancel(signum,frame):raise KeyboardInterrupt('寄存器追踪已取消；已完成端口可恢复')
        signal.signal(signal.SIGTERM,cancel)
        try:
            worker=RegisterWorker(config,directory,memory,report)
            while True:
                pending=db.execute("SELECT id,seed FROM jobs WHERE state!='complete' ORDER BY id LIMIT 1").fetchone()
                if not pending:break
                job,seed_json=pending
                seed=json.loads(seed_json);failures=0
                while True:
                    try:
                        worker.ensure();worker.deadline=time.monotonic()+timeout if timeout else None
                        flow=Flow(worker,root,assumptions)
                        trace_job(db,job,seed,flow,report);worker.deadline=None
                        report('checkpoint','辅助端口已提交断点',{'job':job,**seed})
                        if memory and worker.rss>=memory*GIB*.9:
                            worker.stop();restarts+=1;report('rotate','接近内存阈值，提交后轮换 Verdi',{'job':job})
                        break
                    except PortDeadline as error:
                        db.rollback();worker.stop();timeouts+=1;restarts+=1
                        for other,other_json,other_state in db.execute('SELECT id,seed,state FROM jobs').fetchall():
                            other_seed=json.loads(other_json)
                            if other_state!='complete' and other_seed['instance']==seed['instance']:
                                status='timeout' if other==job else 'skipped'
                                add_result(db,other,other_seed['pin'],'direct',None,{'status':status,'reason':str(error)})
                                db.execute('UPDATE jobs SET state=?,summary=? WHERE id=?',('complete',json.dumps({'counts':{status:1}}),other))
                        db.commit();report('timeout',str(error),{'job':job,**seed});break
                    except MemoryFault as error:
                        db.rollback();worker.stop();failures+=1;restarts+=1
                        report('retry',str(error),{'job':job,'attempt':failures,**seed})
                        if failures>=3:raise WorkerFault('同一辅助端口连续 3 次内存重试无进展，已保留断点')
                    except (QueryError,MappingError) as error:
                        db.rollback();worker.deadline=None
                        add_result(db,job,seed['pin'],'direct',None,{'status':'unresolved','reason':str(error)})
                        db.execute('UPDATE jobs SET state=?,summary=? WHERE id=?',('complete',json.dumps({'counts':{'unresolved':1}}),job));db.commit();break
                # A timeout may already have committed later ports of this
                # same instance; never trace them from a stale loop snapshot.
                write_graph(metrics_path,{'verdi_restarts':restarts,'timeout_instances':timeouts,'peak_rss_bytes':max(metrics.get('peak_rss_bytes',0),worker.peak)})
            counts=dict(db.execute('SELECT status,count(*) FROM results GROUP BY status'))
            total=db.execute('SELECT count(*) FROM jobs').fetchone()[0]
            attention=sum(counts.get(k,0) for k in ('unresolved','boundary','cycle','timeout','skipped'))
            conditional=db.execute("SELECT count(*) FROM results WHERE relation='conditional'").fetchone()[0]
            traced_bits=db.execute('SELECT count(*) FROM (SELECT DISTINCT job,bit FROM results)').fetchone()[0]
            register_bits=db.execute("SELECT count(*) FROM (SELECT DISTINCT job,bit FROM results WHERE status='register')").fetchone()[0]
            result={'schema_version':SCHEMA,'database':'registers.sqlite','config':config,'trace_root':root,'dft_zero':assumptions,
                    'summary':{'ports':total,'counts':counts,'conditional_mappings':conditional,'finished':True,
                               'traced_bits':traced_bits,'register_bits':register_bits,'all_bits_have_register':register_bits==traced_bits and not attention,
                               'complete':not bool(attention or conditional),'verdi_restarts':restarts,'timeout_instances':timeouts,
                               'peak_rss_gib':round(max(metrics.get('peak_rss_bytes',0),worker.peak)/GIB,3)},'operation':'auxiliary-register-trace','sv_generated':False}
            write_graph(directory/'register-trace.json',result)
            report('finished','寄存器追踪完成',result['summary'])
            return result
        except BaseException as error:
            db.rollback();report('interrupted','任务未完成：'+str(error),{'recoverable':True});raise
        finally:
            write_graph(metrics_path,{'verdi_restarts':restarts,'timeout_instances':timeouts,'peak_rss_bytes':max(metrics.get('peak_rss_bytes',0),worker.peak if worker else 0)})
            if worker:worker.close()
            events.close();db.close();signal.signal(signal.SIGTERM,previous)


class RegisterIndex:
    columns=('实例','辅助功能','实际端口','端口 bit','寄存器信号 / 边界','端点方向','映射','状态','说明')
    def __init__(self,directory):self.directory=Path(directory);self.path=self.directory/'registers.sqlite'
    def query(self,search='',offset=0,limit=100):
        search=next((key for key,label in LABELS.items() if search==label),search)
        with closing(sqlite3.connect(str(self.path))) as db:
            where=" WHERE instr(lower(j.seed||r.bit||r.endpoint||r.status||r.reason),lower(?))>0"
            total=db.execute('SELECT count(*) FROM results r JOIN jobs j ON j.id=r.job'+where,(search,)).fetchone()[0]
            rows=[]
            for row in db.execute('SELECT r.id,j.seed,r.bit,r.endpoint,r.side,r.relation,r.status,r.reason FROM results r JOIN jobs j ON j.id=r.job'+where+' ORDER BY r.id LIMIT ? OFFSET ?',(search,limit,offset)):
                identifier,seed,*values=row;seed=json.loads(seed)
                rows.append((identifier,seed['instance'],seed['role'],seed['pin'])+tuple(values))
        return total,rows
    def detail(self,identifier):
        with closing(sqlite3.connect(str(self.path))) as db:
            row=db.execute('SELECT r.path,r.detail,j.seed,j.summary FROM results r JOIN jobs j ON j.id=r.job WHERE r.id=?',(identifier,)).fetchone()
            if not row:return {}
            path,detail,seed,summary=row;result=json.loads(detail);steps=[]
            result['port']=json.loads(seed);result['port_summary']=json.loads(summary)
            while path:
                entry=db.execute('SELECT parent,signal,evidence FROM paths WHERE id=?',(path,)).fetchone()
                if not entry:break
                path,name,evidence=entry;steps.append({'signal':name,'evidence':json.loads(evidence)})
            result['steps']=list(reversed(steps));return result
    def export(self,path):
        total,offset=0,0
        with open(str(path),'w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(self.columns)
            while True:
                total,rows=self.query(offset=offset,limit=1000)
                for row in rows:
                    values=list(row[1:]);values[-2]=LABELS.get(values[-2],values[-2])
                    writer.writerow(["'"+str(v) if str(v).startswith(('=','+','-','@')) else v for v in values])
                offset+=len(rows)
                if offset>=total:break
        return total

    def export_json(self,path):
        """Self-contained evidence with shared path prefixes, streamed to disk."""
        with closing(sqlite3.connect(str(self.path))) as db,open(str(path),'w',encoding='utf-8') as stream:
            manifest=json.loads((self.directory/'manifest.json').read_text(encoding='utf-8'))
            stream.write('{"schema_version":1,"operation":"auxiliary-register-trace","request":')
            json.dump(manifest['request'],stream,ensure_ascii=False)
            for table in ('jobs','results','paths'):
                stream.write(',"'+table+'":[');first=True
                cursor=db.execute('SELECT * FROM '+table+' ORDER BY id')
                columns=[c[0] for c in cursor.description]
                for values in cursor:
                    row=dict(zip(columns,values))
                    for key in ('seed','summary','detail','evidence'):
                        if key in row:row[key]=json.loads(row[key])
                    if not first:stream.write(',')
                    json.dump(row,stream,ensure_ascii=False);first=False
                stream.write(']')
            stream.write('}\n')
