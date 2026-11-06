"""Comparable native Tk benchmark. Run with the same Xvfb/window on both versions."""
import argparse
import json
import os
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--result', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--frames', type=int, default=80)
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
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, indent=2), encoding='utf-8')
    print(json.dumps(output, indent=2), flush=True)
    root.destroy()


if __name__ == '__main__':
    main()
