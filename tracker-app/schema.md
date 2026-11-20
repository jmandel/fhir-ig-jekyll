# tracker.json schema (v1)

`tracker.json` is a machine-readable mirror of `TRACKER.md` plus the tail of each `progress/*.log` file. TRACKER.md is the source of truth; this file is regenerated from it and never edited by hand. The dashboard (`index.html`) reads only this file.

Conventions:
- Types: `string`, `int`, `number`, `bool`, `T[]` (array), `T | null`. "req" means the key is always present (possibly `[]` or `null` where the type allows it); "opt" means the key may be left out.
- Text fields (`text`, `goal`, and so on) hold **inline Markdown copied from the source** (backticks, `**bold**`, `[links](…)` kept as they are). Only the list marker, checkbox, and leading ID are removed. Never paraphrase.
- IDs are copied from the Markdown exactly (`P0`, `P1.3`, `D4`, `Q2`). Never invent or renumber them.
- Timestamps are ISO 8601 UTC with a `Z` suffix, e.g. `2026-11-19T19:51:18Z`. Dates are `YYYY-MM-DD`.

## Top level

| Field | Type | Req | Meaning |
|---|---|---|---|
| `schemaVersion` | int | req | Always `1` for this schema. |
| `generatedAt` | string (ISO) | req | When this JSON was written. |
| `sourceMtime` | string (ISO) | req | TRACKER.md modification time (`stat -f %m`, converted to UTC). |
| `source` | object | req | `{ "path": string, "sha256": string }`: absolute path of TRACKER.md and the sha256 hex of the exact bytes that were translated. |
| `title` | string \| null | req | Text of the `# ` H1 line. |
| `goal` | string \| null | req | Body of `## Goal` (Markdown). |
| `howWeWork` | string[] | req | One entry per bullet in `## How we work`. |
| `summary` | Summary | req | Computed counts (see below). |
| `phases` | Phase[] | req | From `## Plan`, in document order. |
| `decisions` | Decision[] | req | From the `## Decisions` table, in document order. |
| `runningIntro` | string \| null | req | Any prose in the Running section outside the table (Markdown, paragraphs joined with `\n`). |
| `running` | RunningItem[] | req | From the `## Running / monitoring` table. `[]` when the table has no data rows. |
| `openQuestions` | Question[] | req | From `## Open questions`, in document order. |
| `keyFacts` | Fact[] | req | From the `## Key facts…` section (heading may carry a suffix such as "(from P0)"). |
| `notes` | Note[] | req | From `## Notes log`, **newest first** (see ordering). |
| `layout` | string \| null | req | Contents of the code block in `## Layout`, without the fences. |
| `extraSections` | ExtraSection[] | req | Any `## ` section not listed above, kept verbatim. |
| `progress` | object | req | `{ "<logName>": ProgressLog }`, one key per `progress/*.log` file next to TRACKER.md (key = file name without `.log`, e.g. `liquid`). `{}` if the directory is missing or empty. |
| `parseWarnings` | Warning[] | req | Everything that didn't map cleanly. `[]` if none. |

## Summary

Counts are derived **only** from `phases[].tasks[]`, so they can be checked against the task list.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `total` | int | req | Number of tasks across all phases. |
| `byState` | StateCounts | req | Count per state. |
| `percentDone` | number | req | `100 * done / (total - dropped)`, rounded to 1 decimal; `0` if the denominator is 0. |
| `perPhase` | PhaseCount[] | req | One per phase, same order as `phases`. |
| `inProgress` | string[] | req | IDs of every task whose state is `in_progress`. |

`StateCounts` = `{ "todo": int, "in_progress": int, "done": int, "dropped": int, "unknown": int }`. All five keys are always present, and they sum to the total.

`PhaseCount` = `{ "id": string, "total": int, "byState": StateCounts, "percentDone": number }`, using the same formula per phase.

## Phase

