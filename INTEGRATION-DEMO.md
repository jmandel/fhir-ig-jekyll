# In-process Jekyll and lazy fragments: prototype

This branch (`publisher-integration`, based on IG Publisher **Release v3.0.4**, core 7.0.6) runs the
publisher's Jekyll stage with **fhir-ig-jekyll** ("the shim", a Java port of Jekyll 4.4 + Liquid +
kramdown + Rouge) in the publisher's own JVM, and uses that to stop writing, or even generating,
`_includes` fragments that no page includes.

```
-jekyll-engine jekyll      (default) fork `jekyll build`, unchanged
-jekyll-engine shim        Shim.build(temp/pages -> output) in process; fragments still written to _includes
-jekyll-engine shim-mem    in process; fragments held in memory instead of _includes files, all generated
                           eagerly as always (same calls, same order)
-jekyll-engine shim-lazy   in process; fragments held in memory (FragmentRegistry), the expensive ones
                           generated only if a page includes them  -- NOT SAFE, see "Is deferral safe?"
```

## Why

The publisher writes every fragment it can produce for every resource into `temp/pages/_includes`;
templates and pages include a small subset. Across the corpus, 75-86% of `_includes` files are never
included by any page (UTG: 134k of 166k files, 1.25 of 2.06 GB never used). Every one of them costs
rendering time in the publisher, a file write, and Jekyll's scan of the folder. With Jekyll in process,
the publisher can find out which fragments are needed *while Jekyll runs*, so it doesn't have to guess.

## Design

### 1. In-process engine (`-jekyll-engine shim`)

`PublisherGenerator.runJekyll()` calls `runJekyllInProcess()` instead of the `DefaultExecutor`:

```java
Shim.Result r = Shim.build(tempDir, outputDir, new Shim.Options()
    .log(line -> { if (filter.passJekyllFilter(line)) log("Jekyll: " + line); }));
```

* Console lines are the ones Jekyll prints, filtered by the same `passJekyllFilter`; a build failure is a
  `ShimException` carrying Jekyll's message ("Liquid Exception: ..."), rethrown as the `IOException` the
  external process failure produces. Timing goes to the same `jekyll` TimeTracker bucket.
* Side benefit: no 256-byte `MyFilterHandler` line buffer, which overflows on long Jekyll lines
  (`ArrayIndexOutOfBoundsException` with the external process).
* The shim is a plain jar with no dependencies (`org.hl7.fhir:fhir-ig-jekyll`, JDK 17+), added to
  `org.hl7.fhir.publisher.core/pom.xml`. It renders pages on its own threads (default: CPU count).

Shim side (fhir-ig-jekyll `main`, commit 66ab24ba): `org.hl7.fhir.jekyll.Shim` (`build`, `Options`,
`Result`), `ShimException`, `IncludeProvider`. The `jekyll-shim` CLI is now a thin wrapper over the same
`Builder`, so CLI and in-process output are byte-identical (checked by the shim's differential suites and
goldens, and `ShimApiCheck`).

### 2. Lazy fragments (`-jekyll-engine shim-lazy`)

**Shim hook: `IncludeProvider`.** `String include(String name)` is consulted by `{% include %}` (and
`include_relative` into `_includes`) *before* the disk; `null` means "not mine, look on disk", so template
includes, hand-written includes and anything written as a file keep working. The shim asks for each name at
most once per build (it caches the parsed template), from its render threads.

**Publisher side: `FragmentRegistry implements IncludeProvider`** (new, `org.hl7.fhir.igtools.publisher`):

* `put(name, content)`: fragments generated as before, but held in memory instead of written. All
  `fragment()` call sites get this for free: the registry branch in `fragment()` does what
  `checkMakeFile` did except the write. Memory budget `-Dig.fragments.memoryMB` (default 1024 MB of
  content); beyond it, fragments are written to `_includes` as before.
* `putLazy(name, generator)`: a fragment whose content is computed only when Jekyll first includes it.
  `lazyFragment(name, () -> sdr.snapshot(...), ...)` replaces `fragment(name, sdr.snapshot(...), ...)` at
  the converted call sites; without a registry it is exactly the eager call.
