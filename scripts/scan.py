"""Scan IG repos + templates for the Jekyll/Liquid/kramdown surface they rely on.

usage: python3 -I scan.py <repo-root>
Writes data/scan.json (per-unit feature counters) for aggregate.py.
A "unit" is either an IG (its own authored input/ content) or a template dir.
"""
import collections, configparser, hashlib, json, os, re, sys

ROOT = sys.argv[1] if __name__ == '__main__' else None
TEXT_EXT = {'.md', '.html', '.htm', '.xml', '.xhtml', '.liquid', '.txt', '.json', '.yml', '.yaml'}

PLACEHOLDER = re.compile(r'\{\{\[[^\]]*\]\}\}')            # publisher pre-Jekyll substitution
LIQUID = re.compile(r'\{%-?\s*(.*?)\s*-?%\}|\{\{-?\s*(.*?)\s*-?\}\}', re.S)
RAW_BLOCK = re.compile(r'\{%-?\s*raw\s*-?%\}(.*?)\{%-?\s*endraw\s*-?%\}', re.S)
COMMENT_BLOCK = re.compile(r'\{%-?\s*comment\s*-?%\}(.*?)\{%-?\s*endcomment\s*-?%\}', re.S)
STR = re.compile(r'"[^"]*"|\'[^\']*\'')
IDENT_PATH = re.compile(r'[A-Za-z_][\w-]*(?:\.[\w-]+|\[[^\]]*\])*')
KEYWORDS = {'and', 'or', 'contains', 'true', 'false', 'nil', 'null', 'empty', 'blank', 'in',
            'reversed', 'limit', 'offset', 'with', 'for', 'as', 'cols'}


def split_pipes(s):
    out, cur, q = [], '', None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in '"\'':
            q = ch; cur += ch
        elif ch == '|':
            out.append(cur); cur = ''
        else:
            cur += ch
    out.append(cur)
    return out


def norm_path(p):
    # site.data.pages[localPage].titlelang[include.lang] -> site.data.pages[*].titlelang[*]
    p = re.sub(r'\[[^\]]*\]', '[*]', p)
    parts = p.split('.')
    if parts[0] == 'site' and len(parts) > 2 and parts[1] == 'data':
        return '.'.join(parts[:4])
    return '.'.join(parts[:3])


def var_paths(expr):
    expr = STR.sub('""', expr)
    for m in IDENT_PATH.finditer(expr):
        t = m.group(0)
        root = t.split('.')[0].split('[')[0]
        if root in KEYWORDS or root.isdigit():
            continue
        yield t


def scan_liquid(text, c, locals_):
    c['placeholders'] += len(PLACEHOLDER.findall(text))
    text = PLACEHOLDER.sub('X', text)
    for m in RAW_BLOCK.finditer(text):
        c['tag:raw'] += 1
    text = RAW_BLOCK.sub('', text)
    for m in COMMENT_BLOCK.finditer(text):
        c['tag:comment'] += 1
    text = COMMENT_BLOCK.sub('', text)
    for m in LIQUID.finditer(text):
        whole = m.group(0)
        if whole.startswith(('{%-', '{{-')) or whole.endswith(('-%}', '-}}')):
            c['syntax:whitespace-control'] += 1
        if m.group(1) is not None:
            body = m.group(1)
            name, _, args = body.partition(' ')
            name = name.strip()
            c['tag:' + name] += 1
            if name in ('if', 'elsif', 'unless'):
                a = STR.sub('""', args)
                for op in ('==', '!=', '<=', '>=', ' < ', ' > ', ' contains ', ' and ', ' or ', 'blank', 'empty', 'nil'):
                    if op in a:
                        c['op:' + op.strip()] += 1
                for v in var_paths(args):
                    c['var:' + norm_path(v)] += 1
            elif name == 'for':
                if re.search(r'\(\s*\S+\s*\.\.\s*\S+\s*\)', args):
                    c['for:range'] += 1
                for mod in ('limit:', 'offset:', 'reversed'):
                    if mod in args:
                        c['for:' + mod.rstrip(':')] += 1
                mm = re.match(r'\s*(\w+)\s+in\s+(.*)', args)
                if mm:
                    locals_.add(mm.group(1))
                    for v in var_paths(mm.group(2)):
                        c['var:' + norm_path(v)] += 1
            elif name in ('assign', 'capture'):
                lhs, _, rhs = args.partition('=')
                locals_.add(lhs.strip())
                if rhs:
                    parts = split_pipes(rhs)
                    for v in var_paths(parts[0]):
                        c['var:' + norm_path(v)] += 1
                    for f in parts[1:]:
                        c['filter:' + f.strip().split(':')[0].strip()] += 1
            elif name in ('include', 'include_relative'):
                target = args.strip().split()[0] if args.strip() else ''
                c['include:dynamic-name' if '{{' in target or not re.search(r'\.\w+$', target) and not target.startswith(('"', "'")) and '.' not in target else 'include:static-name'] += 1
                params = re.findall(r'(\w+)\s*=', args)
                if params:
                    c['include:with-params'] += 1
                if '{{' in args.split(None, 1)[-1] if ' ' in args.strip() else False:
                    c['include:interpolated-param'] += 1
            elif name in ('case', 'when'):
                pass
        else:
            body = m.group(2)
            parts = split_pipes(body)
            c['output'] += 1
            for v in var_paths(parts[0]):
                c['var:' + norm_path(v)] += 1
            for f in parts[1:]:
                f = f.strip()
                if f:
                    c['filter:' + f.split(':')[0].strip()] += 1


