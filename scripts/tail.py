"""List IGs using features outside a proposed 'core shim' set. usage: python3 -I tail.py <root>"""
import collections, json, os, sys
d = json.load(open(os.path.join(sys.argv[1], 'data', 'scan.json')))
PAGE = {'input/pagecontent','input/intro-notes','input/includes','input/pages','fsh-generated/includes','input/images'}
EXOTIC = ['tag:highlight','tag:include_relative','var:site.static_files','var:site.pages','filter:where_exp','filter:group_by',
          'filter:push','filter:jsonify','filter:date','kd:math','kd:definition-list','kd:footnote','kd:table-footer',
          'kd:extension-options','kd:abbreviation','front-matter','tag:case','for:range','var:site.time','var:site.baseurl']
users = collections.defaultdict(list); clean = 0
for name, ig in d['igs'].items():
    c = collections.Counter()
    for cat, cnt in ig['cats'].items():
        if cat in PAGE: c.update(cnt)
    hit = [k for k in EXOTIC if c.get(k)]
    for k in hit: users[k].append(name)
    clean += not hit
print('IGs using none of the long-tail features:', clean, '/', len(d['igs']))
for k in EXOTIC:
    print(f'{k:28} {len(users[k]):3}  {", ".join(users[k][:6])}{" ..." if len(users[k])>6 else ""}')
