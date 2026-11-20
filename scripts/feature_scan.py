"""Feature survey over golden jekyll-in trees (read-only).

usage (inside a container with /srv/ig mounted read-only):
  python3 -I feature_scan.py /srv/ig/golden <ig> [<ig> ...]   -> one JSON object per line on stdout

Per IG: resource types (from the root *.json the publisher wrote), selected per-type content traits,
ImplementationGuide parameters / fhirVersion, _includes fragment-name families, sizes of a few
fragments that are always emitted but only sometimes non-empty, top-level directories, languages.
Only parses JSON and lists/stat()s files; never executes IG content.
"""
import collections, json, os, re, sys

ROOT = sys.argv[1]
NAMES = sys.argv[2:]

RES_JSON = re.compile(r'^([A-Z][A-Za-z]+)-(.+)\.json$')

# fragment-name families in _includes (presence counts)
FRAG_FAMILIES = {
    'uml': re.compile(r'-uml(-all)?(-[a-z]{2}(-[A-Z]{2})?)?\.xhtml$'),
    'obligations-actor': re.compile(r'-obligations-actor-'),
    'cross-version-analysis': re.compile(r'^cross-version-analysis'),
    'testplan-table': re.compile(r'^StructureDefinition-testplan-table-'),
    'testscript-table': re.compile(r'^StructureDefinition-testscript-table-'),
    'imposes': re.compile(r'^StructureDefinition-imposes-'),
    'maps-classic': re.compile(r'-maps-classic'),
    'sql': re.compile(r'^sql-\d+-fragment'),
    'idempotence': re.compile(r'^OperationDefinition-.*-idempotence'),
    'process-diagram': re.compile(r'^ExampleScenario-.*-process-diagram'),
    'vs-cld': re.compile(r'^ValueSet-.*-cld'),
    'cs-nsinfo': re.compile(r'^CodeSystem-.*-nsinfo'),
    'q-form': re.compile(r'^Questionnaire-.*-form'),
    'qr-tree': re.compile(r'^QuestionnaireResponse-.*-tree'),
    'sm-script': re.compile(r'^StructureMap-.*-script'),
    'cm-sheet': re.compile(r'^ConceptMap-.*-sheet'),
    'ns-xref': re.compile(r'^NamingSystem-.*-xref'),
    'obligation-summary': re.compile(r'^obligation-summary'),
    'summary-observations': re.compile(r'^summary-observations'),
    'maps-': re.compile(r'^maps-'),
    'version-': re.compile(r'^version-'),
    'deprecated-list': re.compile(r'^deprecated-list'),
    'new-extensions': re.compile(r'^new-extensions'),
    'related-igs': re.compile(r'^related-igs'),
    'expansion-params': re.compile(r'^expansion-params'),
    'svg': re.compile(r'\.svg$'),
    'lang-suffixed': re.compile(r'-html-[a-z]{2}(-[A-Z]{2})?\.xhtml$'),
}
# always-emitted SD fragments whose size tells whether the feature is used
SIZE_SUFFIXES = ['-other-versions', '-sd-changes', '-adl', '-sd-xref',
                 '-sd-use-context', '-ctxts', '-experimental-warning', '-search-params', '-uml']


