"""Measure a native Tk workspace and exercise user-visible controls at a given size."""
import argparse
import json
from pathlib import Path
import sys
import time
import traceback


def settle(app):
    deadline = time.monotonic() + 30
    start = time.monotonic()
    while time.monotonic() < deadline:
        app.update()
        if time.monotonic() - start > .26 and not app.busy and not app.diagram.layout_pending and app.diagram._pending is None:
            return
        time.sleep(.01)
    raise AssertionError('Window did not settle: ' + app.status.get())


def measure(app):
    def size(w):
        return [w.winfo_width(), w.winfo_height()]
    return {'window': size(app), 'canvas': size(app.diagram.canvas),
            'panes': [name for name in ('left', 'center', 'right') if getattr(app, name).winfo_ismapped()],
            'extract_visible': bool(app.run_button.winfo_viewable()), 'instances': app.stats['instances'].get()}


def wait_for(app, ready, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.update()
        if ready():
            return
        time.sleep(.02)
    raise AssertionError('Timed out: ' + app.status.get())


def inside(widget, window):
    wait_for(window, widget.winfo_viewable, 3)
    window.update_idletasks()
    x, y = widget.winfo_rootx()-window.winfo_rootx(), widget.winfo_rooty()-window.winfo_rooty()
    assert widget.winfo_viewable(), str(widget) + ' is hidden'
    assert x >= 0 and y >= 0 and x+widget.winfo_width() <= window.winfo_width()+1 and y+widget.winfo_height() <= window.winfo_height()+1, (widget, x, y, window.geometry())


def viewport(diagram):
    canvas = diagram.canvas
    return ((canvas.winfo_width()/2-diagram.ox)/diagram.scale,
            (canvas.winfo_height()/2-diagram.oy)/diagram.scale, diagram.scale)


def labelled_button(window, name):
    todo = list(window.winfo_children())
    while todo:
        widget = todo.pop()
        if widget.winfo_class() == 'TButton' and str(widget.cget('text')) == name:
            return widget
        todo.extend(widget.winfo_children())
    raise AssertionError('Missing button: '+name)


def exercise(app, root, geometry, capture):
    from clocktree.gui import RuleDialog
    from clocktree.input_view import InputPortDialog
    from clocktree.issue_view import IssueDialog
    from clocktree.navigation import NeighbourDialog
    from clocktree.recoveryview import RecoveryDialog
    from clocktree.register_view import RegisterDialog
    from clocktree.sv_view import SVDialog
    from clocktree.session import read_graph
    from unittest.mock import patch
    import xml.etree.ElementTree as ET

    report = {}
    model = app.diagram.model
    positions = model.positions
    instance = 'fixture_top.l2.n0_mux'
    config = app.get_config(validate=False)
    diagram=app.diagram
    assert diagram.selected is None and diagram.scene.nodes
    assert not any(state[1][2] for state in diagram.scene.nodes.values())
    routes=diagram.minimap.find_withtag('map-route')
    assert len(routes)==len(model.edges)
    assert len(diagram.minimap.find_withtag('map-node'))==len(model.positions)
    assert len(diagram.canvas.find_withtag('paper'))==1
    inside(app.run_button,app)
    app.options_button.invoke()
    app.update_idletasks()
    assert app.memory_limit_entry.winfo_viewable()
    app.options_button.invoke()
    app.update_idletasks()
    assert not app.memory_limit_entry.winfo_viewable() and app.get_config(validate=False) == config
    app.legend_button.invoke()
    settle(app)
    labels = [app.devices_legend.itemcget(item,'text') for item in app.devices_legend.find_all()
              if app.devices_legend.type(item)=='text']
    assert all(label in labels for label in ('选择器','根','末端','聚合')), labels
    app.legend_button.invoke()
    app.log_toggle.invoke()
    assert app.log_visible
    app.update()
    inside(app.stage_view,app)
    app.show_stage_log('display')
    app.log_toggle.invoke()
    assert not app.log_visible
    app.focus_force()
    app.event_generate('<Control-f>')
    app.update()
    assert app.focus_get() == app.search_entry
    app.search_var.set(instance)
    app.locate_button.invoke()
    settle(app)
    assert app.diagram.selected == instance and instance in app.search_entry['values']
    assert {key for key,(_,detail,_) in diagram.scene.nodes.items() if detail[2]}=={instance}
    assert diagram.minimap.find_withtag('map-route')==routes
    assert app.identity_name.cget('text')=='n0_mux' and 'L2' in app.identity_type.cget('text')
    assert instance in app.prop_text.get('1.0','end')
    report['selection_and_minimap']=True
    assert app.diagram.model.positions is positions
    inside(app.right,app)
    inside(app.node_actions,app)
    capture(app,'selected')
    app.upstream_button.invoke()
    neighbours=next(w for w in app.winfo_children() if isinstance(w,NeighbourDialog))
    jump=labelled_button(neighbours,'跳转到所选器件')
    inside(jump,neighbours)
    keys=neighbours.table.get_children()
    assert len(keys)==2
    neighbours.table.selection_set(keys[0])
    jump.invoke()
    settle(app)
    assert app.diagram.selected!=instance and app.diagram.selection_target.get('edge')
    app.diagram.navigate_history()
    settle(app)
    assert app.diagram.selected==instance
    before = viewport(app.diagram)
    app.diagram.canvas.focus_force()
    app.update()
    app.event_generate('<F11>')
    settle(app)
    assert app.focused_view and measure(app)['panes']==['center']
    assert all(abs(a-b)<.01 for a,b in zip(before,viewport(app.diagram))), (before,viewport(app.diagram))
    app.event_generate('<Escape>')
    settle(app)
    assert not app.focused_view and app.diagram.selected==instance and model.positions is positions
    assert all(abs(a-b)<.01 for a,b in zip(before,viewport(app.diagram)))
    report['focus_preserves_viewport'] = True
    app.geometry('1000x660+0+0')
    settle(app)
    app.show_panel('left')
    settle(app)
    assert measure(app)['panes']==['left','center']
    app.diagram.select(model.original[instance])
    settle(app)
    assert measure(app)['panes']==['center','right'], (measure(app), app._compact_side, app.panes.panes(), app.panes.winfo_width(), app.center.winfo_width(), app.right.winfo_width())
    inside(app.panel_switch,app)
    app.panel_switch.invoke()
    settle(app)
    assert measure(app)['panes']==['left','center']
    app.geometry(geometry+'+0+0')
    settle(app)
    if int(geometry.split('x')[0])>=1200:
        assert measure(app)['panes']==['left','center','right']
    report['compact_inspector_and_return'] = True

    rule = dict(module='clock_div',kind='div',clock_inputs=['clk_i'],clock_output='clk_o',
                aux_ports={'div':'ratio_i','sw_done':'done_o'})
    saved = []
    editor = RuleDialog(app,1,rule,saved.append)
    app.update()
    inside(editor.save_button,editor)
    assert editor.preview.find_all()
    capture(editor,'rule-editor')
    editor.fields['module'].set('')
    editor.save_button.invoke()
    assert editor.error.get() and not saved
    editor.fields['module'].set('clock_div')
    rows = [dict(name=name,direction='input',width=1,left='',right='',instance='fixture_top.u_div')
            for name in ('clk_i','ratio_i','reset_n')]
    editor.discovered_module = ('clock_div',editor.hierarchy,['input','output'])
    editor.discovered_rows = rows
    editor.port_buttons['input'].invoke()
    ports = next(w for w in editor.winfo_children() if isinstance(w,InputPortDialog))
    ports.expression.set('^clk')
    ports.filter()
    ports.toggle_all(True)
    assert '已选 1 个' in ports.selection_summary.get()
    inside(ports.confirm_button,ports)
    ports.confirm_button.invoke()
    editor.save_button.invoke()
    assert saved and saved[0]['aux_ports']==rule['aux_ports'] and saved[0]['clock_inputs']==['clk_i']
    report['rule_and_port_picker'] = True

    dialog = app.open_branch(model.original[instance])
    wait_for(app,lambda:dialog.load_error or (dialog.graph and not dialog.diagram.layout_pending and dialog.diagram.selected))
    assert not dialog.load_error and dialog.diagram.selected==instance, dialog.load_error
    inside(dialog.retry_button,dialog)
    report['branch_instances'] = len(dialog.graph['nodes'])
    dialog.close()

    library=app.open_device_library()
    close_library=labelled_button(library,'关闭')
    inside(close_library,library)
    close_library.invoke()
    recovery=RecoveryDialog(app,dict(diagnosis={},reason='GUI test',checkpoint={},resumable=False))
    recovery.geometry('650x435')
    inside(recovery.resume_button,recovery)
    assert str(recovery.resume_button['state'])=='disabled'
    labelled_button(recovery,'稍后处理').invoke()

    for index,button,kind,control in [(0,app.register_button,RegisterDialog,'start_button'),
                                       (1,app.sv_button,SVDialog,'export_button'),
                                       (3,app.issue_button,IssueDialog,'table')]:
        if button.winfo_viewable():
            inside(button,app)
            button.invoke()
        else:
            inside(app.tools_button,app)
            app.tools_menu.invoke(index)
        child = next(w for w in app.winfo_children() if isinstance(w,kind))
        app.update()
        inside(getattr(child,control),child)
        assert child.winfo_rootx()+child.winfo_width()<=child.winfo_screenwidth()+1
        assert child.winfo_rooty()+child.winfo_height()<=child.winfo_screenheight()+1
        if kind is IssueDialog:
            wait_for(app,lambda:child.ready)
            child.geometry('780x445')
            inside(labelled_button(child,'定位问题'),child)
            assert child.table.winfo_height()>60
        elif kind is RegisterDialog:
            inside(labelled_button(child,'下一页'),child)
        elif kind is SVDialog:
            inside(child.analyze_button,child)
        child.close()
    report['analysis_dialogs'] = ['register','sv','issues']
    for label,filename in (('导出全量 JSON…','architecture.json'),('导出当前视图 SVG…','view.svg')):
        path=app.runs.parent/filename
        with patch('clocktree.gui.filedialog.asksaveasfilename',return_value=str(path)):
            app.file_menu.invoke(label)
        if path.suffix=='.json':
            saved_graph=read_graph(path)
            assert saved_graph['nodes']==app.graph['nodes'] and saved_graph['edges']==app.graph['edges']
        else:
            assert ET.parse(str(path)).getroot().tag.endswith('svg')
    report['exports']=['json','svg']
    app.show_graph(read_graph(root/'examples/reset_tree/resettree.json'))
    settle(app)
    assert app._active_tree_type=='reset' and str(app.sv_button['state'])=='disabled'
    assert app.tools_menu.entrycget(1,'state')=='disabled' and str(app.register_button['state'])=='normal'
    report['reset_mode'] = True
    return report


def extract(app, config_file, capture):
    from clocktree.session import read_graph
    config=json.loads(config_file.read_text(encoding='utf-8'))
    expected=read_graph(config_file.parent/'clocktree.json')
    app.set_config(config)
    app.run_button.invoke()
    app.update()
    assert app.job_kind=='extract' and app.log_visible and app.stage_view.winfo_viewable()
    assert str(app.cancel_button['state'])=='normal'
    wait_for(app,lambda:not app.busy,180)
    settle(app)
    assert app.graph_file and app.graph_file.parent==app.job_dir, app.status.get()
    endpoint=lambda edge:(edge['source'],edge['source_pin'],edge['target'],edge['target_pin'])
    assert {n['id'] for n in expected['nodes']}=={n['id'] for n in app.graph['nodes']}
    assert sorted(map(endpoint,expected['edges']))==sorted(map(endpoint,app.graph['edges']))
    capture(app,'eda-log')
    app.show_log(False)
    app.diagram.fit()
    settle(app)
    capture(app,'eda-overview')
    chosen = next((node for node in app.graph['nodes'] if node.get('kind')=='div'),app.graph['nodes'][0])
    app.diagram.locate(app.diagram.model.original[chosen['id']])
    settle(app)
    from clocktree.devices import connection_label
    primary_and_aux=list(chosen['inputs'])+list(chosen.get('aux_ports',{}).values())
    tag=app.diagram.scene.nodes[chosen['id']][0]
    def visible_text():
        return [app.diagram.canvas.itemcget(i,'text') for i in app.diagram.canvas.find_withtag(tag)
                if app.diagram.canvas.type(i)=='text' and app.diagram.canvas.itemcget(i,'state')!='hidden']
    wanted=[connection_label(chosen,pin) for pin in primary_and_aux]
    assert all(value in visible_text() for value in wanted), (wanted,visible_text())
    capture(app,'eda-selected')
    detail_scale=app.diagram.scale
    app.diagram.zoom(.35/detail_scale)
    settle(app)
    assert not any(value in visible_text() for value in wanted), (wanted,visible_text())
    assert app.diagram.selected==chosen['id']
    app.diagram.zoom(detail_scale/app.diagram.scale)
    settle(app)
    assert all(value in visible_text() for value in wanted), (wanted,visible_text())
    return dict(instances=len(app.graph['nodes']),edges=len(app.graph['edges']),result=str(app.graph_file))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--geometry', default='1366x768')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--exercise', action='store_true')
    parser.add_argument('--extract-config', type=Path, help='Live EDA config next to a known clocktree.json')
    args = parser.parse_args()
    dest = args.out.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(args.root))
    sys.path.insert(0, str(args.root/'scripts'))
    from clocktree.gui import ClockTreeApp
    from clocktree.session import read_graph
    app = ClockTreeApp(runs=dest/'runs', session_dir=dest/'session', remember=False)
    errors = []
    app.report_callback_exception = lambda *exc: errors.append(''.join(traceback.format_exception(*exc)))
    app.show_error = lambda title,error: errors.append(title+' '+str(error))
    def capture(widget,name):
        if args.capture:
            from capture_x11 import x11_image
            widget.update_idletasks()
            x11_image(widget.winfo_id()).save(dest/(name+'.png'))
    app.geometry(args.geometry + '+0+0')
    settle(app)
    if args.exercise:
        inside(app.empty_actions,app)
        capture(app,'empty')
    app.show_graph(read_graph(args.root/'examples/device_models/clocktree.json'))
    settle(app)
    app.diagram.fit()
    settle(app)
    capture(app,'workspace')
    result = measure(app)
    if args.exercise:
        result['checks'] = exercise(app,args.root,args.geometry,capture)
    if args.extract_config:
        result['extraction'] = extract(app,args.extract_config,capture)
    assert not errors, errors
    (dest/'measure.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    app.destroy()


if __name__ == '__main__':
    main()
