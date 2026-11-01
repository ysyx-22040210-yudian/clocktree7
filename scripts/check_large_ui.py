"""Inspect the full OpenC910 result through the VM's actual HTTP application."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

p = argparse.ArgumentParser()
p.add_argument('--url', default='http://192.168.31.116:8767')
p.add_argument('--run', default='openc910-ordered7-01')
p.add_argument('--output', default=str(Path(__file__).resolve().parents[1] / 'validation/ui'))
args = p.parse_args()
out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel='msedge', headless=True, args=['--no-proxy-server'])
    page = browser.new_page(viewport={'width': 1920, 'height': 1100}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(args.url, wait_until='networkidle', timeout=30000)
    page.select_option('#runs', args.run)
    page.wait_for_function("document.querySelector('#stats b').textContent.replace(/,/g,'') === '6984'", timeout=60000)
    page.wait_for_selector('.aggregate', timeout=15000)
    overview = page.locator('.node').count()
    assert 1 < overview < 200
    assert page.locator('#viewMode').input_value() == 'hierarchy'
    page.screenshot(path=str(out / 'openc910-overview.png'), full_page=True)
    page.locator('.aggregate').first.click()
    page.get_by_role('button', name='展开此层次的', exact=False).click()
    assert page.locator('#scopeBar').is_visible()
    drilled = page.locator('.node').count()
    assert drilled > 0
    page.click('#allScope')
    assert page.locator('.node').count() == overview
    point = page.locator('.edge').first.evaluate('''path => {
      const p=path.getPointAtLength(path.getTotalLength()*0.5);
      const s=new DOMPoint(p.x,p.y).matrixTransform(path.getScreenCTM());
      return {x:s.x,y:s.y};
    }''')
    page.mouse.click(point['x'], point['y'])
    evidence = page.locator('#details').inner_text()
    assert 'npiNl' in evidence and 'connections' in evidence
    page.fill('#search', 'x_ct_rtu_pst_entry_vreg63.x_alloc_gated_clk')
    page.click('#locate')
    assert page.locator('.node').count() == 6984
    assert page.locator('.edge').count() == 6983
    selected = page.locator('.node.selected').get_attribute('data-id')
    assert 'x_ct_rtu_pst_entry_vreg63.x_alloc_gated_clk' in selected
    assert 'downstream' in page.locator('#details').inner_text()
    page.screenshot(path=str(out / 'openc910-locate.png'), full_page=True)
    with page.expect_download() as info:
        page.click('#exportResult')
    artifact = info.value
    artifact.save_as(str(out / 'openc910-ui-export.json'))
    exported = json.loads((out / 'openc910-ui-export.json').read_text(encoding='utf-8'))
    assert len(exported['nodes']) == 6984 and len(exported['edges']) == 6983
    browser.close()
    result = {'pass': not errors, 'url': args.url, 'run': args.run,
              'overview_boxes': overview, 'drilldown_boxes': drilled,
              'full_instances': len(exported['nodes']), 'full_edges': len(exported['edges']),
              'located_instance': selected, 'evidence_inspection': True, 'full_export': True,
              'page_errors': errors}
    (out / 'large-ui-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
    if errors:
        raise SystemExit(1)
