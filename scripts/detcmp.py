"""Compare two IG Publisher output trees built from the same inputs (determinism harness, P7.1).

usage:
  python3 -I detcmp.py --a DIR --b DIR --meta-a JSON --meta-b JSON --out DIR [--jobs N] [--label L]
  python3 -I detcmp.py --selftest

Called by scripts/determinism.sh; see work/determinism/README.md. Every file present in both trees is
compared byte for byte first. A file whose bytes differ is then:

  1. normalized: only values that are genuinely per run are replaced (rules in RULES below; each rule
     counts its substitutions and the files where it absorbed a real A/B difference, so nothing is
     hidden). A timestamp is only treated as per run if it falls inside that build's own run window.
  2. classified (first match wins):
       normalized     equal after normalization (the difference was a per-run value)
       env-leak       equal once the build environment is also ignored: the time zone of the build
                      timestamps and/or the build's absolute paths (IG dir, temp dir) leak into output.
                      Later steps compare the env-stripped text, so a file can be order-only + env leak.
       dom-equal      .html only: equal as a normalized DOM (domdiff.py), bytes still differ
       order-only     same content in a different order: lines (text), arrays/keys (JSON), sibling
                      elements (HTML), rows (package.db), members (zip/tgz)
       content        anything else
     Archives (zip, xlsx, jar, tgz) are opened recursively and their members compared the same way,
     with the member order and entry metadata reported separately. SQLite files (package.db) are
     compared by logical dump: unsorted, sorted, and sorted with generated key columns dropped.
  3. attributed, where the file and the kind of difference allow it, to a cause class C1..C8 of the
     architecture memo (work/consult/publisher-architecture-memo.md, section 3).

Writes report.json (machine-readable, every differing file) and summary.txt (groups, one example each)
to --out, and up to --examples example file pairs to --out/examples/.
"""
import argparse, ast, bisect, datetime as dt, difflib, gzip, hashlib, io, json, os, re, sqlite3, sys
import tarfile, tempfile, zipfile
from collections import Counter, defaultdict
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import domdiff  # noqa: E402  (same directory)

CLASS_RANK = {'same': 0, 'normalized': 1, 'env-leak': 2, 'dom-equal': 3, 'order-only': 4, 'content': 5}

# ----------------------------------------------------------------------------------------------
# Normalization rules. Each rule: name -> why. Substitution counts and "absorbed" counts are kept.
RULES = {
    'build-timestamp-12h': 'a build timestamp that only falls in the run window when read as a 12-hour '
                           'clock value (hh instead of HH in the format: an output bug, but per run)',
    'build-timestamp': 'date-times inside the run window of the build that wrote them (page footers, '
                       'qa, package manifest, Bundle.timestamp ...): the build clock (memo C6, R2)',
    'run-uuid': 'UUIDs that occur in only one of the two files (random per run: expansion bundle id, '
                'table ids; memo C6). UUIDs present in both files are left alone.',
    'measurement': 'timing/memory measurements stored among outputs: qa-time-report.*, the time column '
                   'of fragment-usage-analysis.csv, memory/time figures in qa.json, the "<n>ms" elapsed-time '
                   'prefix of terminology operation logs in qa*.html (memo C8)',
    'tx-http-headers': 'per-request / per-session values the terminology server sends, in the request log qa-tx.html: '
                       'all response headers except content-type (request ids, trace ids, CDN ids, cache ages/hits, '
                       'dates, etags, lengths; which ones a CDN sends varies), the X-Cache-Id request header and the '
                       'cache-id echoed in bodies; '
                       'requests, status lines and the rest of the bodies are still compared',
    'zip-entry-time': 'zip member modification times (wall clock, memo C6/C8)',
    'tar-entry-time': 'tar member mtimes in .tgz (memo C6/C8)',
    'gzip-header-time': 'gzip header MTIME field (memo C8)',
    'sqlite-physical': 'SQLite file bytes differ but every table has identical rows in identical order '
                       '(page layout / free-list only)',
}

MON = {m: i + 1 for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}
DOW = r'(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)'
MONR = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
# (name, regex, groups -> (y, mo, d, h, mi, s, zone-string-or-None))
TS_FORMATS = [
    ('iso', re.compile(r'(?<![\d])(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d)(?:[.,](\d{1,9}))?)?'
                       r'( ?(?:Z|[+-]\d\d:?\d\d|UTC|GMT))?(?![\d])'),
     lambda g: (g[0], g[1], g[2], g[3], g[4], g[5] or 0, g[7])),
    # publisher footer: SimpleDateFormat("EEE, MMM dd, yyyy HH:mmZ") e.g. "Fri, Nov 20, 2026 15:12+0000"
    ('footer', re.compile(DOW + r', (' + MONR + r') (\d\d?), (\d{4}) (\d\d):(\d\d)(?::(\d\d))?( ?[+-]\d{4}| ?[A-Z]{2,5})?((?:[+-]\d\d:\d\d)?)'),
     lambda g: (g[2], MON[g[0]], g[1], g[3], g[4], g[5] or 0, ((g[6] or '') + g[7]) or None)),
    # java.util.Date.toString(): "Fri Nov 20 15:12:41 UTC 2026"
    ('javadate', re.compile(DOW + r' (' + MONR + r') (\d\d) (\d\d):(\d\d):(\d\d) ([A-Z]{2,5}|GMT[+-]\d\d:\d\d) (\d{4})'),
     lambda g: (g[6], MON[g[0]], g[1], g[2], g[3], g[4], g[5])),
    # RFC 822: "Fri, 20 Nov 2026 15:12:41 +0000"
    ('rfc822', re.compile(DOW + r', (\d\d?) (' + MONR + r'),? (\d{4}) (\d\d):(\d\d)(?::(\d\d))? ?([+-]\d{4}|GMT|UTC)?'),
     lambda g: (g[2], MON[g[1]], g[0], g[3], g[4], g[5] or 0, g[6])),
    # compact yyyyMMddHHmmss (package.json "date"), optionally yyyyMMddHHmm
    ('compact', re.compile(r'(?<![\d])(20\d\d)(\d\d)(\d\d)(\d\d)(\d\d)(\d\d)?([+-]\d{4})?(?![\d])'),
     lambda g: (g[0], g[1], g[2], g[3], g[4], g[5] or 0, g[6])),
]
# no look-behind: the table generator's per-run uuid is glued to id prefixes such as "hc" / "cb"
UUID_RE = re.compile(r'(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])')
# default Object.toString() / array toString(): an identity hash code leaking into content
IDLEAK_RE = re.compile(r'(?:\[{1,3}[BCDFIJSZ]|\[{1,3}L[\w.$]+;|\b(?:[a-z_][\w]*\.)+[A-Z][\w$]*)@[0-9a-f]{5,8}\b')