KD = {
    'kd:toc': re.compile(r'\{:toc\}'),
    'kd:ial-class-or-id': re.compile(r'\{:\s*[.#][^}]*\}'),
    'kd:ial-attr': re.compile(r'\{:\s*\w+=[^}]*\}'),
    'kd:ald': re.compile(r'^\{:\w+:[^}]*\}', re.M),
    'kd:extension-nomarkdown': re.compile(r'\{::nomarkdown'),
    'kd:extension-comment': re.compile(r'\{::comment'),
    'kd:extension-options': re.compile(r'\{::options'),
    'kd:header-id': re.compile(r'^#{1,6} .*\{#[\w-]+\}\s*$', re.M),
    'kd:markdown-attr': re.compile(r'markdown\s*=\s*["\']?(1|block|span)', re.I),
    'kd:footnote': re.compile(r'\[\^[^\]]+\]'),
    'kd:abbreviation': re.compile(r'^\*\[[^\]]+\]:', re.M),
    'kd:definition-list': re.compile(r'^[^\s:|#>*-][^\n]*\n:\s+\S', re.M),
    'kd:math': re.compile(r'\$\$'),
    'kd:table-footer': re.compile(r'^\|?\s*={3,}', re.M),
    'md:pipe-table': re.compile(r'^\s*\|?\s*:?-{3,}:?\s*\|', re.M),
    'md:fence-backtick': re.compile(r'^\s*```', re.M),
    'md:fence-tilde': re.compile(r'^\s*~~~', re.M),
    'md:ref-link-def': re.compile(r'^\s{0,3}\[[^\]^]+\]:\s*\S', re.M),
    'md:link-to-anchor': re.compile(r'\]\(#[^)]+\)'),
    'md:html-block': re.compile(r'^\s*<(div|table|p|ul|ol|blockquote|pre|section|details|figure|img|br|a)\b', re.M | re.I),
    'md:setext-heading': re.compile(r'^\S.*\n(=+|-{3,})\s*$', re.M),
    'md:hard-break-2sp': re.compile(r'\S  $', re.M),
}
FRONT_MATTER = re.compile(r'\A---\s*\n.*?\n---\s*\n', re.S)


def local_template_dir(repo):
    for dp, dn, fn in os.walk(repo):
        dn[:] = [d for d in dn if d not in ('.git', 'node_modules')]
        if dp.count(os.sep) - repo.count(os.sep) > 2:
            continue
        if 'ig.ini' in fn:
            cp = configparser.ConfigParser(strict=False)
            try:
                cp.read(os.path.join(dp, 'ig.ini'), encoding='utf-8-sig')
                t = cp.get('IG', 'template', fallback='').strip()
            except Exception:
                t = ''
            t = t.lstrip('#').split('#')[0].strip()
            cand = os.path.normpath(os.path.join(dp, t)) if t else None
            if cand and os.path.isdir(cand):
                return dp, t, cand
            return dp, t, None
    return None, None, None