* `alias(name, target)`: the unsuffixed copy of a default-language fragment (`X.xhtml` for `X-en.xhtml`)
  becomes a second name for the same entry (generated once), not a second file.
* Entries are dropped once delivered; whatever is left is released after Jekyll. One summary line is
  logged after the build (`Fragments in memory: ...`).

**What stays the same.** At call time a lazy fragment does what `fragment()` does besides producing
content: it is registered in `allOutputs` and the file's `outputTracker` (duplicate-name detection and the
temp-folder clean-up are unchanged). Liquid wrapping, `doReplacements`, `-track-fragments` markers and the
QA copy are applied to the generated content exactly as `fragment()` would.

**Threading.** The shim renders pages in parallel; generators touch publisher state that isn't thread-safe,
so `FragmentRegistry` runs them one at a time under one lock (the publisher's main thread is blocked in
`Shim.build` meanwhile, so nothing else touches that state). Content already in memory is served without
the lock.

**Static files written by generators.** Some renderers write images while rendering (`tbl_bck*.png` from
the table generator). With lazy generation that happens during Jekyll's render, after Jekyll has listed the
static files; `Options.rescanStaticsAfterRender(true)` makes the shim copy static files that appeared in
the source during rendering.

**Converted to lazy (this prototype).**

* `generateOutputsStructureDefinition`: 69 call sites whose content is a call on the
  `StructureDefinitionRenderer` (`sdr.*`) or `TemplateRenderer` (`tr.*`): summary/summary-all/
  summary-table, class-table, header, uses, ctxts, experimental-warning, eview(-all), diff/snapshot and
  by-key/by-mustsupport views (plain, -bindings, -obligations; each with -all), grid, pseudo-json, tx*,
  inv*, dict*, crumbs, maps, sd-xref, sd-use-context, search-params, template-xml/json/ttl, sd-changes,
  typename, span/spanall, example-/testplan-/testscript- list/table.
* `saveDirectResourceOutputs`: xml-html, json-html, ttl-html (each lambda composes with a fresh parser
  from the same element).

(`pubint/lazify.py` is the rewrite used for the first group.)