def walk_strings(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == 'url' and isinstance(v, str):
                acc.add(v)
            walk_strings(v, acc)
    elif isinstance(o, list):
        for v in o:
            walk_strings(v, acc)


EXT_MARKERS = {
    'obligation': 'StructureDefinition/obligation',
    'additional-binding': 'additionalBinding',
    'additional-binding-ext': 'StructureDefinition/additional-binding',
    'impose-profile': 'structuredefinition-imposeProfile',
    'complies-with': 'structuredefinition-compliesWithProfile',
    'translation': 'StructureDefinition/translation',
    'type-must-support': 'elementdefinition-type-must-support',
    'sdc': 'sdc-questionnaire',
    'q-ext': 'StructureDefinition/questionnaire-',
    'xver-ext': '/xver/',
    'xver-ext2': 'hl7.org/fhir/5.0/StructureDefinition/extension-',
    'xver-ext3': 'hl7.org/fhir/4.0/StructureDefinition/extension-',
    'xver-ext4': 'hl7.org/fhir/3.0/StructureDefinition/extension-',
    'cqf': 'cqf-',
    'rendering-xhtml': 'rendering-xhtml',
    'rendering-markdown': 'rendering-markdown',
    'rendering-style': 'rendering-style',
    'capabilitystatement-expectation': 'capabilitystatement-expectation',
    'narrative-link': 'narrativeLink',
    'standards-status': 'structuredefinition-standards-status',
    'actor-ref': 'ActorDefinition/',
    'uml-ext': 'uml',
}


def scan(ig):
    base = os.path.join(ROOT, ig, 'jekyll-in')
    out = {'ig': ig}
    if not os.path.isdir(base):
        out['missing'] = True
        return out
    types = collections.Counter()
    traits = collections.Counter()
    ig_info = {}
    top_dirs = []
    for e in os.scandir(base):
        if e.is_dir():
            if not e.name.startswith('_') and e.name != 'assets':
                top_dirs.append(e.name)
            continue
        m = RES_JSON.match(e.name)
        if not m:
            continue
        try:
            if e.stat().st_size > 30_000_000:
                traits['skipped-large'] += 1
                continue
            with open(e.path, 'rb') as fh:
                raw = fh.read()
            txt = raw.decode('utf-8', 'replace')
            r = json.loads(txt)
        except Exception:
            traits['unparseable'] += 1
            continue
        if not isinstance(r, dict):
            continue
        rt = r.get('resourceType') or m.group(1)
        types[rt] += 1
        for k, s in EXT_MARKERS.items():
            if s in txt:
                traits[rt + ':' + k] += 1
                traits['any:' + k] += 1
        if r.get('contained'):
            traits['any:contained'] += 1
            for c in r['contained']:
                if isinstance(c, dict):
                    traits['contained:' + str(c.get('resourceType'))] += 1
        if 'language' in r:
            traits['any:language-attr'] += 1
        t = r.get('text')
        if not t:
            traits['any:no-narrative'] += 1
        elif isinstance(t, dict):
            traits['narrative-status:' + str(t.get('status'))] += 1
        if rt == 'StructureDefinition':
            kind, der = r.get('kind'), r.get('derivation')
            traits['sd:kind=' + str(kind)] += 1
            traits['sd:derivation=' + str(der)] += 1
            if kind == 'logical':
                traits['sd:logical'] += 1
                if der == 'specialization':
                    traits['sd:logical-specialization'] += 1
            if r.get('type') == 'Extension':
                traits['sd:extension'] += 1
                ctx = r.get('context') or []
                if any(isinstance(c, dict) and c.get('type') == 'extension' for c in ctx):
                    traits['sd:ext-on-ext'] += 1
            if kind in ('complex-type', 'primitive-type') and der == 'constraint':
                traits['sd:datatype-profile'] += 1
            if kind == 'resource' and der == 'specialization':
                traits['sd:custom-resource'] += 1
            if r.get('abstract'):
                traits['sd:abstract'] += 1
            diff = (r.get('differential') or {}).get('element') or []
            if any(isinstance(el, dict) and el.get('mapping') for el in diff):
                traits['sd:diff-mapping'] += 1
            if any(isinstance(el, dict) and el.get('slicing') for el in diff):
                traits['sd:slicing'] += 1
            if any(isinstance(el, dict) and el.get('sliceName') and '.extension' not in str(el.get('path', ''))
                   for el in diff):
                traits['sd:non-ext-slices'] += 1
            if any(isinstance(el, dict) and any(isinstance(ty, dict) and ty.get('profile') and len(ty.get('profile')) > 1
                                                for ty in el.get('type') or []) for el in diff):
                traits['sd:multi-type-profile'] += 1
            if any(isinstance(el, dict) and any(k.startswith('pattern') for k in el) for el in diff):
                traits['sd:pattern'] += 1
            if any(isinstance(el, dict) and el.get('contentReference') for el in diff):
                traits['sd:contentReference'] += 1
            if not r.get('snapshot'):
                traits['sd:no-snapshot-in-output'] += 1
            if r.get('mapping'):
                traits['sd:has-mapping-decl'] += 1
        elif rt == 'Bundle':
            traits['bundle:' + str(r.get('type'))] += 1
            entries = r.get('entry') or []
            if any(isinstance(en, dict) and isinstance(en.get('resource'), dict) and en['resource'].get('resourceType') == 'Bundle'
                   for en in entries):
                traits['bundle:nested-bundle'] += 1
            if any(isinstance(en, dict) and isinstance(en.get('resource'), dict) and en['resource'].get('resourceType') == 'Provenance'
                   for en in entries):
                traits['bundle:with-provenance'] += 1
        elif rt == 'Library':
            for c in r.get('content') or []:
                if isinstance(c, dict):
                    traits['library:content:' + str(c.get('contentType'))] += 1
        elif rt == 'Questionnaire':
            if '"enableWhen"' in txt:
                traits['q:enableWhen'] += 1
            if '"answerValueSet"' in txt:
                traits['q:answerValueSet'] += 1
            if '"definition"' in txt:
                traits['q:item-definition'] += 1
            if 'variable' in txt or 'calculatedExpression' in txt:
                traits['q:expressions'] += 1
        elif rt == 'CodeSystem':
            traits['cs:content=' + str(r.get('content'))] += 1
            if r.get('supplements'):
                traits['cs:supplement'] += 1
            if r.get('property'):
                traits['cs:properties'] += 1
            cs_concepts = r.get('concept') or []
            if any(isinstance(c, dict) and c.get('concept') for c in cs_concepts):
                traits['cs:hierarchy'] += 1
            if '"designation"' in txt:
                traits['cs:designations'] += 1
        elif rt == 'ValueSet':
            comp = r.get('compose') or {}
            if r.get('expansion'):
                traits['vs:has-expansion'] += 1
            inc = comp.get('include') or []
            if any(isinstance(i, dict) and i.get('filter') for i in inc):
                traits['vs:filter'] += 1
            if any(isinstance(i, dict) and i.get('valueSet') for i in inc):
                traits['vs:import'] += 1
            if comp.get('exclude'):
                traits['vs:exclude'] += 1
        elif rt == 'ConceptMap':
            if '"dependsOn"' in txt or '"product"' in txt:
                traits['cm:dependsOn-product'] += 1
            if '"unmapped"' in txt:
                traits['cm:unmapped'] += 1
        elif rt == 'CapabilityStatement':
            if '"searchParam"' in txt:
                traits['cps:searchParam'] += 1
            if '"imports"' in txt or '"instantiates"' in txt:
                traits['cps:imports'] += 1
            if '"messaging"' in txt:
                traits['cps:messaging'] += 1
            if '"document"' in txt:
                traits['cps:document'] += 1
        elif rt == 'SearchParameter':
            if r.get('component'):
                traits['sp:composite'] += 1
            if r.get('chain') or '"chain"' in txt:
                traits['sp:chain'] += 1
        elif rt == 'ImplementationGuide' and not ig_info:
            ig_info['fhirVersion'] = r.get('fhirVersion')
            ig_info['dependsOn'] = [d.get('packageId') for d in r.get('dependsOn') or [] if isinstance(d, dict)]
            params = collections.defaultdict(list)
            for p in (r.get('definition') or {}).get('parameter') or []:
                if not isinstance(p, dict):
                    continue
                code = p.get('code')
                if isinstance(code, dict):
                    code = code.get('code')
                params[str(code)].append(str(p.get('value'))[:120])
            # extension-based parameters (R4 IGs)
            for ex in (r.get('definition') or {}).get('extension') or []:
                if isinstance(ex, dict) and 'ig-parameter' in str(ex.get('url')):
                    code = val = None
                    for sub in ex.get('extension') or []:
                        if sub.get('url') == 'code':
                            vc = sub.get('valueCode') or (sub.get('valueCoding') or {}).get('code')
                            code = vc
                        elif sub.get('url') == 'value':
                            val = sub.get('valueString')
                    if code:
                        params[str(code)].append(str(val)[:120])
            ig_info['params'] = params
            ig_info['pages'] = txt.count('"nameUrl"') + txt.count('"name" : "') // 50  # rough
            ig_info['global'] = len(r.get('global') or [])
            ig_info['language'] = r.get('language')
    out['types'] = dict(types)
    out['traits'] = dict(traits)
    out['ig_info'] = ig_info
    out['top_dirs'] = sorted(top_dirs)

    inc = os.path.join(base, '_includes')
    fam = collections.Counter()
    sizes = {s: [0, 0, 0] for s in SIZE_SUFFIXES}  # count, >300B, >3000B
    if os.path.isdir(inc):
        for e in os.scandir(inc):
            n = e.name
            for k, rx in FRAG_FAMILIES.items():
                if rx.search(n):
                    fam[k] += 1
            if n.startswith('StructureDefinition-') and n.endswith('.xhtml'):
                stem = n[:-6]
                for s in SIZE_SUFFIXES:
                    if stem.endswith(s):
                        try:
                            sz = e.stat().st_size
                        except OSError:
                            sz = 0
                        sizes[s][0] += 1
                        if sz > 300:
                            sizes[s][1] += 1
                        if sz > 3000:
                            sizes[s][2] += 1
                        break
            if n.startswith('cross-version-analysis'):
                try:
                    out['cva_size'] = max(out.get('cva_size', 0), e.stat().st_size)
                except OSError:
                    pass
    out['frag'] = dict(fam)
    out['sizes'] = sizes
    try:
        with open(os.path.join(base, '_data', 'languages.json')) as fh:
            lj = json.load(fh)
        out['langs'] = lj.get('langs')
        out['hasTranslations'] = lj.get('hasTranslations')
    except Exception:
        pass
    try:
        with open(os.path.join(base, '_data', 'resources.json')) as fh:
            rj = json.load(fh)
        out['res_testplan'] = sum(1 for v in rj.values() if isinstance(v, dict) and v.get('testplan'))
        out['res_testscript'] = sum(1 for v in rj.values() if isinstance(v, dict) and v.get('testscript'))
        out['res_history'] = sum(1 for v in rj.values() if isinstance(v, dict) and v.get('history'))
    except Exception:
        pass
    return out


for ig in NAMES:
    try:
        print(json.dumps(scan(ig)), flush=True)
    except Exception as ex:
        print(json.dumps({'ig': ig, 'error': repr(ex)}), flush=True)
