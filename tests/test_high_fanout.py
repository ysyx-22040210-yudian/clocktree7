"""Traversal must stream every branch, release handles and survive alias cycles."""
from collections import Counter
import json
from pathlib import Path
import tkinter

ROOT = Path(__file__).resolve().parents[1]


class Netlist:
    def __init__(self, count=300):
        self.tcl = tkinter.Tcl()
        self.tcl.eval((ROOT/'npi/extract.tcl').read_text(encoding='utf-8').split('if {[catch {ct_main}')[0])
        self.objects = {
            'top.src':('npiNlInst','',''), 'top':('npiNlInst','',''),
            'top.src.o':('npiNlInstPort','npiNlOutput','top.src'),
            'top.net':('npiNlDeclNet','','top'),
            'top.alias':('npiNlPseudoNet','','top'),
        }
        # Keep this alias a declaration in the mock; real slice/concat bit
        # mapping is checked against Verdi by scripts/check_high_fanout.py.
        self.objects['top.alias']=('npiNlDeclNet','','top')
        loads=[]
        for i in range(count):
            inst='top.g%d' % i
            self.objects[inst]=('npiNlInst','','')
            self.objects[inst+'.i']=('npiNlInstPort','npiNlInput',inst)
            loads.append(inst+'.i')
        self.relations = {
            ('top.src.o','npiNlLoad'):['top.net'],
            ('top.net','npiNlConnectivity'):loads+['top.alias'],
            # Duplicate connections and a cycle must not multiply the FIFO.
            ('top.alias','npiNlConnectivity'):loads+['top.net'],
        }
        self.refs=Counter({'top.src.o':1})
        self.iterators={};self.sequence=0;self.events=[];self.journal=[];self.opens=Counter()
        self.fail_on_scan=None
        for name,fn in [('npi_nl_get_str',self.get_str),('npi_nl_get',self.get),
                        ('npi_nl_handle',self.handle),('npi_nl_iterate',self.iterate),
                        ('npi_nl_scan',self.scan),('npi_nl_release_handle',self.release)]:
            self.tcl.createcommand(name,fn)
        self.tcl.createcommand('test_emit',lambda s:self.events.append(json.loads(s)) or '')
        self.tcl.createcommand('test_journal',lambda *a:self.journal.append(a) or '')
        self.tcl.eval('''
            set ct_trace_scope top; set ct_top top; set ct_max_steps 0
            set ct_selected {}; set ct_instances {}; set ct_owner_paths {}; set ct_stream unused
            proc ct_emit {s} {test_emit $s}
            proc ct_journal {args} {test_journal {*}$args}
            proc flush {args} {}
            namespace eval ::npi_L1 {}
            proc ::npi_L1::npi_nl_trace_load_by_hdl2 {args} {error "Full load-list call is forbidden"}
        ''')
        for h in loads:
            key=self.tcl.call('ct_key',h)
            self.tcl.call('dict','set','ct_selected',key,(h.rsplit('.',1)[0],'i','input'))

    def acquire(self,h):
        if h:self.refs[h]+=1
        return h

    def get_str(self,*args):
        p,h=args[1],args[3]
        if not h:return ''
        kind,direction,owner=self.objects[h]
        return {'npiNlType':kind,'npiNlName':h.rsplit('.',1)[-1],
                'npiNlFullName':h,'npiNlDirection':direction,'npiNlCellType':'npiNlModuleCell'}.get(p,'')

    def get(self,*args):
        return 1 if args[1]=='npiNlSize' else 0

    def handle(self,*args):
        rel,h=args[1],args[3]
        kind,_,owner=self.objects[h]
        return self.acquire(owner if rel=='npiNlInst' else h if rel=='npiNlNet' and kind.endswith('Net') else '')

    def iterate(self,*args):
        rel,h=args[1],args[3]
        self.opens[(h,rel)]+=1
        self.sequence+=1;key='iterator%d'%self.sequence
        self.iterators[key]=iter(self.relations.get((h,rel),[]))
        self.refs[key]+=1
        return key

    def scan(self,*args):
        key=args[1]
        if self.fail_on_scan==key:raise RuntimeError('simulated native scan failure')
        h=next(self.iterators[key],None)
        if h is None:
            self.release('-object',key)
            return ''
        return self.acquire(h)

    def release(self,*args):
        h=args[1]
        self.refs[h]-=1
        assert self.refs[h]>=0,('Double release',h)
        self.iterators.pop(h,None)
        return 1

    def walk(self):
        self.tcl.call('ct_walk','top.src.o','load','top.src','o',1)
        assert {h:c for h,c in self.refs.items() if c}=={'top.src.o':1}
        assert not self.iterators
        return [e['result'] for e in self.events if e['event']=='trace_result']


def test_all_fanout_targets_stream_once_despite_duplicate_aliases():
    model=Netlist(1000)
    results=model.walk()
    assert len(results)==1000 and all(r['status']=='target' for r in results)
    assert len({r['instance'] for r in results})==1000
    assert max(model.opens.values())==1
    assert int(model.tcl.call('dict','get',model.tcl.getvar('ct_walk_stats'),'duplicates'))==1001
    records=[json.loads(e[2]) for e in model.journal if e[0]=='progress']
    assert any(e.get('api')=='npi_nl_iterate:npiNlConnectivity' for e in records)
    assert records[-1]['pending']==0 and records[-1]['results']==1000


def test_net_direction_filter_does_not_walk_other_drivers():
    model=Netlist(3)
    model.relations[('top.net','npiNlConnectivity')].insert(0,'top.src.o')
    results=model.walk()
    assert len(results)==3 and all(r['status']=='target' for r in results)


def test_iteration_error_is_reported_incomplete_and_iterator_is_released():
    model=Netlist(3)
    model.fail_on_scan='iterator2'
    results=model.walk()
    assert len(results)==1 and results[0]['status']=='error'
    assert model.events[-1]['event']=='trace_end'


def test_explicit_step_limit_releases_queued_fanout_without_marking_complete():
    model=Netlist(1000)
    model.tcl.setvar('ct_max_steps',2)
    results=model.walk()
    assert len(results)==1 and results[0]['status']=='trace_limit'


def test_external_hierarchy_is_never_expanded():
    model=Netlist(3)
    model.objects['other.net']=('npiNlDeclNet','','top')
    model.relations[('top.net','npiNlConnectivity')].append('other.net')
    results=model.walk()
    assert sum(r['status']=='target' for r in results)==3
    assert sum(r['status']=='scope_boundary' for r in results)==1
    assert not any(h=='other.net' for h,_ in model.opens)


def test_unloaded_alias_net_is_a_leaf_not_a_logic_loop():
    model=Netlist(0)
    results=model.walk()
    assert len(results)==1 and results[0]['status']=='no_load'


def test_driver_cannot_hide_an_outside_parent_net_and_reenter_scope():
    model=Netlist(0)
    model.objects['top.src.o']=('npiNlInstPort','npiNlInput','top.src')
    model.objects['other.net']=('npiNlDeclNet','','top')
    original=model.handle
    def handle(*args):
        if args[1]=='npiNlNet' and args[3]=='top.src.o':return model.acquire('other.net')
        return original(*args)
    model.tcl.createcommand('npi_nl_handle',handle)
    model.tcl.call('ct_walk','top.src.o','driver','top.src','o',1)
    results=[e['result'] for e in model.events if e['event']=='trace_result']
    assert len(results)==1 and results[0]['status']=='scope_boundary'
    assert not model.opens
    assert {h:c for h,c in model.refs.items() if c}=={'top.src.o':1}
