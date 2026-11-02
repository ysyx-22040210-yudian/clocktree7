"""Generate reset RTL and independent complete instance/edge/terminal sets."""
import argparse
import json
from pathlib import Path


def generate(destination, layer_count=2):
    if layer_count < 2:
        raise ValueError('This fixture requires at least two layers')
    out=Path(destination).resolve()
    out.mkdir(parents=True,exist_ok=True)
    records=[('root_a',1,'mux',['rst_a_ni','rst_b_ni']),
             ('root_b',1,'mux',['rst_c_ni','rst_c_ni']),
             ('early1',1,'rsy_sync',['root_b']),
             ('sync0',2,'rsy_sync',['root_a']),('sync1',2,'rsy_sync',['sync0']),
             ('early2',2,'rsy_sync',['root_a']),
             ('mux_mid',3,'mux',['sync1','root_a']),('sync3',4,'rsy_sync',['mux_mid']),
             ('sync4',5,'rsy_sync',['sync3']),('early8',8,'rsy_sync',['sync4']),
             ('mux9',9,'mux',['sync4','root_a']),('sync10',10,'rsy_sync',['mux9'])]
    assigned={name:min(layer,layer_count) for name,layer,kind,sources in records}
    def path(name):return 'reset_top.l{}.u_{}'.format(assigned[name],name)
    def signal(name):return 's_'+name if name in assigned else name
    used={source for _,_,_,sources in records for source in sources if source in assigned}
    leaves=sorted(set(assigned)-used)
    rtl=[
        'module reset_mux(input rst0_ni,rst1_ni,sel, output rst_no); assign rst_no=sel?rst1_ni:rst0_ni; endmodule',
        'module reset_sync(input clk_i,rst_ni, output rst_no); reg [1:0] sync_ff=0; always @(posedge clk_i or negedge rst_ni) if(!rst_ni) sync_ff<=0; else sync_ff<={sync_ff[0],1\'b1}; assign rst_no=sync_ff[1]; endmodule',
        'module reset_top(input clk_i,rst_a_ni,rst_b_ni,rst_c_ni,sel, output [{}:0] sinks, output noise);'.format(len(leaves)-1),
        'wire '+','.join(signal(name) for name in assigned)+';',
        # Same module definition outside every configured reset scope. It must
        # not appear in the result or introduce the clock as a reset source.
        'reset_mux outside_reset_tree(.rst0_ni(clk_i),.rst1_ni(clk_i),.sel(sel),.rst_no(noise));']
    layers,edges=[],[]
    for layer in range(1,layer_count+1):
        members=[r for r in records if assigned[r[0]]==layer]
        rtl.append('generate if(1) begin : l{}'.format(layer))
        for name,_,kind,sources in members:
            pins=['rst0_ni','rst1_ni'] if kind=='mux' else ['rst_ni']
            connections=['.{}({})'.format(pin,signal(source)) for pin,source in zip(pins,sources)]
            connections.append('.sel(sel)' if kind=='mux' else '.clk_i(clk_i)')
            connections.append('.rst_no({})'.format(signal(name)))
            rtl.append('{} u_{}({});'.format('reset_mux' if kind=='mux' else 'reset_sync',name,','.join(connections)))
            edges.extend([path(source),'rst_no',path(name),pin] for pin,source in zip(pins,sources) if source in assigned)
        rtl.append('end endgenerate')
        rules=[{'module':'reset_mux' if kind=='mux' else 'reset_sync','kind':kind,
                'reset_inputs':['rst0_ni','rst1_ni'] if kind=='mux' else ['rst_ni'],'reset_output':'rst_no'}
               for kind in sorted({r[2] for r in members})]
        layers.append({'index':layer,'hierarchy':'reset_top.l{}'.format(layer),'modules':rules})
    rtl+=['assign sinks={'+','.join(signal(name) for name in leaves)+'};','endmodule']
    (out/'fixture.sv').write_text('\n'.join(rtl)+'\n',encoding='utf-8')
    (out/'rtl.f').write_text(str(out/'fixture.sv')+'\n',encoding='utf-8')
    config={'tree_type':'reset','design':{'top':'reset_top','filelist':str(out/'rtl.f')},'layers':layers}
    golden={'nodes':sorted(path(name) for name in assigned),'edges':sorted(edges),
            'terminals':sorted(path(name) for name in leaves)}
    for name,data in [('config.json',config),('golden.json',golden)]:
        (out/name).write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')
    return config,golden


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('destination')
    parser.add_argument('--layers',type=int,default=2)
    args=parser.parse_args()
    config,golden=generate(args.destination,args.layers)
    print(json.dumps({'tree_type':'reset','layers':len(config['layers']),'nodes':len(golden['nodes']),'edges':len(golden['edges'])}))
