import copy
import xml.etree.ElementTree as ET

from clocktree.viewmodel import GraphView, clip_segment


def example():
    nodes=[]
    for path,layer,component in [('top.source.u0',1,0),('top.gates.u0',2,0),('top.gates.u1',2,0),('top.end.u0',5,0),('top.other.u0',1,1)]:
        nodes.append(dict(id=path,path=path,layer=layer,component=component,module='gate',inputs=['i'],output='o'))
    links=[('top.source.u0','top.gates.u0'),('top.gates.u0','top.gates.u1'),('top.gates.u1','top.end.u0')]
    index={n['id']:n for n in nodes}
    edges=[dict(id=str(i),source=a,source_pin='o',target=b,target_pin='i',layer_delta=index[b]['layer']-index[a]['layer']) for i,(a,b) in enumerate(links)]
    return {'nodes':nodes,'edges':edges}


def test_aggregation_keeps_all_real_instances_edges_and_source_data():
    data=example()
    saved=copy.deepcopy(data)
    view=GraphView(data).project(mode='hierarchy',depth=2)
    group=next(n for n in view.nodes.values() if n.get('aggregate'))
    assert group['members']==['top.gates.u0','top.gates.u1']
    assert group['internal_edges']==1
    assert sum(len(e['members']) for e in view.edges)+group['internal_edges']==len(data['edges'])
    assert view.actual_count==5 and len(view.nodes)==4
    assert data==saved
    view.project(mode='instances')
    assert len(view.nodes)==5 and len(view.edges)==3
    assert view.positions['top.gates.u0'][0]<view.positions['top.gates.u1'][0]


def test_scope_and_component_never_hide_other_networks_in_canonical_result():
    view=GraphView(example())
    view.project(component=1)
    assert list(view.nodes)==['top.other.u0']
    assert len(view.original)==5
    view.project(scope='top.gates')
    assert len(view.nodes)==2 and len(view.edges)==1
    view.project()
    assert len(view.nodes)==5
    assert len(view.path_edges('top.end.u0','upstream'))==3
    assert len(view.find('gates'))==2


def test_svg_exports_all_nodes_and_real_edges_in_current_scope():
    view=GraphView(example()).project()
    xml=ET.fromstring(view.svg())
    assert len(xml.findall('.//{http://www.w3.org/2000/svg}title'))==5
    assert len(xml.findall('.//{http://www.w3.org/2000/svg}polyline'))==3


def test_viewport_clipping_preserves_wire_direction_and_rejects_outside():
    assert clip_segment((5,50),(5,-10),(0,0,20,20))==((5,20),(5,0))
    assert clip_segment((-10,5),(50,5),(0,0,20,20))==((0,5),(20,5))
    assert clip_segment((30,0),(30,40),(0,0,20,20)) is None


def test_skipped_layer_wire_does_not_cross_unrelated_device_body():
    nodes=[dict(id='u'+str(i),path='u'+str(i),layer=i,component=0,module='div',kind='div',inputs=['i'],output='o')
           for i in (1,2,3)]
    edge=dict(id='skip',source='u1',source_pin='o',target='u3',target_pin='i',layer_delta=2)
    view=GraphView({'nodes':nodes,'edges':[edge]}).project()
    x,y,w,h=view.positions['u2']
    points=view.edge_points(edge)
    assert all(clip_segment(a,b,(x,y,x+w,y+h)) is None for a,b in zip(points,points[1:]))
