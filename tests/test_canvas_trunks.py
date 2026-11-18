import pytest

from clocktree.canvas_wires import TrunkInterval, partition_trunks


@pytest.mark.parametrize('axis,sign', [(0,1),(0,-1),(1,1),(1,-1)])
def test_shared_trunk_membership_and_highlight_stop_at_each_tap(axis, sign):
    def point(offset):
        p=[40,80]
        p[axis]+=sign*offset
        return tuple(p)
    def span(tap, port='clk', **flags):
        edge={'id':port+str(tap),'source':'root','source_pin':port}
        data=dict(a=point(0),b=point(tap),arrow=False,reverse=False,cycle=False,edges=[edge])
        data.update(flags)
        return data
    segments={tap:span(tap) for tap in range(1,33)}
    for tag,extra in [('arrow',dict(arrow=True)),('reverse',dict(reverse=True)),('cycle',dict(cycle=True))]:
        segments[tag]=span(32,**extra)
    segments['other']=span(32,'clk1')
    trunks=partition_trunks(segments)
    assert len(trunks)==1
    trunk=trunks[0]
    for tag in ('arrow','reverse','cycle','other'):
        assert not isinstance(segments[tag]['edges'],TrunkInterval)
    for tap in range(1,33):
        s=segments[tap]
        assert (s['a'],s['b'])==(point(tap-1),point(tap))
        edges=s['edges']
        assert len(edges)==33-tap
        assert {e['id'] for e in edges}=={'clk'+str(n) for n in range(tap,33)}
    trunk.highlight({'clk3','clk15'})
    assert [tap for tap in range(1,33) if segments[tap]['edges'].highlighted()]==list(range(1,16))
    trunk.highlight(set())
    assert not any(segments[tap]['edges'].highlighted() for tap in range(1,33))
