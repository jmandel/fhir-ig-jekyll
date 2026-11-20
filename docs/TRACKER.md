# FHIR IG Publisher: deterministic, JIT-able publishing with fhir-ig-jekyll: TRACKER

This is the project's working tracker: plan, decisions, evidence and open items. It is the single source of
truth for the work and is published as is. Paths under `work/`, `data/`, `repos/`, `templates/`, `src/` and
`progress/` are local working files (build outputs, sampled IG clones, logs) that are not in this repository;
everything else refers to files in this repo.

## How we work

- **This file is the source of truth.** Read it at the start of every session and update it before ending one. If something isn't written here, assume it will be forgotten. It is public: write for an outside reader (no credentials, no machine-specific secrets, no private paths).
- **Tasks are checkboxes with IDs** (`P1.3` etc.). States: `[ ]` todo, `[~]` in progress, `[x]` done, `[-]` dropped (say why). Never delete a task; mark it done or dropped.
- **New discoveries go straight onto the list.** When work turns up a bug, an unknown, or a new feature in use, add a task (or an Open Question) right away, in the phase it belongs to, with a one-line note on where it came from.
- **Keep notes as we learn.** Each completed task gets a short dated entry in the [Notes log](#notes-log): what we found, with numbers, and file paths/commands so it can be reproduced. Facts that change a decision also go under [Decisions](#decisions).
- **Use subagents where they help.** Independent, parallelizable work with a clear brief and a checkable output goes to subagents (porting a contained module, triaging golden-diff failures, drafting upstream patches). Each brief names the files to read, the acceptance check, and what's already ruled out. Results are verified (tests/diffs) before a task is marked done. Cross-cutting design and integration stay in the main thread.
- **Monitor long-running work.** Corpus builds and full-corpus diffs run in the background. Track them under [Running / monitoring](#running--monitoring) with the command and where the output lands; record results and clear the entry when they finish.
- **The golden corpus is the referee.** "Works" means it matches real Jekyll output under the equivalence rule, not that it looks right. Every bug fix gets a minimal regression case recorded from the real gems.
- **Outward-facing actions need explicit approval** (opening upstream PRs, posting to Zulip/GitHub issues). Draft them first, then ask.
- **Log publisher/core bugs as they turn up.** Any defect found in the publisher or core along the way (broken output, crashes, robustness), separate from the determinism work itself, gets a P10 entry with where it was found and the evidence. Upstream reports need approval.
- **Report failures plainly.** If a diff fails or a step was skipped, record it as it happened.

## Goal

**A deterministic, faster IG Publisher whose renderers are pure enough that fragments can be generated just in
time (JIT) for the in-process shim.** fhir-ig-jekyll v0.1.0 (the Java Jekyll replacement) shipped on 2026-11-20
with output identical to Ruby Jekyll across a 266-IG corpus; that effort's tracker is archived at
[`archive/TRACKER-shim-v0.1.md`](archive/TRACKER-shim-v0.1.md). Integrating it showed that the publisher can't
safely defer fragment generation today: renderers read state that changes later in the build, and two builds
of the same IG differ (D13, consult P6.15 in the archive, `work/consult/publisher-architecture-memo.md`).
P7 fixes the foundations (determinism), P8 makes rendering pure and brings the shim in process upstream, then
revisits JIT fragments; P9 is shim maintenance. All changes are aimed at upstream (org.hl7.fhir.core,
fhir-ig-publisher) as small, reviewable PRs.

## Decisions

Earlier decisions (D1–D12, the shim effort) are in the archive; these still constrain current work.

| # | Decision | Date |
|---|----------|------|
| D11 | **Heavy data stays on the VM's own disk** (`/srv/ig`, `scripts/lib/vmdata.sh`), never in the folder shared from the Mac: virtiofs holds a host file handle per guest-cached file and macOS caps them system-wide, which broke the host (P6.10). Only small results (logs, summaries, diffs) live in the shared folder. | 2026-11-20 |
| D13 | **JIT (lazy) fragments are off until renderers are provably pure.** The prototype (`publisher-integration` branch, `-jekyll-engine shim-lazy`) showed deferred renderers reading state that changes later in the build (side-effecting getters such as `getTypeFirstRep()`, later rewrites, first-caller caches, terminology state), with order-dependent results; keep in-process `shim` / `shim-mem` (identical to default builds). Revisit under P8.7 once P8.3/P8.4 hold. | 2026-11-20 |
| D14 | **Every change is gated** by the determinism harness (two builds identical under perturbations), output equivalence with the version before (domdiff), and the touched modules' unit tests; changes land as small upstream-ready commits on `determinism/<topic>` / later branches; nothing goes upstream without the project owner's approval. | 2026-11-20 |

## Running / monitoring

Subagents append milestone lines to `progress/<name>.log` (`<ISO time> | started|milestone|blocked|done | text`); the main agent watches those files and folds them into this tracker.

| What | Command | Output | Status |
|------|---------|--------|--------|
| Dashboard server | `PORT=8766 tracker-app/serve.sh` | http://127.0.0.1:8766/ | serving |
| tracker.json watcher (script, replaced the agent) | `tracker-app/watch.sh` (gen.py + check.py on every change, polled every 5 s) | `tracker-app/tracker.json`, log `work/tracker-watch.log` | running |
| Subagent: P7.1 determinism harness + baseline (Opus) | background agent | `scripts/determinism.sh`, `work/determinism/`, `progress/determinism.log` | running |
| Subagent: P7.3a ordered collections (Opus) | worktree set `ordered`, branch `determinism/ordered` | `work/determinism/fixes/ordered.md`, `progress/det-ordered.log` | running |
| P7.3b build clock + archives (Opus) | worktree set `clock`, branch `determinism/clock` | `work/determinism/fixes/clock.md` | done (jar `wt/clock/jars/publisher-clock-v7.jar`) |
| Subagent: P7.6 terminology record/replay + package lock (Opus) | worktree set `txrecord`, branch `determinism/txrecord` | `work/determinism/fixes/txrecord.md`, `progress/det-txrecord.log` | running |
| Subagent: P7.3c render-path getters (Opus) | worktree set `getters`, branch `determinism/getters` | `work/determinism/fixes/getters.md`, `progress/det-getters.log` | running |
| Subagent: P8.1a phase markers + fingerprint report (Opus) | worktree set `phases`, branch `determinism/phases` | `work/determinism/fixes/phases.md`, `progress/det-phases.log` | running |

## Plan

### P7: Determinism foundations (from the architecture consult; every change gated per D14)
- [x] P7.1 Determinism harness: build an IG twice under perturbations (threads, identity-hash seed, directory order, time zone), normalize, diff, classify; baseline on ~6 IGs with the current release. (Opus subagent, `progress/determinism.log`)
  - 15:24Z harness works end to end (`scripts/determinism.sh`, `scripts/detcmp.py`, `scripts/lib/HashShift.java`; perturbations: identity-hash shift, readdir order via a fresh ext4 hash seed, threads, time zone, path); frozen package-cache snapshot per baseline.
  - 15:36Z first v3.0.4 baselines: UDAP 1 real difference between two unperturbed builds (qa-tx); IHE ITI.MHD 67 (terminology version links 6.5.0 vs 7.3.0 even with a frozen cache, `[B@…` object ids in Binary markdown, `pub_xref_used` lists, package.db). US Core and hl7-be terminology runs being redone (killed by another agent at 15:45Z). Then the same baseline on the master jar.
  - 16:15Z v3.0.4 baseline, 5 of 6 IGs complete (real diffs, two identical builds / perturbed build): UDAP 1 / 45, IHE ITI.MHD 65 / 1,314, genomics-reporting 11 / 2,548, AU Core 33 / 1,234, hl7-be terminology 8 / 1,081; US Core finishing. Perturbed counts are dominated by **env-leak** (time zone and build path reaching output), a cause class for the clock workstream; normalizations (timestamps, run UUIDs, timing counters) reported per IG. Results: `work/determinism/baseline/<ig>/summary.md`.
  - 16:17Z US Core baseline complete: 1 real diff between two identical builds, 6,271 under perturbation; all 6 v3.0.4 baselines done. Next: the same on the master jar.
  - 17:00Z per-perturbation attribution on MHD (each perturbation alone vs none): **time zone 1,268 files** (C6: build times rendered in the machine's zone), path 11 (environment leaks), filesystem order 8 (unsorted `spec.internals`, `llms.txt`), identity hash and threads nothing beyond run-to-run noise (the ordering bugs, ref lists and THO version links, already appear at random without perturbation). So the large perturbed counts are almost all one cause, fixed in 3b.
  - 17:25Z attribution on US Core confirms the MHD picture: time zone 5,277 files (C6), path 913 (the comparison pages, P10.1), hash / fsorder / threads no class of their own; the THO-version links (58–66 files) and ref lists differ between b1 and 4 of 5 other builds whatever the perturbation (a random binary choice per run; fixed on `determinism/ordered`).
  - 17:01Z master baseline (749d8d63) started: UDAP 1 / 45 real diffs (control / perturbed), the same as v3.0.4.
  - 17:05Z master MHD: 12 / 1,292 (v3.0.4: 65 / 1,314). The control count varies a lot between runs because the ordering bugs strike at random; compare fixes over several control pairs, not one.
  - 17:08Z master hl7-be terminology: 8 / 1,081 (identical to v3.0.4).
  - 17:19Z master genomics-reporting: 35 / 2,547 (v3.0.4: 11 / 2,548; live terminology, so the control spread is wide).
  - 17:31Z master AU Core: 25 / 1,234 (v3.0.4: 33 / 1,234).
  - 17:53Z master US Core: 81 / 6,247 (live terminology). **All six master baselines done**; master matches v3.0.4 in perturbed counts, controls within the measured noise.
  - 17:15Z approved: harness gets a **frozen per-IG terminology cache** (warm once, snapshot, every build starts from a copy; remaining server requests counted), so builds stop depending on live tx.fhir.org and stop loading it; master control pairs re-run with it; fix agents re-baseline against it. (Interim step toward P7.6.)
  - 17:18Z harness: **frozen per-IG terminology cache** (`-T live|frozen|warm`, snapshots under `/srv/ig/determinism/txsnap/`), terminology requests per build counted, per-request HTTP headers normalized, N-control spread in summary.md, failed builds mark a comparison INVALID, compare-then-delete by default with a disk guard (`-F`). UDAP frozen: live requests 9 → 4; control 1 diff (qa-tx only).
  - 17:26Z **frozen terminology cache works:** MHD master, terminology requests 88 (warm-up) → 4 per build; `qa-tx.html` no longer differs. **Noise spread over 4 control builds: 13 / 64 / 65 real diffs (min / median / max)**, 65 files in the union, 13 differ in every pair. The THO-version links still differ with terminology frozen, so they are in-process ordering (fixed on `determinism/ordered`), not the server. This spread is the bar for before/after claims.
- [x] P7.2 Toolchain (2026-11-20): core master `62b21509` (7.0.7-SNAPSHOT) and publisher master `749d8d63` (3.0.6-SNAPSHOT) built from source on the VM (`/srv/ig/toolchain`, Temurin 21, `-Dcore_version` override, no pom edits); `scripts/toolchain.sh` (sync/build/test, per-topic **worktree sets** with their own Maven repo so fix branches build in parallel; edit via get/put). Tests: core utilities 1,716 pass, core r5 6,676 (2 transient live-tx-server errors), publisher.core 690 pass; core full suite partly run (rest to do). UDAP smoke on master: OK, 1,738 files, differences vs v3.0.4 explained by release changes. Notes: `work/toolchain/README.md`.
- [~] P7.3 Quick wins (three parallel agents: 3a ordered, 3b clock+archives, 3c getters), small commits each, each verified by P7.1: R1 ordered collections / total orders (core `CanonicalResourceManager.allResources`, publisher `pub_xref_used`, `DBBuilder` inserts, directory listings); R2 one build clock (`SOURCE_DATE_EPOCH`) and deterministic ids; R3 reproducible zips/tgz/package.db; R4 guard render-path `getTypeFirstRep()` calls, make the empty-type normalization explicit, remove `redoRenderingChanges`.
  - 3a ordered (15:55Z, MHD, first version: insertion-ordered registry, `pub_xref_used`, total-order sorters, sorted listings): real differences between two unperturbed builds 14 → 8, under perturbation 58 → 6; terminology version-link flips gone. Being re-verified after the stale-jar fix.
  - 3b clock (15:54Z): core `BuildClock` + ZipGenerator / NPMPackageGenerator / expander changes and publisher call sites compile; jar v3 rebuilt after the stale-jar fix; verification next.
  - 3c getters (15:58Z): `RenderMutationChecker` (`-Dig.debug.render-mutations=true`) built; on US Core master the HTML phase changes 135 resources via 3 list-changing call sites; fixes next.
  - 16:05Z toolchain bug found by 3a: publisher rebuilds re-shaded the previous cli jar, so later jars could be stale; fixed in `scripts/toolchain.sh` (52ee345f); all fix agents told to rebuild and re-verify.
  - 16:31Z VM disk full (parallel runs; determinism runs 46 GB) killed builds of all three fix agents; freed 54 GB by deleting the regenerable `shim-out` trees of the 266 goldens (82 GB free); rule: ≤ 15 GB per agent, delete trees after comparing, no new builds below 25 GB free; coordinator monitor now alarms below 20 GB.
  - 16:44Z 3b clock resumed after the disk cleanup; core commits rebased (CRLF fix): 3bc364c8, c29f64de, 51951d1d, 39842350, 486711e7; jar v6.
  - 17:09Z **3b clock works** (with `SOURCE_DATE_EPOCH`): MHD time-zone-only perturbation → **0** time-zone diffs (baseline 1,268 files); genomics env-leak 2,489 → 74, all remaining from the build path; MHD path-only: 7 leaks (QA source paths, default `file://` target URL).
  - 17:18Z **3a ordered on US Core: real diffs between two identical builds master 80 → 4** (the 4 left are terminology-server logs: qa-tx, qa-txservers, qa.html anchors); perturbed order-only 18 → 0, content 77 → 3. The random terminology.hl7.org version links (5.5.0/6.5.0 per run) were sort ties in `NamingSystemUtilities` (type + id); core 7dfd671b11 adds a numeric version tie-break. Also fixed: `[B@` identity strings in AI markdown (377d62c6, P10.2) and an NPE in `CanonicalListSorter` (1642a793c0). Re-verifying MHD + genomics.
  - 17:28Z **3b clock done** (`work/determinism/fixes/clock.md`): core 3bc364c8, c29f64de, 51951d1d, 39842350, 486711e7, 57e4f06d; publisher c0a7767f, cb543040, fecfe043. With `SOURCE_DATE_EPOCH`, no difference between two identical builds needs the time / UUID / archive normalizers any more (UDAP: 6 files left, all measurements + qa-tx); time-zone leak 0.
  - 3b final: gates passed. Two identical builds with `SOURCE_DATE_EPOCH`, no time/uuid/archive normalization: UDAP 39 → 6 of 1,738 files, MHD 715 → 69 of 2,352, genomics 1,264 → 28 of 4,826, US Core 93 of 17,226; what remains is outside the lane (measurement files, server-supplied expansion timestamps, collection order, `[B@`). Without `SOURCE_DATE_EPOCH` output = master apart from documented build-start/entry-time changes; unit tests: no new failures (validation's 21 `TerminologyServiceTests` failures also fail on master). `package.tgz` no longer records the JVM user name. Jar `/srv/ig/toolchain/wt/clock/jars/publisher-clock-v7.jar`; harness gained `-e NAME=VALUE` (env for the publisher JVM).
  - 17:38Z **3c getters:** core commit with non-creating reads + tests; publisher 395429bb (render-mutation checker, debug flag) and a56585ee (fix; the spreadsheet empty types made explicit; `redoRenderingChanges` removed). US Core checker: type-list additions during HTML generation **113 → 0**. Next: Rapido (incremental) check, harness runs with `-T frozen`.
  - 17:53Z 3c Rapido check (MHD, 136/189 files replayed incl. all profiles): `all-profiles.csv` and 84 profile CSVs identical to the full build, xlsx identical apart from the build date, for master and for getters **without** `redoRenderingChanges`, so removing it is safe. Harness runs with the frozen terminology cache started.
- [ ] P7.4 Two-build determinism CI job (small IG) for the publisher project.
- [x] P7.5 Design (Fable 5.2, started in parallel with P7.1/P7.2): frozen flag in the core model + named phase model (R5/R6) with non-creating reads (R4) and pure renderers (R7), audit mode first; rollout as reviewable PRs. Then implementation and an audit report over the corpus IGs. (`progress/design.log`)
  - 15:57Z inventories done and spot-checked (model write paths, phase map with late writes, userData key classes, FirstRep/auto-create sites, caches); core master 62b2150 identical to 7.0.6 for the relevant files; genomics Bundle rewrite located (core `BundleRenderer.java:134`, in-place strip on the live narrative); design doc being written.
  - 16:44Z first complete version of the design on disk (`work/consult/frozen-model-design.md`, sections 0–10 + appendices; unverified items marked); final summary pending.
  - **Done 16:44Z** (`work/consult/frozen-model-design.md`, 720 lines; claims from code reading, unverified items in section 10). Flag: one `transient byte` on `Base` (open / frozen-audit / frozen-enforce) set by a tree walk; ~15 hand-written choke points (BaseList writes, userData, PrimitiveType writers, Element extension helpers) need no regeneration; generated setters/auto-creating getters last, only `org.hl7.fhir.model`; frozen list getters return an immutable empty list; `XhtmlNode` and element-model `Element` freeze separately. Audit: listener in utilities, publisher adds phase/activity/resource, writes `qa-mutations.json`. userData: 339 writes / 530 reads classified; ~16 keys written on real resources during rendering → identity-keyed side table on `RenderingContext`; derived data such as `pub_xref_used` → an index built before rendering. 48 of 90 live `*FirstRep()` calls in renderers unguarded → `firstOrNull` helper + checkstyle rule. Phases: load, normalize, validate, finalize, freeze, index, render, assemble, report; 15 groups of late writes located, incl. the genomics Bundle anchor strip (core `BundleRenderer.java:134`), mid-render conversions (PG:959), `{% fragment %}` elision writes (PG:6609), `templateBeforeGenerate` after native outputs (PG:3945); worst cache: global `sdMapCache` (first fill rewrites live snapshot elements via `updateURLs`). Rollout: 14 PRs; **first PR = publisher-only phase markers + opt-in phase-boundary fingerprint report** (no core change, no output change); audit mode = PRs 6–7. 10 open questions for maintainers (enforce: throw vs frozen detached instance; generated `getXOrNull()`; empty type in all-profiles; …).
- [~] P7.6 Recorded terminology answers + package lock (R9): record/replay mode for terminology (zero live requests on replay, server timestamps/ids kept as recorded) and a package lock (resolved id#version + integrity hash); optional, off by default. Started in parallel (Opus, branch `determinism/txrecord`, `progress/det-txrecord.log`); also answers whether the AU Core tooltip (P10.3) is terminology-driven.
- [ ] P7.7 Decide upstream route with the project owner: forks + PRs to hapifhir/org.hl7.fhir.core and HL7/fhir-ig-publisher, and how to introduce the work to the maintainers.

### P8: A JIT-able publisher: pure rendering, then the shim in process upstream, then JIT fragments
- [~] P8.1 Frozen model in **audit mode** (from the P7.5 design): record every mutation of a resource after the freeze point, with stack traces, without changing behavior; corpus audit report (counts by class and call site).
  - Started with the design's PR 1 (P8.1a): publisher-only named phase markers + opt-in phase-boundary fingerprint report (no output change), to measure per IG which resources change across which boundary.
  - 16:59Z P8.1a: `BuildPhase` / `BuildPhaseTracker` + `PhaseFingerprinter` / report built, unit test passes; MHD sanity run next.
  - 17:09Z P8.1a MHD sanity: report works; late writes found after rendering: assemble/combined-package (27 resources pinned/expanded in place) and report/QA (113 narratives get CRLF text nodes); gate runs started (master ×2, report off ×2, on ×2) on MHD, genomics, US Core.
  - 17:20Z P8.1a commits 42a5519c (phase markers) + 045ee7d5 (fingerprint report) on `determinism/phases`; publisher.core 692 tests pass. MHD gate: flag off vs master 14 real diffs, all within master's own control noise (control 15); flag on = the same plus the new `qa-phases.*`; the report is identical across two builds; fingerprinting costs 2.8 s. genomics + US Core gates next.
  - 17:36Z P8.1a genomics gate passed: flag off vs master 44 real diffs = master's own control noise + the 8 comparison pages whose only change is the printed stack trace's line numbers (P10.13, shifted by the new code); flag on = same + `qa-phases.*`; report identical across two builds; fingerprinting 2.8–3.1 s. US Core gate running.
- [ ] P8.2 Fix the violations P8.1 finds: deliberate rewrites moved before the freeze, render scratch state off `userData`, memo caches built eagerly at the freeze or only over frozen data, remaining auto-creating reads replaced.
- [ ] P8.3 **Enforce** the freeze during HTML generation (a mutation throws in CI/test builds, logs in production), with the determinism harness as the gate.
- [ ] P8.4 Pure renderers with declared inputs (R7), starting with the StructureDefinition views (the family JIT would defer first).
- [ ] P8.5 Upstream the in-process engine: `-jekyll-engine shim|shim-mem` (from the prototype), with the code-home decision (publisher module vs separate artifact) and a maintainer (Q1). (Was P6.0/P6.3.)
- [ ] P8.6 Rollout: autobuilder shadow mode (run the shim alongside Jekyll, log DOM diffs, ship Jekyll output) → opt-in → default with Jekyll fallback → remove Ruby from the image. (Was P6.1/P6.2.)
- [ ] P8.7 Revisit JIT fragments once P8.3/P8.4 hold: eager-vs-JIT byte verification over the corpus plus perturbed real builds; adopt only if identical everywhere (D13).
- [ ] P8.8 Fragment-level, content-addressed build graph keyed by declared inputs (R8): incremental and watch-mode rebuilds, safe parallel rendering.

### P9: fhir-ig-jekyll maintenance (shipped product)
- [ ] P9.1 Apply the kramdown 2.5.2 → 2.6.1 delta (about 200 lines) once production runs the newer image; re-run the feature suite with the `-next` oracle image (the real corpus is already identical). (Was P4.8.)
- [ ] P9.2 Make a failed shim build write nothing, as Jekyll does (it renders everything before writing), instead of leaving partial output. (Was P6.13.)
- [ ] P9.3 A better default memory footprint for the wrapper without brittle sizing flags: with only `-XX:MaxRAMPercentage=75` the JVM grows to ~2× Jekyll's RSS on a large machine although the shim needs 50–78% of it; measure a lower percentage, G1 heap shrinking, or ZGC `SoftMaxHeapSize` on the bench IGs. (Was P6.14.)

### P10: Publisher and core bugs found along the way (log; each to be reported upstream with evidence once approved)
- [ ] P10.1 **Absolute build paths in published output (broken images).** (a) Comparison pages `comparison-v*/sd-*-{union,intersection}.html` reference `<img src="/home/publisher/ig/temp/pages/comparison-v3.1.1/tbl_spacer.png">` and `url(…/comparison-v3.1.1tbl_bck1.png)` (missing `/`): broken images on 902 US Core pages; (b) `HierarchicalTableGenerator` background images written as `url(/home/publisher/ig/temp/pagesdata:image/png;base64,…)` (a local path glued to a data URI) in `dependency-table*.xhtml` → `about.html`, `index.html` (all IGs); (c) `package.json` in every `.tgz` has `"url" : "file:///home/publisher/ig/output"`. Found by the harness `path` perturbation (RC8, `work/determinism/README.md`).
  - (b) fixed on `determinism/clock` (core 39842350): `HierarchicalTableGenerator` prefixed inline `data:` tree-line backgrounds with the image path; evidence `work/determinism/clock/cmp-master-clock/report.json`. (a) and (c) still open.
- [x] P10.2 **Java object identity strings in content (fixed on `determinism/ordered` 377d62c6: AI markdown for text Binary resources printed the `byte[]` identity instead of the content; `AIProcessor.java:332`, `OldIGAIGenerator.java:218`):** `[B@<hex>` (a byte array's default `toString()`) printed into Binary resources' markdown pages; differs every run. (RC, `work/determinism/README.md`.)
- [ ] P10.3 **Display tooltip present or absent at random (AU Core):** `StructureDefinition-au-core-rsg-sexassignedab.html` (+ definitions, json/xml/ttl, package.db, zips) renders a pattern code with or without `<span title="Biological sex at birth">` between two default builds; cause open (terminology cache/server state or a first-caller cache). (RC11.)
- [ ] P10.4 **No integrity check on cached packages:** a package folder without `package/package.json` (e.g. a truncated download) is treated as installed; the publisher then fails with "File …/package.json not found", and Sushi with "Valid StructureDefinition resource not found". Found in the shim corpus run (12 IGs failed on a truncated `hl7.fhir.r6.core#6.0.0-snapshot1`); should re-download or report the damaged package.
- [ ] P10.5 **`MyFilterHandler` 256-byte line buffer** overflows on long Jekyll output lines (found during the in-process prototype, P6.8 in the archive).
- [ ] P10.6 **Publisher crashes on real IGs** (from the shim corpus): NullPointerException in terminology validation (`ValidationResult.getSeverity()` null → `IssueSeverity.ordinal()`) on AuDigitalHealth__HCPD; NPE loading a dependency on davinci-pas. Reproduce on master first.
- [ ] P10.7 **Empty `type` published in Turtle definitions:** `definitions.ttl.zip` carries `fhir:ElementDefinition.type [ fhir:index 0 ]` on profile roots and contentReference elements: the empty type that rendering the `-expansion` fragment adds (`getTypeFirstRep()`) is serialized by the Turtle writer, while JSON/XML skip empty elements. Seen on master builds of US Core and MHD (found by 3c; evidence `work/determinism/fixes/getters-runs/IHE__ITI.MHD.master-vs-fix2.residual.txt`). Fixed as a side effect of P7.3c if its getter guard lands.
- [ ] P10.8 **Liquid narratives rejected:** `LiquidRenderer.buildNarrative` checks `x.getName()` (the target) instead of the parsed output `xn` for `div`, so Liquid-template narratives fail with "Root element is not 'div'" and are dumped to `[tmp]/liquid-*.html` (genomics `Bundle-bundle-oncologyexamples-r4`). Found by 3b; evidence `work/determinism/clock/genomics-v6/cmp-b1-b3/summary.txt`. Not fixed (render path). The temp-file path also leaks into published output (all formats, package.db, packages); also seen by 3a (`work/determinism/ordered/GEN-v3/cmp-b1-b3/summary.txt`).
- [ ] P10.9 **12-hour clock in build dates** (confirmed by the harness in all 6 IGs, e.g. `20261120031954+0000` for a 15:19:54 UTC build): `"yyyyMMddhhmmssZ"` (`hh`, not `HH`) in `PublisherGenerator.makeNewVersionInfo` (`version.info`) and `SpecMapManager` (`spec.internals` date-time), so afternoon builds print 01–11. Found by 3b; not fixed (changes printed output; needs a maintainer decision).
- [ ] P10.10 **Zone offset printed twice and from the wrong date** in `package.json`'s description, e.g. "(built Tue, Nov 14, 2023 22:13+0000+00:00)": the `Z` pattern plus `timezone()`, and `timezone()` uses today's offset, not the build date's (wrong across DST). Found by 3b; zone source fixed via `BuildClock.timeZone`, duplication left as is.
- [x] P10.11 **NPE sorting canonical resources without a `url`** in `CanonicalResourceManager.CanonicalListSorter`; fixed on `determinism/ordered` 1642a793c0 (sorts them first, as `ResourceSorters` does).
- [~] P10.12 **Random terminology.hl7.org version in links** (6.5.0 vs 7.3.0 / 5.5.0 vs 6.5.0, e.g. `NamingSystem-uri`, `CodeSystem-v3-ActReason`) between builds with an identical package cache (MHD, US Core, AU Core): sort ties in `NamingSystemUtilities` (type + id); fix on `determinism/ordered` core 7dfd671b11 (numeric version tie-break), being verified.
- [ ] P10.13 **Java stack traces in published pages:** genomics-reporting `comparison-v3.0.0/xx-*-*.html` (8 pages) contain a full stack trace (`DefinitionException` "Found a slice at Observation.component but …" from `PreviousVersionComparator.finishChecks` via `PublisherProcessor.checkConformanceResources`); besides exposing internals, any publisher code change shifts the line numbers in that output. Found by P8.1a (evidence under `work/determinism/fixes/phases-runs/`).

## Open questions
- Q1. Where should the in-process engine live (a module in fhir-ig-publisher vs a separate artifact), and who maintains it? Discuss with the publisher maintainers (P8.5).
- Q2. How to introduce this work upstream: one umbrella issue with the memo and harness, then small PRs in core and publisher? (P7.7)
- Q3. Build times without `SOURCE_DATE_EPOCH`: render them in a fixed zone (UTC or an IG setting) too, removing the time-zone leak in normal builds, at the cost of changed printed output? (from 3b)
- Q4. Should measurement files (`fragment-usage-analysis.csv`, `qa-time-report.*`, `qa.json`) stay inside `full-ig.zip`, and should QA source paths be relative to the IG folder? (from 3b)

## Notes log
- **2026-11-20:** fhir-ig-jekyll v0.1.0 shipped; that effort's tracker archived (`archive/TRACKER-shim-v0.1.md`). This tracker now covers the publisher work: P7 started (determinism harness, from-source toolchain at upstream master, three quick-win fix branches, frozen-model design).
- **2026-11-20:** Toolchain: core master `62b21509` and publisher master `749d8d63` build from source on the VM with per-topic worktrees (P7.2). An agent's `pkill -f determinism.sh` killed two of the harness agent's baseline runs; rule since then: kill only own processes by exact PID or container id.

## Layout
```
docs/TRACKER.md         this file (the workspace's TRACKER.md is a symlink to it)
docs/archive/           frozen trackers of finished efforts (TRACKER-shim-v0.1.md)
shim/                   Java sources, tests (shim/tests/**), the Ruby oracle (shim/oracle), wrappers
scripts/                corpus sampling, golden builds, comparison (domdiff.py), timing/memory tools,
                        CI checks (test-suites.sh, e2e.sh, test-wrapper.sh), lib/vmdata.sh
docker/                 reference image (pinned digest; optional extra CAs, not committed)
lima/                   Lima VM definition and notes for corpus runs
tracker-app/            dashboard for this tracker (tracker.json is generated from it)
third_party/            upstream Ruby/C sources as optional shallow submodules
.github/workflows/      CI (3 OS × 2 JDKs) and release (tag v* → wrappers, jar, zip, checksums)

Local working files, not in the repo:
data/                   build sample (qas.json, targets.tsv), scans, strict sweep
repos/  templates/      sampled IG and template clones (untrusted; read-only use)
work/                   publisher.jar, logs, summaries, small per-golden files (golden trees live on the VM disk)
progress/               subagent milestone logs
/srv/ig/ (VM disk)      golden trees, build copies, package cache (scripts/lib/vmdata.sh)
```
