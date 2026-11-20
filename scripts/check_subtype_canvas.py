"""Check real Tk card colors on the four-subtype Verdi example, including X11 pixels."""
import argparse
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from capture_x11 import x11_image
from clocktree.devices import CLOCK_SUBTYPES, device_style
from clocktree.gui import ClockTreeApp


def wait_view(app, diagram, ready=lambda: True):
    started = time.monotonic()
    while True:
        app.update()
        if (ready() and diagram.model and diagram.scene.nodes and not diagram.layout_pending
                and not diagram._zoom_active and diagram._pending is None):
            return
        if time.monotonic() - started > 30:
            raise AssertionError('Canvas did not settle: ' + app.status.get())
        time.sleep(.01)


def card_item(diagram, key):
    tag = diagram.scene.nodes[key][0]
    return diagram.canvas.find_withtag(tag)[0]


def capture(window, diagram, path):
    window.update()
    screen = x11_image()
    left, top = window.winfo_rootx(), window.winfo_rooty()
    screen.crop((left, top, left+window.winfo_width(), top+window.winfo_height())).save(path)
    canvas = diagram.canvas
    counts = {}
    for kind in CLOCK_SUBTYPES:
        node = next(n for n in diagram.model.nodes.values() if n.get('kind') == kind)
        item = card_item(diagram, node['id'])
        expected = device_style(kind)['card']
        assert canvas.itemcget(item, 'fill') == expected
        x1,y1,x2,y2 = canvas.bbox(item)
        x,y = canvas.winfo_rootx(),canvas.winfo_rooty()
        area = screen.crop((x+x1,y+y1,x+x2,y+y2))
        color = bytes.fromhex(expected[1:])
        count = bytes(area.pixels).count(color)
        assert count > area.width*area.height*.20, (kind, count, area.width, area.height)
        counts[kind] = dict(fill=expected, pixels=count)
    return counts


def svg_colors(path):
    ns = {'s':'http://www.w3.org/2000/svg'}
    groups = ET.parse(str(path)).getroot().findall('s:g',ns)
    seen = {}
    for group in groups:
        kind = group.get('data-device-kind')
        if kind in CLOCK_SUBTYPES:
            seen[kind] = group.find('s:rect',ns).get('fill')
    assert seen == {k:device_style(k)['card'] for k in CLOCK_SUBTYPES}
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--graph', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    app = ClockTreeApp(runs=out/'runs', session_dir=out/'session', remember=False,
                       result=args.graph.resolve())
    app.geometry('1850x990+18+18')
    errors = []
    app.report_callback_exception = lambda cls, err, tb: errors.append(repr(err))
    branch = None
    try:
        d = app.diagram
        wait_view(app,d,lambda:not app.busy)
        app.legend_var.set(True)
        app.toggle_legend()
        app.update()
        d.fit()
        wait_view(app,d)
        report = {'main': capture(app,d,out/'subtypes-main.png')}
        app.export_svg(out/'main.svg')
        report['main_svg'] = svg_colors(out/'main.svg')
        for scale in (.15,.24,.36,1.):
            if scale >= 1:
                # Locate each selected card in detail without assuming that every node fits.
                for kind in CLOCK_SUBTYPES:
                    node = next(n for n in d.model.nodes.values() if n.get('kind') == kind)
                    d.locate(node)
                    wait_view(app,d)
                    assert d.canvas.itemcget(card_item(d,node['id']),'fill') == device_style(kind)['card']
            else:
                d.zoom(scale/d.scale)
                wait_view(app,d)
                for kind in CLOCK_SUBTYPES:
                    key = next(k for k in d.scene.nodes if d.model.nodes[k].get('kind') == kind)
                    expected = device_style(kind)['fill' if scale < .19 else 'card']
                    assert d.canvas.itemcget(card_item(d,key),'fill') == expected, (kind,scale)
        d.fit()
        wait_view(app,d)
        seed = next(n for n in d.model.nodes.values() if n.get('kind') == 'sync_gate')
        branch = app.open_branch(seed)
        branch.geometry('1800x920+22+22')
        wait_view(app,branch.diagram,lambda:branch.graph is not None)
        branch.diagram.fit()
        wait_view(app,branch.diagram)
        report['branch'] = capture(branch,branch.diagram,out/'subtypes-branch.png')
        with patch('clocktree.branch_view.filedialog.asksaveasfilename',return_value=str(out/'branch.svg')):
            branch.export_svg()
        report['branch_svg'] = svg_colors(out/'branch.svg')
        assert not errors, errors
        report['tk'] = app.tk.call('package','provide','Tk')
        (out/'canvas-check.json').write_text(json.dumps(report,indent=2), encoding='utf-8')
        print(json.dumps(report))
    finally:
        if branch is not None:
            branch.close()
        app.destroy()


if __name__ == '__main__':
    main()
