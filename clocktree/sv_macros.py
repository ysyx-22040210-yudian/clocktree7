"""Format branch source with one header of configured SystemVerilog macros."""
from functools import lru_cache
from itertools import chain
import re

from .sv_template_text import template_tokens


NAME = re.compile(r'[a-zA-Z_][a-zA-Z0-9_$]*')
DIRECTIVE_OPERAND = re.compile(r'[ \t]+[a-zA-Z_][a-zA-Z0-9_$]*')
DIRECTIVES = frozenset(('define undef undefine undefineall ifdef ifndef elsif else endif include line timescale '
                        'default_nettype resetall celldefine endcelldefine unconnected_drive '
                        'nounconnected_drive begin_keywords end_keywords pragma __FILE__ __LINE__').split())
DECLARATIONS = frozenset(('define', 'undef', 'undefine', 'undefineall'))
STRING_BODY = r'(?:\\(?:\r\n|[\s\S])|[^"\\\r\n])*'
STRING_LITERAL = re.compile('"' + STRING_BODY + '"')
LEX = re.compile(
    r'(?P<comment>//[^\r\n]*|/\*[\s\S]*?(?:\*/|\Z))'
    r'|(?P<macro_string>`"(?:`\\`"|\\(?:\r\n|[\s\S])|(?!`")[^\\\r\n])*`")'
    r'|(?P<string>"' + STRING_BODY + r'(?:"|(?=[\r\n]|\Z)))'
    r'|(?P<escaped>\\[^\s]+)'
    r'|(?P<macro>`[a-zA-Z_][a-zA-Z0-9_$]*|``|`")'
    r"|(?P<number>(?:[0-9][0-9_]*[ \t]*)?'[sS]?[bBoOdDhH][ \t]*[0-9a-fA-F_xXzZ?]+"
    r"|'[01xXzZ]|[0-9][0-9_]*(?:\.[0-9][0-9_]*)?(?:[eE][+-]?[0-9][0-9_]*)?(?:ms|us|ns|ps|fs|s)?)"
    r'|(?P<word>[a-zA-Z_$][a-zA-Z0-9_$]*)|(?P<space>\s+)'
    r'|(?P<operator><<<=?|>>>=?|<<=?|>>=?|\|->|\|=>|<->|===|!==|==\?|!=\?|&&&|'
    r'::|\+:|-:|\+\+|--|&&|\|\||\*\*|##|->|=>|<=|>=|==|!=|~[&|^]|\^~|[+\-*/%&|^]=)|.')
ESCAPE = re.compile(r'\\(?:\r?\n|[0-7]{1,3}|x[0-9a-fA-F]{1,2}|[\s\S])')
QUOTED_SPECIAL = re.compile(ESCAPE.pattern + r'|`|/(?=[/*])|(?<=\*)/')


def quote_special(match):
    text = match.group()
    if len(text) > 1:
        return {r'\"': r'\042', '\\`': r'\140'}.get(text, text)
    return '\\{:03o}'.format(ord(text))


def lex(source, directives=DIRECTIVES):
    """Yield (kind, spelling). A template can also contain existing directives."""
    pos = 0
    while pos < len(source):
        match = LEX.match(source, pos)
        kind, spelling = match.lastgroup, match.group()
        end = match.end()
        if kind == 'macro' and spelling[1:] in directives:
            kind = 'directive'
            directive = spelling[1:]
            if directive in ('ifdef', 'ifndef', 'elsif', 'undef', 'undefine'):
                operand = DIRECTIVE_OPERAND.match(source, end)
                if operand:
                    end = operand.end()
                    spelling = source[pos:end]
            elif directive not in ('else', 'endif', 'undefineall', '__FILE__', '__LINE__'):
                end = source.find('\n', end)
                while end >= 0 and source[max(0, end-2):end].endswith(('\\', '\\\r')):
                    end = source.find('\n', end + 1)
                if end < 0:
                    end = len(source)
                spelling = source[pos:end]
        yield kind, spelling
        pos = end


@lru_cache(maxsize=8)
def clean_template(source, mask=False):
    """Strip comments and template macro declarations before resolving variables.

    The editor uses a same-length mask to keep source offsets. Scan comments
    first: a block comment can span a define's EOL, and other directives can
    contain quoted paths that look like comments.
    """
    def blank(text):
        if mask:
            return re.sub(r'[^\r\n]', ' ', text)
        newlines = ''.join(re.findall(r'\r\n|[\r\n]', text))
        return newlines or ' '

    if not mask and '\r' in source:
        source = source.replace('\r\n', '\n').replace('\r', '\n')
    if '//' in source or '/*' in source:
        source = ''.join(blank(m.group()) if m.lastgroup == 'comment' else m.group()
                         for m in LEX.finditer(source))
    if '`' in source:
        source = ''.join(blank(token) if kind == 'directive' else token
                         for kind, token in lex(source, DECLARATIONS))
    if mask:
        return source
    # Leave indentation and continued string contents intact.
    return re.sub(r'\A(?:[ \t]*\r?\n)+', '', source).rstrip()


