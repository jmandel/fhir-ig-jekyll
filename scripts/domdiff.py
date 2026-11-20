"""Normalized-DOM comparison of two output trees (real Jekyll vs shim). Decision D1 in TRACKER.md.

usage:
  python3 -I domdiff.py <expected-dir> <actual-dir> [--json out.json] [--max-report N]
  python3 -I domdiff.py --selftest

Rules:
  * File sets must match. Non-HTML files must be byte-identical (static copies), except rendered
    text files (.js/.css/.json/.xml/.txt) which are compared modulo trailing whitespace per line.
  * .html/.htm files are parsed into a tree and compared after normalization:
      - entities/char refs decoded (html.parser convert_charrefs)
      - attributes compared as a sorted mapping, values stripped
      - text: runs of whitespace collapsed to one space, whitespace-only text nodes dropped,
        except inside <pre>, <textarea>, <script>, <style> where text is kept verbatim
      - comments compared with collapsed whitespace
      - D7: inside <pre class="highlight"><code>, only the text content is compared (Rouge token
        <span>s are not reproduced); the wrapper divs/classes are still compared strictly.
        This rule is now OFF by default (exact comparison incl. spans); pass --d7 to enable it (diagnostics).
"""
import html.parser, json, os, re, sys

VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param',
        'source', 'track', 'wbr'}
VERBATIM = {'pre', 'textarea', 'script', 'style'}
WS = re.compile(r'\s+')
HTML_EXT = {'.html', '.htm'}
TEXT_EXT = {'.js', '.css', '.json', '.xml', '.txt', '.md', '.svg', '.ttl', '.csv'}


class Node:
    __slots__ = ('tag', 'attrs', 'children', 'line')

    def __init__(self, tag, attrs=None, line=0):
        self.tag, self.attrs, self.children, self.line = tag, attrs or {}, [], line


class TreeBuilder(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node('#root'); self.stack = [self.root]

    def _verbatim(self):
        return any(n.tag in VERBATIM for n in self.stack)

    def handle_starttag(self, tag, attrs):
        n = Node(tag, {k: (v or '').strip() for k, v in attrs}, self.getpos()[0])
        self.stack[-1].children.append(n)
        if tag not in VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: (v or '').strip() for k, v in attrs}, self.getpos()[0]))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):  # tolerant: close up to the matching open tag
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not self._verbatim():
            data = WS.sub(' ', data)
            if data == ' ' or data == '':
                return
        kids = self.stack[-1].children
        if kids and isinstance(kids[-1], str):
            kids[-1] = kids[-1] + data
            if not self._verbatim():
                kids[-1] = WS.sub(' ', kids[-1])
        else:
            kids.append(data)

    def handle_comment(self, data):
        self.stack[-1].children.append(('#comment', WS.sub(' ', data).strip()))

    def handle_decl(self, decl):
        self.stack[-1].children.append(('#decl', decl.strip().lower()))

    def unknown_decl(self, data):
        self.stack[-1].children.append(('#decl', data.strip()))


def parse(text):
    b = TreeBuilder(); b.feed(text); b.close()
    return b.root


def _norm_children(children):
    # trim leading/trailing spaces of text adjacent to block boundaries isn't attempted: a single
    # collapsed space is significant inline. But leading/trailing space at the very edge of a
    # child list is dropped (equivalent rendering in all cases we care about).
    out = list(children)
    if out and isinstance(out[0], str):
        out[0] = out[0].lstrip()
        if not out[0]:
            out.pop(0)
    if out and isinstance(out[-1], str):
        out[-1] = out[-1].rstrip()
        if not out[-1]:
            out.pop()
    return out


def text_content(n):
    if isinstance(n, str):
        return n
    if isinstance(n, tuple):
        return ''
    return ''.join(text_content(c) for c in n.children)


NO_D7 = True  # exact by default (D7 reversed 2026-11-19); --d7 re-enables text-only highlighted-code comparison


