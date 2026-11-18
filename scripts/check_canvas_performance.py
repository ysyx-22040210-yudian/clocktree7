"""Comparable native Tk benchmark. Run with the same Xvfb/window on both versions."""
import argparse
import json
import os
import sys
import time
from pathlib import Path


def verify_trunk(diagram):
    from clocktree.canvas_wires import TrunkInterval
    scene=diagram.scene
    for key in scene.wires:
        span=scene.segments[key]
        if not isinstance(span['edges'],TrunkInterval) or len(span['edges'])<100:
            continue
        a,b=span['a'],span['b']
        x,y=(a[0]+b[0])/2,(a[1]+b[1])/2
        px,py=x*diagram.scale+diagram.ox,y*diagram.scale+diagram.oy
        if not (6<px<diagram.canvas.winfo_width()-6 and 6<py<diagram.canvas.winfo_height()-6):
            continue
        hit=diagram._hit(px,py)
        if not hit or hit[0]!='edge':continue
        net=next(iter(span['edges']))
        expected=set()
        for edge in diagram.model.edges:
            if (edge['source'],edge['source_pin'])!=(net['source'],net['source_pin']):continue
            points=diagram.model.edge_points(edge)
            for c,d in zip(points,points[1:]):
                if (c[0]==d[0]==x and min(c[1],d[1])<y<max(c[1],d[1]) or
                        c[1]==d[1]==y and min(c[0],d[0])<x<max(c[0],d[0])):
                    expected.add(edge['id'])
                    break
        assert {e['id'] for e in hit[1]}==expected
        return len(expected)
    raise AssertionError('No visible high-fanout trunk was verified')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--result', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--frames', type=int, default=80)
    parser.add_argument('--verify-trunk',action='store_true',help='Compare a picked high-fanout interval with the original routed edges')
    args = parser.parse_args()
    sys.path.insert(0, args.source)
    import tkinter as tk
    from clocktree.session import read_graph
    from clocktree.tkdiagram import ClockDiagram
    root = tk.Tk()
    root.geometry('1840x1000+0+0')
    diagram = ClockDiagram(root, 'DejaVu Sans', lambda n: None, lambda e: None,
                           lambda s: None, lambda s: None)
    diagram.pack(fill='both', expand=True)
    root.update()
    graph = read_graph(args.result)
    start = time.perf_counter()
    diagram.load(graph)
    diagram.mode = 'instances'
    diagram.rebuild()
    def settle():
        while getattr(diagram, 'layout_pending', False) or diagram._pending is not None:
            root.update()
            time.sleep(.002)
        root.update()
    settle()
    load = time.perf_counter() - start
    node = next(iter(diagram.model.nodes.values()))
    diagram.locate(node)
    settle()
    frames = []
    original = diagram.paint
    def paint():
        begin = time.perf_counter()
        original()
        root.update_idletasks()
        frames.append((time.perf_counter()-begin)*1000)
    diagram.paint = paint
    reports = {}
    class Event:
        num = 1
        x, y = 900, 500
    for action in ('pan', 'zoom'):
        frames[:] = []
        diagram._press(Event())
        for i in range(args.frames):
            if action == 'pan':
                event = Event()
                event.x += ((i % 20)-10)*9
                event.y += ((i % 12)-6)*7
                diagram._motion(event)
            else:
                diagram.zoom(1.035 if i % 20 < 10 else 1/1.035, 900, 500)
            settle()
        diagram._release(Event())
        idle_start = time.perf_counter()
        end = time.perf_counter()+.3
        while time.perf_counter()<end:
            root.update()
            time.sleep(.003)
        ordered = sorted(frames)
        reports[action] = {'frames': len(frames), 'p50_ms': ordered[len(ordered)//2],
                           'p95_ms': ordered[min(len(ordered)-1, int(len(ordered)*.95))],
                           'max_ms': max(ordered), 'items': len(diagram.canvas.find_all()),
                           'visible_nodes': diagram.visible_nodes,
                           'detail_restore_ms': getattr(diagram, 'last_detail_restore_ms', None)}
    rss = None
    if Path('/proc/self/status').exists():
        rss = next(line.strip() for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('VmRSS:'))
    output = {'source': args.source, 'result': args.result, 'instances': len(graph['nodes']),
              'window': [1840, 1000], 'tk': root.tk.call('info', 'patchlevel'),
              'load_seconds': load, 'rss': rss, 'measurements': reports}
    if args.verify_trunk:output['verified_pick_edges']=verify_trunk(diagram)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps(output, indent=2), flush=True)
    root.destroy()


if __name__ == '__main__':
    main()
