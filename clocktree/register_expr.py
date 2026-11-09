"""Bit-accurate expressions from NPI objects (never parse RTL source text).

Vectors are LSB first. Unsupported constructs are explicit diagnostics, not
guessed bit slices. A dependency through logic is distinct from a direct wire.
"""
from collections import namedtuple
import re


class MappingError(ValueError):
    pass


Bit = namedtuple('Bit', 'signals value relation')
ZERO = Bit((), 0, 'constant')
ONE = Bit((), 1, 'constant')


def signal(name):return Bit((name,), None, 'direct')


def combine(bits, relation='logic'):
    return Bit(tuple(sorted({s for b in bits for s in b.signals})), None,
               'conditional' if any(b.relation == 'conditional' for b in bits) else relation)


def bit_not(a):
    if a.value is not None:return ONE if not a.value else ZERO
    return Bit(a.signals,None,'direct' if a.relation=='inverted' else ('inverted' if a.relation=='direct' else a.relation))


def binary(op,a,b):
    if op=='and':
        if a.value==0 or b.value==0:return ZERO
        if a.value==1:return b
        if b.value==1:return a
    if op=='or':
        if a.value==1 or b.value==1:return ONE
        if a.value==0:return b
        if b.value==0:return a
    if op=='xor':
        if a.value==0:return b
        if b.value==0:return a
        if a.value==1:return bit_not(b)
        if b.value==1:return bit_not(a)
    if a==b:
        if op!='xor':return a
    return combine([a,b])


def resize(bits,width,signed=False):
    if width<1:raise MappingError('unknown_width')
    return (bits+[bits[-1] if signed and bits else ZERO]*max(0,width-len(bits)))[:width]


def number(bits):
    if not bits or any(b.value is None for b in bits):return None
    return sum(b.value<<i for i,b in enumerate(bits))


def integer(bits,signed=False):
    value=number(bits)
    return value-(1<<len(bits)) if value is not None and signed and bits[-1].value==1 else value


def truth(bits):
    if any(b.value==1 for b in bits):return ONE
    if bits and all(b.value==0 for b in bits):return ZERO
    return combine(bits)


def index_range(left,right):
    # The rightmost declared index is the least-significant bit.
    return list(range(right,left+(1 if left>=right else -1),1 if left>=right else -1))


