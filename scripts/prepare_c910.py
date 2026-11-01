"""Resolve OpenC910's official ASIC file list and SMART SoC library paths."""
from pathlib import Path
import json

root=Path(__file__).resolve().parents[1]/"validation"
project=root/"projects/openc910"
factory=project/"C910_RTL_FACTORY"
text=(factory/"gen_rtl/filelists/C910_asic_rtl.fl").read_text()
lines=["+libext+.v+.sv", "+define+APB_BASE_ADDR=40'hb0000000",
       "+incdir+"+str(factory/"gen_rtl/cpu/rtl"), "+incdir+"+str(factory/"gen_rtl/mmu/rtl")]
lines.extend(text.replace("${CODE_BASE_PATH}",str(factory)).splitlines())
for directory in ("ahb","apb","axi","common","gpio","mem","uart","pmu"):
    lines.append("-y "+str(project/"smart_run/logical"/directory))
lines.append(str(project/"smart_run/logical/common/soc.v"))
config_dir=root/"configs";config_dir.mkdir(exist_ok=True)
filelist=config_dir/"openc910.f"
filelist.write_text("\n".join(lines)+"\n",encoding="utf-8")
config={"design":{"top":"soc","filelist":str(filelist),"verdi_home":"/home/synopsys/verdi/Verdi_O-2018.09-SP2"},
        "layers":[{"index":i,"hierarchy":"soc","modules":[{"module":"gated_clk_cell","clock_inputs":["clk_in"],"clock_output":"clk_out"}] if i==1 else []} for i in range(1,8)]}
(config_dir/"openc910.json").write_text(json.dumps(config,indent=2),encoding="utf-8")
print("Prepared",filelist)
