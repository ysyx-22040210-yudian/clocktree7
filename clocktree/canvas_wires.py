"""Draw nested runs of one output net once. Every interval retains its actual edges."""
from collections import defaultdict


class SharedTrunk:
    def __init__(self):
        self.edges = []
        self.last_highlight = -1

    def highlight(self, ids):
        self.last_highlight = -1
        if ids:
            self.last_highlight = max((i for i, edge in enumerate(self.edges) if edge['id'] in ids), default=-1)


class TrunkInterval:
    """All routes reaching this interval form a suffix of one shared edge list."""
    def __init__(self, trunk, start):
        self.trunk, self.start = trunk, start

    def __len__(self):
        return len(self.trunk.edges) - self.start

    def __iter__(self):
        edges = self.trunk.edges
        return (edges[i] for i in range(self.start, len(edges)))

    def highlighted(self):
        return self.start <= self.trunk.last_highlight


def partition_trunks(segments):
    groups = defaultdict(list)
    for segment in segments.values():
        a, b = segment['a'], segment['b']
        if segment['arrow'] or a == b or (a[0] != b[0] and a[1] != b[1]):
            continue
        axis = 1 if a[0] == b[0] else 0
        edge = segment['edges'][0]
        key = (tuple(a), axis, b[axis] > a[axis], edge['source'], edge['source_pin'],
               segment['reverse'], segment['cycle'])
        groups[key].append(segment)

    trunks = []
    for (origin, axis, forward, _, _, _, _), group in groups.items():
        if len(group) < 16:
            continue
        trunk = SharedTrunk()
        trunks.append(trunk)
        previous = origin
        for segment in sorted(group, key=lambda s:s['b'][axis], reverse=not forward):
            interval = TrunkInterval(trunk, len(trunk.edges))
            trunk.edges.extend(segment['edges'])
            segment['edges'] = interval
            segment['a'] = previous
            previous = segment['b']
    return trunks