def zone_offsets(zone, meta):
    """Candidate UTC offsets (seconds) for a zone string; None/abbreviations -> UTC and the build's zone."""
    cands = {0, meta.get('tz_offset', 0)}
    if zone:
        z = zone.strip()
        m = re.match(r'(?:GMT)?([+-])(\d\d):?(\d\d)', z)
        if m:
            off = (int(m.group(2)) * 3600 + int(m.group(3)) * 60) * (1 if m.group(1) == '+' else -1)
            return [off]
        if z == 'Z':
            return [0]
    return sorted(cands)


class Normalizer:
    """Per-file-pair normalizer: knows both builds' run windows and which UUIDs are stable."""

    def __init__(self, meta, stable_uuids=None):
        self.meta = meta  # dict with start, end (epoch s), tz_offset (s)
        self.stable = stable_uuids or set()
        self.subs = Counter()     # rule -> substitutions
        self.values = defaultdict(list)  # rule -> replaced values (to detect absorbed differences)

    def in_window(self, parts):
        """'build-timestamp' if the value is inside the run window, 'build-timestamp-12h' if only as a
        12-hour clock value, else None."""
        y, mo, d, h, mi, s, zone = parts
        try:
            naive = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        except ValueError:
            return None
        epoch = (naive - dt.datetime(1970, 1, 1)).total_seconds()
        for add, rule in ((0, 'build-timestamp'), (43200, 'build-timestamp-12h')):
            if add and int(h) >= 12:
                continue
            for off in zone_offsets(zone, self.meta):
                t = epoch + add - off
                if self.meta['start'] - 900 <= t <= self.meta['end'] + 900:
                    return rule
        return None

    def text(self, s, strip_zone=False, strip_env=False):
        if strip_env:  # build paths -> placeholder (longest first; the tmp dir may sit under the IG dir)
            for k, ph in sorted(((self.meta.get('ig_path'), '\u27e8IGDIR\u27e9'), (self.meta.get('tmpdir'), '\u27e8TMPDIR\u27e9')),
                                key=lambda x: -len(x[0] or '')):
                if k and k != '/tmp':
                    s = s.replace(k, ph).replace(k.replace('/', '_'), ph)  # also the mangled form used in anchors
        for name, rx, conv in TS_FORMATS:
            def rep(m):
                parts = conv(m.groups())
                rule = self.in_window(parts)
                if not rule:
                    return m.group(0)
                self.subs[rule] += 1
                self.values[rule].append(m.group(0))
                z = '' if strip_zone else (parts[6] or '').strip()
                return '\u27e8T' + (':' + z if z else '') + '\u27e9'
            s = rx.sub(rep, s)
        ids = {}

        def urep(m):
            u = m.group(0).lower()
            if u in self.stable:
                return m.group(0)
            self.subs['run-uuid'] += 1
            self.values['run-uuid'].append(u)
            if u not in ids:
                ids[u] = len(ids) + 1
            return '\u27e8U%d\u27e9' % ids[u]
        return UUID_RE.sub(urep, s)


def measurement(rel, s, norm):
    """File-specific measurement rules; returns the text with measurements blanked."""
    base = os.path.basename(rel)
    if base == 'qa-tx.html':
        # response headers (lower-case names in this log; request headers are capitalized): dropped, except
        # content-type; which headers a CDN adds varies per request (x-cache-hits, transfer-encoding, ...)
        hdr = r'(?m)^(?!content-type:)([a-z][a-z0-9-]*):(?!//).*\n'
        s3, k = re.subn(hdr, '', s)
        # the server-assigned cache id, which the client sends back as a request header
        s3, k3 = re.subn(r'(?m)^(X-Cache-Id): .*$', r'\1: #', s3)
        k += k3
        # server-side elapsed times in the trace text of response bodies ("\n12ms http://...")
        s3, k4 = re.subn(r'(\\n|\n)\d+ms ', r'\1#ms ', s3)
        k += k4
        # the server's per-session cache id is also echoed in response bodies
        s3, k2 = re.subn(r'(&quot;cache-id&quot;,&quot;valueId&quot;:&quot;)[^&]*(&quot;)', r'\1#\2', s3)
        k += k2
        if k:
            norm.subs['tx-http-headers'] += k
            norm.values['tx-http-headers'].extend(m.group(0) for m in re.finditer(r'(?m)^(?:(?!content-type:)[a-z][a-z0-9-]*:(?!//).*|X-Cache-Id: .*)$', s))
            # (an absorbed difference here can also be a header present in one build only)
            s = s3
    if base.startswith('qa-time-report'):  # the whole file is a timing report (which steps, how long)
        s2 = '\u27e8measurement file\u27e9'
    elif base == 'fragment-usage-analysis.csv':  # name,count,time,size
        s2 = re.sub(r'^([^,\n]*,[^,\n]*,)[^,\n]*', r'\1#', s, flags=re.M)
    elif base.startswith('qa') and base.endswith('.html'):  # tx-op logs in qa.html: "12ms <message>"
        s2 = re.sub(r'(?m)^(\s*(?:<pre>)?)\d+ms ', r'\1#ms ', s)
    elif base in ('qa.json',):
        s2 = re.sub(r'("(?:maxMemory|totalMemory|freeMemory|time|elapsed|duration)"\s*:\s*)\d+(?:\.\d+)?', r'\1#', s)
    else:
        return s
    if s2 != s:
        k = len(re.findall('#', s2)) - len(re.findall('#', s))
        norm.subs['measurement'] += max(k, 1)
        norm.values['measurement'].append(hashlib.md5(s.encode('utf-8', 'surrogateescape')).hexdigest())
    return s2


# ----------------------------------------------------------------------------------------------
# Order-only canonical forms

def json_canon(v, sort_arrays):
    if isinstance(v, dict):
        return '{' + ','.join(json.dumps(k) + ':' + json_canon(v[k], sort_arrays) for k in sorted(v)) + '}'
    if isinstance(v, list):
        items = [json_canon(x, sort_arrays) for x in v]
        if sort_arrays:
            items.sort()
        return '[' + ','.join(items) + ']'
    return json.dumps(v)


def html_canon(n, sort_children):
    if isinstance(n, str):
        return 'T' + n
    if isinstance(n, tuple):
        return 'C' + repr(n)
    kids = domdiff._norm_children(n.children) if n.tag not in domdiff.VERBATIM else n.children
    ks = [html_canon(c, sort_children) for c in kids]
    if sort_children:
        ks.sort()
    return '<' + n.tag + repr(sorted(n.attrs.items())) + '>' + '\x00'.join(ks) + '</>'