def can_default(text):
    """A default argument must fit between the formal's '=' and its ')'."""
    closing = {'(': ')', '[': ']', '{': '}'}
    stack = []
    for kind, token in lex(text):
        if token in closing:
            stack.append(closing[token])
        elif token in (')', ']', '}'):
            if not stack or stack.pop() != token:
                return False
        elif token == ',' and not stack:
            return False
        elif kind == 'escaped' and text.endswith(token):
            return False
    return not stack


def template_macros(settings):
    rows = settings.get('macros', [])
    if not isinstance(rows, list):
        raise ValueError('macros 需要宏名和替换原文的列表')
    pairs = []
    for idx, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ('name', 'text')):
            raise ValueError('第 {} 条宏需要 name 和 text 两个文本字段'.format(idx))
        name = row['name'].strip()
        if name.startswith('`'):
            name = name[1:]
        text = row['text']
        if any(c in text for c in '\r\n\0'):
            raise ValueError('第 {} 条宏的替换原文须为单行'.format(idx))
        pairs.append((name, text.strip()))
    sources = tuple(clean_template(settings[key]) for key in ('with_mux', 'without_mux') if key in settings)
    return _compile(tuple(pairs), sources)


@lru_cache(maxsize=8)
def _compile(pairs, sources):
    return SVMacros(pairs, sources)


