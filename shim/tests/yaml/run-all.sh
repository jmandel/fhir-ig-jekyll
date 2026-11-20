#!/bin/bash
# Run every YAML-port suite against its oracle expectations (build first: shim/build.sh yaml).
# usage: shim/tests/yaml/run-all.sh [--regen]   (--regen: regenerate cases and re-run the Ruby oracle)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
if [ "${1:-}" = --regen ]; then
  python3 -I shim/tests/yaml/gen_suite_cases.py
  python3 -I shim/tests/yaml/gen_typing_cases.py
  shim/oracle/oracle.sh shim/tests/yaml/suite-cases.jsonl shim/tests/yaml/suite-expected.jsonl
  shim/oracle/oracle.sh shim/tests/yaml/typing-cases.jsonl shim/tests/yaml/typing-expected.jsonl
fi
run() { printf '%-28s ' "$1"; BUILD=build-yaml shim/run.sh org.hl7.fhir.jekyll.test.yaml.YamlDiff "$2" "$3" --show 0 2>&1 | tail -1 || true; }
run "real _config/_data yaml" shim/tests/site/yaml-cases.jsonl shim/tests/site/yaml-expected.jsonl
run "real _data files" shim/tests/site/data-cases.jsonl shim/tests/site/data-expected.jsonl
run "yaml-test-suite" shim/tests/yaml/suite-cases.jsonl shim/tests/yaml/suite-expected.jsonl
run "typing suite" shim/tests/yaml/typing-cases.jsonl shim/tests/yaml/typing-expected.jsonl
run "real front matter" shim/tests/yaml/frontmatter-cases.jsonl shim/tests/yaml/frontmatter-expected.jsonl
