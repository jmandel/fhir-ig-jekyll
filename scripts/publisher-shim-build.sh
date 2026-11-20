#!/bin/bash
# P3.7: full IG Publisher run with the shim as its Jekyll (`-jekyll shim/jekyll-shim`), then compare
# the publisher's final output/ against a baseline publisher output built with real Jekyll.
# usage: scripts/publisher-shim-build.sh <repos-name> [baseline-output-dir, default work/igs/<name>/output] [-Xmx, default 6g]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$1"; BASE="${2:-$ROOT/work/igs/$NAME/output}"; XMX="${3:-6g}"
SRC="$ROOT/repos/$NAME"; WORK="$ROOT/work/igs-shim/$NAME"; LOG="$ROOT/work/igs-shim/$NAME.publisher.log"
igini=$(cd "$SRC" && find . -maxdepth 3 -name ig.ini -not -path '*/node_modules/*' | head -1); igsub=$(dirname "$igini")
rm -rf "$WORK"; mkdir -p "$WORK"; rsync -a --exclude .git "$SRC/" "$WORK/"
start=$(date +%s)
docker run --rm -v "$WORK":/home/publisher/ig -v "${FHIR_CACHE:-$ROOT/work/fhir-cache}":/home/publisher/.fhir \
  -v "$ROOT/work/bin":/opt/pub:ro -v "$ROOT/shim":/opt/shim:ro -w "/home/publisher/ig/$igsub" ig-publisher-localdev-ca \
  bash -c "java -Xmx$XMX -jar /opt/pub/publisher.jar -ig . -no-network-check -jekyll /opt/shim/jekyll-shim 2>&1 | tail -n 4000" > "$LOG" 2>&1 || true
end=$(date +%s)
echo "full_build_seconds=$((end-start))"
grep -E 'Run jekyll:|Jekyll: .*done in|Jekyll has failed|Publishing Content Failed|Finished @' "$LOG" | cut -c1-160 | head -6
grep -oE 'jekyll: [0-9:.]+' "$LOG" | tail -1 || true
echo "== final output/ vs baseline (publisher+real Jekyll):"
python3 -I "$ROOT/scripts/domdiff.py" "$BASE" "$WORK/$igsub/output" --max-report 8 --json "$ROOT/work/igs-shim/$NAME.diff.json" || true
