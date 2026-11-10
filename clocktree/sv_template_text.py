"""Literal SV template tokens, shared by generation and the native editor."""
from collections import namedtuple
from functools import lru_cache
import re


ROLES={'var_pllsel':'pll_sel','var_div':'div','var_swdone':'sw_done','var_gt0':'gt','var_gt1':'gt','var_gt2':'gt','var_gt':'gt'}
CLOCK_VARS={'var_pll0','var_pll1','var_pll2','var_pll3','var_clkip','var_rst'}
VARIABLE_BUTTONS=('var_clkip','var_rst','var_pll0','var_pll1','var_pll2','var_pll3',
                  'var_pllsel','var_div','var_swdone','var_gt0','var_gt1','var_gt2')
NAMES=frozenset(ROLES)|frozenset(CLOCK_VARS)
_LONGEST=sorted(NAMES,key=lambda value:(-len(value),value))
_CANDIDATE=re.compile(r'(?<!\$)\$(?:\{(?P<braced>var_[A-Za-z0-9_]+)(?P<close>\})?|(?P<bare>var_[A-Za-z0-9_]+))')
_WORD=re.compile(r'[A-Za-z0-9_]')
TemplateToken=namedtuple('TemplateToken','start end name embedded error')


@lru_cache(maxsize=8)
def template_tokens(template):
    """Match known names before underscore suffixes; retain unknown-name errors.

    Longest-name matching keeps gt0 separate from the legacy gt alias, while
    pll10 remains an error. Explicit ${var_name} delimiters are also accepted.
    An existing $$var_name escape stays literal, including both dollar signs.
    """
    raw=[]
    for match in _CANDIDATE.finditer(template):
        name=match.group('braced') or match.group('bare');end=match.end();error=''
        if match.group('braced'):
            if not match.group('close'):error='模板变量缺少右花括号：'+match.group(0)
        else:
            known=next((v for v in _LONGEST if name==v or name.startswith(v+'_')),None)
            if known:name=known;end=match.start()+len(name)+1
        if not error and name not in NAMES:error='未知模板变量：$'+name
        raw.append(TemplateToken(match.start(),end,name,False,error))
    result=[]
    for i,token in enumerate(raw):
        before=template[token.start-1:token.start] if token.start else ''
        after=template[token.end:token.end+1]
        adjacent=(i>0 and raw[i-1].end==token.start) or (i+1<len(raw) and token.end==raw[i+1].start)
        result.append(token._replace(embedded=bool(_WORD.fullmatch(before) or _WORD.fullmatch(after) or adjacent)))
    return tuple(result)


def variables(template):
    tokens=template_tokens(template);errors=sorted({t.error for t in tokens if t.error})
    if errors:raise ValueError('；'.join(errors))
    return {t.name for t in tokens}


def identifier_fragment(value):
    # Dots, bit/range brackets and expression punctuation cannot be part of an
    # ordinary SV identifier. Keep the entire path and all index digits.
    fragment='_'.join(re.findall(r'[A-Za-z0-9_]+',value))
    if not fragment:raise ValueError('变量值无法组成 SV 名称：'+repr(value))
    return ('_' if fragment[0].isdigit() else '')+fragment


def render(template,values):
    missing=variables(template)-set(values)
    if missing:raise ValueError('缺少变量：'+', '.join('$'+v for v in sorted(missing)))
    result=[];cursor=0
    for token in template_tokens(template):
        result.append(template[cursor:token.start]);value=values[token.name]
        result.append(identifier_fragment(value) if token.embedded else value);cursor=token.end
    result.append(template[cursor:])
    return ''.join(result)