class Expressions:
    def __init__(self, assumptions=('dft_mode',), resolver=None):
        self.assumptions=tuple(assumptions)
        self.used=set()
        self.resolver=resolver
        self.resolving_condition=False

    def condition(self,expr):
        previous=self.resolving_condition;self.resolving_condition=True
        try:return truth(self.vector(expr))
        finally:self.resolving_condition=previous

    def assumed(self,name):
        # A bare identifier matches that exact leaf name, never a substring.
        base=re.sub(r'\[-?\d+\]$','',name)
        for item in self.assumptions:
            if base==item or ('.' not in item and base.rsplit('.',1)[-1]==item):
                self.used.add(base)
                return True
        return False

    def names(self,expr):
        bits=self.vector(expr,resolve=False)
        if any(b.relation!='direct' or len(b.signals)!=1 for b in bits):
            raise MappingError('non_signal_lvalue')
        return [b.signals[0] for b in bits]

    def vector(self,e,width=None,resolve=True,depth=0,context_signed=None):
        if not e:raise MappingError('unconnected')
        if depth>128:raise MappingError('expression_depth')
        kind=e.get('type','');size=e.get('width',-1)
        def vec(child,context=None,signed=None):return self.vector(child,context,resolve,depth+1,signed)
        if kind in ('npiExtRef','npiRefObj'):
            result=vec(e.get('actual'))
        elif kind in ('npiConstant','npiParameter','npiParameterBit','npiGenVar'):
            value=e.get('value','').lower().replace('_','')
            if not value or any(c not in '01xz' for c in value):raise MappingError('unavailable_constant')
            result=[ONE if c=='1' else ZERO if c=='0' else Bit((),None,'unknown_constant') for c in value[::-1]]
            result=resize(result,size or len(result),e.get('signed',False))
        elif kind in ('npiPartSelect','npiIndexedPartSelect','npiBitSelect','npiNetBit','npiRegBit','npiVarBit'):
            parent=e.get('parent') or {}
            name=parent.get('full')
            if not name:raise MappingError('unaddressable_selection')
            ranges=parent.get('ranges',[])
            if len(ranges)>1:raise MappingError('multidimensional_selection')
            if kind=='npiPartSelect':
                try:left,right=int(e['left']),int(e['right'])
                except (KeyError,ValueError,TypeError):raise MappingError('dynamic_index')
                indices=index_range(left,right)
            elif kind=='npiIndexedPartSelect':
                # Indexed select bounds must be elaboration constants, not a
                # runtime selector that happened to evaluate to zero.
                base=integer(self.vector(e.get('base'),resolve=False,depth=depth+1),(e.get('base') or {}).get('signed',False))
                count=number(self.vector(e.get('count'),resolve=False,depth=depth+1))
                if base is None or not count:raise MappingError('dynamic_index')
                if not ranges:raise MappingError('unknown_declared_range')
                lo,hi=(base,base+count-1) if e.get('select')=='npiPosIndexed' else (base-count+1,base)
                descending=int(ranges[0][0])>=int(ranges[0][1])
                indices=index_range(hi,lo) if descending else index_range(lo,hi)
            else:
                indices=e.get('indices',[])
                if len(indices)!=1 or not re.fullmatch(r'-?\d+',str(indices[0])):raise MappingError('dynamic_or_multidimensional_index')
                indices=[int(indices[0])]
            if size!=len(indices):raise MappingError('multidimensional_selection')
            if ranges:
                limits=sorted(map(int,ranges[0]))
                if any(i<limits[0] or i>limits[1] for i in indices):raise MappingError('out_of_range')
            result=[signal(name+'[{}]'.format(i)) for i in indices]
        elif kind=='npiOperation':
            op=e.get('op');args=e.get('args',[])
            size=max(size,width or 0)
            # SV context propagates unsignedness into both operands. Extending
            # a narrow signed operand independently would invent high bits.
            signed=e.get('signed',False) if context_signed is None else context_signed
            if op=='npiConcatOp':
                result=[bit for arg in reversed(args) for bit in vec(arg)]
            elif op=='npiMultiConcatOp':
                repeat=number(vec(args[0]))
                if repeat is None or repeat<0:raise MappingError('dynamic_repeat')
                part=[bit for arg in reversed(args[1:]) for bit in vec(arg)]
                result=part*repeat
                if len(result)!=e.get('width'):raise MappingError('repeat_width_mismatch')
            elif op=='npiConditionOp':
                cond=self.condition(args[0])
                if cond.value is not None:result=vec(args[1] if cond.value else args[2],size,signed)
                else:
                    yes,no=vec(args[1],size,signed),vec(args[2],size,signed)
                    result=[a if a==b else combine([cond,a,b],'conditional') for a,b in zip(yes,no)]
            elif op in ('npiTypeCastOp','npiSigningCastOp','npiSizeCastOp','npiConstCastOp','npiPlusOp'):
                if len(args)!=1:raise MappingError('unsupported_cast')
                result=resize(vec(args[0]),size,args[0].get('signed',False))
            elif op=='npiBitNegOp':result=[bit_not(b) for b in vec(args[0],size,signed)]
            elif op=='npiNotOp':result=[bit_not(truth(vec(args[0])))]
            elif op in ('npiBitAndOp','npiBitOrOp','npiBitXorOp','npiBitXNorOp','npiBitXnorOp'):
                operation={'npiBitAndOp':'and','npiBitOrOp':'or'}.get(op,'xor')
                result=[binary(operation,a,b) for a,b in zip(vec(args[0],size,signed),vec(args[1],size,signed))]
                if op in ('npiBitXNorOp','npiBitXnorOp'):result=[bit_not(b) for b in result]
            elif op in ('npiLogAndOp','npiLogOrOp'):
                result=[binary('and' if op=='npiLogAndOp' else 'or',truth(vec(args[0])),truth(vec(args[1])))]
            elif op in ('npiEqOp','npiNeqOp','npiCaseEqOp','npiCaseNeqOp'):
                n=max(a.get('width',0) for a in args)
                compare_signed=all(a.get('signed',False) for a in args)
                equal=ONE
                for a,b in zip(vec(args[0],n,compare_signed),vec(args[1],n,compare_signed)):equal=binary('and',equal,bit_not(binary('xor',a,b)))
                result=[bit_not(equal) if op in ('npiNeqOp','npiCaseNeqOp') else equal]
            elif op in ('npiLShiftOp','npiRShiftOp','npiArithLShiftOp','npiArithRShiftOp'):
                amount=number(vec(args[1]));source=vec(args[0],size,signed)
                if amount is None:raise MappingError('dynamic_shift')
                amount=min(size,amount)
                if op in ('npiLShiftOp','npiArithLShiftOp'):result=([ZERO]*amount+source)[:size]
                else:result=source[amount:]+[source[-1] if op=='npiArithRShiftOp' and signed else ZERO]*amount
            elif op in ('npiAddOp','npiSubOp'):
                a,b=vec(args[0],size,signed),vec(args[1],size,signed);carry=ONE if op=='npiSubOp' else ZERO
                if op=='npiSubOp':b=[bit_not(x) for x in b]
                result=[]
                for x,y in zip(a,b):
                    result.append(binary('xor',binary('xor',x,y),carry))
                    carry=binary('or',binary('and',x,y),binary('and',carry,binary('xor',x,y)))
            elif op in ('npiGtOp','npiGeOp','npiLtOp','npiLeOp'):
                signed=all(a.get('signed',False) for a in args)
                n=max(a.get('width',0) for a in args)
                values=[integer(resize(vec(a),n,signed),signed) for a in args]
                if any(v is None for v in values):result=[combine([b for a in args for b in vec(a)])]
                else:
                    x,y=values;value={'npiGtOp':x>y,'npiGeOp':x>=y,'npiLtOp':x<y,'npiLeOp':x<=y}[op]
                    result=[ONE if value else ZERO]
            else:raise MappingError('unsupported_operator:'+str(op))
        elif kind in ('npiNet','npiReg','npiLogicVar','npiBitVar','npiIntVar','npiIntegerVar','npiVar'):
            name=e.get('full')
            if not name:raise MappingError('unaddressable_signal')
            ranges=e.get('ranges',[])
            if len(ranges)>1:raise MappingError('multidimensional_packed_signal')
            if ranges:
                try:indices=index_range(*map(int,ranges[0]))
                except (ValueError,TypeError):raise MappingError('unknown_declared_range')
                if len(indices)!=size:raise MappingError('multidimensional_packed_signal')
                result=[signal(name+'[{}]'.format(i)) for i in indices]
            elif size==1:result=[signal(name)]
            else:raise MappingError('unknown_declared_range')
        else:raise MappingError('unsupported_expression:'+kind)
        if resolve:
            updated=[]
            for bit in result:
                if bit.relation=='direct' and len(bit.signals)==1:
                    name=bit.signals[0]
                    if self.assumed(name):bit=ZERO
                    elif self.resolver and self.resolving_condition:
                        value=self.resolver(name)
                        if value in (0,1):bit=ONE if value else ZERO
                updated.append(bit)
            result=updated
        if width is not None:result=resize(result,width,e.get('signed',False) if context_signed is None else context_signed)
        return result


def event_edges(expr):
    result=[];pending=[expr]
    while pending:
        item=pending.pop()
        if not item:continue
        if item.get('op') in ('npiPosedgeOp','npiNegedgeOp'):result.append(item.get('text') or item['op'])
        pending.extend(item.get('args',[]))
    return result


def sequential_proof(row):
    ancestors=row.get('ancestors',[])
    processes=[a for a in ancestors if a['type'] in ('npiAlways','npiInitial')]
    edges=[edge for a in ancestors if a['type']=='npiEventControl' for edge in event_edges(a.get('condition'))]
    if processes and processes[-1]['type']=='npiAlways' and edges:
        return {'kind':'flipflop','basis':'edge_triggered_always','events':edges,'process':processes[-1],
                'assignment':row['statement']}
    return None
