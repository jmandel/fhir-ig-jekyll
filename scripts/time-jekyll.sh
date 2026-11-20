#!/bin/bash
# P0.12: run real Jekyll with the timing plugin on one golden -> work/timing/<name>.jsonl
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; n="$1"
rm -f "$ROOT/work/timing/$n.jsonl"
docker run --rm --entrypoint bash -v "$ROOT":/w -e TIMING_OUT=/w/work/timing/$n.jsonl -w /w/work/golden/$n/jekyll-in ig-publisher-localdev-ca \
  -c 'rm -rf .jekyll-cache; s=$(date +%s.%N); jekyll build --plugins /w/shim/oracle/timing-plugin --destination /tmp/out >/dev/null 2>&1; e=$(date +%s.%N); rm -rf .jekyll-cache; echo "total $(echo "$e - $s" | bc)"' 
