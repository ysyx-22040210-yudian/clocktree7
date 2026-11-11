"""Complete issue table, separate counts for problems and affected instances."""
import csv
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from .graph import UNRESOLVED
from .devices import connection_label


LABELS={
    'multiple_l1_inputs':('多个 L1 输入来源','核对多驱动或位映射；不能自动选择其中一个 L1 端口'),
    'wire_connection_unresolved':('穿线内部未解析','核对输出是否由已配置输入直接连接；逻辑运算、常量、时序电路不作直连'),
    'unselected_wire_input':('穿线输入未配置','把实际参与穿线的输入加入器件规则后重新提取'),
    'trace_timeout':('端口超时','检查耗时端口；可调整端口阈值后重跑'),
    'trace_skipped_timeout':('随实例跳过','本例化其他端口超时，当前端口未追踪'),
    'instance_trace_timeout':('实例超时','查看阶段日志中的超时断点'),
    'missing_pin':('缺失端口','核对端口名及该实例参数化接口'),
    'missing_trace':('未执行追踪','查看 worker 日志及已提交断点'),
    'missing_module':('未找到 module','核对直接父层和 module 定义名'),
    'scope_not_found':('层次不存在','核对 elab 中的完整实例路径'),
    'unknown_cell':('未解析逻辑','检查未纳入规则的逻辑或器件'),
    'unsupported_expression':('不支持的表达式','查看原始 NPI 表达式，不应推定为连通'),
    'multiple_drivers':('多驱动','核对该输入的各个实际驱动'),
    'reverse_layer':('逆序层连接','核对层次规则及真实连接'),
    'cycle_or_cycle_downstream':('环路及受影响下游','核对环路；保留全部实际边'),
    'root_outside_L1':('根不在 L1','检查是否遗漏上游结构或层次规则'),
    'fatal':('导入 / 后端失败','打开阶段日志查看失败原因'),
    'elaboration_error':('RTL 编译失败','打开 compiler.log 修复编译错误'),
    'verdi_exit':('Verdi 退出','查看退出码、内存记录及最后完成阶段'),
    'width_requires_bit':('位宽待明确','检查 elab 范围；多维接口请显式指定位'),
    'pin_validation':('端口校验失败','核对端口方向、名称及该实例的实际位宽'),
    'trace_mismatch':('核验方向不一致','查看输入反查和输出负载的原始证据'),
    'target_outside_trace_scope':('实例超出追踪范围','调整端口追踪根层次以包含此实例'),
    'layer_conflict':('实例层级冲突','同一实例只能归属一个传播规则层级'),
    'trace_protocol':('追踪数据不完整','查看 worker 退出原因及端口断点'),
    'module_checkpoint_sequence':('模块断点次序异常','核对原任务实现版本和断点数据'),
    'missing_module_checkpoint':('缺少模块断点','查看查找阶段是否正常提交'),
    'unknown_target':('连接端点不存在','核对实例查找结果与原始追踪记录'),
    'aux_port_missing':('辅助端口不存在','核对该 module 的实际接口名；修正规则后重新提取'),
    'aux_port_direction':('辅助端口方向不符','pll_sel / div / gt 应为输入，sw_done 应为输出'),
    'aux_port_unconnected':('辅助端口未连接','核对 RTL 例化绑定；此端口仅用于展示'),
    'aux_connection_unavailable':('辅助端口未取得','查看 NPI 端口绑定信息或重新提取；不推测信号'),
}
COLUMNS=('category','severity','layer','module','instance','pin','signal','reason','hint')
HEADINGS=('类别','严重程度','层级','Module','实例','端口','实际信号','原因','处理提示')