From each `### Pn: Title` heading under `## Plan`.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `id` | string \| null | req | `P0`, `P1`, … `null` only for the synthetic "(unphased)" phase that holds checkbox tasks found under `## Plan` before the first `###` heading, or for a `###` heading with no `Pn` (warn in both cases). |
| `title` | string | req | Heading text after `Pn:`, as written (including any parenthetical such as "(mostly done)"). |
| `description` | string | opt | Prose lines (not tasks) directly under the phase heading, joined with `\n`. |
| `acceptance` | string | opt | If the heading contains `acceptance = …` (or `acceptance: …`), the text after it. |
| `delegated` | bool | opt | `true` if the heading says the phase is delegated (e.g. "delegated to a subagent"). Leave the key out otherwise. |
| `status` | enum | req | Derived: `done` if every task is `done` or `dropped` (and there is at least one task); `in_progress` if any task is `in_progress` or `done`; otherwise `todo`. |
| `tasks` | Task[] | req | In document order. |

## Task

From `- [m] Pn.k text` lines.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `id` | string | req | `P1.3`. If the line has no ID, use `null` and add a warning. |
| `state` | enum | req | `todo` (`[ ]`), `in_progress` (`[~]`), `done` (`[x]` or `[X]`), `dropped` (`[-]`), `unknown` (any other marker). |
| `text` | string | req | Inline Markdown after the ID. Continuation lines (indented, not a new bullet) are joined with one space. |
| `marker` | string | opt | Raw marker character. Include it only when `state` is `unknown`. |
| `notes` | string[] | opt | Indented sub-bullets beneath the task, one entry each. |
| `droppedReason` | string | opt | For `dropped` tasks: the stated reason, if the text gives one (e.g. after "dropped:" or "because"). |
| `links` | Link[] | opt | References pulled out of `text` (and `notes`). Leave the key out if there are none. |

## Link

`{ "kind": enum, "target": string, "label"?: string }`

| `kind` | Extracted from | `target` |
|---|---|---|
| `ref` | A bare tracker ID in prose (`P0.8`, `D3`, `Q2`), outside code spans; not the item's own ID | The ID |
| `path` | A code span that looks like a file or dir path (has `/` or `.`, no spaces, not Liquid like `{{…}}`) | The path as written |
| `url` | `http(s)://…`, or a Markdown link to one | The URL; `label` = link text |
| `anchor` | Markdown link to `#fragment` | `#fragment`; `label` = link text |
| `section` | Prose such as "see Running" or "see Notes log" | Section name as written |

Links are a convenience for the UI. Pick them out sensibly; leaving one out is fine, but don't invent targets.

## Decision

From the Decisions table. `{ "id": string | null, "text": string, "date": string | null, "links"?: Link[] }`. `id` comes from the `#` column (`D1`), `text` from the Decision column, `date` from the Date column.

## RunningItem

From the Running / monitoring table. `{ "what": string, "command": string, "output": string, "status": string, "taskRef"?: string }`. Cells are copied as Markdown. `taskRef` is a background-task ID if the command cell names one (e.g. ``(bg task `b3b4nnkt0`)`` → `"b3b4nnkt0"`). Extra columns go into an `extra` object keyed by header name, with a warning.

## Question

`{ "id": string | null, "text": string, "resolved"?: bool, "links"?: Link[] }`, from bullets of the form `- Q1. text`. The trailing `.` after the ID is not part of the text. Set `resolved: true` only when the item is clearly marked as resolved (struck through `~~…~~`, or it starts with or contains "Resolved"/"Answered"); otherwise leave the key out. Bullets with no `Qn` get `id: null` and a warning.

## Fact

`{ "text": string, "links"?: Link[] }`, one per bullet.

## Note

`{ "date": string | null, "text": string, "seq": int, "links"?: Link[] }`, from bullets of the form `- **YYYY-MM-DD:** text`.
- `seq` = 1-based position in the source file (top to bottom).
- **Ordering:** sort by `date` descending, then `seq` descending. The log is appended at the bottom, so for the same date a later line is newer.
- A bullet with no parseable date gets `date: null`, sorts last, and gets a warning.

## ProgressLog

Built from `progress/<name>.log`. Subagents append one line per milestone in the form `<ISO timestamp> | started|milestone|blocked|done | <text>`.