def scan_file(path, c, files_seen):
    try:
        with open(path, 'rb') as fh:
            raw = fh.read()
    except OSError:
        return
    if len(raw) > 3_000_000:
        return
    h = hashlib.sha1(raw).hexdigest()
    files_seen.add(h)
    text = raw.decode('utf-8', 'replace')
    ext = os.path.splitext(path)[1].lower()
    if FRONT_MATTER.match(text):
        c['front-matter'] += 1
        fm = FRONT_MATTER.match(text).group(0)
        for k in re.findall(r'^(\w[\w-]*):', fm, re.M):
            c['fm-key:' + k] += 1
    if '{%' in text or '{{' in text:
        c['files-with-liquid'] += 1
    scan_liquid(text, c, set())
    if ext == '.md':
        c['md-files'] += 1
        body = RAW_BLOCK.sub('', text)
        for k, rx in KD.items():
            n = len(rx.findall(body))
            if n:
                c[k] += n
    elif ext in ('.html', '.xml', '.xhtml', '.htm'):
        n = len(KD['kd:markdown-attr'].findall(text))
        if n:
            c['kd:markdown-attr'] += n
        n = len(KD['kd:toc'].findall(text))
        if n:
            c['kd:toc'] += n


def walk(base, exclude=None, subdirs=None):
    roots = [os.path.join(base, s) for s in subdirs] if subdirs else [base]
    for r in roots:
        for dp, dn, fn in os.walk(r):
            dn[:] = [d for d in dn if d not in ('.git', 'node_modules', 'temp', 'output', 'template', 'fsh-generated') or (d == 'fsh-generated')]
            if exclude and os.path.abspath(dp).startswith(exclude):
                dn[:] = []
                continue
            for f in fn:
                if os.path.splitext(f)[1].lower() in TEXT_EXT:
                    yield os.path.join(dp, f)


def category(rel):
    parts = rel.split(os.sep)
    for i, p in enumerate(parts):
        if p == 'input' and i + 1 < len(parts):
            return 'input/' + parts[i + 1]
        if p == 'fsh-generated' and i + 1 < len(parts):
            return 'fsh-generated/' + parts[i + 1]
    return 'other'


def main():
    out = {'igs': {}, 'templates': {}}
    repos = os.path.join(ROOT, 'repos')
    for name in sorted(os.listdir(repos)):
        repo = os.path.join(repos, name)
        igdir, tname, tdir = local_template_dir(repo)
        if not igdir:
            continue
        per_cat = collections.defaultdict(collections.Counter)
        seen = set()
        for sub in ('input', 'fsh-generated'):
            base = os.path.join(igdir, sub)
            if not os.path.isdir(base):
                continue
            for p in walk(base, exclude=os.path.abspath(tdir) if tdir else None):
                cat = category(os.path.relpath(p, igdir))
                # resources/fsh/examples contain FHIR content, not pages: only track Liquid-looking text there
                scan_file(p, per_cat[cat], seen)
        out['igs'][name] = {'template': tname, 'local_template': bool(tdir),
                            'cats': {k: dict(v) for k, v in per_cat.items()}}
        if tdir:
            c, s = collections.Counter(), set()
            for p in walk(tdir):
                scan_file(p, c, s)
            out['templates']['local:' + name] = {'counts': dict(c), 'hashes': sorted(s)}
    tpls = os.path.join(ROOT, 'templates')
    for name in sorted(os.listdir(tpls)):
        c, s = collections.Counter(), set()
        for p in walk(os.path.join(tpls, name)):
            scan_file(p, c, s)
        out['templates']['pkg:' + name] = {'counts': dict(c), 'hashes': sorted(s)}
    with open(os.path.join(ROOT, 'data', 'scan.json'), 'w') as fh:
        json.dump(out, fh)
    print('igs', len(out['igs']), 'templates', len(out['templates']))


if __name__ == '__main__':
    main()
