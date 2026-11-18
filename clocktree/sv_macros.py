"""Object-like SV macros applied to rendered branch code and string literals."""
from functools import lru_cache
import hashlib
from itertools import chain
import re


NAME = re.compile(r'[a-zA-Z_][a-zA-Z0-9_$]*')
HELPER_NAME = re.compile(r'CT7_TEXT_[a-zA-Z0-9_$]*')
DIRECTIVE_OPERAND = re.compile(r'[ \t]+[a-zA-Z_][a-zA-Z0-9_$]*')
DIRECTIVES = frozenset(('define undef undefineall ifdef ifndef elsif else endif include line timescale '
                        'default_nettype resetall celldefine endcelldefine unconnected_drive '
                        'nounconnected_drive begin_keywords end_keywords pragma __FILE__ __LINE__').split())
STRING_BODY = r'(?:\\(?:\r\n|[\s\S])|[^"\\\r\n])*'
STRING_LITERAL = re.compile('"' + STRING_BODY + '"')
LEX = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?(?:\*/|\Z))'
    r'|(?P<string>"' + STRING_BODY + r'(?:"|(?=[\r\n]|\Z)))'
    r'|(?P<escaped>\\[^\s]+)'
    r'|(?P<macro>`[a-zA-Z_][a-zA-Z0-9_$]*|``|`")'
    r"|(?P<number>(?:[0-9][0-9_]*[ \t]*)?'[sS]?[bBoOdDhH][ \t]*[0-9a-fA-F_xXzZ?]+"
    r"|'[01xXzZ]|[0-9][0-9_]*(?:\.[0-9][0-9_]*)?(?:[eE][+-]?[0-9][0-9_]*)?(?:ms|us|ns|ps|fs|s)?)"
    r'|(?P<word>[a-zA-Z_$][a-zA-Z0-9_$]*)|(?P<space>\s+)'
    r'|(?P<operator><<<=?|>>>=?|<<=?|>>=?|\|->|\|=>|<->|===|!==|==\?|!=\?|&&&|'
    r'::|\+:|-:|\+\+|--|&&|\|\||\*\*|##|->|=>|<=|>=|==|!=|~[&|^]|\^~|[+\-*/%&|^]=)|.')
ESCAPE = re.compile(r'\\(?:\r?\n|[0-7]{1,3}|x[0-9a-fA-F]{1,2}|[\s\S])')


def lex(source):
    """Yield (kind, spelling). A template can also contain existing directives."""
    pos = 0
    while pos < len(source):
        match = LEX.match(source, pos)
        kind, spelling = match.lastgroup, match.group()
        end = match.end()
        if kind == 'macro' and spelling[1:] in DIRECTIVES:
            kind = 'directive'
            directive = spelling[1:]
            if directive in ('ifdef', 'ifndef', 'elsif', 'undef'):
                operand = DIRECTIVE_OPERAND.match(source, end)
                if operand:
                    end = operand.end()
                    spelling = source[pos:end]
            elif directive not in ('else', 'endif', '__FILE__', '__LINE__'):
                end = source.find('\n', end)
                while end >= 0 and source[max(0, end-2):end].endswith(('\\', '\\\r')):
                    end = source.find('\n', end + 1)
                if end < 0:
                    end = len(source)
                spelling = source[pos:end]
        yield kind, spelling
        pos = end


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
    return _compile(tuple(pairs))


@lru_cache(maxsize=8)
def _compile(pairs):
    return SVMacros(pairs)


class SVMacros:
    def __init__(self, pairs):
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
            if not any(k in ('string', 'macro', 'escaped') for k, _ in tokens):
                fragments.append(text)
        self.in_string = (re.compile('|'.join(re.escape(t) for t in sorted(fragments, key=len, reverse=True)))
                          if fragments else None)
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

    def rows(self):
        return [{'name': name, 'text': text} for name, text in self.pairs]

    def header(self):
        return ''.join('`define {} {}\n'.format(name, text) for name, text in self.pairs)

    def _quoted_body(self, literal):
        if self.in_string is None or not STRING_LITERAL.fullmatch(literal):
            return None
        content = literal[1:-1]
        if '\n' in content:
            # A physical continued line is one string, also at macro-name boundaries.
            content = ESCAPE.sub(lambda m: '' if m.group() in ('\\\n', '\\\r\n') else m.group(), content)
        parts = []
        changed = False
        pos = 0
        # Preserve SV escapes. A changed literal must still preprocess to ONE
        # string token, including when it is the format argument to $display.
        def plain(text, following_macro):
            delimiter = '``' if following_macro and re.match(r'[a-zA-Z0-9_$]', text) else ''
            return delimiter + text.replace('`', r'\140')

        for escape in chain(ESCAPE.finditer(content), (None,)):
            end = escape.start() if escape else len(content)
            start = pos
            for match in self.in_string.finditer(content, pos, end):
                parts.append(plain(content[start:match.start()], start != pos))
                parts.append('`' + self.by_text[match.group()])
                start = match.end()
                changed = True
            parts.append(plain(content[start:end], start != pos))
            if escape:
                value = escape.group()
                parts.append({r'\"': r'\042', '\\`': r'\140'}.get(value, value))
                pos = escape.end()
        return ''.join(parts) if changed else None

    def apply(self, code):
        if not self.pairs:
            return code
        tokens = list(lex(code))
        result, strings = [], {}
        occupied = None
        i = 0
        while i < len(tokens):
            kind, token = tokens[i]
            if kind == 'directive':
                if token.startswith('`undefineall'):
                    raise ValueError('模板中的 `undefineall 会清除已配置的宏')
                declaration = re.match(r'`(?:define|undef)\s+([a-zA-Z_][a-zA-Z0-9_$]*)', token)
                if declaration and declaration.group(1) in self.names:
                    raise ValueError('模板也定义或撤销了同名宏：' + declaration.group(1))
            node = self.trie
            j, end, name = i, i, None
            while j < len(tokens) and tokens[j][1] in node:
                node = node[tokens[j][1]]
                j += 1
                if None in node:
                    name, end = node[None], j
            if name is not None:
                result.append('`' + name)
                if end < len(tokens) and re.match(r'[a-zA-Z0-9_$]', tokens[end][1]):
                    result.append(' ')
                i = end
                continue
            if kind == 'string':
                body = self._quoted_body(token)
                if body is not None:
                    if body not in strings:
                        # Private helpers are defined and released per block.
                        if occupied is None:
                            occupied = self.names.union(HELPER_NAME.findall(code))
                        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]
                        helper = 'CT7_TEXT_' + digest
                        while helper in occupied:
                            helper += '_'
                        occupied.add(helper)
                        strings[body] = helper
                    token = '`' + strings[body]
            result.append(token)
            i += 1
        if not strings:
            return ''.join(result)
        # Stable order even on interpreters without ordered dict iteration.
        helpers = sorted(strings.items(), key=lambda item: item[1])
        definitions = ''.join('`define {} `"{}`"\n'.format(name, body) for body, name in helpers)
        cleanup = ''.join('`undef {}\n'.format(name) for _, name in helpers)
        return definitions + ''.join(result) + '\n' + cleanup