def first_diff_lines(la, lb, ctx=3, maxw=240):
    """A short unified-style diff around the first differing region (works on huge files)."""
    i = 0
    while i < min(len(la), len(lb)) and la[i] == lb[i]:
        i += 1
    j = 0
    while j < min(len(la), len(lb)) - i and la[-1 - j] == lb[-1 - j]:
        j += 1
    lo = max(0, i - ctx)
    wa, wb = la[lo:max(lo, len(la) - j) + ctx][:120], lb[lo:max(lo, len(lb) - j) + ctx][:120]

    # long lines: show a window around the first differing column (else clipped lines may look equal)
    col = 0
    if i < len(la) and i < len(lb):
        x, y = la[i], lb[i]
        col = next((k for k, (p, q) in enumerate(zip(x, y)) if p != q), min(len(x), len(y)))
    lo_c = max(0, col - maxw // 3)

    def clip(x):
        if len(x) <= maxw:
            return x
        return ('…' if lo_c else '') + x[lo_c:lo_c + maxw] + ('…' if len(x) > lo_c + maxw else '')
    out = ['@@ line %d @@' % (lo + 1)]
    for l in difflib.unified_diff([clip(x) for x in wa], [clip(x) for x in wb], lineterm='', n=ctx):
        if l.startswith(('---', '+++')):
            continue
        out.append(l)
        if len(out) > 40:
            out.append('…')
            break
    return '\n'.join(out)


def split_long(s):
    """Lines, with very long lines (minified html/json) broken at tag/element boundaries for diffs."""
    lines = s.splitlines()
    if lines and max(len(l) for l in lines) > 2000:
        lines = re.sub(r'(>)(?=<)|(,)(?=")', r'\1\2\n', s).splitlines()
    return lines


# ----------------------------------------------------------------------------------------------
# Comparison

def decode(b):
    if b'\x00' in b[:8192]:
        return None
    try:
        return b.decode('utf-8')
    except UnicodeDecodeError:
        return None


def kind_of(name, b):
    if b[:4] == b'PK\x03\x04' or b[:4] == b'PK\x05\x06':
        return 'zip'
    if b[:2] == b'\x1f\x8b':
        return 'gzip'
    if b[:16] == b'SQLite format 3\x00':
        return 'sqlite'
    ext = os.path.splitext(name)[1].lower()
    if ext in ('.html', '.htm', '.xhtml'):
        return 'html'
    if ext == '.json':
        return 'json'
    return 'text'


class Result(dict):
    pass


def compare_bytes(rel, a, b, ctx, depth=0):
    """Classify one file (or archive member). Returns dict(class, where, what, rules, diff, members, leak)."""
    if a == b:
        return {'class': 'same'}
    k = kind_of(rel, a)
    if k != kind_of(rel, b):
        return {'class': 'content', 'what': 'file kind differs', 'rules': {}}
    if k == 'zip':
        return compare_zip(rel, a, b, ctx, depth)
    if k == 'gzip':
        return compare_gzip(rel, a, b, ctx, depth)
    if k == 'sqlite':
        return compare_sqlite(rel, a, b, ctx)
    sa, sb = decode(a), decode(b)
    if sa is None or sb is None:
        return {'class': 'content', 'what': 'binary differs (%d vs %d bytes)' % (len(a), len(b)), 'rules': {}}
    return compare_text(rel, sa, sb, k, ctx)


def absorbed_rules(na, nb):
    rules = {}
    for r in set(na.subs) | set(nb.subs):
        rules[r] = {'subs': na.subs[r] + nb.subs[r], 'absorbed': na.values[r] != nb.values[r]}
    return rules


def compare_text(rel, sa, sb, kind, ctx):
    ua = {u.lower() for u in UUID_RE.findall(sa)}
    ub = {u.lower() for u in UUID_RE.findall(sb)}
    stable = ua & ub
    na, nb = Normalizer(ctx['meta_a'], stable), Normalizer(ctx['meta_b'], stable)
    ta, tb = measurement(rel, na.text(sa), na), measurement(rel, nb.text(sb), nb)
    rules = absorbed_rules(na, nb)
    res = {'rules': rules}
    leak = sorted(set(IDLEAK_RE.findall(sa)) ^ set(IDLEAK_RE.findall(sb)))
    if leak:
        res['idleak'] = leak[:5]
    if ta == tb:
        res['class'] = 'normalized'
        return res
    # build environment ignored: zone of the build timestamps, absolute build paths
    def env(strip_zone, strip_env):
        xa, xb = Normalizer(ctx['meta_a'], stable), Normalizer(ctx['meta_b'], stable)
        return (measurement(rel, xa.text(sa, strip_zone, strip_env), xa),
                measurement(rel, xb.text(sb, strip_zone, strip_env), xb))
    ea, eb = env(True, True)
    ma, mb = ctx['meta_a'], ctx['meta_b']
    leaks = []
    if ma.get('tz') != mb.get('tz') and env(True, False) != (ta, tb):
        leaks.append('tz')
    if (ma.get('ig_path'), ma.get('tmpdir')) != (mb.get('ig_path'), mb.get('tmpdir')) and env(False, True) != (ta, tb):
        leaks.append('path')
    if leaks:
        res['leaks'] = leaks
    if ea == eb:
        res.update({'class': 'env-leak', 'what': 'build environment leaks into output: ' + ', '.join(leaks or ['?'])})
        res['diff'] = first_diff_lines(split_long(ta), split_long(tb))
        return res
    ta, tb = ea, eb
    la, lb = split_long(ta), split_long(tb)
    res['diff'] = first_diff_lines(la, lb)
    if kind == 'html':
        try:
            pa, pb = domdiff.parse(ta), domdiff.parse(tb)
            r = domdiff.compare(pa, pb)
            if r is None:
                res.update({'class': 'dom-equal', 'what': 'bytes differ, DOM equal after domdiff normalization'})
                return res
            res['where'], res['what'] = r[0], r[1]
            if html_canon(pa, True) == html_canon(pb, True):
                res['class'] = 'order-only'
                res['order'] = 'sibling elements'
                return res
        except Exception as e:  # malformed html: fall back to lines
            res['what'] = 'html parse failed: %s' % e
    elif kind == 'json' or (kind == 'text' and ta.lstrip()[:1] in ('{', '[') and tb.lstrip()[:1] in ('{', '[')):
        try:
            ja, jb = json.loads(ta), json.loads(tb)
            if ja == jb:
                res.update({'class': 'order-only', 'order': 'object keys'})
                return res
            if json_canon(ja, True) == json_canon(jb, True):
                res.update({'class': 'order-only', 'order': 'array elements'})
                return res
        except ValueError:
            pass
    if 'what' not in res:
        i = next((i for i, (x, y) in enumerate(zip(la, lb)) if x != y), min(len(la), len(lb)))
        res['where'] = 'line %d' % (i + 1)
        res['what'] = (domdiff.short(la[i] if i < len(la) else '<eof>') + ' vs ' +
                       domdiff.short(lb[i] if i < len(lb) else '<eof>'))
    if sorted(la) == sorted(lb):
        res['class'] = 'order-only'
        res.setdefault('order', 'lines')
        return res
    # same words and punctuation, in another order: lists inside a line (CSV cells, "Child profiles: a, b")
    tok = re.compile(r'[^\s,;|<>"]+')
    if Counter(tok.findall(ta)) == Counter(tok.findall(tb)):
        res['class'] = 'order-only'
        res['order'] = 'tokens within lines'
        return res
    res['class'] = 'content'
    return res


def merge_members(rel, results, order_differs, only_a, only_b, meta_rules):
    cls = 'normalized'
    members = {}
    for name, r in results.items():
        if r['class'] == 'same':
            continue
        members[name] = r
        if CLASS_RANK[r['class']] > CLASS_RANK[cls]:
            cls = r['class']
    if order_differs and CLASS_RANK[cls] < CLASS_RANK['order-only']:
        cls = 'order-only'
    if only_a or only_b:
        cls = 'content'
    rules = dict(meta_rules)
    leaks = sorted({l for r in members.values() for l in r.get('leaks', [])})
    for r in members.values():
        for k, v in r.get('rules', {}).items():
            x = rules.setdefault(k, {'subs': 0, 'absorbed': False})
            x['subs'] += v['subs']
            x['absorbed'] = x['absorbed'] or v['absorbed']
    res = {'class': cls, 'rules': rules, 'members': members}
    if leaks:
        res['leaks'] = leaks
    if order_differs:
        res['member_order_differs'] = True
    if only_a or only_b:
        res['members_only_a'], res['members_only_b'] = only_a[:20], only_b[:20]
    worst = sorted(members.items(), key=lambda kv: -CLASS_RANK[kv[1]['class']])
    if only_a or only_b:
        res['what'] = 'members only in A: %d, only in B: %d' % (len(only_a), len(only_b))
    elif worst and CLASS_RANK[worst[0][1]['class']] >= CLASS_RANK['env-leak']:
        res['what'] = '%d members differ (worst: %s, %s)' % (
            sum(1 for _, r in worst if CLASS_RANK[r['class']] >= 2), worst[0][0], worst[0][1]['class'])
    elif order_differs:
        res['what'] = 'member order differs'
    return res


def compare_zip(rel, a, b, ctx, depth):
    try:
        za, zb = zipfile.ZipFile(io.BytesIO(a)), zipfile.ZipFile(io.BytesIO(b))
    except zipfile.BadZipFile:
        return {'class': 'content', 'what': 'bad zip', 'rules': {}}
    ia, ib = za.infolist(), zb.infolist()
    na, nb = [i.filename for i in ia], [i.filename for i in ib]
    sa, sb = set(na), set(nb)
    common = [n for n in na if n in sb]
    order_differs = common != [n for n in nb if n in sa]
    da, db = {i.filename: i for i in ia}, {i.filename: i for i in ib}
    times = sum(1 for n in common if da[n].date_time != db[n].date_time)
    meta = {}
    if times:
        meta['zip-entry-time'] = {'subs': times, 'absorbed': True}
    results = {}
    for n in common:
        ma, mb = za.read(n), zb.read(n)
        results[n] = compare_bytes(n, ma, mb, ctx, depth + 1) if ma != mb else {'class': 'same'}
    r = merge_members(rel, results, order_differs, sorted(sa - sb), sorted(sb - sa), meta)
    r['container'] = 'zip'
    return r


def compare_gzip(rel, a, b, ctx, depth):
    meta = {}
    if a[4:8] != b[4:8]:
        meta['gzip-header-time'] = {'subs': 1, 'absorbed': True}
    try:
        ua, ub = gzip.decompress(a), gzip.decompress(b)
    except OSError:
        return {'class': 'content', 'what': 'bad gzip', 'rules': {}}
    if ua == ub:
        if meta:
            return {'class': 'normalized', 'rules': meta}
        return {'class': 'content', 'what': 'gzip stream differs (same payload)', 'rules': {}}
    try:
        ta, tb = tarfile.open(fileobj=io.BytesIO(ua)), tarfile.open(fileobj=io.BytesIO(ub))
        ma, mb = ta.getmembers(), tb.getmembers()
    except tarfile.TarError:
        r = compare_bytes(rel[:-3] if rel.endswith('.gz') else rel + '!', ua, ub, ctx, depth + 1)
        r['rules'] = {**r.get('rules', {}), **meta}
        return r
    na, nb = [m.name for m in ma], [m.name for m in mb]
    sa, sb = set(na), set(nb)
    common = [n for n in na if n in sb]
    order_differs = common != [n for n in nb if n in sa]
    da, db = {m.name: m for m in ma}, {m.name: m for m in mb}
    mt = sum(1 for n in common if da[n].mtime != db[n].mtime)
    if mt:
        meta['tar-entry-time'] = {'subs': mt, 'absorbed': True}
    results = {}
    for n in common:
        x, y = da[n], db[n]
        if (x.mode, x.uid, x.gid, x.uname, x.gname, x.type) != (y.mode, y.uid, y.gid, y.uname, y.gname, y.type):
            results[n] = {'class': 'content', 'what': 'tar entry metadata (mode/owner/type) differs', 'rules': {}}
            continue
        if not x.isfile():
            continue
        ba, bb = ta.extractfile(x).read(), tb.extractfile(y).read()
        results[n] = compare_bytes(n, ba, bb, ctx, depth + 1) if ba != bb else {'class': 'same'}
    r = merge_members(rel, results, order_differs, sorted(sa - sb), sorted(sb - sa), meta)
    r['container'] = 'tgz'
    return r


def sqlite_dump(b, ctx_meta):
    with tempfile.NamedTemporaryFile(suffix='.db') as f:
        f.write(b)
        f.flush()
        con = sqlite3.connect('file:%s?mode=ro&immutable=1' % f.name, uri=True)
        con.text_factory = lambda x: x.decode('utf-8', 'surrogateescape')
        out = {}
        for name, sql in con.execute("select name, sql from sqlite_master where type='table' order by name"):
            cols = [r[1] for r in con.execute('pragma table_info("%s")' % name)]
            pk = [r[1] for r in con.execute('pragma table_info("%s")' % name) if r[5]]
            rows = [tuple(r) for r in con.execute('select * from "%s"' % name)]
            out[name] = {'sql': sql, 'cols': cols, 'pk': pk, 'rows': rows}
        con.close()
    return out


def compare_sqlite(rel, a, b, ctx):
    try:
        da, db = sqlite_dump(a, ctx['meta_a']), sqlite_dump(b, ctx['meta_b'])
    except sqlite3.Error as e:
        return {'class': 'content', 'what': 'sqlite read failed: %s' % e, 'rules': {}}
    tables = {}
    worst = 'normalized'
    rules = {}
    na, nb = Normalizer(ctx['meta_a']), Normalizer(ctx['meta_b'])

    def cell(c, n, env):
        if isinstance(c, bytes):
            try:
                return n.text(c.decode('utf-8'), env, env).encode('utf-8')
            except UnicodeDecodeError:
                return c
        return n.text(c, env, env) if isinstance(c, str) else c

    def norm_rows(rows, n, env=False):
        return [tuple(cell(c, n, env) for c in r) for r in rows]
    for t in sorted(set(da) | set(db)):
        if t not in da or t not in db:
            tables[t] = 'only in ' + ('A' if t in da else 'B')
            worst = 'content'
            continue
        x, y = da[t], db[t]
        if x['sql'] != y['sql']:
            tables[t] = 'schema differs'
            worst = 'content'
            continue
        if x['rows'] == y['rows']:
            continue
        ra, rb = norm_rows(x['rows'], na), norm_rows(y['rows'], nb)
        if ra == rb:
            tables[t] = 'normalized'
            continue
        ea = norm_rows(x['rows'], Normalizer(ctx['meta_a']), True)
        eb = norm_rows(y['rows'], Normalizer(ctx['meta_b']), True)
        if ea == eb:
            tables[t] = 'env-leak (time zone / build path in rows)'
            worst = max(worst, 'env-leak', key=CLASS_RANK.get)
            continue
        ra, rb = ea, eb
        key = lambda r: repr(r)
        if sorted(ra, key=key) == sorted(rb, key=key):
            tables[t] = 'order-only (rows)'
            worst = max(worst, 'order-only', key=CLASS_RANK.get)
            continue
        # drop generated integer keys (primary key / *Key columns that are integers) and compare as multisets
        keyidx = [i for i, c in enumerate(x['cols']) if c in x['pk'] or c.lower().endswith('key')
                  and all(isinstance(r[i], int) or r[i] is None for r in ra[:200] + rb[:200])]
        strip = lambda rows: sorted((tuple(c for i, c in enumerate(r) if i not in keyidx) for r in rows), key=key)
        if keyidx and strip(ra) == strip(rb):
            tables[t] = 'order-only (rows; generated keys renumbered: %s)' % ','.join(x['cols'][i] for i in keyidx)
            worst = max(worst, 'order-only', key=CLASS_RANK.get)
            continue
        sa, sb = Counter(map(key, strip(ra) if keyidx else ra)), Counter(map(key, strip(rb) if keyidx else rb))
        onlya, onlyb = list((sa - sb).elements()), list((sb - sa).elements())
        cols = [c for i, c in enumerate(x['cols']) if not keyidx or i not in keyidx]
        ex = ''
        if onlya and onlyb:
            ra0, rb0 = ast.literal_eval(onlya[0]), ast.literal_eval(onlyb[0])  # repr of a row tuple
            ex = '; '.join('%s: %s | %s' % (c, domdiff.short(u, 120), domdiff.short(v, 120))
                           for c, u, v in zip(cols, ra0, rb0) if u != v)[:600]
        tables[t] = 'content: %d rows only in A, %d only in B; e.g. %s' % (len(onlya), len(onlyb), ex)
        worst = 'content'
    rules = absorbed_rules(na, nb)
    if not tables:
        rules['sqlite-physical'] = {'subs': 1, 'absorbed': True}
    res = {'class': worst, 'rules': rules, 'tables': tables, 'container': 'sqlite'}
    if tables:
        res['what'] = '; '.join('%s: %s' % kv for kv in list(tables.items())[:4])
    return res


# ----------------------------------------------------------------------------------------------
# Attribution to the memo's cause classes

def attribute(rel, r):
    base = os.path.basename(rel.split('!')[-1])
    cls = r['class']
    if r.get('idleak'):
        return 'C4a', 'identity hash code printed into content (default toString)'
    if cls == 'env-leak':
        lk = r.get('leaks', [])
        if lk == ['tz']:
            return 'C6', 'build timestamp rendered in the JVM default time zone'
        if lk == ['path']:
            return 'ENV', 'absolute build path (IG dir / temp dir) written into output'
        return 'C6+ENV', 'time zone and absolute build path leak into output'
    if re.match(r'(codesystem|valueset)-ref-(all-)?list', base):
        return 'C4a', 'pub_xref_used HashSet<Resource> iteration (CrossViewRenderer -> PublisherGenerator ref lists)'
    if r.get('container') == 'sqlite':
        t = r.get('tables', {})
        if any(k in ('CodeSystemListRefs', 'ValueSetListRefs') for k in t):
            return 'C4a/C8', 'package.db reference tables filled from pub_xref_used (DBBuilder)'
        return 'C8', 'package.db rows: ' + ', '.join(t)
    if base.startswith('qa-txservers') or base.startswith('qa-tx'):
        return 'C7', 'terminology server log in call order / server answers'
    if r.get('container') in ('zip', 'tgz'):
        if r.get('member_order_differs') and cls == 'order-only' and not any(
                CLASS_RANK[m['class']] >= 2 for m in r.get('members', {}).values()):
            return 'C4/C8', ('archive member order: an unsorted directory listing (C4c) or a hash-ordered collection '
                             '(C4a); a difference in the unperturbed control build rules out C4c')
        worst = max(r.get('members', {}).items(), key=lambda kv: CLASS_RANK[kv[1]['class']], default=None)
        inner = attribute(worst[0], worst[1])[0] if worst else '?'
        return 'C8<' + inner, 'archive contains differing members (worst member cause: %s)' % inner
    extra = ' (+ env leak: %s)' % ','.join(r['leaks']) if r.get('leaks') else ''
    if cls == 'content' and re.search(r'terminology\.hl7\.org/\d+\.\d+\.\d+/', (r.get('what') or '') + '\n'.join(
            l for l in (r.get('diff') or '').splitlines() if l[:1] in '+-')):
        return 'C4b', ('link to a different terminology.hl7.org version: a canonical resolved among several loaded THO '
                       'versions in run-dependent order (frozen cache, so not package drift)' + extra)
    if cls == 'order-only':
        return 'C4', 'iteration order (%s)%s' % (r.get('order', ''), extra)
    if cls == 'dom-equal':
        return '?', 'serialization only (whitespace / attribute order)'
    return '?', 'unattributed content difference (candidates: C1-C3 render-time mutation, C7 terminology)' + extra


def pattern_of(rel):
    b = os.path.basename(rel)
    m = re.match(r'([A-Z][A-Za-z]+)-(.+?)(\.[a-z]+(?:\.html)?)$', b)
    if m:
        return m.group(1) + '-*' + m.group(3)
    m = re.match(r'([a-z]+)-(.+)-(union|intersection)(\.html)$', b)  # comparison pages
    if m:
        return m.group(1) + '-*-' + m.group(3) + m.group(4)
    return re.sub(r'\d+', '#', b)


# ----------------------------------------------------------------------------------------------

CTX = {}


def worker(rel):
    pa, pb = os.path.join(CTX['a'], rel), os.path.join(CTX['b'], rel)
    try:
        if os.path.getsize(pa) == os.path.getsize(pb):
            with open(pa, 'rb') as fa, open(pb, 'rb') as fb:
                same = True
                while True:
                    x, y = fa.read(1 << 20), fb.read(1 << 20)
                    if x != y:
                        same = False
                        break
                    if not x:
                        break
            if same:
                return rel, None
        with open(pa, 'rb') as f:
            a = f.read()
        with open(pb, 'rb') as f:
            b = f.read()
        r = compare_bytes(rel, a, b, CTX)
    except Exception as e:  # never lose a file: a comparison failure is reported as content
        r = {'class': 'content', 'what': 'comparison failed: %r' % e, 'rules': {}}
    return rel, r


def init(ctx):
    CTX.update(ctx)


def walk(d):
    out = []
    for dp, dn, fn in os.walk(d):
        for f in fn:
            out.append(os.path.relpath(os.path.join(dp, f), d))
    return out


def flatten_members(rel, r, out):
    for n, m in r.get('members', {}).items():
        sub = rel + '!' + n
        if m['class'] != 'normalized':
            out.append((sub, m))
        flatten_members(sub, m, out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', required=True)
    ap.add_argument('--b', required=True)
    ap.add_argument('--meta-a', required=True)
    ap.add_argument('--meta-b', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--label', default='')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--examples', type=int, default=40)
    ap.add_argument('--selftest', action='store_true')
    o = ap.parse_args()
    meta_a, meta_b = json.load(open(o.meta_a)), json.load(open(o.meta_b))
    for d, m in ((o.a, meta_a), (o.b, meta_b)):  # a failed build must never look like "no differences"
        if not os.path.isdir(d) or not any(True for _ in os.scandir(d)):
            sys.exit('detcmp: %s (%s, rc=%s) has no output tree: build failed?' % (d, m.get('label'), m.get('rc')))
    bad = [m.get('label') for m in (meta_a, meta_b) if str(m.get('rc')) != '0' or m.get('failed')]
    if bad:  # still compared, but the result says so loudly: a failed build is not a determinism result
        print('detcmp: WARNING: build(s) %s failed (rc != 0 or "Publishing Content Failed"); comparison INVALID' % ', '.join(bad))
    fa, fb = set(walk(o.a)), set(walk(o.b))
    common = sorted(fa & fb)
    ctx = {'a': o.a, 'b': o.b, 'meta_a': meta_a, 'meta_b': meta_b}
    results = {}
    with Pool(o.jobs, initializer=init, initargs=(ctx,)) as p:
        for rel, r in p.imap_unordered(worker, common, chunksize=64):
            if r is not None:
                results[rel] = r
    init(ctx)
    os.makedirs(o.out, exist_ok=True)

    # normalization accounting
    norm = {k: {'rule': k, 'why': v, 'substitutions': 0, 'files_touched': 0, 'files_absorbed': 0} for k, v in RULES.items()}
    for rel, r in results.items():
        for k, v in r.get('rules', {}).items():
            n = norm[k]
            n['substitutions'] += v['subs']
            n['files_touched'] += 1
            n['files_absorbed'] += 1 if v['absorbed'] else 0
    classes = Counter(r['class'] for r in results.values())

    # attribution + groups (top-level files; archive members are listed inside their container)
    groups = {}
    files = {}
    for rel in sorted(results):
        r = results[rel]
        if r['class'] == 'normalized':
            files[rel] = {'class': 'normalized', 'rules': sorted(k for k, v in r.get('rules', {}).items() if v['absorbed'])}
            continue
        cause, why = attribute(rel, r)
        entry = {k: r[k] for k in ('class', 'where', 'what', 'order', 'idleak', 'leaks', 'tables', 'member_order_differs',
                                   'members_only_a', 'members_only_b') if k in r}
        entry['cause'], entry['cause_note'] = cause, why
        mem = []
        flatten_members(rel, r, mem)
        if mem:
            entry['members'] = {m: {'class': x['class'], 'cause': attribute(m, x)[0],
                                    **{k: x[k] for k in ('what', 'where') if k in x}} for m, x in mem[:200]}
        files[rel] = entry
        key = (r['class'], cause, pattern_of(rel) if r['class'] != 'env-leak' else '*')
        g = groups.setdefault(key, {'class': r['class'], 'cause': cause, 'cause_note': why, 'pattern': key[2],
                                    'count': 0, 'files': [], 'example': None})
        g['count'] += 1
        if len(g['files']) < 50:
            g['files'].append(rel)
        if g['example'] is None:
            ex = {'file': rel, **{k: r[k] for k in ('where', 'what', 'diff') if k in r}}
            if not ex.get('diff') and mem:  # archive: show the worst member's diff
                m, x = max(mem, key=lambda mx: CLASS_RANK[mx[1]['class']])
                ex['member'] = m
                ex.update({('member_' + k): x[k] for k in ('class', 'where', 'what', 'diff') if k in x})
            g['example'] = ex
    glist = sorted(groups.values(), key=lambda g: (-CLASS_RANK[g['class']], -g['count'], g['pattern']))
    for i, g in enumerate(glist):
        g['id'] = 'G%02d' % (i + 1)

    # example file pairs
    exdir = os.path.join(o.out, 'examples')
    for g in glist[:o.examples]:
        rel = g['example']['file']
        for side, root in (('a', o.a), ('b', o.b)):
            p = os.path.join(root, rel)
            if os.path.getsize(p) <= 2_000_000 and g['example'].get('member') is None:
                d = os.path.join(exdir, g['id'], side)
                os.makedirs(d, exist_ok=True)
                with open(p, 'rb') as f, open(os.path.join(d, os.path.basename(rel)), 'wb') as w:
                    w.write(f.read())

    report = {
        'label': o.label, 'a': o.a, 'b': o.b, 'meta_a': meta_a, 'meta_b': meta_b,
        'files_a': len(fa), 'files_b': len(fb), 'common': len(common),
        'only_in_a': sorted(fa - fb), 'only_in_b': sorted(fb - fa),
        'identical': len(common) - len(results),
        'classes': {c: classes.get(c, 0) for c in CLASS_RANK if c != 'same'},
        'real_differences': sum(v for c, v in classes.items() if c != 'normalized') + len(fa ^ fb),
        'valid': not bad, 'failed_builds': bad,
        'normalization': list(norm.values()),
        'groups': glist, 'files': files,
    }
    with open(os.path.join(o.out, 'report.json'), 'w') as f:
        json.dump(report, f, indent=1, default=str)
    with open(os.path.join(o.out, 'summary.txt'), 'w') as f:
        f.write(summary_text(report))
    sys.stdout.write(summary_text(report, brief=True))


def summary_text(r, brief=False):
    L = []
    L.append('%s: A=%s B=%s' % (r['label'], r['meta_a'].get('label'), r['meta_b'].get('label')))
    L.append('  perturbations B: %s' % (r['meta_b'].get('perturbations') or 'none'))
    L.append('  files A=%d B=%d common=%d identical=%d only-A=%d only-B=%d' % (
        r['files_a'], r['files_b'], r['common'], r['identical'], len(r['only_in_a']), len(r['only_in_b'])))
    L.append('  classes: ' + ', '.join('%s=%d' % kv for kv in r['classes'].items()))
    if not r.get('valid', True):
        L.append('  INVALID: build(s) %s failed; the numbers below do not measure determinism' % ', '.join(r['failed_builds']))
    L.append('  REAL DIFFERENCES (not normalized): %d' % r['real_differences'])
    L.append('  normalization rules (substitutions / files touched / files where it absorbed an A-B difference):')
    for n in r['normalization']:
        if n['substitutions']:
            L.append('    %-18s %8d %7d %7d' % (n['rule'], n['substitutions'], n['files_touched'], n['files_absorbed']))
    for k in ('only_in_a', 'only_in_b'):
        if r[k]:
            L.append('  %s: %s%s' % (k, ', '.join(r[k][:8]), ' …' if len(r[k]) > 8 else ''))
    L.append('  groups:')
    for g in r['groups']:
        L.append('    %s %-10s %-6s %5d  %s  -- %s' % (g['id'], g['class'], g['cause'], g['count'], g['pattern'], g['cause_note']))
        if brief:
            continue
        ex = g['example']
        L.append('        e.g. %s%s' % (ex['file'], (' ! ' + ex['member']) if ex.get('member') else ''))
        for k in ('where', 'what', 'member_class', 'member_where', 'member_what'):
            if ex.get(k):
                L.append('        %s: %s' % (k, ex[k]))
        d = ex.get('diff') or ex.get('member_diff')
        if d:
            L.extend('        | ' + l for l in d.splitlines()[:25])
        if g['count'] > 1:
            L.append('        files: ' + ', '.join(g['files'][:6]) + (' …' if g['count'] > 6 else ''))
    return '\n'.join(L) + '\n'


def selftest():
    meta = {'start': 1795100000, 'end': 1795100600, 'tz_offset': 0}
    n = Normalizer(meta)
    t = dt.datetime.utcfromtimestamp(1795100300)
    s = t.strftime('%Y-%m-%dT%H:%M:%S+00:00 ') + t.strftime('%a, %b %d, %Y %H:%M+0000 ') + '2001-01-01T00:00:00Z'
    out = n.text(s)
    ok = out.count('\u27e8T') == 2 and '2001-01-01' in out
    a = compare_text('x.csv', 'a\nb\n', 'b\na\n', 'text', {'meta_a': meta, 'meta_b': meta})
    ok = ok and a['class'] == 'order-only'
    j = compare_text('x.json', '{"a":[1,2]}', '{"a":[2,1]}', 'json', {'meta_a': meta, 'meta_b': meta})
    ok = ok and j['class'] == 'order-only'
    u = compare_text('x.txt', 'id 11111111-2222-3333-4444-555555555555 k', 'id 11111111-2222-3333-4444-666666666666 k',
                     'text', {'meta_a': meta, 'meta_b': meta})
    ok = ok and u['class'] == 'normalized'
    h = compare_text('x.html', '<ul><li>a</li><li>b</li></ul>', '<ul><li>b</li><li>a</li></ul>', 'html',
                     {'meta_a': meta, 'meta_b': meta})
    ok = ok and h['class'] == 'order-only'
    print('selftest', 'OK' if ok else 'FAILED', out, a['class'], j['class'], u['class'], h['class'])
    return ok


def aggregate(run):
    """Markdown summary of one determinism.sh run directory (run.json, b*/meta/meta.json, cmp-*/report.json)."""
    import glob
    rj = json.load(open(os.path.join(run, 'run.json')))
    metas = []
    for p in sorted(glob.glob(os.path.join(run, 'b*', 'meta', 'meta.json')), key=lambda p: int(re.search(r'/b(\d+)/', p).group(1))):
        metas.append(json.load(open(p)))
    reps = []
    for p in sorted(glob.glob(os.path.join(run, 'cmp-b1-b*', 'report.json')), key=lambda p: int(re.search(r'-b(\d+)/', p).group(1))):
        reps.append(json.load(open(p)))
    L = ['# Determinism run %s' % rj['run_id'], '',
         'IG `%s` @ %s, jar %s (sha256 %s…), %s, -Xmx%s, SUSHI %s, package cache snapshot %s (%d packages)' % (
             rj['ig'], rj['ig_commit'][:12], rj['jar'], rj['jar_sha256'][:12], rj['java'], rj['xmx'], rj['sushi'],
             os.path.basename(rj['package_cache_snapshot']), rj['snapshot_packages']), '',
         'Terminology: %s%s' % (rj.get('tx_mode', 'live'), (' (snapshot %s)' % os.path.basename(rj['tx_snapshot'])) if rj.get('tx_snapshot') else ''), '',
         '| build | perturbations | rc | seconds | output files | tz | hashshift | packages fetched | tx cache files at start | tx requests to server |',
         '|---|---|---|---|---|---|---|---|---|---|']
    for m in metas:
        L.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (m['label'] + (' (tx warm-up)' if m['label'] == 'b0' else ''),
                 m['perturbations'], m['rc'], m['seconds'], m['output_files'], m['tz'], m['hashshift'], len(m['downloads']),
                 m.get('tx_cache_files_at_start', '?'), m.get('tx_requests', '?')))
    # '<id>#dev' is the IG's own package, which the build installs into the cache: an output, not an input
    dls = [tuple(d for d in m['downloads'] if '#dev ' not in d + ' ') for m in metas]
    if any(dls):
        L += ['', 'Packages the frozen cache lacked, fetched by each build (uncontrolled input; the IG\'s own #dev package excluded): ' +
              ('**identical in all builds**' if len(set(dls)) == 1 else '**DIFFER between builds**')]
        for m in (metas if len(set(dls)) > 1 else metas[:1]):
            L += ['- %s: %s' % (m['label'], '; '.join(d for d in m['downloads'] if '#dev ' not in d + ' ') or '-')]
    bad = [m['label'] for m in metas if str(m['rc']) != '0' or m.get('failed')]
    if bad:
        L += ['', '**Builds that failed: %s** (see b<n>.publisher.log.tail)' % ', '.join(bad)]
    L += ['', '| pair | B perturbations | only A | only B | normalized | env-leak | dom-equal | order-only | content | **real diffs** |',
          '|---|---|---|---|---|---|---|---|---|---|']
    for r in reps:
        c = r['classes']
        L.append('| %s | %s | %d | %d | %d | %d | %d | %d | %d | **%s** |' % (
            r['label'].split(' ', 1)[1], r['meta_b']['perturbations'], len(r['only_in_a']), len(r['only_in_b']),
            c['normalized'], c['env-leak'], c['dom-equal'], c['order-only'], c['content'],
            r['real_differences'] if r.get('valid', True) else 'INVALID (build failed)'))
    ctl = [r for r in reps if r['meta_b']['perturbations'] == 'none' and r.get('valid', True)]
    if len(ctl) >= 2:  # N control builds: the spread of run-to-run noise, and which files it hits
        import statistics
        n = sorted(r['real_differences'] for r in ctl)
        hits = Counter(f for r in ctl for f, e in r['files'].items() if e['class'] != 'normalized')
        for r in ctl:
            hits.update(r['only_in_a'] + r['only_in_b'])
        L += ['', '**Control spread over %d unperturbed pairs** (each vs b1): real diffs min %d / median %s / max %d; '
              'union of differing files %d, in every pair %d.' % (len(ctl), n[0], statistics.median(n), n[-1], len(hits),
                                                                 sum(1 for v in hits.values() if v == len(ctl))), '',
              '| file | pairs differing |', '|---|---|']
        L += ['| `%s` | %d/%d |' % (f, v, len(ctl)) for f, v in sorted(hits.items(), key=lambda kv: (-kv[1], kv[0]))[:40]]
        if len(hits) > 40:
            L.append('| … %d more | |' % (len(hits) - 40))
    for r in reps:
        L += ['', '## %s (B: %s)' % (r['label'], r['meta_b']['perturbations']), '',
              'Normalization: ' + ('; '.join('%s %d subs / %d files absorbed' % (n['rule'], n['substitutions'], n['files_absorbed'])
                                             for n in r['normalization'] if n['substitutions']) or 'none'), '']
        for k in ('only_in_a', 'only_in_b'):
            if r[k]:
                L.append('- %s (%d): %s%s' % (k, len(r[k]), ', '.join('`%s`' % x for x in r[k][:8]), ' …' if len(r[k]) > 8 else ''))
        if r['groups']:
            L += ['| group | class | cause | files | pattern | example | note |', '|---|---|---|---|---|---|---|']
            for g in r['groups']:
                ex = g['example']
                L.append('| %s | %s | %s | %d | `%s` | `%s`%s | %s |' % (
                    g['id'], g['class'], g['cause'], g['count'], g['pattern'], ex['file'],
                    (' ! `%s`' % ex['member']) if ex.get('member') else '', g['cause_note']))
        L.append('')
        L.append('Example diffs: cmp-b1-b%s/summary.txt' % r['meta_b']['label'][1:])
    return '\n'.join(L) + '\n'


def table(dirs):
    """One row per determinism.sh result dir (on the Mac or VM): the baseline table of the README."""
    L = ['| IG | files | build s | pair | B perturbations | real diffs | env-leak (tz / path / both) | order-only | content | causes (files) |',
         '|---|---|---|---|---|---|---|---|---|---|']
    for d in dirs:
        import glob
        metas = {os.path.basename(os.path.dirname(os.path.dirname(p))): json.load(open(p))
                 for p in glob.glob(os.path.join(d, 'b*', 'meta', 'meta.json'))}
        rj = json.load(open(os.path.join(d, 'run.json')))
        for rp in sorted(glob.glob(os.path.join(d, 'cmp-b1-b*', 'report.json')), key=lambda p: int(re.search(r'-b(\d+)/', p).group(1))):
            r = json.load(open(rp))
            c = r['classes']
            lk = Counter()
            causes = Counter()
            for f, e in r['files'].items():
                if e['class'] == 'normalized':
                    continue
                if e['class'] == 'env-leak':
                    lk['+'.join(e.get('leaks', ['?']))] += 1
                causes[e['cause']] += 1
            secs = '/'.join('%.0f' % m['seconds'] for _, m in sorted(metas.items()))
            L.append('| %s | %d | %s | %s | %s | **%s** | %d (%d / %d / %d) | %d | %d | %s |' % (
                rj['ig'], r['files_a'], secs, r['label'].split(' ', 1)[1], r['meta_b']['perturbations'],
                r['real_differences'] if r.get('valid', True) else 'INVALID', c['env-leak'], lk['tz'], lk['path'], lk['tz+path'], c['order-only'], c['content'],
                ', '.join('%s %d' % kv for kv in sorted(causes.items(), key=lambda kv: -kv[1]))))
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    if sys.argv[1:2] == ['--table']:
        sys.stdout.write(table(sys.argv[2:]))
        sys.exit(0)
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    if sys.argv[1:2] == ['--aggregate']:
        sys.stdout.write(aggregate(sys.argv[2]))
        sys.exit(0)
    main()