**The SD as the eager views saw it (`PreExpansionView`).** Deferred views run after everything else, so
they see any later change to publisher state. One showed up in US Core: rendering `-expansion` (PEBuilder)
calls `ElementDefinition.getTypeFirstRep()` on contentReference elements (e.g.
`Observation.component.referenceRange`), and that *getter adds an empty type*. Eagerly the snapshot/key views
(which run before the expansion) showed those rows with the Element icon, deferred ones with the Resource
icon (46 profile pages). `PreExpansionView` remembers, before an SD is rendered, which of its elements have no
type, and while a deferred pre-expansion view renders hides the empty types added since, then restores them.
(Located with the diagnostic `-Dig.fragments.drain=file|html`, which generates the deferred fragments right
after their file's html generation / at the end of the html phase.) The real fix belongs in core: getters
that don't mutate, or renderers that don't call them on shared resources.

**Kept eager on purpose** (they change state other code reads): `-expansion` (mutates the SD; see
`redoRenderingChanges`), `-obligations`/`-obligations-all` and the per-actor loop (populate
`getObligationActors()`), `-uml`/`-uml-all` (`r.setUmlGenerated`, diagram files), `-other-versions`
(registers outputs), `imposes-`, `-adl`, and everything outside these two methods (they still go to
memory, and are skipped as files, but are generated).

**Off automatically** (fragments are written as files, with a log line) with Rapido (it replays the
last build's `_includes`), `-generation-off` (copies temp/pages as is), and `tx-unload-early` (lazy
fragments can need terminology, which is unloaded before Jekyll).

**By-design differences in output** for `shim-lazy`:

* `fragment-usage-analysis.csv` (and its copy in `full-ig.zip`): counts/sizes only cover fragments that
  were generated; fragments no page included don't appear (or appear with lower counts).
* QA copies of fragments (`qa/<fragment>.html`, not part of output/) only exist for generated fragments.
* `tbl_bck*.png` images that only skipped fragments would have written are not produced (US Core: 9 files,
  referenced by no page of the output).
* `qa-txservers.html` (QA report of terminology-server routing): the order of entries follows the order of
  terminology calls, which changes when fragments are generated later.
* Times in `qa-time-report.*` and the per-fragment times differ, as they do between any two builds.

## How to run

```bash
# 1. the shim jar (fhir-ig-jekyll main)       -> shim/build/shim.jar
shim/build.sh
mvn install:install-file -Dfile=shim/build/shim.jar -DgroupId=org.hl7.fhir \
    -DartifactId=fhir-ig-jekyll -Dversion=0.1.0-SNAPSHOT -Dpackaging=jar
# 2. this branch
mvn -DskipTests -Dmaven.buildNumber.revisionOnScmFailure=pubint package -pl org.hl7.fhir.publisher.cli -am
# 3. a build
java -jar org.hl7.fhir.publisher.cli/target/org.hl7.fhir.publisher.cli-3.0.4.jar -ig . -jekyll-engine shim-lazy
```

`pubint/` has the scripts used for the measurements below (run-mode.sh needs `FHIR_IG_JEKYLL_ROOT`, the
fhir-ig-jekyll workspace; see its header for the other settings): `run-mode.sh` (one build of an IG in one
mode, in the reference Docker image, package cache as a throw-away overlay), `treemem.sh` (peak RSS of the
whole process tree, i.e. the JVM plus the forked Jekyll in default mode) and `residual.py` (re-checks
the files `domdiff.py` reports as different with per-build noise removed: GUIDs, build timestamps, zip/tgz
member timestamps, timing reports).

## Measurements

All runs: the reference Docker image (`ig-publisher-localdev-ca`, Java 21, Jekyll 4.4.1) on a Lima VM
(16 vCPU, 96 GB), `-Xmx8g`, `-no-network-check`, package cache = a frozen hard-link snapshot under a
throw-away overlay (the shared cache was being modified by other jobs, which changed which
terminology.hl7.org version links resolved to). Other jobs were running on the same VM, so times are noisy;
runs were made in pairs (two builds at a time) and each lazy/shim run is compared with the default run made
alongside it. The publisher's `previous-version` phase (comparison with published versions; network-bound)
varies most between runs, so the table also gives the time without it. RSS = peak of the summed RSS of the
container's process tree (the JVM, plus Jekyll in default mode), sampled every 0.2 s.

### US Core (HL7__US-Core, 10.0.0-ballot: 3036 pages, 16.5k output files)

| run (pair) | mode | whole build | without previous-version | jekyll stage | peak RSS (tree) | `_includes` on disk |
|---|---|---|---|---|---|---|
| B | default (`jekyll`) | 386.2 s | 278.1 s | 18.7 s | 10.1 GB | 17,605 files, 375 MB |
| B | `shim` | 366.6 s | 255.2 s | 3.1 s | 9.8 GB | 17,605 files, 375 MB |
| C | default (`jekyll`) | 381.3 s | 257.0 s | 16.2 s | 10.4 GB | 17,605 files, 375 MB |
| C | `shim-lazy` | 349.9 s | 226.7 s | 7.8 s * | 9.7 GB | 1,306 files, 38 MB |

\* includes 5.9 s generating the deferred fragments pages asked for (the shim renders pages in 2.1 s of
that stage in `shim` mode). Pair A (an earlier lazy build, before the PreExpansionView fix) gave the same
picture: default 441.7 s / jekyll 17.2 s / 10.2 GB vs shim-lazy 396.3 s / 8.3 s / 9.8 GB.

Registry summary (shim-lazy, US Core):

```
Fragments in memory: 9759 generated during the build (40787 KB), 6540 deferred, 0 aliases;
Jekyll included 4110 (209167 KB); deferred fragments generated: 2966 in 5942 ms, skipped: 3574
```

So of 16,299 fragments, 4,110 were included by a page; 3,574 deferred fragments were never generated, and
the other ~8,600 never-included ones were generated (eager families) but not written. `temp/pages/_includes`
goes from 17,605 files / 375 MB to 1,306 files / 38 MB (what is left: template includes and fragments
written outside `fragment()`).

Correctness (domdiff of `output/` against the default run of the same pair, then `pubint/residual.py`):

| comparison | domdiff: differ / missing / extra | after removing per-build noise |
|---|---|---|
| default vs default (two runs, control) | 7704 / 0 / 0 | 14 files: `codesystem-/valueset-ref-(all-)list.csv/json` (row order), `csvs.zip`, `full-ig.zip`, `package.db`, `qa.html`, `qa.min.html`, `fragment-usage-analysis.csv` |
| `shim` vs default | 7704 / 0 / 0 | the same 14 files: **identical to a second default build** |
| `shim-lazy` vs default | 7704 / 9 / 0 | the same 14, plus `qa-txservers.html` (order) and `StructureDefinition-us-core-questionnaireresponse.html` (2 rows); missing: 9 unreferenced `tbl_bck*.png` |

The one remaining page difference: in the key views of US Core QuestionnaireResponse, the rows
`QuestionnaireResponse.item.item` and `.item.answer.item` (contentReference to `#QuestionnaireResponse.item`)
get the Resource icon instead of the Element icon: the same class of problem as above (state that changes
after the eager point), from a mutation PreExpansionView doesn't cover (not yet located).

Per-build noise removed by residual.py: GUIDs, ISO/RFC/locale build timestamps, zip and tgz member
timestamps (members compared), timing files (`qa-time-report.*`, `qa-tx.html` server headers, times in
`fragment-usage-analysis.csv`, `maxMemory` in `qa.json`) and the terminology.hl7.org version in links (two
default builds of US Core pick different versions for some NamingSystem links).

### UDAP Security (HL7__fhir-udap-security-ig: 30 pages, no profiles)

Three sequential rounds of the three modes (median; noise from other jobs is of the same order as the
differences):

| mode | whole build | jekyll stage | peak RSS (tree) | `_includes` on disk |
|---|---|---|---|---|
| default (`jekyll`) | 15.0 s | 0.43 s | 1.8 GB | 9,517 files, 38 MB |
| `shim` | 14.9 s | 0.26 s | 1.8 GB | 9,517 files, 38 MB |
| `shim-lazy` | 14.2 s | 0.31 s | 1.7 GB | 59 files, 0.5 MB |

Registry summary: `Fragments in memory: 4740 generated during the build (665 KB), 2 deferred, 4716 aliases;
Jekyll included 6 (19 KB); deferred fragments generated: 0 in 0 ms, skipped: 2` (UDAP has no profiles, so
almost nothing is deferrable; the unsuffixed default-language copies are aliases instead of files).

Correctness: `shim` vs default: identical after noise (0 files). `shim-lazy` vs default:
`fragment-usage-analysis.csv` (and its copy in `full-ig.zip`) only, by design (the 2 skipped json-html
fragments aren't counted). A second default build vs the first: 0 files.

## Is deferral safe? (investigation)

**Verdict: no, not at a cost that makes sense.** Deferral can't be made rock solid without making the
renderers provably read-only, which is an audit and redesign of the renderers in core and the publisher. The
output is only guaranteed to be right if every deferred renderer reads only state that is frozen at its old
render point. In this code base the renderers read, and write, four kinds of shared mutable state. Deferral
breaks each of them, and because pages are rendered in parallel the result is also nondeterministic.
Recommendation: drop `shim-lazy`; keep `shim` and adopt `shim-mem`, which keeps the I/O savings with no
deferral at all.

### Root cause of the QuestionnaireResponse residual (US Core)

* **Object:** the copies of the QuestionnaireResponse profile's snapshot elements that the publisher's
  `StructureDefinitionRenderer` keeps in `keyElements`. `getKeyElements()` builds them on first call
  (`ed.copy()` + `copyUserData`) and reuses them for every key view: snapshot-by-key(-all), tx-key, inv-key,
  dict-key.
* **Mutation:** rendering the `-expansion` fragment calls `ElementDefinition.getTypeFirstRep()` on
  `QuestionnaireResponse`, `.item.item` and `.item.answer.item` (contentReference elements with no type).
  That getter adds an empty `TypeRefComponent`. The calls come from `PEBuilder.listChildren`
  (core, `PEBuilder.java:358`) and the publisher's `StructureDefinitionRenderer.genElement`
  (`StructureDefinitionRenderer.java:2981`). They were caught with the verifier's type-list trap, with the stack.
* **Phase:** the html generation of that profile, at the `-expansion` call in
  `generateOutputsStructureDefinition`. That is after the views listed before it (snapshot-by-key) and
  before the ones listed after it (tx-key, inv-key, dict-key).
* **Eager:** snapshot-by-key is the first caller of `getKeyElements()`, before the expansion. The copies are
  clean, and all later key views reuse them.
* **Lazy:** fragments are generated in page-render order, on the shim's parallel render threads.
  `StructureDefinition-...-definitions.html` includes `-dict-key`, a view that comes after the expansion and so
  isn't covered by PreExpansionView. That page renders before the profile page reaches `-snapshot-by-key`, so
  the copies are made from the mutated elements. PreExpansionView hides the empty types on the original
  elements, not on copies made from them. The rows then render with the Resource icon instead of the Element icon.
* **Evidence:**
  * The verifier (eager timeline, below) shows the fragments identical, because there the cache is built
    eagerly.
  * `SHIM_RENDER_THREADS=1` reproduces the residual deterministically: `-definitions.html` sorts before
    `.html`.
  * Building the cache at registration (`-Dig.fragments.primeKey=true`) under the same order removes it.

### What deferred renderers read, and what goes wrong (evidence from 5 IGs)

| state | example found | how it breaks deferral |
|---|---|---|
| the resource being rendered, changed after the eager point by side-effecting getters | `getTypeFirstRep()` during `-expansion` (US Core: 46 pages before PreExpansionView; 176 fragments with it off) | deferred views see the later state |
| the resource, rewritten later on purpose | genomics-reporting: the Bundle entries' narrative divs lose their `<a name=...>` anchors after the Bundle's xml/json/ttl-html point (9 fragments) | same |
| renderer-local memo caches (first call wins) | `keyElements` (above); the same renderer also has `mustSupportElements`, `diffElements`, `differentialHash`, `mustSupportHash` | result depends on which view asks first, i.e. on page order and thread timing |
| global session state | terminology calls and caches, failed lookups remembered for the run, `qa-txservers.html` order, tbl_bck image side files | order of calls changes what is recorded or returned |

Per-IG results. Each lazy or mem build is compared with a default build. Noise is filtered as
in `residual.py`, and so are the differences two default builds also show:

| IG | deferred (skipped) | verifier (eager timeline) | real `shim-lazy` build | `shim-mem` build |
|---|---|---|---|---|
| US Core | 6540 (3574) | 0 differ (176 with PreExpansionView off) | QR key views (order-dependent) | = default |
| genomics-reporting | 3744 (2101) | 9 differ: Bundle xml/json/ttl-html | the 9 Bundle pages + 1 profile's key views (order-dependent) | = default (one child-profile list order: publisher set-iteration nondeterminism, unrelated) |
| IHE ITI.MHD | 3189 (1970) | 0 differ | 3 profiles: key and snapshot-all views (order-dependent) | = default |
| AU Core | 4818 (3657) | 0 differ | = default (117 unreferenced tbl_bck pngs missing) | = default |
| UDAP | 2 (2) | 0 differ | = default | (not run) |

Two default builds of AU Core also differ, in one profile's narrative (a tooltip on a pattern code).
That difference is default-mode nondeterminism, not something deferral causes.

### The options

* **(a) Snapshot the inputs at registration** (deep-copy the SD/element tree, with its userData, per deferred
  family). It would fix the "resource changed later" rows for the resource itself, and the memo-cache row if
  the renderer is built on the copy. Cost: a copy of every SD (and of every element tree for
  xml/json/ttl-html; Bundles of several MB) held until Jekyll. The cost is not the problem, though. Renderers
  also read other resources (base SDs, value sets, the terminology cache), identity-keyed caches
  (`sdUsageCache` is an IdentityHashMap of elements), and session state. A copy of the one resource doesn't
  freeze any of that, so it isn't a guarantee.
* **(b) Side-effect-free getters in core.** Scope measured in 7.0.6:
  * **`*FirstRep()` call sites:** 64 in the core renderers (`org.hl7.fhir.services.renderers`, 70 files,
    37k lines), 16 in `profilemodel`, 52 in `conformance/profile`, 12 in the publisher's renderers (16.5k lines).
    By getter: `getTypeFirstRep` 50, `getElementFirstRep` 37, `getTargetFirstRep` 12, and so on.
  * **Other auto-create getters:** every getter of a complex singleton child (`getBinding()`, `getSlicing()`,
    `getSnapshot()`, ...) also auto-creates. `hasX()` hides most of those, but list sizes don't.
  * **Memo caches and userData:** 2 core renderer classes have lazily built fields, and there are 19 userData
    writes in core renderers plus 8 in the publisher's SD renderer.

  Fixing the call sites removes this instance. It doesn't guarantee anything unless it is enforced, e.g.
  with a "frozen" flag in the model that makes mutation throw while deferred renderers run. That is a change
  to the generated model and to every renderer that legitimately builds into the resources. It is also
  ongoing: new renderer code brings the problem back.
* **(c) Verification as a gate** (`-Dig.fragments.verify=true`, built on this branch). It renders every
  deferred fragment both at its eager point and deferred, and compares the bytes. It also fingerprints the
  SDs at every phase boundary and traps type-list changes. It finds changes made after the eager point
  (genomics, and US Core without PreExpansionView). By construction it **cannot** find the order-dependent
  failures: in its timeline the eager renders run first and fill the caches. Those failures (US Core QR,
  MHD, genomics) only show up when real lazy builds are compared with eager builds, in several render orders
  (threads=1, reversed, shuffled). Even then the gate only covers the IGs and orders it was run on.
  It is a good regression net. It is not a proof.
* **(d) Restrict deferral to renderers proven pure.** Nothing here can be proven pure statically. They all
  take the shared model, which has mutating getters. An empirical whitelist would only rest on "no difference
  seen in N IGs". The families that failed (SD snapshot, key and all views; xml/json/ttl-html) are also the
  ones worth deferring.

### What not deferring costs

`shim-mem` makes the same calls in the same order as the default; only where the content goes changes. It
came out identical to the default on US Core, MHD, AU Core and genomics (beyond default-vs-default noise).
US Core timing pairs (run two at a time, previous-version phase subtracted):

| pair | A | B | A without prev-version | B without prev-version |
|---|---|---|---|---|
| 1 | `shim` 384.5 s | `shim-mem` 374.7 s | 255.2 s | 244.8 s (-10 s) |
| 2 | `shim-mem` 359.7 s | `shim-lazy` 339.4 s | 235.2 s | 212.9 s (-22 s) |

Deferral is worth about 20 s (about 9% of US Core's non-network build time) on top of `shim-mem`. For that
the output can be wrong in ways that depend on thread timing. Not worth it.

## Findings / next steps

(Written before the investigation above; the verdict there supersedes the lazy-related points here.)

* In process alone (`shim`) removes the Jekyll stage (US Core 16-19 s -> 3 s) with output identical to a
  second default build; peak memory doesn't grow (the JVM is already large; the separate Ruby process goes).
* Laziness pays in proportion to the deferrable share: US Core skips 3,574 renderings and ~16,300 file writes
  (-337 MB in temp), ~30 s (-12% without previous-version) in these runs. The bigger win needs the other
  families deferred (CodeSystem/ValueSet/summary lists: UTG's 134k unused includes are mostly those) and a
  way to deal with state that changes after the eager point; the getTypeFirstRep() case shows that
  deferral can expose getter side effects in core. A safe rule for the next step: only defer renderers whose
  inputs are frozen (or render from a copy), and keep `-Dig.fragments.drain` as the regression check.
* Generators are serialized: deferred work runs single-threaded while pages wait (5.9 s for US Core). Fine
  at this size; a per-resource lock would let independent resources render in parallel.
