#!/bin/bash
# One differential fuzz round: shim/tests/rouge/fuzzround.sh <seed> <per-lexer> [build-dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"; cd "$ROOT"
S=$1; N=$2; B=${3:-build-rouge}; D=shim/tests/rouge/fuzz
mkdir -p $D
python3 shim/tests/rouge/fuzz.py $S $N > $D/cases-$S.jsonl
shim/oracle/oracle.sh $D/cases-$S.jsonl $D/expected-$S.jsonl
BUILD=$B shim/run.sh org.hl7.fhir.jekyll.test.DiffRunner $D/cases-$S.jsonl $D/expected-$S.jsonl --no-d7 --show 0 --report $D/report-$S.jsonl | tail -1 || true
