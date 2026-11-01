"""Prepare concrete benchmark rules; never infer or fabricate instance edges."""
from pathlib import Path
import argparse
import json

p=argparse.ArgumentParser()
p.add_argument("--opentitan-build")
p.add_argument("--root", default=str(Path(__file__).resolve().parents[1] / "validation"))
args=p.parse_args()
root=Path(args.root).resolve()
configs=root / "configs"
configs.mkdir(parents=True, exist_ok=True)

def config(name, top, filelist, rules):
    layers=[{"index":i,"hierarchy":top,"modules":rules if i==1 else []} for i in range(1,8)]
    # Real project clocks are initially audited in L1 to avoid imposing seven
    # artificial pipeline stages on an unmodified open-source SoC.
    data={"design":{"top":top,"filelist":str(filelist),"verdi_home":"/home/synopsys/verdi/Verdi_O-2018.09-SP2"},"layers":layers}
    (configs/(name+".json")).write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")

def rule(module, inputs, output):
    return {"module":module,"clock_inputs":inputs,"clock_output":output}

if args.opentitan_build:
    build=Path(args.opentitan_build).resolve()
    script=next(build.glob("*.scr"))
    lines=["+define+SYNTHESIS"]
    for text in script.read_text().splitlines():
        text=text.strip()
        if not text: continue
        if text.startswith("+incdir+"):
            lines.append("+incdir+"+str((build/text[len("+incdir+"):]).resolve()))
        elif text.startswith(("+", "-")):
            lines.append(text)
        else:
            lines.append(str((build/text).resolve()))
    filelist=configs/"opentitan.f"
    filelist.write_text("\n".join(lines)+"\n",encoding="utf-8")
    config("opentitan", "top_earlgrey", filelist,[rule("prim_clock_gating",["clk_i"],"clk_o"),
           rule("prim_clock_mux2",["clk0_i","clk1_i"],"clk_o"),rule("prim_clock_buf",["clk_i"],"clk_o"),
           rule("prim_clock_inv",["clk_i"],"clk_no")])
config("pulp_soc", "pulp_soc", configs/"pulp_soc.f",[rule("pulp_clock_gating",["clk_i"],"clk_o"),
       rule("pulp_clock_mux2",["clk0_i","clk1_i"],"clk_o"),rule("pulp_clock_buffer",["clk_i"],"clk_o"),
       rule("pulp_clock_inverter",["clk_i"],"clk_o")])
print("Prepared benchmark configurations:",configs)
