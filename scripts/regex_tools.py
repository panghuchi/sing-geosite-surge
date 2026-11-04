"""Conservative RE2 subset: exact hostname globs, then validated JS fallback."""
import itertools
import json
try:
    from re import _parser as parser, _constants as C
except ImportError:  # Python 3.9/3.10
    import sre_parse as parser
    import sre_constants as C


class RegexUnsupported(ValueError):
    pass


def parsed(pattern):
    tree = parser.parse(pattern)
    if tree.state.flags != 32:  # default UNICODE flag; inline flags need explicit translation
        raise RegexUnsupported('regex flags require separate translation')
    validate(tree)
    return tree


def validate(tree):
    for op, arg in tree:
        if op in (C.LITERAL, C.NOT_LITERAL, C.ANY):
            continue
        if op == C.AT and arg in (C.AT_BEGINNING, C.AT_END):
            continue
        if op == C.CATEGORY and arg in (C.CATEGORY_DIGIT, C.CATEGORY_WORD, C.CATEGORY_NOT_SPACE):
            continue
        if op == C.IN:
            validate([(op, value) for op, value in arg if op != C.NEGATE])
            continue
        if op == C.RANGE:
            continue
        if op == C.SUBPATTERN:
            if arg[1] or arg[2]:
                raise RegexUnsupported('scoped flags are unsupported')
            validate(arg[3])
            continue
        if op == C.BRANCH:
            for branch in arg[1]:
                validate(branch)
            continue
        if op in (C.MAX_REPEAT, C.MIN_REPEAT):
            validate(arg[2])
            continue
        raise RegexUnsupported('unsupported RE2/JavaScript construct: ' + str(op))


def product(left, right):
    if len(left) * len(right) > 512:
        raise RegexUnsupported('native glob expansion exceeds 512 variants')
    return [a + b for a in left for b in right]


def literal(code):
    char = chr(code)
    if not (char.isascii() and (char.isalnum() or char in '.-_')):
        raise RegexUnsupported('literal needs escaping in a Surge wildcard')
    if char.isupper():
        raise RegexUnsupported('case sensitive regex cannot become a case insensitive wildcard')
    return char


def paths(tree):
    result = [[]]
    for op, arg in tree:
        if op == C.LITERAL:
            current = [[literal(arg)]]
        elif op == C.ANY:
            current = [['?']]
        elif op == C.AT:
            current = [[arg]]
        elif op == C.CATEGORY:
            if arg != C.CATEGORY_DIGIT:
                raise RegexUnsupported('category needs script matching')
            current = [['[0-9]']]
        elif op == C.IN:
            chars = ''
            for child_op, child_arg in arg:
                if child_op == C.LITERAL:
                    char = literal(child_arg)
                    if char in '.-':
                        raise RegexUnsupported('class escaping needs script matching')
                    chars += char
                elif child_op == C.RANGE:
                    chars += literal(child_arg[0]) + '-' + literal(child_arg[1])
                elif child_op == C.CATEGORY and child_arg == C.CATEGORY_DIGIT:
                    chars += '0-9'
                else:
                    raise RegexUnsupported('class needs script matching')
            current = [['[' + chars + ']']]
        elif op == C.SUBPATTERN:
            current = paths(arg[3])
        elif op == C.BRANCH:
            current = list(itertools.chain.from_iterable(paths(branch) for branch in arg[1]))
        elif op in (C.MAX_REPEAT, C.MIN_REPEAT):
            minimum, maximum, child = arg
            if maximum == C.MAXREPEAT:
                if list(child) != [(C.ANY, None)]:
                    raise RegexUnsupported('unbounded constrained repetition needs script matching')
                current = [['?' * minimum + '*']]
            else:
                if maximum > 10:
                    raise RegexUnsupported('bounded expansion too large')
                current = []
                unit = paths(child)
                for count in range(minimum, maximum + 1):
                    repeated = [[]]
                    for _ in range(count):
                        repeated = product(repeated, unit)
                    current += repeated
        else:
            raise RegexUnsupported('construct needs script matching')
        result = product(result, current)
    return result


def native_rules(pattern):
    result = set()
    for path in paths(parsed(pattern)):
        start = bool(path and path[0] == C.AT_BEGINNING)
        end = bool(path and path[-1] == C.AT_END)
        if start:
            path = path[1:]
        if end:
            path = path[:-1]
        if any(not isinstance(atom, str) for atom in path):
            raise RegexUnsupported('internal anchors need script matching')
        glob = ('' if start else '*') + ''.join(path) + ('' if end else '*')
        result.add('DOMAIN-WILDCARD,' + glob)
    return sorted(result)


def script_source(patterns):
    for pattern in patterns:
        parsed(pattern)  # reject constructs with different or unknown JS semantics
    return ('// Generated hostname matcher. No DNS queries, network calls, or MITM.\n'
            'var patterns = ' + json.dumps(sorted(set(patterns)), ensure_ascii=True) + ';\n'
            'var host = typeof $request.hostname === "string" ? $request.hostname : "";\n'
            'var matched = host !== "" && patterns.some(function (pattern) {\n'
            '  return new RegExp(pattern).test(host);\n'
            '});\n'
            '$done({matched: matched});\n')
