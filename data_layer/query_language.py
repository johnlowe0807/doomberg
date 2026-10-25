"""Optional SQL-style text adapter for the typed table API. Quotes remain literal."""
from __future__ import annotations

import re
from dataclasses import replace

from .query_models import BooleanExpression, Condition, SortOrder, TableQuery
from .tabular import normalize

VERBS = ('WHERE', 'SEARCH', 'SORT', 'FIELDS', 'LIMIT', 'CLEAR', 'HELP', 'HOME')


def dot_offsets(text):
    """Only unquoted, token-leading dots before letters/? introduce commands."""
    offsets, quote, i = [], None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == '\\' and i + 1 < len(text):
                i += 2
                continue
            if c == quote:
                if i + 1 < len(text) and text[i + 1] == quote:
                    i += 2
                    continue
                quote = None
        elif c in ('"', "'"):
            quote = c
        elif c == '.' and (i == 0 or text[i - 1].isspace()) and i + 1 < len(text) and (text[i + 1].isalpha() or text[i + 1] == '?'):
            offsets.append(i)
        i += 1
    return offsets


def split_navigation(text):
    offsets = dot_offsets(text)
    return (text[:offsets[0]].strip(), text[offsets[0]:]) if offsets else (text, '')


def unquote(text):
    quote = text[0]
    inner = text[1:-1].replace(quote + quote, quote)
    return re.sub(r'\\([\\\'\"])', r'\1', inner)


TOKEN = re.compile(r'''\s*(?:("(?:[^"\\]|\\.|"")*"|'(?:[^'\\]|\\.|'')*')|([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)|(>=|<=|!=|<>|[=><(),])|([A-Za-z_][A-Za-z0-9_]*))''')


class ExpressionParser:
    def __init__(self, text):
        self.tokens, self.index = [], 0
        pos = 0
        while pos < len(text.rstrip()):
            match = TOKEN.match(text, pos)
            if not match:
                raise ValueError('Invalid WHERE syntax near ' + text[pos:pos + 25] + '; quote text/tickers')
            quoted, number, punctuation, word = match.groups()
            self.tokens.append(('literal', unquote(quoted)) if quoted else
                               ('literal', float(number)) if number else
                               ('punctuation', punctuation) if punctuation else ('word', word))
            pos = match.end()
            if len(self.tokens) > 1024:
                raise ValueError('WHERE supports at most 1024 tokens')

    def peek(self, text):
        return self.index < len(self.tokens) and self.tokens[self.index][0] != 'literal' and self.tokens[self.index][1].upper() == text

    def take(self, text):
        if self.peek(text):
            self.index += 1
            return True
        return False

    def expect(self, text):
        if not self.take(text):
            raise ValueError('Expected ' + text + ' in WHERE')

    def pop(self):
        if self.index >= len(self.tokens):
            raise ValueError('Incomplete WHERE expression')
        token = self.tokens[self.index]
        self.index += 1
        return token

    def literal(self):
        kind, value = self.pop()
        if kind == 'literal':
            return value
        if kind == 'word' and value.upper() in ('TRUE', 'FALSE'):
            return value.upper() == 'TRUE'
        raise ValueError('Quote text/tickers; numbers and TRUE/FALSE are unquoted. Use IS NULL for missing data')

    def parse(self):
        result = self.group('OR', 0)
        if self.index != len(self.tokens):
            raise ValueError('Unexpected text after WHERE expression')
        return result

    def group(self, operator, depth):
        if depth > 32:
            raise ValueError('WHERE expressions support at most 32 levels')
        parse_child = (lambda: self.group('AND', depth + 1)) if operator == 'OR' else (lambda: self.atom(depth + 1))
        children = [parse_child()]
        while self.take(operator):
            children.append(parse_child())
        return children[0] if len(children) == 1 else BooleanExpression(operator, tuple(children))

    def atom(self, depth):
        if depth > 32:
            raise ValueError('WHERE expressions support at most 32 levels')
        if self.take('NOT'):
            return BooleanExpression('NOT', (self.atom(depth + 1),))
        if self.take('('):
            result = self.group('OR', depth + 1)
            self.expect(')')
            return result
        kind, name = self.pop()
        if kind != 'word':
            raise ValueError('WHERE conditions must start with a field name')
        if self.take('IS'):
            negated = self.take('NOT')
            self.expect('NULL')
            return Condition(name, 'IS NOT NULL' if negated else 'IS NULL')
        kind, op = self.pop()
        if kind == 'literal':
            raise ValueError('Expected a comparison operator')
        op = op.upper()
        if op == 'IN':
            self.expect('(')
            values = [self.literal()]
            while self.take(','):
                values.append(self.literal())
            self.expect(')')
            return Condition(name, op, tuple(values))
        return Condition(name, op, self.literal())


