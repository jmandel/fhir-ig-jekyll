"""Scan captured Jekyll input dirs (work/golden/*/jekyll-in), bucketed by Jekyll role.

usage: python3 -I scan_jekyll_in.py <golden-dir> [<golden-dir> ...]
"""
import collections, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scan  # noqa: E402


def role(rel):
    top = rel.split(os.sep)[0]
    if top in ('_includes', '_layouts', '_data'):
        return top
    return 'pages' if rel.endswith(('.html', '.md', '.xml', '.json', '.ttl')) else 'other'


for gd in sys.argv[1:]:
    base = os.path.join(gd, 'jekyll-in')
    buckets = collections.defaultdict(collections.Counter)
    nfiles = collections.Counter(); nbytes = collections.Counter()
    for dp, dn, fn in os.walk(base):
        for f in fn:
            p = os.path.join(dp, f); rel = os.path.relpath(p, base); r = role(rel)
            nfiles[r] += 1; nbytes[r] += os.path.getsize(p)
            if os.path.splitext(f)[1].lower() in scan.TEXT_EXT and r != '_data':
                scan.scan_file(p, buckets[r], set())
    print('##', os.path.basename(gd))
    for r in sorted(nfiles):
        print(f'  {r:10} files={nfiles[r]:6} MB={nbytes[r]/1e6:8.1f}')
    allc = collections.Counter()
    for c in buckets.values(): allc.update(c)
    for prefix in ('tag:', 'filter:', 'include:', 'op:', 'kd:', 'front-matter', 'fm-key:', 'files-with-liquid', 'placeholders'):
        items = sorted(((k, v) for k, v in allc.items() if k.startswith(prefix)), key=lambda kv: -kv[1])
        if items:
            print('  ' + prefix.rstrip(':'), ', '.join(f'{k.split(":",1)[-1]}={v}' for k, v in items[:40]))
    roots = collections.Counter()
    for k, v in allc.items():
        if k.startswith('var:'):
            roots['.'.join(k[4:].split('.')[:3]).split('[')[0]] += v
    print('  vars', ', '.join(f'{k}={v}' for k, v in roots.most_common(30)))
