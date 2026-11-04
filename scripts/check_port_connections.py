"""Real Verdi/Tk checks of instantiation-side expressions, including aliases."""
import argparse
import importlib
import json
from pathlib import Path
import re
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clocktree.storage import write_graph
from clocktree.session import read_graph
from clocktree.devices import connection_label
from clocktree.gui import ClockTreeApp
from check_flexible_gui import pump, capture


def fixture(out):
    out.mkdir(parents=True, exist_ok=True)
    rtl = '''module cell1(input i,output o); assign o=i; endmodule
module cell4(input [3:0] i,output [3:0] o); assign o=i; endmodule
module port_top(input [7:0] clks,input en,output [7:0] sinks,output long_sink);
wire alias_clk; assign alias_clk=clks[3];
wire very_long_clock_signal_name_from_parent_for_truncation;
assign very_long_clock_signal_name_from_parent_for_truncation=clks[7];
cell1 u_alias(.i(alias_clk),.o(sinks[0]));
cell1 u_const(.i(1'b0),.o());
cell1 u_expr(.i(clks[2]&en),.o(sinks[1]));
cell1 u_pos(clks[5],sinks[3]);
cell1 u_long(.i(very_long_clock_signal_name_from_parent_for_truncation),.o(long_sink));
cell4 u_bus(.i({clks[3:2],clks[5:4]}),.o(sinks[7:4]));
generate if(1) begin:l1
cell1 u_gen(.i(clks[6]),.o(sinks[2]));
wire i; wire o; assign i=clks[0]; cell1 u_auto(.*);
end endgenerate
endmodule
'''
    (out/'fixture.sv').write_text(rtl, encoding='utf-8')
    (out/'rtl.f').write_text(str(out/'fixture.sv')+'\n', encoding='utf-8')
    config = {'design':{'top':'port_top','filelist':str(out/'rtl.f')},'layers':[
        {'index':1,'hierarchy':'port_top','modules':[
            {'module':'cell1','kind':'gate','clock_inputs':['i'],'clock_output':'o'},
            {'module':'cell4','kind':'gate','clock_inputs':['i[0]'],'clock_output':'o[0]'}]},
        {'index':2,'hierarchy':'port_top.l1','modules':[
            {'module':'cell1','kind':'gate','clock_inputs':['i'],'clock_output':'o'}]}]}
    write_graph(out/'config.json',config)
    return out/'config.json'


def check(graph):
    nodes = {node['id']:node for node in graph['nodes']}
    def ports(name):return nodes['port_top.'+name]['port_connections']
    expected = {'u_alias':('alias_clk','sinks[0]'), 'u_pos':('clks[5]','sinks[3]'),
        'u_expr':('(clks[2] & en)','sinks[1]'), 'l1.u_gen':('clks[6]','sinks[2]'),
        'l1.u_auto':('i','o'), 'u_long':('very_long_clock_signal_name_from_parent_for_truncation','long_sink')}
    for name, values in expected.items():
        for pin,value in zip(('i','o'),values):
            assert ports(name)[pin]['status']=='connected'
            assert re.sub(r'\s+','',ports(name)[pin]['expression'])==re.sub(r'\s+','',value),(name,pin,ports(name))
    assert ports('u_alias')['i']['full_name']=='port_top.alias_clk'
    assert ports('u_const')['o']['status']=='unconnected'
    assert ports('u_const')['i']['expression'] in ("'b0","1'b0")
    assert ports('u_bus')['i[0]']['coverage']=='whole_port'
    assert 'clks[3:2]' in ports('u_bus')['i[0]']['expression'] and 'clks[5:4]' in ports('u_bus')['i[0]']['expression']
    assert ports('u_bus')['o[0]']['coverage']=='whole_port' and ports('u_bus')['o[0]']['expression']=='sinks[7:4]'
    assert len(nodes)==8
    assert sum(len(node['port_connections']) for node in nodes.values())==16
    return nodes


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default=str(ROOT/'validation/port-signals/acceptance'))
    parser.add_argument('--gui',action='store_true',help='Read the same output and test native Tk labels/hover/SVG')
    args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    report={'pass':True}
    if not args.gui:
        source=fixture(out/'fixture')
        graph=importlib.import_module('clocktree.extract').extract(source,out/'run')
        nodes=check(graph)
        report.update(instances=len(nodes),connections=16,aliases=True,constant=True,open_port=True,
            expression=True,positional=True,implicit=True,generate=True,vector_whole_connection=True,
            topology_scope='annotation fixture; combinational expression may have unresolved trace diagnostics')
    else:
        graph=read_graph(out/'run/clocktree.json');nodes=check(graph)
        errors=[]
        app=ClockTreeApp(runs=out/'gui-runs',remember=False)
        app.report_callback_exception=lambda *exc:errors.append(''.join(traceback.format_exception(*exc)))
        app.show_error=lambda title,error:errors.append(title+': '+str(error))
        app.geometry('1840x1000+20+20')
        try:
            pump(app);app.show_graph(graph);pump(app)
            for name in ('u_alias','u_const','u_long','u_bus'):
                node=nodes['port_top.'+name]
                app.diagram.locate(node);pump(app)
                properties=app.prop_text.get('1.0','end')
                for pin in node['inputs']+[node['output']]:assert connection_label(node,pin) in properties
                app.export_svg(out/(name+'.svg'))
                if name=='u_long':
                    text='very_long_clock_signal_name_from_parent_for_truncation'
                    assert text in (out/(name+'.svg')).read_text(encoding='utf-8')
                    item=next(key for key,value in app.diagram.port_tooltips.items() if text in value)
                    x,y=app.diagram.canvas.coords(item)
                    hover=[];previous=app.diagram.on_hover
                    def on_hover(value):
                        hover.append(value);previous(value)
                    app.diagram.on_hover=on_hover
                    app.diagram.canvas.event_generate('<Motion>',x=int(x+3),y=int(y));pump(app)
                    app.diagram.on_hover=previous
                    assert hover and text in hover[-1]
                    app.prop_text.yview(app.prop_text.search('输入口例化连接','1.0'))
                    pump(app)
                    capture(app,out/'port-signal-details.png')
            assert not errors,errors
        finally:app.destroy()
        report.update(native_gui=True,properties=True,hover=True,svg=True,callback_errors=errors)
    write_graph(out/('gui.json' if args.gui else 'acceptance.json'),report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