def apply_commands(query, line, dataset):
    """Return a validated replacement. A failed chain never mutates caller state."""
    if len(line) > 16000:
        raise ValueError('Command text is too long')
    line = line.strip()
    if not line:
        raise ValueError('Enter a command; HELP or ? lists commands')
    offsets = dot_offsets(line)
    starts = sorted(set([0, *offsets]))
    ends = starts[1:] + [len(line)]
    candidate, help_requested = query, False
    for start, end in zip(starts, ends):
        command = line[start:end].strip()
        match = re.fullmatch(r'[./]?([A-Za-z]+|\?)(?:\s+(.*))?', command, re.DOTALL)
        if not match:
            raise ValueError('Use WHERE/SEARCH/SORT/FIELDS/LIMIT/CLEAR; separate commands with dots')
        verb, value = match.group(1).upper(), (match.group(2) or '').strip()
        if verb in ('HELP', '?', 'HOME'):
            if value or end != len(line):
                raise ValueError('Put HELP, HOME or ? last, without arguments')
            help_requested = True
        elif verb == 'WHERE':
            node = ExpressionParser(value).parse()
            candidate = replace(candidate, where=BooleanExpression('AND', (candidate.where, node)) if candidate.where else node)
        elif verb == 'SEARCH':
            tokens = ExpressionParser(value).tokens
            if len(tokens) != 1 or tokens[0][0] != 'literal' or not isinstance(tokens[0][1], str) or not tokens[0][1].strip():
                raise ValueError('Use SEARCH "text"; quote text containing spaces or command names')
            candidate = replace(candidate, search=(*candidate.search, tokens[0][1]))
        elif verb == 'SORT':
            orders = []
            for part in value.split(','):
                match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)(?:\s+(ASC|DESC))?', part.strip(), re.I)
                if not match:
                    raise ValueError('Use SORT field [ASC|DESC], field [ASC|DESC]')
                orders.append(SortOrder(match[1], (match[2] or 'ASC').upper() == 'DESC'))
            candidate = replace(candidate, sort=tuple(orders))
        elif verb == 'FIELDS':
            fields = () if value == '*' else tuple(re.split(r'\s*,\s*|\s+', value))
            if not value:
                raise ValueError('Use FIELDS symbol, name, price or FIELDS *')
            candidate = replace(candidate, fields=fields)
        elif verb == 'LIMIT':
            if not re.fullmatch(r'\d+', value):
                raise ValueError('Use LIMIT integer (1–10000)')
            candidate = replace(candidate, limit=int(value))
        elif verb == 'CLEAR':
            target = value.upper()
            if target in ('', 'ALL'):
                candidate = TableQuery(query.dataset, parameters=dict(query.parameters))
            elif target in ('WHERE', 'SEARCH', 'SORT', 'FIELDS', 'LIMIT'):
                default = {'where': None, 'search': (), 'sort': (), 'fields': (), 'limit': 50}
                candidate = replace(candidate, **{target.lower(): default[target.lower()]})
            else:
                raise ValueError('Use CLEAR [WHERE|SEARCH|SORT|FIELDS|LIMIT|ALL]')
        else:
            raise ValueError(f'Unknown command {verb}; HELP lists commands')
        # Validate each step, even if a later CLEAR would discard it.
        normalized, _ = normalize(candidate, dataset)
        candidate = replace(normalized, fields=normalized.fields if candidate.fields else ())
    return candidate, help_requested


def format_query(query):
    """Readable, round-trippable command settings for clients and saved requests."""
    def literal(value):
        if isinstance(value, str):
            return "'" + value.replace('\\', '\\\\').replace("'", "''") + "'"
        return str(value).upper() if type(value) is bool else str(value)

    def expression(node):
        if isinstance(node, BooleanExpression):
            if node.operator == 'NOT':
                return 'NOT (' + expression(node.children[0]) + ')'
            return '(' + (' ' + node.operator + ' ').join(expression(c) for c in node.children) + ')'
        value = '' if node.operator in ('IS NULL', 'IS NOT NULL') else (
            ' (' + ', '.join(literal(v) for v in node.value) + ')' if node.operator == 'IN' else ' ' + literal(node.value))
        return node.field + ' ' + node.operator + value

    parts = []
    if query.where:
        parts.append('.WHERE ' + expression(query.where))
    parts += ['.SEARCH ' + literal(term) for term in query.search]
    if query.sort:
        parts.append('.SORT ' + ', '.join(s.field + (' DESC' if s.descending else ' ASC') for s in query.sort))
    if query.fields:
        parts.append('.FIELDS ' + ','.join(query.fields))
    parts.append('.LIMIT ' + str(query.limit))
    return ' '.join(parts)
