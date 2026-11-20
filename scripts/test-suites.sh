#!/bin/bash
# Run the committed differential suites (expected outputs recorded from the real Ruby gems) with a
# plain JDK; no Docker or Ruby needed. Builds first unless SHIM_NO_BUILD=1. Documented expected
# differences live in <cases>.known (see DiffRunner --known); anything else fails the run.
# usage: scripts/test-suites.sh            (from anywhere; Linux, macOS, Windows Git Bash)
# The YAML file-case suites hold /w/... paths (the oracle container's mount point); they run only
# where /w is the repo root (the reference container, or CI with a /w symlink): set SHIM_W_SUITES=1.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[ -n "${SHIM_NO_BUILD:-}" ] || shim/build.sh >/dev/null || exit 1
SEP=":"; case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) SEP=";";; esac
B="${SHIM_BUILD:-shim/build}"   # a full build dir (BUILD_OUT=x shim/build.sh -> shim/x)
CP="$B/classes${SEP}$B/test-classes"
suites=(
  "shim/tests/smoke-cases.jsonl shim/tests/smoke-expected.jsonl"
  "shim/tests/liquid/cases.jsonl shim/tests/liquid/expected.jsonl"
  "shim/tests/liquid/cases-crlf.jsonl shim/tests/liquid/expected-crlf.jsonl"
  "shim/tests/kramdown/cases.jsonl shim/tests/kramdown/expected.jsonl"
  "shim/tests/kramdown/cases-emskip.jsonl shim/tests/kramdown/expected-emskip.jsonl"
  "shim/tests/kramdown/cases-d7.jsonl shim/tests/kramdown/expected-d7.jsonl"
  "shim/tests/rouge/cases.jsonl shim/tests/rouge/expected.jsonl"
  "shim/tests/rouge/targeted-cases.jsonl shim/tests/rouge/targeted-expected.jsonl"
  "shim/tests/site/jekyll-cases.jsonl shim/tests/site/jekyll-expected.jsonl"
  "shim/tests/site/liquidcfg-cases.jsonl shim/tests/site/liquidcfg-expected.jsonl"
)
if [ -n "${SHIM_W_SUITES:-}" ]; then
  suites+=("shim/tests/yaml/suite-cases.jsonl shim/tests/yaml/suite-expected.jsonl"
           "shim/tests/yaml/typing-cases.jsonl shim/tests/yaml/typing-expected.jsonl")
fi
fail=0
for s in "${suites[@]}"; do
  set -- $s
  known=(); [ -f "$1.known" ] && known=(--known "$1.known")   # documented expected differences
  out=$(java -Xss16m -cp "$CP" org.hl7.fhir.jekyll.test.DiffRunner "$1" "$2" --show 3 "${known[@]}" 2>&1); rc=$?
  printf '%-48s %s\n' "$1" "$(echo "$out" | tail -1)"
  if [ $rc -ne 0 ]; then fail=1; echo "$out" | head -20; fi
done
exit $fail
