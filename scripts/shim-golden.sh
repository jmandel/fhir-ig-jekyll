#!/bin/bash
# P1.3: run the shim (as the publisher would: `build --destination X` from jekyll-in) on a golden
# and compare its output with real Jekyll's using the normalized-DOM rule (scripts/domdiff.py).
# usage: scripts/shim-golden.sh <golden-name> [build-dir, default build-site]
# Writes work/golden/<name>/shim-out, shim.log, shim-diff.json; prints the domdiff summary.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$1"; B="${2:-build-site}"
G="$ROOT/work/golden/$NAME"
[ -d "$G/jekyll-in" ] || { echo "no golden $NAME"; exit 1; }
rm -rf "$G/shim-out" "$G/jekyll-in/.jekyll-cache"
# SHIM_XMX (default 6g) and SHIM_JAVA_OPTS (e.g. -Xlog:gc) tune the JVM; DOCKER_CONTEXT picks the daemon.
docker run --rm --entrypoint bash -e SHIM_TIMING=1 -v "$ROOT":/w -w "/w/work/golden/$NAME/jekyll-in" ig-publisher-localdev-ca -c \
  "TIMEFORMAT='shim_elapsed=%R'; time java -Xss16m -Xmx${SHIM_XMX:-6g} ${SHIM_JAVA_OPTS:-} -cp /w/shim/$B/classes org.hl7.fhir.jekyll.site.Main build --destination /w/work/golden/$NAME/shim-out; echo exit=\$?" \
  > "$G/shim.log" 2>&1 || echo "shim exited non-zero (see $G/shim.log)"
grep -E 'done in|Liquid Exception|Error|shim_elapsed' "$G/shim.log" | head -5
# DOMDIFF_OPTS (e.g. --no-d7: compare Rouge token spans too) is passed to domdiff.py
python3 -I "$ROOT/scripts/domdiff.py" "$G/jekyll-out" "$G/shim-out" --json "$G/shim-diff.json" --max-report 12 ${DOMDIFF_OPTS:-} || true
