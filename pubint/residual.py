"""Re-check the files domdiff reports as different, ignoring per-build noise: GUIDs, build timestamps
(ISO date-times and the publisher's 'Generated' dates), and zip member timestamps (zips are compared by
member list and normalized member contents). Prints the files that still differ.
usage: residual.py <domdiff.json> <expected-tree> <actual-tree>"""
import json, re, sys, zipfile, os, tarfile, io
def nested_same(a, b):
    # a zip inside a zip (xlsx in excels.zip, packs in full-ig.zip)
    kind = 'x.zip' if a[:2] == b'PK' and b[:2] == b'PK' else 'x.tgz' if a[:2] == b'\x1f\x8b' and b[:2] == b'\x1f\x8b' else None
    if kind:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=kind[1:]) as fa, tempfile.NamedTemporaryFile(suffix=kind[1:]) as fb:
            fa.write(a); fa.flush(); fb.write(b); fb.flush()
            return same(fa.name, fb.name, kind) is None
    return False
res = json.load(open(sys.argv[1])); A, B = sys.argv[2], sys.argv[3]
UUID = re.compile(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
ISO = re.compile(rb'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?([+-]\d\d:\d\d|Z)?')
TS = re.compile(rb'(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}|\w{3}, \w{3} \d\d?, \d{4} \d\d:\d\d[+-]\d{4}|\d{14}|\d{8}T\d{6}|\w{3}, \d\d? \w{3},? \d{4} \d\d:\d\d:\d\d [+-]\d{4}|\w{3} \w{3} \d\d? \d\d:\d\d:\d\d \w+ \d{4}|\d\d?/\d\d?/\d\d,? \d\d?:\d\d ?[AP]M|\w+ \d\d?, \d{4},? \d\d?:\d\d:\d\d ?[AP]M( \w+)?)')
# the publisher picks the terminology.hl7.org version of a NamingSystem link nondeterministically (differs between
# two default-mode builds of US Core)
THO = re.compile(rb'terminology\.hl7\.org/\d+\.\d+\.\d+/')
def norm(b): return THO.sub(b'terminology.hl7.org/<V>/', TS.sub(b'<T>', ISO.sub(b'<T>', UUID.sub(b'<U>', b))))
TIMING = ('qa-time-report.json', 'qa-time-report.tsv', 'qa-tx.html')  # per-run timings / tx server headers
def normfile(rel, b):
    base = os.path.basename(rel)
    if base == 'fragment-usage-analysis.csv':  # name,count,time-ms,size: drop the time
        b = re.sub(rb'^([^,\n]*,[^,\n]*,)[^,\n]*,', rb'\1', b, flags=re.M)
    if base == 'qa.json':
        b = re.sub(rb'"maxMemory" : \d+', b'"maxMemory" : 0', b)
    return norm(b)
def tarmembers(p):
    with tarfile.open(p) as t:
        return {m.name: (t.extractfile(m).read() if m.isfile() else b'') for m in t.getmembers()}
def same(pa, pb, rel=''):
    if os.path.basename(rel) in TIMING: return None
    if rel.endswith('.tgz'):
        ma, mb = tarmembers(pa), tarmembers(pb)
        if sorted(ma) != sorted(mb): return 'members differ'
        for n in ma:
            if normfile(n, ma[n]) != normfile(n, mb[n]): return 'member ' + n
        return None
    if zipfile.is_zipfile(pa) and zipfile.is_zipfile(pb):
        with zipfile.ZipFile(pa) as za, zipfile.ZipFile(pb) as zb:
            if sorted(za.namelist()) != sorted(zb.namelist()): return 'members differ'
            for n in za.namelist():
                if zb.getinfo(n) and (normfile(n, za.read(n)) != normfile(n, zb.read(n))) and not nested_same(za.read(n), zb.read(n)): return 'member ' + n
        return None
    a, b = open(pa, 'rb').read(), open(pb, 'rb').read()
    return None if normfile(rel, a) == normfile(rel, b) else 'content'
left = {}
for rel in res['differ']:
    why = same(os.path.join(A, rel), os.path.join(B, rel), rel)
    if why: left[rel] = why
print(f"domdiff differ={len(res['differ'])} missing={len(res['missing'])} extra={len(res['extra'])}; after ignoring GUIDs/timestamps: {len(left)} differ")
for k in ('missing', 'extra'):
    for r in res[k][:20]: print(f'  {k}: {r}')
for rel, why in sorted(left.items())[:60]:
    print(f'  {rel}: {why}' + ('' if why == 'content' else '') + ('  [domdiff: ' + res['differ'][rel]['what'][:200] + ']'))
