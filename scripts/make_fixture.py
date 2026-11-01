"""Generate independent RTL + expected pin edges for complete-network checks."""
import argparse
import json
from collections import defaultdict
from pathlib import Path


def generate(destination, copies=1, generate_scopes=False, device_models=False):
    out = Path(destination).resolve()
    out.mkdir(parents=True, exist_ok=True)
    nodes = []
    def add(layer, name, sources, kind=None):
        kind = kind or ('ct_occ' if device_models else 'ct_buf')
        key = (layer, name)
        nodes.append((key, sources, kind))
        return key
    for c in range(copies):
        p = "n{}_".format(c)
        a = add(1, p + "root_a", ["clk_a"])
        b = add(1, p + "root_b", ["clk_b"])
        z = add(1, p + "root_c", ["clk_c"])
        add(1, p + "end1", [a])
        g = add(2, p + "gate", [a], "ct_gate")
        add(2, p + "end2", [g])
        m = add(2, p + "mux", [g, b], "ct_mux")
        n = add(2, p + "same_layer", [m])
        h = add(3, p + "gate", [g], "ct_gate")
        add(3, p + "end3", [h])
        add(4, p + "end4", [b])
        q = add(5, p + "mux", [n, h], "ct_mux")
        add(5, p + "end5", [q], "ct_gate")
        add(6, p + "end6", [z])
        t = add(7, p + "enter7", [q])
        add(7, p + "end7", [t], "ct_gate")
    by_layer = defaultdict(list)
    used = set()
    for node in nodes:
        by_layer[node[0][0]].append(node)
        used.update(s for s in node[1] if isinstance(s, tuple))
    def signal(key):
        return "s{}_{}".format(*key)
    rtl = [
        "module ct_buf(input wire i, output wire o); assign o=i; endmodule",
        "module ct_gate(input wire i, input wire en, output wire o); assign o=i & en; endmodule",
        "module ct_mux(input wire a,b,sel, output wire o); assign o=sel ? b:a; endmodule",
        # Small clock-pulse controller for extraction/visualization validation;
        # it is not a production scan/at-speed-test OCC implementation.
        "module ct_occ(input wire i,en, output wire o); reg [1:0] pulses=2; reg pass=0; always @(posedge i) if(en) pulses<=2; else if(pulses!=0) pulses<=pulses-1'b1; always @(negedge i) pass<=en || (pulses!=0); assign o=i & pass; endmodule",
    ]
    wrapper_ports = {}
    for layer in range(1, 8):
        group = by_layer[layer]
        external = sorted(set(s for _, sources, _ in group for s in sources if not isinstance(s, tuple) or s[0] != layer), key=str)
        indexes = {s: i for i, s in enumerate(external)}
        wrapper_ports[layer] = external
        declarations = ["input wire en,sel", "input wire [{}:0] incoming".format(len(external)-1)]
        declarations.extend("output wire " + signal(key) for key, _, _ in group)
        rtl.append("module stage_{}({});".format(layer, ",".join(declarations)))
        for key, sources, kind in group:
            src = [signal(s) if isinstance(s, tuple) and s[0] == layer else "incoming[{}]".format(indexes[s]) for s in sources]
            if kind == "ct_mux":
                pins = ".a({}),.b({}),.sel(sel)".format(*src)
            else:
                pins = ".i({})".format(src[0]) + (",.en(en)" if kind in ("ct_gate", "ct_occ") else "")
            if generate_scopes:
                rtl.append("for(genvar n=0;n<1;n++) begin : g_{}".format(key[1]))
                rtl.append("{} u({},.o({}));".format(kind, pins, signal(key)))
                rtl.append("end")
            else:
                rtl.append("{} {}({},.o({}));".format(kind, key[1], pins, signal(key)))
        rtl.append("endmodule")
    leaves = [key for key, _, _ in nodes if key not in used]
    rtl.append("module fixture_top(input wire clk_a,clk_b,clk_c,en,sel, output wire [{}:0] sinks);".format(len(leaves)-1))
    for key, _, _ in nodes:
        rtl.append("wire {};".format(signal(key)))
    for layer in range(1, 8):
        external = wrapper_ports[layer]
        vector = ",".join(signal(s) if isinstance(s, tuple) else s for s in reversed(external))
        conns = [".en(en)", ".sel(sel)", ".incoming({" + vector + "})"]
        conns.extend(".{0}({0})".format(signal(key)) for key, _, _ in by_layer[layer])
        rtl.append("stage_{} l{}({});".format(layer, layer, ",".join(conns)))
    rtl.append("assign sinks={" + ",".join(signal(key) for key in leaves) + "};")
    rtl.append("endmodule")
    (out / "fixture.sv").write_text("\n".join(rtl)+"\n", encoding="utf-8")
    (out / "rtl.f").write_text(str(out / "fixture.sv")+"\n", encoding="utf-8")
    layers = []
    for layer in range(1, 8):
        modules = sorted(set(kind for _, _, kind in by_layer[layer]))
        layers.append({"index": layer, "hierarchy": "fixture_top.l{}".format(layer),
                       "modules": [{"module": kind, "clock_inputs": ["a", "b"] if kind == "ct_mux" else ["i"], "clock_output": "o"} for kind in modules]})
        if device_models:
            for rule in layers[-1]['modules']:
                rule['kind'] = {'ct_mux':'mux','ct_gate':'gate','ct_occ':'occ'}[rule['module']]
    config = {"design": {"top": "fixture_top", "filelist": str(out / "rtl.f"),
                         "verdi_home": "/home/synopsys/verdi/Verdi_O-2018.09-SP2"}, "layers": layers}
    (out / "config.json").write_text(json.dumps(config, indent=2)+"\n", encoding="utf-8")
    def path(key):
        return "fixture_top.l{}.g_{}[0].u".format(*key) if generate_scopes else "fixture_top.l{}.{}".format(*key)
    edges = []
    for key, sources, kind in nodes:
        for pin, source in zip(["a", "b"] if kind == "ct_mux" else ["i"], sources):
            if isinstance(source, tuple): edges.append([path(source), "o", path(key), pin])
    golden = {"nodes": sorted(path(key) for key, _, _ in nodes), "edges": sorted(edges), "terminals": sorted(path(key) for key in leaves)}
    (out / "golden.json").write_text(json.dumps(golden, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"directory": str(out), "instances": len(nodes), "edges": len(edges), "terminals": len(leaves)}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("destination")
    p.add_argument("--copies", type=int, default=1)
    p.add_argument("--generate-scopes", action="store_true")
    p.add_argument("--device-models", action="store_true", help="Use explicitly typed MUX / GATE / OCC modules only")
    args = p.parse_args()
    generate(args.destination, args.copies, args.generate_scopes, args.device_models)
