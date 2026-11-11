"""Deterministic branch layout and orthogonal routing; no Tk or EDA imports."""
from collections import defaultdict, deque

from .devices import node_height, port_offsets, pin_index, output_offset


WIDTH, STEP = 382, 466


def branch_layout(nodes, edges):
    from .viewmodel import horizontal_ranks
    ranks = horizontal_ranks(nodes, edges, same_layer=False)
    successors, incoming = defaultdict(set), defaultdict(set)
    for edge in edges:
        successors[edge['source']].add(edge['target'])
        incoming[edge['target']].add(edge['source'])
    # A stable iterative depth-first order keeps each subtree contiguous.
    # Shared descendants get one rank and one position; cycles are condensed
    # by horizontal_ranks and use marked return lanes.
    order, seen = {}, set()
    roots = sorted(nodes, key=lambda key:(bool(incoming[key]), nodes[key]['component'], ranks[key], key))
    for root in roots:
        if root in seen: continue
        stack = [root]
        while stack:
            key = stack.pop()
            if key in seen: continue
            seen.add(key); order[key] = len(order)
            stack.extend(sorted(successors[key]-seen, reverse=True))
    columns = defaultdict(list)
    for key in nodes: columns[ranks[key]].append(key)
    heights = {key: node_height(node) for key, node in nodes.items()}
    positions = {}
    for column in sorted(columns, reverse=True):
        cursor = 92
        for key in sorted(columns[column], key=lambda k:order[k]):
            centres = sorted(positions[child][1]+positions[child][3]/2 for child in successors[key]
                             if child in positions and ranks[child] > column)
            preferred = centres[len(centres)//2]-heights[key]/2 if centres else cursor
            y = max(cursor, preferred)
            positions[key] = (30+column*STEP, y, WIDTH, heights[key])
            cursor = y + heights[key] + 44
    # Root-first relaxation aligns serial chains without moving sibling nodes
    # through one another. Parents of mergers remain distinct actual nodes.
    for column in sorted(columns):
        cursor = 92
        for key in sorted(columns[column], key=lambda k:order[k]):
            values = sorted(positions[p][1]+positions[p][3]/2 for p in incoming[key] if ranks[p]<column)
            x, y, w, h = positions[key]
            if len(values) == 1 and len(successors[next(p for p in incoming[key] if ranks[p]<column)]) == 1:
                y = values[0]-h/2
            y = max(cursor, y)
            positions[key] = (x, y, w, h)
            cursor = y+h+44
    width = (max(ranks.values())+1)*STEP+40 if ranks else 700
    height = max([420]+[y+h+50 for x,y,w,h in positions.values()])
    return {'positions': positions, 'node_columns': ranks,
            'component_tops': dict.fromkeys((n['component'] for n in nodes.values()), 72),
            'bounds': (0,0,width,height)}


def route_edges(nodes, edges, positions, columns):
    routes = {}
    # All vertical channels are in gaps between columns. A long/backward
    # connection takes the empty strip above all device rectangles. Crossings
    # have no junction dots; coincident output stubs can share a trunk.
    ceiling = min([92]+[p[1] for p in positions.values()])-24
    for edge in edges:
        sx,sy,sw,sh = positions[edge['source']]
        tx,ty,tw,th = positions[edge['target']]
        src, dst = nodes[edge['source']], nodes[edge['target']]
        out = output_offset(src,sw,sh,edge['source_pin'])
        if out is None:
            raise ValueError('An input-only subsystem cannot be an edge source')
        pins, _ = port_offsets(dst,tw,th)
        pin = edge['target_pin']
        index = pin_index(dst,pin)
        start, end = (sx+out[0],sy+out[1]), (tx+pins[index][0],ty+pins[index][1])
        distance = columns[edge['target']]-columns[edge['source']]
        if distance == 1:
            lane = (start[0]+end[0])/2
            if src.get('kind')=='wire':
                # Separate output nets must not share a vertical trunk merely
                # because their ports belong to the same physical instance.
                from .profiles import output_ports
                outputs=output_ports(src)
                slot=outputs.index(edge['source_pin'])
                if len(outputs)>1:lane += (slot/(len(outputs)-1)-.5)*min(48,max(0,end[0]-start[0]-24))
            points = [start,end] if start[1]==end[1] else [start,(lane,start[1]),(lane,end[1]),end]
        elif distance == 0:
            points = [start,(start[0]+22,start[1]),(start[0]+22,ty-18),
                      (tx-22,ty-18),(tx-22,end[1]),end]
        else:
            points = [start,(start[0]+22,start[1]),(start[0]+22,ceiling),
                      (tx-22,ceiling),(tx-22,end[1]),end]
        routes[edge['id']] = [point for i,point in enumerate(points) if not i or point != points[i-1]]
    return routes


class SpatialIndex:
    """Uniform cells with a separate wide-segment list; bounded index size."""
    def __init__(self, boxes, cell=512):
        self.boxes, self.cell, self.cells, self.wide = boxes, cell, defaultdict(list), []
        for key, (x1,y1,x2,y2) in boxes.items():
            a,b,c,d = int(x1//cell),int(y1//cell),int(x2//cell),int(y2//cell)
            if (c-a+1)*(d-b+1)>128:
                self.wide.append(key); continue
            for x in range(a,c+1):
                for y in range(b,d+1): self.cells[x,y].append(key)

    def query(self, bounds):
        x1,y1,x2,y2 = bounds
        a,b,c,d = (int(value//self.cell) for value in bounds)
        if (c-a+1)*(d-b+1) > max(256,len(self.cells)*2):
            candidates = self.boxes
        else:
            candidates = set(self.wide)
            for x in range(a,c+1):
                for y in range(b,d+1): candidates.update(self.cells.get((x,y),()))
        return [key for key in candidates if self.boxes[key][0]<=x2 and self.boxes[key][2]>=x1
                and self.boxes[key][1]<=y2 and self.boxes[key][3]>=y1]


def compute(payload):
    from .viewmodel import GraphView
    model = GraphView.__new__(GraphView)
    model.nodes, model.edges = payload['nodes'], payload['edges']
    model.layer_count = payload['layer_count']
    model.layout_mode = payload.get('layout_mode','branch')
    model._layout()
    return {key:getattr(model,key) for key in ('positions','node_columns','component_tops','bounds',
                                               'layer_spans','layer_columns','routes')}


def main():
    import json
    import sys
    with open(sys.argv[1], encoding='utf-8') as stream: payload = json.load(stream)
    result = compute(payload)
    with open(sys.argv[2], 'w', encoding='utf-8') as stream: json.dump(result,stream)


if __name__ == '__main__': main()