| Field | Type | Req | Meaning |
|---|---|---|---|
| `file` | string | req | Path relative to the project root, e.g. `progress/liquid.log`. |
| `mtime` | string (ISO) | req | File modification time, in UTC. |
| `lineCount` | int | req | Number of non-blank lines in the file. |
| `entries` | ProgressEntry[] | req | The **last 5** non-blank lines, **newest first** (the reverse of file order). |

`ProgressEntry` = `{ "timestamp": string | null, "status": enum, "text": string }`
- Split each line on the first two `|`, then trim each part. `timestamp` is copied as written (minute precision such as `2026-11-19T20:01Z` is fine; don't rewrite it).
- `status` is one of `started`, `milestone`, `blocked`, `done`. A different value, or a line that doesn't split into three parts with an ISO-looking timestamp, gives `timestamp: null`, `status: "unknown"`, `text` = the whole raw line, and a warning with `section: "progress/<name>.log"`.
- Text after the second `|` is kept whole, even if it contains more `|` characters.

## ExtraSection

`{ "heading": string, "markdown": string }`, with the section body verbatim.

## Warning

`{ "section": string, "line"?: int, "message": string, "raw"?: string }`. `line` is the 1-based line number in TRACKER.md, and `raw` is the offending source line (trimmed, at most about 200 chars).

## Example (abridged)

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-11-19T20:05:00Z",
  "sourceMtime": "2026-11-19T19:51:18Z",
  "source": { "path": "<project-root>/TRACKER.md", "sha256": "01336b8e…" },
  "title": "Jekyll → Java replacement for the IG Publisher: TRACKER",
  "goal": "Replace the Ruby/Jekyll stage of …",
  "howWeWork": ["**This file is the source of truth.** Read it …"],
  "summary": {
    "total": 48,
    "byState": { "todo": 35, "in_progress": 2, "done": 11, "dropped": 0, "unknown": 0 },
    "percentDone": 22.9,
    "perPhase": [
      { "id": "P0", "total": 10, "byState": { "todo": 2, "in_progress": 1, "done": 7, "dropped": 0, "unknown": 0 }, "percentDone": 70.0 }
    ],
    "inProgress": ["P0.8", "P1.1"]
  },
  "phases": [
    {
      "id": "P1", "title": "Golden corpus and harness", "status": "in_progress",
      "tasks": [
        { "id": "P1.1", "state": "in_progress", "text": "Golden builds, batch 1 (see Running).",
          "links": [{ "kind": "section", "target": "Running" }] }
      ]
    }
  ],
  "decisions": [{ "id": "D3", "text": "Liquid = **our own port of Liquid 4.0.4**, not Liqp. …", "date": "2026-11-19" }],
  "runningIntro": "Subagents append milestone lines to `progress/<name>.log` …",
  "running": [{ "what": "Golden batch 1 (9 IGs: …)", "command": "loop over `scripts/golden-build.sh` (bg task `b3b4nnkt0`)",
                "output": "`work/batch1.log`, `work/golden/<ig>/`", "status": "running since 2026-11-19", "taskRef": "b3b4nnkt0" }],
  "openQuestions": [{ "id": "Q1", "text": "Is Jekyll actually the slow part? … (P0.8).", "links": [{ "kind": "ref", "target": "P0.8" }] }],
  "keyFacts": [{ "text": "Jekyll config everywhere is kramdown options only. …" }],
  "notes": [{ "date": "2026-11-19", "seq": 7, "text": "Lax probes: …" }],
  "layout": "project/\n  TRACKER.md            this file\n  …",
  "extraSections": [],
  "progress": {
    "liquid": {
      "file": "progress/liquid.log", "mtime": "2026-11-19T20:01:24Z", "lineCount": 1,
      "entries": [{ "timestamp": "2026-11-19T20:01Z", "status": "started", "text": "read README/TRACKER/liquid-4.0.4 lib; starting Java port" }]
    }
  },
  "parseWarnings": []
}
```

The full worked example is `tracker.json`, next to this file.
