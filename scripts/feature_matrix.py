"""Feature x IG matrix + greedy coverage set from data/feature-scan.jsonl (feature_scan.py output),
repo-side file checks, data/scan.json page commands, and golden timing.

usage: python3 -I scripts/feature_matrix.py <root>   -> data/feature-matrix.json, markdown on stdout
"""
import json, os, re, sys, collections

ROOT = sys.argv[1]
SIX = ['HL7__US-Core', 'IHE__ITI.MHD', 'HL7__genomics-reporting', 'hl7au__au-fhir-core',
       'HL7__fhir-udap-security-ig', 'hl7-be__terminology']

scan = {}
for line in open(os.path.join(ROOT, 'data/feature-scan.jsonl')):
    d = json.loads(line)
    scan[d['ig']] = d
liq = json.load(open(os.path.join(ROOT, 'data/scan.json')))['igs']


def page_tags(ig):
    c = collections.Counter()
    for cat, m in (liq.get(ig) or {}).get('cats', {}).items():
        for k, v in m.items():
            if k.startswith('tag:'):
                c[k[4:]] += v
    return c


def timing(ig):
    try:
        for l in open(os.path.join(ROOT, 'work/golden', ig, 'timing.txt')):
            if l.startswith('full_build_seconds='):
                return int(l.split('=')[1])
    except Exception:
        pass
    return None


SKIP_DIRS = {'node_modules', 'input-cache', 'temp', 'output', 'template', '.git', 'qa', 'txCache'}


def repo_files(ig):
    c = collections.Counter()
    base = os.path.join(ROOT, 'repos', ig)
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        rel = os.path.relpath(dp, base)
        if rel.count(os.sep) > 5:
            dns[:] = []
        for fn in fns:
            ext = os.path.splitext(fn)[1].lower()
            if ext in ('.map', '.fml', '.adl', '.cql', '.liquid', '.po', '.xliff', '.xlf', '.plantuml', '.puml'):
                c[ext] += 1
            if fn == 'sushi-config.yaml':
                c['sushi'] += 1
        if os.path.basename(dp) == 'diagrams' and fns:
            c['diagrams-dir'] += 1
        if os.path.basename(dp) == 'translations' and fns:
            c['translations-dir'] += 1
    return c


def g(d, *ks, default=0):
    for k in ks:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
        if d is None:
            return default
    return d


# feature id -> (label, renderer reference, predicate(ctx) -> int weight>0 if used)
def T(ctx, t):
    return ctx['types'].get(t, 0)


def TR(ctx, k):
    return ctx['traits'].get(k, 0)


def P(ctx, k):
    return k in ctx['params']


