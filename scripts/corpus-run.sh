#!/bin/bash
# P1.4: build goldens for many IGs on the Lima VM in chunks, keeping disk bounded.
# usage: scripts/corpus-run.sh <names-file> [-j N] [-m HEAP] [-c CHUNK]
#   After each chunk: append work/batch-summary.tsv to work/corpus-summary.tsv, delete the build
#   copies work/igs/<name> (the golden keeps jekyll-in/jekyll-out/logs), log to progress/corpus.log.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIST="$1"; shift
J=8; M=8g; C=32
while getopts "j:m:c:" o; do case $o in j) J=$OPTARG;; m) M=$OPTARG;; c) C=$OPTARG;; *) exit 2;; esac; done
export DOCKER_CONTEXT="${DOCKER_CONTEXT:-igbuild}"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) | $1 | $2" >> "$ROOT/progress/corpus.log"; }
NAMES=()
while IFS= read -r line; do NAMES+=("$line"); done < <(grep -vE '^[[:space:]]*(#|$)' "$LIST")  # bash 3.2: no mapfile
total=${#NAMES[@]}
[ -f "$ROOT/work/corpus-summary.tsv" ] || printf 'name\tvalid\tfull_build_seconds\tjekyll_seconds\texit\n' > "$ROOT/work/corpus-summary.tsv"
log started "corpus run: $total IGs, -j $J, -m $M, chunks of $C, daemon $DOCKER_CONTEXT"
done_n=0
for ((s = 0; s < total; s += C)); do
  chunk=("${NAMES[@]:s:C}")
  "$ROOT/scripts/golden-batch.sh" -j "$J" -m "$M" "${chunk[@]}" > "$ROOT/work/logs/corpus-chunk-$s.log" 2>&1 || true
  tail -n +2 "$ROOT/work/batch-summary.tsv" >> "$ROOT/work/corpus-summary.tsv"
  for n in "${chunk[@]}"; do rm -rf "$ROOT/work/igs/$n" "$ROOT/work/igs/$n.fhir-cache"; done
  done_n=$((done_n + ${#chunk[@]}))
  valid=$(tail -n +2 "$ROOT/work/corpus-summary.tsv" | awk -F'\t' '$2=="valid"' | wc -l | tr -d ' ')
  log milestone "chunk done: $done_n/$total built; valid so far $valid; disk free $(df -h / | tail -1 | awk '{print $4}')"
done
log done "corpus run finished: $done_n IGs; valid $(tail -n +2 "$ROOT/work/corpus-summary.tsv" | awk -F'\t' '$2=="valid"' | wc -l | tr -d ' ')"
