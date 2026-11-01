"""Browser acceptance of the real VM server, including a fresh NPI job."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

p=argparse.ArgumentParser()
p.add_argument("--url",default="http://192.168.31.116:8767")
p.add_argument("--output",default=str(Path(__file__).resolve().parents[1]/"validation/ui"))
p.add_argument("--extract",action="store_true")
args=p.parse_args()
out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
with sync_playwright() as pw:
    browser=pw.chromium.launch(channel="msedge",headless=True,args=["--no-proxy-server"])
    page=browser.new_page(viewport={"width":1920,"height":1100},device_scale_factor=1)
    errors=[]
    page.on("pageerror",lambda error:errors.append(str(error)))
    page.goto(args.url,wait_until="networkidle",timeout=30000)
    page.wait_for_selector(".node",timeout=15000)
    total=page.locator(".node").count()
    assert total>0
    page.locator(".node").first.click()
    assert page.locator("#details").inner_text()
    page.screenshot(path=str(out/"full-network.png"),full_page=True)
    page.select_option("#component",index=1)
    filtered=page.locator(".node").count()
    assert 0<filtered<=total
    page.select_option("#component","all")
    assert page.locator(".node").count()==total
    page.fill("#search","root_a")
    matches=page.locator(".search-match").count()
    assert matches>0
    page.fill("#search","")
    job_id=None
    if args.extract:
        page.click("#run")
        page.wait_for_function("document.getElementById('run').disabled === false && document.getElementById('state').textContent === '全量连接已解析' && document.getElementById('runs').value !== ''",timeout=120000)
        assert page.locator("#state").inner_text()=="全量连接已解析"
        job_id=page.locator("#runs").input_value()
        assert job_id
        page.screenshot(path=str(out/"after-npi-extraction.png"),full_page=True)
    browser.close()
    result={"pass":not errors,"url":args.url,"instances_rendered":total,"filtered_instances":filtered,"search_matches":matches,"npi_run":job_id,"page_errors":errors}
    (out/"ui-check.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
    if errors:raise SystemExit(1)