class IssueIndex:
    def __init__(self,graph,extra=()):
        self.temporary=tempfile.TemporaryDirectory(prefix='ct7-issues-')
        self.path=Path(self.temporary.name)/'issues.sqlite3'
        self.graph=graph
        self.extra=extra

    def build(self,cancel=None):
        from .indexed_view import RecordMap
        from .storage import IndexedGraph
        nodes=RecordMap(self.graph,'nodes') if isinstance(self.graph,IndexedGraph) else {n['id']:n for n in self.graph['nodes']}
        edges=RecordMap(self.graph,'edges') if isinstance(self.graph,IndexedGraph) else {e['id']:e for e in self.graph['edges']}
        conn=sqlite3.connect(str(self.path))
        try:
            conn.execute('CREATE TABLE issues(id INTEGER PRIMARY KEY,category TEXT,severity TEXT,layer TEXT,module TEXT,instance TEXT,pin TEXT,signal TEXT,reason TEXT,hint TEXT,raw TEXT)')
            conn.execute('CREATE TABLE objects(issue INTEGER,instance TEXT,pin TEXT)')
            conn.execute('CREATE INDEX object_issue ON objects(issue)')
            sources=((row,False) for row in self.graph.get('diagnostics',[]))
            from itertools import chain
            sources=chain(sources,((row,True) for row in self.graph.get('boundaries',[]) if row.get('status') in UNRESOLVED),
                          ((row,False) for row in self.extra))
            for row,boundary in sources:
                if cancel and cancel.is_set():return
                code=row.get('status') if boundary else row.get('code','diagnostic')
                label,hint=LABELS.get(code,('其他诊断（{}）'.format(code),'查看阶段日志和原始证据'))
                refs=[(row['instance'],row.get('pin',''))] if row.get('instance') else []
                refs.extend((key,'') for key in row.get('instances',[]))
                if row.get('edge') and row['edge'] in edges:
                    edge=edges[row['edge']]
                    refs.extend([(edge['source'],edge['source_pin']),(edge['target'],edge['target_pin'])])
                detail=row.get('detail',{})
                if isinstance(detail,dict) and detail.get('instance'):refs.append((detail['instance'],detail.get('pin','')))
                refs=list(dict.fromkeys(refs))
                instance,pin=refs[0] if refs else ('','')
                node=nodes[instance] if instance in nodes else {}
                severity='错误' if code in ('fatal','elaboration_error','verdi_exit','missing_module','scope_not_found','missing_pin','missing_trace') else '警告'
                status=row.get('status','')
                reason=row.get('message') or row.get('reason') or LABELS.get(status,(status or label,''))[0]
                values=(label,severity,str(node.get('layer',row.get('layer',''))),node.get('module',row.get('module','')),
                        instance,pin,connection_label(node,pin) if pin else '',str(reason),hint)
                # Keep references to bulky paths outside the issue rows.
                raw={k:v for k,v in row.items() if k not in ('path','instances')}
                raw['associated_objects']=len(refs)
                seq=conn.execute('INSERT INTO issues VALUES (NULL,?,?,?,?,?,?,?,?,?,?)',values+(json.dumps(raw,ensure_ascii=False,default=str),)).lastrowid
                conn.executemany('INSERT INTO objects VALUES (?,?,?)',((seq,key,pin) for key,pin in refs))
            conn.commit()
        finally:conn.close()

    def connect(self):return sqlite3.connect(str(self.path))

    def query(self,category='',search='',offset=0,limit=100):
        where,params=self.condition(category,search)
        with closing(self.connect()) as conn:
            total=conn.execute('SELECT COUNT(*) FROM issues'+where,params).fetchone()[0]
            rows=conn.execute('SELECT * FROM issues'+where+' ORDER BY id LIMIT ? OFFSET ?',params+(limit,offset)).fetchall()
            affected=conn.execute('SELECT COUNT(DISTINCT instance) FROM objects WHERE issue IN (SELECT id FROM issues'+where+')',params).fetchone()[0]
        return total,affected,rows

    def condition(self,category,search):
        clauses,params=[],[]
        if category:clauses.append('category=?');params.append(category)
        if search:
            clauses.append('instr(lower(category||module||instance||pin||signal||reason||hint),lower(?))>0 OR id IN (SELECT issue FROM objects WHERE instr(lower(instance),lower(?))>0)')
            params.extend([search,search])
        return (' WHERE '+' AND '.join('('+clause+')' for clause in clauses) if clauses else ''),tuple(params)

    def categories(self):
        with closing(self.connect()) as conn:return [r[0] for r in conn.execute('SELECT DISTINCT category FROM issues ORDER BY category')]

    def objects(self,issue,offset=0,limit=100):
        with closing(self.connect()) as conn:return conn.execute('SELECT instance,pin FROM objects WHERE issue=? LIMIT ? OFFSET ?',(issue,limit,offset)).fetchall()

    def export(self,path,category='',search=''):
        where,params=self.condition(category,search)
        count=0
        with closing(self.connect()) as conn,open(str(path),'w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(('问题编号',)+HEADINGS+('关联对象数量',))
            for row in conn.execute('SELECT * FROM issues'+where+' ORDER BY id',params):
                # Prevent spreadsheet formula interpretation of RTL identifiers.
                values=[("'"+v if isinstance(v,str) and v[:1] in ('=','+','-','@') else v) for v in row[:-1]]
                writer.writerow(values+[json.loads(row[-1])['associated_objects']]);count+=1
        return count

    def close(self):self.temporary.cleanup()
