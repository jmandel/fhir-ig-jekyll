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
| Subagent: tracker.json watcher | background agent following `tracker-app/watcher-prompt.md` | `tracker-app/tracker.json` | running |
| Subagent: P7.1 determinism harness + baseline (Opus) | background agent | `scripts/determinism.sh`, `work/determinism/`, `progress/determinism.log` | running |
| Subagent: P7.3a ordered collections (Opus) | worktree set `ordered`, branch `determinism/ordered` | `work/determinism/fixes/ordered.md`, `progress/det-ordered.log` | running |
| Subagent: P7.3b build clock + archives (Opus) | worktree set `clock`, branch `determinism/clock` | `work/determinism/fixes/clock.md`, `progress/det-clock.log` | running |
| Subagent: P7.3c render-path getters (Opus) | worktree set `getters`, branch `determinism/getters` | `work/determinism/fixes/getters.md`, `progress/det-getters.log` | running |
| Subagent: P7.5 frozen-model / phase design (Fable 5.2) | background agent | `work/consult/frozen-model-design.md`, `progress/design.log` | running |

## Plan

### P7: Determinism foundations (from the architecture consult; every change gated per D14)
- [~] P7.1 Determinism harness: build an IG twice under perturbations (threads, identity-hash seed, directory order, time zone), normalize, diff, classify; baseline on ~6 IGs with the current release. (Opus subagent, `progress/determinism.log`)
- [x] P7.2 Toolchain (2026-11-20): core master `62b21509` (7.0.7-SNAPSHOT) and publisher master `749d8d63` (3.0.6-SNAPSHOT) built from source on the VM (`/srv/ig/toolchain`, Temurin 21, `-Dcore_version` override, no pom edits); `scripts/toolchain.sh` (sync/build/test, per-topic **worktree sets** with their own Maven repo so fix branches build in parallel; edit via get/put). Tests: core utilities 1,716 pass, core r5 6,676 (2 transient live-tx-server errors), publisher.core 690 pass; core full suite partly run (rest to do). UDAP smoke on master: OK, 1,738 files, differences vs v3.0.4 explained by release changes. Notes: `work/toolchain/README.md`.
- [~] P7.3 Quick wins (three parallel agents: 3a ordered, 3b clock+archives, 3c getters), small commits each, each verified by P7.1: R1 ordered collections / total orders (core `CanonicalResourceManager.allResources`, publisher `pub_xref_used`, `DBBuilder` inserts, directory listings); R2 one build clock (`SOURCE_DATE_EPOCH`) and deterministic ids; R3 reproducible zips/tgz/package.db; R4 guard render-path `getTypeFirstRep()` calls, make the empty-type normalization explicit, remove `redoRenderingChanges`.
- [ ] P7.4 Two-build determinism CI job (small IG) for the publisher project.
- [~] P7.5 Design (Fable 5.2, started in parallel with P7.1/P7.2): frozen flag in the core model + named phase model (R5/R6) with non-creating reads (R4) and pure renderers (R7), audit mode first; rollout as reviewable PRs. Then implementation and an audit report over the corpus IGs. (`progress/design.log`)
- [ ] P7.6 Recorded terminology answers + package lock (R9).
- [ ] P7.7 Decide upstream route with the project owner: forks + PRs to hapifhir/org.hl7.fhir.core and HL7/fhir-ig-publisher, and how to introduce the work to the maintainers.

### P8: A JIT-able publisher: pure rendering, then the shim in process upstream, then JIT fragments
- [ ] P8.1 Frozen model in **audit mode** (from the P7.5 design): record every mutation of a resource after the freeze point, with stack traces, without changing behavior; corpus audit report (counts by class and call site).
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

## Open questions
- Q1. Where should the in-process engine live (a module in fhir-ig-publisher vs a separate artifact), and who maintains it? Discuss with the publisher maintainers (P8.5).
- Q2. How to introduce this work upstream: one umbrella issue with the memo and harness, then small PRs in core and publisher? (P7.7)

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