FEATURES = [
    ('questionnaire', 'Questionnaire views (tree/form/links/logic/dict)', lambda c: T(c, 'Questionnaire')),
    ('q-expansion', 'Questionnaire answerValueSet / item.definition lookups', lambda c: TR(c, 'q:answerValueSet') + TR(c, 'q:item-definition')),
    ('qresponse', 'QuestionnaireResponse tree/form', lambda c: T(c, 'QuestionnaireResponse')),
    ('structuremap', 'StructureMap / FML', lambda c: T(c, 'StructureMap') + c['repo'].get('.map', 0) + c['repo'].get('.fml', 0)),
    ('logical', 'Logical models (kind=logical)', lambda c: TR(c, 'sd:logical')),
    ('uml', 'UML / class diagrams (generate-uml, {% class-diagram %}, input/diagrams)',
     lambda c: c['frag'].get('uml', 0) + (5 if P(c, 'generate-uml') else 0) + c['tags'].get('class-diagram', 0) + c['tags'].get('uml', 0)),
    ('obligations', 'Obligations on profiles', lambda c: TR(c, 'any:obligation')),
    ('obligation-actors', 'Per-actor obligation fragments', lambda c: c['frag'].get('obligations-actor', 0)),
    ('actordefinition', 'ActorDefinition', lambda c: T(c, 'ActorDefinition')),
    ('requirements', 'Requirements', lambda c: T(c, 'Requirements')),
    ('adl', 'ADL archetypes', lambda c: (c['sizes'].get('-adl') or [0, 0])[1] + c['repo'].get('.adl', 0)),
    ('testplan', 'TestPlan', lambda c: T(c, 'TestPlan')),
    ('testscript', 'TestScript', lambda c: T(c, 'TestScript')),
    ('capstmt', 'CapabilityStatement', lambda c: T(c, 'CapabilityStatement')),
    ('capstmt-imports', 'CapabilityStatement imports/instantiates', lambda c: TR(c, 'cps:imports')),
    ('capstmt-messaging', 'CapabilityStatement messaging/document', lambda c: TR(c, 'cps:messaging') + TR(c, 'cps:document')),
    ('opdef', 'OperationDefinition', lambda c: T(c, 'OperationDefinition')),
    ('searchparam', 'SearchParameter', lambda c: T(c, 'SearchParameter')),
    ('sp-composite', 'Composite SearchParameter', lambda c: TR(c, 'sp:composite')),
    ('conceptmap', 'ConceptMap', lambda c: T(c, 'ConceptMap')),
    ('cm-dependson', 'ConceptMap dependsOn/product/unmapped', lambda c: TR(c, 'cm:dependsOn-product') + TR(c, 'cm:unmapped')),
    ('namingsystem', 'NamingSystem', lambda c: T(c, 'NamingSystem')),
    ('examplescenario', 'ExampleScenario', lambda c: T(c, 'ExampleScenario')),
    ('bundle-document', 'Document Bundles', lambda c: TR(c, 'bundle:document')),
    ('bundle-message', 'Message Bundles', lambda c: TR(c, 'bundle:message')),
    ('bundle-history', 'History Bundles / Provenance history', lambda c: TR(c, 'bundle:history') + g(c, 'res_history')),
    ('bundle-nested', 'Bundles nested in Bundles', lambda c: TR(c, 'bundle:nested-bundle')),
    ('library-cql', 'Library with CQL (CqlSubSystem)', lambda c: TR(c, 'library:content:text/cql') + c['repo'].get('.cql', 0)),
    ('library', 'Library (base-template Library.liquid)', lambda c: T(c, 'Library')),
    ('measure', 'Measure (Measure.liquid)', lambda c: T(c, 'Measure')),
    ('plandef', 'PlanDefinition / ActivityDefinition (liquid)', lambda c: T(c, 'PlanDefinition') + T(c, 'ActivityDefinition')),
    ('own-liquid', 'IG-authored Liquid narrative templates', lambda c: c['repo'].get('.liquid', 0)),
    ('newml', 'New multi-language mode, single language (lang-suffixed fragments, redirect wrappers)',
     lambda c: 1 if len(c['langs'] or []) == 1 else 0),
    ('multilang', 'Two or more languages (PublisherTranslator, per-language rendering passes)',
     lambda c: len(c['langs']) if len(c['langs'] or []) >= 2 else 0),
    ('translation-files', 'Translation source files (.po/.xliff) in the repo',
     lambda c: c['repo'].get('.po', 0) + c['repo'].get('.xliff', 0) + c['repo'].get('.xlf', 0)),
    ('translation-ext', 'Translation extensions in content', lambda c: TR(c, 'any:translation')),
    ('xver-ext', 'Cross-version extensions used in resources (ProfileDrivenRenderer xver path)',
     lambda c: TR(c, 'any:xver-ext2') + TR(c, 'any:xver-ext3') + TR(c, 'any:xver-ext4')),
    ('prev-version', 'Previous-version comparison', lambda c: sum(1 for d in c['top_dirs'] if d.startswith('comparison-'))),
    ('ipa-ips', 'IPA / IPS comparison', lambda c: sum(1 for d in c['top_dirs'] if d.startswith(('ipa-comparison', 'ips-comparison')))),
    ('sd-changes', 'Profile change summary (sd-changes non-empty)', lambda c: g(c['sizes'], '-sd-changes')[1] if c['sizes'].get('-sd-changes') else 0),
    ('other-versions', '"Other versions" (R4/R4B/R5 inclusion) fragment non-empty',
     lambda c: (c['sizes'].get('-other-versions') or [0, 0])[1]),
    ('multi-version-params', 'r4/r4b/r5 inclusion/exclusion or x-version params',
     lambda c: sum(1 for k in c['params'] if re.match(r'r4b?-(in|ex)clusion|r5-inclusion|x-version|conversion-version', k))),
    ('diff-mappings', 'Profile mappings in differential (maps/maps-classic)', lambda c: TR(c, 'sd:diff-mapping')),
    ('imposes', 'imposeProfile / compliesWithProfile', lambda c: TR(c, 'any:impose-profile') + TR(c, 'any:complies-with') + c['frag'].get('imposes', 0)),
    ('additional-binding', 'Additional bindings', lambda c: TR(c, 'any:additional-binding') + TR(c, 'any:additional-binding-ext')),
    ('custom-resource', 'Custom resources (kind=resource specialization)', lambda c: TR(c, 'sd:custom-resource') + (5 if P(c, 'custom-resource') else 0)),
    ('datatype-profile', 'Datatype profiles', lambda c: TR(c, 'sd:datatype-profile')),
    ('ext-on-ext', 'Extensions on extensions (context type=extension)', lambda c: TR(c, 'sd:ext-on-ext')),
    ('contentref', 'contentReference in differential', lambda c: TR(c, 'sd:contentReference')),
    ('cs-supplement', 'CodeSystem supplements', lambda c: TR(c, 'cs:supplement')),
    ('cs-fragment', 'CodeSystem content fragment/not-present', lambda c: TR(c, 'cs:content=fragment') + TR(c, 'cs:content=not-present')),
    ('vs-filter', 'ValueSet with filters', lambda c: TR(c, 'vs:filter')),
    ('vs-expansion', 'ValueSet with stored expansion', lambda c: TR(c, 'vs:has-expansion')),
    ('subscription', 'SubscriptionTopic', lambda c: T(c, 'SubscriptionTopic')),
    ('graphdef', 'GraphDefinition', lambda c: T(c, 'GraphDefinition')),
    ('messagedef', 'MessageDefinition', lambda c: T(c, 'MessageDefinition')),
    ('compartment', 'CompartmentDefinition', lambda c: T(c, 'CompartmentDefinition')),
    ('evidence', 'Evidence / EvidenceVariable / Citation', lambda c: T(c, 'Evidence') + T(c, 'EvidenceVariable') + T(c, 'Citation')),
    ('group', 'Group', lambda c: T(c, 'Group')),
    ('consent', 'Consent', lambda c: T(c, 'Consent')),
    ('provenance', 'Provenance', lambda c: T(c, 'Provenance')),
    ('binary', 'Binary', lambda c: T(c, 'Binary')),
    ('parameters', 'Parameters / OperationOutcome', lambda c: T(c, 'Parameters') + T(c, 'OperationOutcome')),
    ('viewdef', 'ViewDefinition / SQL ({% sql %}, DBBuilder)', lambda c: T(c, 'ViewDefinition') + c['frag'].get('sql', 0) + c['tags'].get('sql', 0) + (5 if P(c, 'viewDefinition') else 0)),
    ('dataset', 'Datasets / test-data-factories', lambda c: c['tags'].get('dataset', 0) + (5 if P(c, 'test-data-factories') or P(c, 'dataset') else 0)),
    ('page-fragment', '{% fragment %} / {% json %} / {% multi-map %} page commands', lambda c: c['tags'].get('fragment', 0) + c['tags'].get('json', 0) + c['tags'].get('multi-map', 0)),
    ('rendering-ext', 'rendering-xhtml/markdown/style extensions', lambda c: TR(c, 'any:rendering-xhtml') + TR(c, 'any:rendering-markdown') + TR(c, 'any:rendering-style')),
    ('narrativeless', 'Examples without narrative (ProfileDrivenRenderer)', lambda c: TR(c, 'any:no-narrative')),
    ('contained', 'Contained resources', lambda c: TR(c, 'any:contained')),
    ('r5', 'FHIR R5 / R6 IG', lambda c: 1 if any(str(v).startswith(('5.', '6.')) for v in c['fhirVersion']) else 0),
    ('r4b', 'FHIR R4B IG', lambda c: 1 if any(str(v).startswith('4.3') for v in c['fhirVersion']) else 0),
    ('stu3', 'FHIR STU3 IG', lambda c: 1 if any(str(v).startswith('3.') for v in c['fhirVersion']) else 0),
    ('pin-canonicals', 'pin-canonicals / pin-manifest', lambda c: 1 if P(c, 'pin-canonicals') or P(c, 'pin-manifest') else 0),
    ('special-url', 'special-url / instance-hyperlinks', lambda c: 1 if P(c, 'special-url') or P(c, 'instance-hyperlinks') else 0),
    ('profile-test-cases', 'profile-test-cases', lambda c: 1 if P(c, 'profile-test-cases') else 0),
    ('related-ig', 'related-ig', lambda c: 1 if P(c, 'related-ig') else 0),
]


