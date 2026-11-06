"""Native issue paging, export, object expansion and port location."""
import argparse
import csv
import json
from pathlib import Path
import sys
import traceback
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from clocktree.gui import ClockTreeApp
from clocktree.issue_view import IssueDialog
from clocktree.storage import write_graph
from check_workbench_gui import pump
from check_indexed_gui import capture


def main():
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--result',required=True);a=p.parse_args()
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=True)
    graph=json.loads(Path(a.result).read_text())
    graph['diagnostics']=[{'code':'missing_trace','instance':'wb_top.s0','pin':'aux_clk','message':'验收夹具：已中断的输入追踪 {}'.format(i)} for i in range(701)]
    graph['diagnostics'] += [{'code':'cycle_or_cycle_downstream','instances':['wb_top.g0','wb_top.s0','wb_top.s1']},
                             {'code':'missing_module','layer':2,'module':'not_found','hierarchy':'wb_top'}]
    graph['boundaries'] += [{'status':'trace_timeout','instance':'wb_top.s1','pin':'aux_clk','direction':'driver'},
                            {'status':'scope_boundary','instance':'wb_top.g0','pin':'i','direction':'driver'}]
    errors=[];app=ClockTreeApp(runs=out/'runs',remember=False,session_dir=out/'session');app.geometry('1840x1000+0+0')
    app.report_callback_exception=lambda *e:errors.append(''.join(traceback.format_exception(*e)))
    pump(app,lambda:not app.busy);app.show_graph(graph);pump(app)
    issues=IssueDialog(app);pump(app,lambda:issues.ready)
    assert len(issues.table.get_children())==100
    total,affected,rows=issues.index.query();assert total==704 and affected==3,(total,affected)
    for i in range(7):issues.page(1)
    assert len(issues.table.get_children())==4
    count=issues.index.export(out/'issues.csv');assert count==704
    with (out/'issues.csv').open(encoding='utf-8-sig',newline='') as stream:assert len(list(csv.reader(stream)))==705
    issues.category.set('端口超时');issues.page(0,True)
    key=issues.table.get_children()[0];issues.table.selection_set(key);issues.locate();pump(app)
    assert app.selected_object['id']=='wb_top.s1' and app.diagram.issue_port==('wb_top.s1','aux_clk')
    issues.category.set('环路及受影响下游');issues.page(0,True)
    key=issues.table.get_children()[0];issues.table.selection_set(key);issues.details();pump(app)
    for child in issues.winfo_children():
        if child.winfo_class()=='Toplevel':child.destroy()
    issues.category.set('全部类别');issues.search.set('s0');issues.page(0,True)
    assert issues.index.query(search='s0')[0]==702
    issues.table.selection_set(issues.table.get_children()[0]);pump(app)
    capture(issues,out/'issues.png');issues.close();pump(app)
    app.layout_var.set('分支紧凑布局');app.change_layout();pump(app)
    app.diagram.locate(app.diagram.model.original['wb_top.g0']);pump(app)
    app.highlight_path('branch');app.diagram.fit();pump(app)
    capture(app,out/'branch.png')
    app.export_svg(out/'branch.svg')
    app.graph=None;app.diagram.clear()
    app._latest_job_failure={'code':'fatal','message':'验收夹具：elab 导入失败，尚未生成结果','run_directory':str(out/'failed-run')}
    app.open_issues()
    failed=next(w for w in app.winfo_children() if isinstance(w,IssueDialog));pump(app,lambda:failed.ready)
    assert failed.index.query()[0:2]==(1,0)
    failed.table.selection_set(failed.table.get_children()[0]);failed.locate();pump(app)
    assert app.log_visible and app.log_source.get()=='阶段日志'
    failed.close()
    assert not errors,errors
    write_graph(out/'acceptance.json',{'passed':True,'problems':704,'affected_instances':3,'csv_rows':704,'page_size':100,'no_result_failure_located':True,'errors':errors})
    app.close()
    print('ISSUE_GUI_ACCEPTED',flush=True)


if __name__=='__main__':main()