def is_highlight_code(n, parent):
    """kramdown fenced code (<pre class="highlight"><code>) or Jekyll's {% highlight %} tag
    (<figure class="highlight"><pre><code data-lang=…>)."""
    if NO_D7:
        return False
    if not (isinstance(n, Node) and n.tag == 'code'):
        return False
    if 'highlighter-rouge' in n.attrs.get('class', '').split():  # kramdown inline codespan with a language
        return True
    if parent is None or parent.tag != 'pre':
        return False
    return 'highlight' in parent.attrs.get('class', '').split() or 'data-lang' in n.attrs


def compare(a, b, path='', verbatim=False, parent=None):
    """Return None if equal, else (path, description)."""
    if is_highlight_code(a, parent) and is_highlight_code(b, parent):
        if a.attrs != b.attrs:
            return f'{path}/code', f'attrs {a.attrs} vs {b.attrs}'
        ta, tb = text_content(a), text_content(b)
        if ta != tb:
            return f'{path}/code', f'highlighted text differs: {short(ta)!r} vs {short(tb)!r}'
        return None
    if isinstance(a, str) or isinstance(b, str):
        if a != b:
            return path, f'text differs: {short(a)!r} vs {short(b)!r}'
        return None
    if isinstance(a, tuple) or isinstance(b, tuple):
        if a != b:
            return path, f'{short(a)!r} vs {short(b)!r}'
        return None
    if a.tag != b.tag:
        return path, f'tag <{a.tag}> vs <{b.tag}>'
    here = f'{path}/{a.tag}'
    if a.attrs != b.attrs:
        keys = sorted(set(a.attrs) | set(b.attrs))
        diffs = [f'{k}: {a.attrs.get(k)!r} vs {b.attrs.get(k)!r}' for k in keys if a.attrs.get(k) != b.attrs.get(k)]
        return here + f'@L{a.line}', 'attrs ' + '; '.join(diffs[:3])
    verbatim = verbatim or a.tag in VERBATIM
    ac = a.children if verbatim else _norm_children(a.children)
    bc = b.children if verbatim else _norm_children(b.children)
    for i, (x, y) in enumerate(zip(ac, bc)):
        r = compare(x, y, f'{here}[{i}]', verbatim, a)
        if r:
            return r
    if len(ac) != len(bc):
        extra = (ac if len(ac) > len(bc) else bc)[min(len(ac), len(bc))]
        return here + f'@L{a.line}', f'child count {len(ac)} vs {len(bc)}; first extra: {short(extra)!r}'
    return None


def short(x, n=90):
    if isinstance(x, Node):
        s = f'<{x.tag} {x.attrs}>' if x.attrs else f'<{x.tag}>'
    else:
        s = str(x)
    return s if len(s) <= n else s[:n] + '…'


def compare_files(pa, pb):
    ext = os.path.splitext(pa)[1].lower()
    with open(pa, 'rb') as f: ra = f.read()
    with open(pb, 'rb') as f: rb = f.read()
    if ra == rb:
        return None
    if ext in HTML_EXT:
        return compare(parse(ra.decode('utf-8', 'replace')), parse(rb.decode('utf-8', 'replace')))
    if ext in TEXT_EXT:
        la = [l.rstrip() for l in ra.decode('utf-8', 'replace').rstrip().splitlines()]
        lb = [l.rstrip() for l in rb.decode('utf-8', 'replace').rstrip().splitlines()]
        if la == lb:
            return None
        for i, (x, y) in enumerate(zip(la, lb)):
            if x != y:
                return f'line {i + 1}', f'{short(x)!r} vs {short(y)!r}'
        return f'line {min(len(la), len(lb)) + 1}', f'line count {len(la)} vs {len(lb)}'
    return '', f'binary differs ({len(ra)} vs {len(rb)} bytes)'


def walk(d):
    out = set()
    for dp, dn, fn in os.walk(d):
        for f in fn:
            out.add(os.path.relpath(os.path.join(dp, f), d))
    return out


