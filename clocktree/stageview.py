"""Native Tk execution-stage strip; no graph data or trace paths are cached."""
import tkinter as tk
from tkinter import ttk
import time
from .runtime import STAGES, LABELS, stage_node


class StageView(ttk.Frame):
    def __init__(self,master,family,on_stage):
        super().__init__(master,style='White.TFrame',padding=(14,5,14,6))
        self.family,self.on_stage=family,on_stage
        self.steps={};self.context={};self.current=''
        self.caption=tk.StringVar(value='运行定位 · 等待任务')
        self.item=tk.StringVar(value='各阶段会在执行前记录；异常退出后可查看阶段日志。')
        ttk.Label(self,textvariable=self.caption,style='Small.TLabel').pack(anchor='w')
        self.canvas=tk.Canvas(self,height=47,bg='white',highlightthickness=0)
        self.canvas.pack(fill='x')
        self.canvas.bind('<Configure>',lambda event:self.draw())
        ttk.Label(self,textvariable=self.item,style='Muted.TLabel',anchor='w').pack(fill='x')

    def update_state(self,backend=None,gui=None):
        backend,gui=backend or {},gui or {}
        steps=dict(backend.get('steps',{}))
        current_gui=gui.get('last_event',{}).get('time_ms',0)>=backend.get('started_at_ms',0)
        if current_gui and gui.get('steps',{}).get('display'):steps['display']=gui['steps']['display']
        active=gui if current_gui and gui.get('state')=='running' and stage_node(gui.get('stage'))=='display' else backend
        if not active:active=gui
        self.steps=steps;self.current=stage_node(active.get('stage',''))
        state=active.get('state','ready')
        words={'running':'运行中','restarting':'准备重启','finished':'已完成','attention':'完成，存在诊断',
               'failed':'失败','cancelled':'已取消','interrupted':'未记录正常结束'}
        stage=LABELS.get(active.get('stage'),active.get('stage','等待任务'))
        attempt=backend.get('attempt')
        self.caption.set('运行定位 · {} · {}{}'.format(stage,words.get(state,state),
            ' · Verdi 第 {} 次'.format(attempt) if attempt else ''))
        context=active.get('context') or {}
        if context.get('module') and isinstance(context['module'],dict):context=context['module']
        parts=[]
        if context.get('module'):parts.append('module '+str(context['module']))
        if context.get('scope'):parts.append(str(context['scope']))
        if context.get('instance'):parts.append(str(context['instance']))
        if context.get('pin'):parts.append('端口 '+str(context['pin'])+' '+str(context.get('direction','')))
        if context.get('trace_id'):parts.append('追踪 #'+str(context['trace_id']))
        if 'scanned' in context:
            parts.append('已扫描 {} · 已处理 {} · 待处理 {} · 命中/边界 {}'.format(
                context['scanned'],context.get('processed',0),context.get('pending',0),context.get('results',0)))
            phases={'iterator_open':'打开连接迭代器','iterator_scan':'逐项读取连接',
                    'iterator_done':'迭代完成','primitive_begin':'穿过透明器件',
                    'primitive_end':'透明器件完成','driver_begin':'查询驱动',
                    'driver_end':'驱动查询完成','bind':'读取实际端口连接','complete':'端口完成'}
            if 'cache_hits' in context:parts.append('公共路径复用 {}'.format(context['cache_hits']))
            parts.append(phases.get(context.get('phase'),context.get('phase','')))
            age=max(0,int(time.time()-active.get('last_event',{}).get('time_ms',time.time()*1000)/1000))
            if state=='running' and age>=5:parts.append('距上次进度 {} 秒（持续等待）'.format(age))
        checkpoint=backend.get('checkpoint') or {}
        if checkpoint:parts.append('已提交 {} module / {} 追踪'.format(checkpoint.get('completed_modules',0),checkpoint.get('completed_traces',0)))
        self.item.set((' · '.join(parts) or active.get('detail') or '点击阶段节点查看对应日志')[-175:])
        self.draw()

    def draw(self):
        canvas=self.canvas;canvas.delete('all')
        width=max(500,canvas.winfo_width());step=width/len(STAGES)
        colors={'done':'#26a985','running':'#2868df','failed':'#d95559','interrupted':'#d39a39','pending':'#bbc7d8'}
        for index,(key,label) in enumerate(STAGES):
            x=step*(index+.5);state=self.steps.get(key,'pending');color=colors.get(state,colors['pending'])
            if index<len(STAGES)-1:
                canvas.create_line(x+9,14,x+step-9,14,fill='#dce5f1',width=2)
            tag='stage:'+key
            canvas.create_oval(x-7,7,x+7,21,fill=color,outline=color,tags=(tag,))
            mark='✓' if state=='done' else ('!' if state in ('failed','interrupted') else str(index+1))
            canvas.create_text(x,14,text=mark,fill='white',font=(self.family,7,'bold'),tags=(tag,))
            canvas.create_text(x,35,text=label,fill=color,font=(self.family,8,'bold' if key==self.current else 'normal'),tags=(tag,))
            canvas.tag_bind(tag,'<Button-1>',lambda event,stage=key:self.on_stage(stage))
