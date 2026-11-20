"""Aggregate data/scan.json into prevalence tables.

usage: python3 -I aggregate.py <root> [prefix-filter]
"""
import collections, json, os, sys

ROOT = sys.argv[1]
PREFIX = sys.argv[2] if len(sys.argv) > 2 else ''
d = json.load(open(os.path.join(ROOT, 'data', 'scan.json')))
PAGE_CATS = {'input/pagecontent', 'input/intro-notes', 'input/includes', 'input/pages',
             'fsh-generated/includes', 'input/images', 'input/pagecontents'}

ig_use = collections.Counter(); ig_tot = collections.Counter()
res_use = collections.Counter()
for name, ig in d['igs'].items():
    page = collections.Counter(); res = collections.Counter()
    for cat, cnt in ig['cats'].items():
        (page if cat in PAGE_CATS else res).update(cnt)
    for k, v in page.items():
        ig_use[k] += 1; ig_tot[k] += v
    for k in res:
        res_use[k] += 1

tpl_use = collections.Counter(); tpl_tot = collections.Counter()
for name, t in d['templates'].items():
    for k, v in t['counts'].items():
        tpl_use[k] += 1; tpl_tot[k] += v

n_ig = len(d['igs']); n_tpl = len(d['templates'])
keys = sorted(set(ig_use) | set(tpl_use), key=lambda k: (-ig_use[k], -tpl_use[k], k))
print(f'{"feature":55} {"IGs/" + str(n_ig):>8} {"occ":>7} {"tpls/" + str(n_tpl):>9} {"occ":>7} {"res-IGs":>8}')
for k in keys:
    if not k.startswith(PREFIX):
        continue
    print(f'{k[:55]:55} {ig_use[k]:8} {ig_tot[k]:7} {tpl_use[k]:9} {tpl_tot[k]:7} {res_use[k]:8}')