def diff_trees(exp, act):
    fe, fa = walk(exp), walk(act)
    res = {'missing': sorted(fe - fa), 'extra': sorted(fa - fe), 'differ': {}, 'same': 0}
    for rel in sorted(fe & fa):
        r = compare_files(os.path.join(exp, rel), os.path.join(act, rel))
        if r:
            res['differ'][rel] = {'where': r[0], 'what': r[1]}
        else:
            res['same'] += 1
    return res


def selftest():
    global NO_D7
    # exact default: highlighted code with token spans differs from plain code
    NO_D7 = True
    exact_ok = bool(compare(parse('<pre class="highlight"><code><span class="p">{</span>}</code></pre>'),
                            parse('<pre class="highlight"><code>{}</code></pre>')))
    if not exact_ok:
        print('FAIL exact mode should distinguish token spans')
    # the cases below exercise the optional --d7 rule
    NO_D7 = False
    r = _selftest_cases()
    NO_D7 = True
    return r and exact_ok


def _selftest_cases():
    eq = [('<p>a  b</p>', '<p>a\nb</p>'),
          ('<div class="x" id="y">t</div>', '<div id="y" class="x">t</div>'),
          ('<p>&quot;hi&quot; &amp;</p>', '<p>"hi" &amp;</p>'),
          ('<ul>\n  <li>x</li>\n</ul>', '<ul><li>x</li></ul>'),
          ('<br/>', '<br>'),
          ('<p>\n x </p>', '<p>x</p>'),
          ('<pre class="highlight"><code><span class="p">{</span>}</code></pre>',
           '<pre class="highlight"><code>{}</code></pre>')]
    ne = [('<p>a b</p>', '<p>ab</p>'),
          ('<pre>a  b</pre>', '<pre>a b</pre>'),
          ('<h2 id="a">x</h2>', '<h2 id="b">x</h2>'),
          ('<p><em>a</em> b</p>', '<p><em>a</em>b</p>'),
          ('<ul><li>x</li></ul>', '<ol><li>x</li></ol>'),
          ('<pre class="highlight"><code>{}</code></pre>', '<pre class="highlight"><code>{ }</code></pre>'),
          ('<div class="language-json highlighter-rouge"></div>', '<div class="language-json"></div>')]
    ok = True
    for x, y in eq:
        r = compare(parse(x), parse(y))
        if r: ok = False; print('FAIL expected equal:', x, '|', y, '->', r)
    for x, y in ne:
        if not compare(parse(x), parse(y)): ok = False; print('FAIL expected different:', x, '|', y)
    print('selftest', 'OK' if ok else 'FAILED')
    return ok


def main():
    if sys.argv[1:2] == ['--selftest']:
        sys.exit(0 if selftest() else 1)
    global NO_D7
    NO_D7 = '--d7' not in sys.argv  # --no-d7 accepted as a no-op for old scripts
    exp, act = sys.argv[1], sys.argv[2]
    out_json = sys.argv[sys.argv.index('--json') + 1] if '--json' in sys.argv else None
    maxr = int(sys.argv[sys.argv.index('--max-report') + 1]) if '--max-report' in sys.argv else 25
    res = diff_trees(exp, act)
    total = res['same'] + len(res['differ']) + len(res['missing'])
    print(f"same={res['same']} differ={len(res['differ'])} missing={len(res['missing'])} extra={len(res['extra'])} "
          f"pass={res['same'] / total * 100 if total else 100:.1f}%")
    for k in ('missing', 'extra'):
        if res[k]:
            print(f'  {k}: ' + ', '.join(res[k][:10]) + (' …' if len(res[k]) > 10 else ''))
    for rel, d in list(res['differ'].items())[:maxr]:
        print(f'  DIFF {rel} {d["where"]}: {d["what"]}')
    if out_json:
        with open(out_json, 'w') as f:
            json.dump(res, f, indent=1)
    sys.exit(0 if not res['differ'] and not res['missing'] and not res['extra'] else 1)


if __name__ == '__main__':
    main()
