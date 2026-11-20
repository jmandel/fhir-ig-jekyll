import json, re, sys
for l in open(sys.argv[1]):
    r = json.loads(l)
    if r['kind'] == 'template':
        d = re.sub(r'.*? in "', '', r['detail'])[:70]
        print(f"{r['unit'][:42]:42} {r['file'][:48]:48} {d}")
