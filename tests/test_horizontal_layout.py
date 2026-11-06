import copy
import random
import xml.etree.ElementTree as ET

from clocktree.viewmodel import GraphView, clip_segment, NODE_WIDTH, COLUMN_STEP
from clocktree.indexed_view import IndexedGraphView
from clocktree.storage import index_graph, new_store
from functools import partial

# These are regression checks for the retained, explicitly selectable layer layout.
GraphView = partial(GraphView, layout_mode='layers')
IndexedGraphView = partial(IndexedGraphView, layout_mode='layers')


def graph(specs,links):
    nodes=[dict(id=name,path=name,module='cell_'+kind,kind=kind,layer=layer,component=component,
                inputs=['a','b'] if kind=='mux' else ['i'],output='o') for name,layer,kind,component in specs]
    lookup={node['id']:node for node in nodes}
    edges=[dict(id='e'+str(index),source=a,source_pin='o',target=b,target_pin=pin,
                layer_delta=lookup[b]['layer']-lookup[a]['layer'],evidence={})
           for index,(a,b,pin) in enumerate(links)]
    return {'nodes':nodes,'edges':edges,'components':[],'boundaries':[],'diagnostics':[],
            'clock_sources':[],'metadata':{},'summary':{},'config':{}}


def assert_clear(view):
    # Small adversarial fixtures only; production layout never does an N^2 scan.
    for key,(x,y,w,h) in view.positions.items():
        band,width=view.layer_spans[view.nodes[key]['layer']]
        assert band<=x and x+w<=band+width
        for other,(a,b,c,d) in view.positions.items():
            if other==key:continue
            assert x+w<=a or a+c<=x or y+h<=b or b+d<=y
    for edge in view.edges:
        points=view.edge_points(edge)
        for key,(x,y,w,h) in view.positions.items():
            if key in (edge['source'],edge['target']):continue
            assert all(clip_segment(a,b,(x+.1,y+.1,x+w-.1,y+h-.1)) is None for a,b in zip(points,points[1:])),(edge,key)


def test_same_layer_chain_is_horizontal_and_next_layer_moves_right():
    data=graph([('mux',1,'mux',0),('gate',2,'gate',0),('occ',2,'occ',0),
                ('div',2,'div',0),('out',3,'gate',0)],
               [('mux','gate','i'),('gate','occ','i'),('occ','div','i'),('div','out','i')])
    original=copy.deepcopy(data)
    view=GraphView(data).project()
    assert len({view.positions[key][1] for key in ('gate','occ','div','out')})==1
    assert view.positions['gate'][0]<view.positions['occ'][0]<view.positions['div'][0]<view.positions['out'][0]
    assert view.layer_columns=={1:1,2:3,3:1}
    assert len(view.edge_points(view.edges[1]))==2
    assert_clear(view)
    assert data==original


def test_fanout_merge_skip_and_independent_network_preserve_every_node():
    data=graph([('root',1,'gate',0),('a',2,'gate',0),('b',2,'gate',0),('merge',2,'mux',0),
                ('end',4,'occ',0),('other',2,'gate',1)],
               [('root','a','i'),('root','b','i'),('a','merge','a'),('b','merge','b'),('merge','end','i'),('root','end','i')])
    view=GraphView(data).project()
    assert view.positions['a'][0]==view.positions['b'][0]<view.positions['merge'][0]
    assert view.positions['a'][1]!=view.positions['b'][1]
    assert view.component_tops[1]>max(y+h for key,(x,y,w,h) in view.positions.items() if key!='other')
    assert view.layer_columns[3]==1
    assert len(view.edges)==6 and len(view.nodes)==6
    assert_clear(view)
    shuffled=copy.deepcopy(data);random.Random(17).shuffle(shuffled['nodes']);shuffled['edges'].reverse()
    other=GraphView(shuffled).project()
    assert other.positions==view.positions


def test_skipping_same_layer_columns_routes_around_intermediate_device():
    data=graph([(key,2,'gate',0) for key in ('a','b','c')],
               [('a','b','i'),('b','c','i'),('a','c','i')])
    view=GraphView(data).project()
    assert len(view.edge_points(view.edges[-1]))==6
    assert_clear(view)


def test_cycle_self_loop_and_reverse_layer_remain_visible_without_infinite_ranking():
    data=graph([('a',1,'gate',0),('b',1,'gate',0),('c',1,'div',0),('d',2,'occ',0)],
               [('a','b','i'),('b','a','i'),('b','c','i'),('c','c','i'),('c','d','i'),('d','a','i')])
    view=GraphView(data).project()
    assert view.positions['a'][0]==view.positions['b'][0]<view.positions['c'][0]<view.positions['d'][0]
    assert len(view.edges)==6
    assert_clear(view)


def test_indexed_projection_aggregation_and_svg_use_the_same_expanded_bands(tmp_path):
    data=graph([('top.a.u',1,'gate',0),('top.b.u',1,'occ',0),('top.c.u',1,'div',0)],
               [('top.a.u','top.b.u','i'),('top.b.u','top.c.u','i')])
    plain=GraphView(data).project()
    indexed=IndexedGraphView(index_graph(data,new_store(tmp_path)))
    for mode in ('instances','hierarchy'):
        indexed.project(mode,depth=2)
        assert indexed.positions==plain.positions
        assert indexed.layer_spans==plain.layer_spans
        assert [indexed.edge_points(edge) for edge in indexed.edges]==[plain.edge_points(edge) for edge in plain.edges]
    xml=ET.fromstring(indexed.svg())
    bands=xml.findall('.//{http://www.w3.org/2000/svg}rect[@class="layer-band"]')
    assert float(bands[0].get('width'))==3*COLUMN_STEP-24
    indexed.project('hierarchy',depth=1)
    assert len(indexed.nodes)==1 and indexed.layer_columns[1]==1


def test_long_chain_does_not_use_recursion_or_drop_nodes():
    count=1600
    names=['u{:04d}'.format(i) for i in range(count)]
    view=GraphView(graph([(key,1,'gate',0) for key in names],
                        [(a,b,'i') for a,b in zip(names,names[1:])])).project()
    assert len(view.positions)==count and len(view.edges)==count-1
    assert view.layer_columns[1]==count
    assert len({position[1] for position in view.positions.values()})==1
    assert view.bounds[2]>view.positions[names[-1]][0]+NODE_WIDTH
