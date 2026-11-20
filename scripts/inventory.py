"""Inventory a captured jekyll-in dir: which files Jekyll renders vs copies, and what drives them.

usage: python3 -I inventory.py <golden-dir> [...]
"""
import collections, os, re, sys

INC = re.compile(r'\{%-?\s*include\s+(\S+)')
for gd in sys.argv[1:]:
    base = os.path.join(gd, 'jekyll-in')
    rendered = collections.Counter(); static = collections.Counter(); fm_keys = collections.Counter()
    inc_targets = collections.Counter(); rendered_bytes = 0; static_bytes = 0
    liquid_pages_no_fm = 0
    for dp, dn, fn in os.walk(base):
        rel = os.path.relpath(dp, base)
        if rel.split(os.sep)[0] in ('_includes', '_data', '.jekyll-cache'):
            dn[:] = []; continue
        for f in fn:
            p = os.path.join(dp, f); ext = os.path.splitext(f)[1].lower() or '(none)'
            with open(p, 'rb') as fh:
                head = fh.read(4096)
            if head.startswith(b'\xef\xbb\xbf'):
                head = head[3:]
            if head.startswith(b'---\n') or head.startswith(b'---\r\n'):
                rendered[ext] += 1; rendered_bytes += os.path.getsize(p)
                text = open(p, encoding='utf-8', errors='replace').read()
                m = re.match(r'\A﻿?---\r?\n(.*?)\r?\n?---', text, re.S)
                if m:
                    for k in re.findall(r'^(\w[\w-]*):', m.group(1), re.M):
                        fm_keys[k] += 1
                for t in INC.findall(text):
                    t = re.sub(r'\{\{[^}]*\}\}', '{X}', t)
                    t = re.sub(r'^[A-Z][A-Za-z]+-[^.{]+', 'Type-ID', t)
                    inc_targets[t] += 1
            else:
                static[ext] += 1; static_bytes += os.path.getsize(p)
                if b'{{' in head or b'{%' in head:
                    liquid_pages_no_fm += 1
    print('##', os.path.basename(gd))
    print(f'  rendered (front matter): {sum(rendered.values())} files, {rendered_bytes/1e6:.1f} MB  {dict(rendered.most_common())}')
    print(f'  static copy:            {sum(static.values())} files, {static_bytes/1e6:.1f} MB  {dict(static.most_common(8))}')
    print(f'  static files containing liquid-looking text (copied verbatim): {liquid_pages_no_fm}')
    print(f'  front-matter keys: {dict(fm_keys)}')
    print('  include targets:', ', '.join(f'{k}={v}' for k, v in inc_targets.most_common(25)))