def ctx_for(ig):
    d = scan[ig]
    info = d.get('ig_info') or {}
    fv = info.get('fhirVersion') or []
    if isinstance(fv, str):
        fv = [fv]
    return {
        'types': d.get('types', {}), 'traits': d.get('traits', {}), 'frag': d.get('frag', {}),
        'sizes': d.get('sizes', {}), 'top_dirs': d.get('top_dirs', []), 'langs': d.get('langs') or [],
        'params': info.get('params') or {}, 'fhirVersion': fv, 'res_history': d.get('res_history', 0),
        'tags': page_tags(ig), 'repo': repo_files(ig),
    }


matrix = {}
times = {}
for ig in sorted(scan):
    c = ctx_for(ig)
    row = {}
    for fid, label, pred in FEATURES:
        try:
            v = pred(c)
        except Exception:
            v = 0
        if v:
            row[fid] = v
    matrix[ig] = row
    times[ig] = timing(ig)

json.dump({'matrix': matrix, 'times': times}, open(os.path.join(ROOT, 'data/feature-matrix.json'), 'w'), indent=1)

six_cov = set()
for ig in SIX:
    six_cov |= set(matrix.get(ig, {}))

print('| Feature | IGs | covered by the six | example IGs (fewest-build-time first) |')
print('|---|---:|:-:|---|')
for fid, label, pred in FEATURES:
    igs = [ig for ig in matrix if fid in matrix[ig]]
    igs.sort(key=lambda i: (times[i] or 99999))
    ex = ', '.join(f'{i} ({times[i]}s)' for i in igs[:4])
    print(f'| `{fid}` {label} | {len(igs)} | {"yes" if fid in six_cov else ""} | {ex} |')