class SVMacros:
    def __init__(self, pairs, sources):
        self.pairs = pairs
        self.trie = {}
        self.names = set()
        self.by_text = {}
        fragments = []
        references = {}
        for name, text in pairs:
            if not NAME.fullmatch(name) or name in DIRECTIVES:
                raise ValueError('无效的 SV 宏名：' + name)
            if not text or text.endswith('\\'):
                raise ValueError('宏 {} 需要单行原文，末尾不能使用续行符'.format(name))
            if name in self.names or text in self.by_text:
                raise ValueError('宏名或替换原文重复：' + name)
            tokens = list(lex(text))
            if any(k in ('directive', 'comment') for k, _ in tokens):
                raise ValueError('宏 {} 的原文包含编译指令或注释；完整字符串可连同双引号填写'.format(name))
            if any(k == 'string' and not STRING_LITERAL.fullmatch(t) for k, t in tokens):
                raise ValueError('宏 {} 的原文含有未闭合的双引号'.format(name))
            references[name] = {t[1:] for k, t in tokens if k == 'macro'}
            self.names.add(name)
            self.by_text[text] = name
            node = self.trie
            for _, token in tokens:
                node = node.setdefault(token, {})
            node[None] = name
            if not any(k in ('string', 'macro_string', 'macro', 'escaped') for k, _ in tokens):
                fragments.append(text)
        self.in_string = (re.compile('|'.join(re.escape(t) for t in sorted(fragments, key=len, reverse=True)))
                          if fragments else None)
        self.forward = None
        self.split_default = False
        if self.in_string and (not sources or any(
                kind == 'string' and (template_tokens(token) or
                                      (token not in self.by_text and self._string_parts(token)))
                for source in sources for kind, token in lex(source))):
            self.forward = next((name for name, text in pairs if can_default(text)), None)
            if not self.forward:
                # Punctuation-only rules (e.g. a comma) cannot be put between
                # '=' and ')' in a macro's formal parameter list.
                self.forward = self.by_text[fragments[0]]
                self.split_default = True
        dependencies = {name: refs & self.names for name, refs in references.items()}
        users = {name: [] for name in self.names}
        for name, refs in dependencies.items():
            for ref in refs:
                users[ref].append(name)
        ready = [name for name, refs in dependencies.items() if not refs]
        while ready:
            name = ready.pop()
            for user in users[name]:
                dependencies[user].remove(name)
                if not dependencies[user]:
                    ready.append(user)
        if any(dependencies.values()):
            raise ValueError('宏定义中存在循环引用：' + '、'.join(sorted(k for k, v in dependencies.items() if v)))
        self.quoted_ref = (re.compile(ESCAPE.pattern + r'|`' + re.escape(self.forward) + r'(?![a-zA-Z0-9_$])')
                           if self.forward else None)

    def rows(self):
        return [{'name': name, 'text': text} for name, text in self.pairs]

    def header(self):
        rows = []
        for name, text in self.pairs:
            value = self._references(text)
            if name == self.forward and self.split_default:
                left, middle, right = re.fullmatch(r'([a-zA-Z0-9_$]*)(.*?)([a-zA-Z0-9_$]*)', value).groups()
                pre, post = 'ct7_prefix', 'ct7_suffix'
                while pre in value or post in value:
                    pre += '_'
                    post += '_'
                rows.append('`define {}({}={},{}={}) {}{}{}\n'.format(name, pre, left, post, right, pre, middle, post))
            elif name == self.forward:
                rows.append('`define {}(ct7_value={}) ct7_value\n'.format(name, value))
            else:
                rows.append('`define {} {}\n'.format(name, value))
        return ''.join(rows)

    def _ref(self, name):
        return '`' + name + ('()' if name == self.forward else '')

    def _references(self, text):
        if not self.forward or '`' not in text:
            return text
        parts = []
        for m in LEX.finditer(text):
            token = m.group()
            if m.lastgroup == 'macro' and token[1:] == self.forward:
                token = self._ref(self.forward)
            elif m.lastgroup == 'macro_string':
                token = self.quoted_ref.sub(lambda ref: self._ref(self.forward) if ref.group().startswith('`')
                                            else ref.group(), token)
            parts.append(token)
        return ''.join(parts)

    def _string_parts(self, literal):
        if self.in_string is None or not STRING_LITERAL.fullmatch(literal):
            return None
        content = literal[1:-1]
        if '\n' in content:
            # A physical continued line is one string, also at macro-name boundaries.
            content = ESCAPE.sub(lambda m: '' if m.group() in ('\\\n', '\\\r\n') else m.group(), content)
        parts = []
        pos = 0
        start = 0
        for escape in chain(ESCAPE.finditer(content), (None,)):
            end = escape.start() if escape else len(content)
            for match in self.in_string.finditer(content, pos, end):
                if match.start() > start:
                    parts.append((False, content[start:match.start()]))
                parts.append((True, self.by_text[match.group()]))
                start = match.end()
            if escape:
                pos = escape.end()
        if not parts:
            return None
        if start < len(content):
            parts.append((False, content[start:]))
        return parts

    def _format_string(self, parts):
        # Quote in an expansion of a configured name. This yields ONE literal
        # even in grammar positions (e.g. DPI) that cannot take a concatenation.
        if not self.forward:
            raise ValueError('渲染出的字符串与宏配置使用的模板不一致，请用同一份模板生成')
        chunks = ['`"']
        after_macro = False
        for is_macro, value in parts:
            if is_macro:
                chunks.append(self._ref(value))
            else:
                # SV 2018 preprocessors may scan comments or swallow a suffix
                # in a quoted macro argument. SV octal escapes decode to the
                # original bytes after preprocessing, including format text.
                if after_macro and re.match(r'[a-zA-Z0-9_$]', value):
                    chunks.append('\\{:03o}'.format(ord(value[0])))
                    value = value[1:]
                chunks.append(QUOTED_SPECIAL.sub(quote_special, value))
            after_macro = is_macro
        chunks.append('`"')
        arg = ''.join(chunks)
        if self.split_default:
            # The normal (empty-argument) call inserts the rule verbatim. In
            # forwarding mode its own defined name suppresses the punctuation.
            return '`{0}({1} `ifndef {0} ;, `endif)'.format(self.forward, arg)
        return '`{}({})'.format(self.forward, arg)

    def apply(self, code):
        if not self.pairs:
            return code
        tokens = list(lex(code))
        result = []
        i = 0
        while i < len(tokens):
            kind, token = tokens[i]
            node = self.trie
            j, end, name = i, i, None
            while j < len(tokens) and tokens[j][1] in node:
                node = node[tokens[j][1]]
                j += 1
                if None in node:
                    name, end = node[None], j
            if name is not None:
                result.append(self._ref(name))
                if end < len(tokens) and re.match(r'[a-zA-Z0-9_$]', tokens[end][1]):
                    result.append(' ')
                i = end
                continue
            if kind == 'string':
                parts = self._string_parts(token)
                if parts:
                    token = self._format_string(parts)
            elif kind == 'macro' and token[1:] == self.forward:
                token = self._ref(self.forward)
            elif kind in ('directive', 'macro_string'):
                token = self._references(token)
            result.append(token)
            i += 1
        return ''.join(result)
