#!/bin/bash
# One full publisher build of an IG with the publisher-integration jar, in one -jekyll-engine mode.
# usage: work/pubint/scripts/run-mode.sh <repos-name> <jekyll|shim|shim-lazy> [Xmx, default 8g]
# Build copy on the VM disk: /srv/ig/pubint/runs/<name>/<mode>; the package cache is an overlay of
# /srv/ig/fhir-cache (writes discarded). Log + memory line on the Mac: work/pubint/runs/<name>.<mode>.log
set -euo pipefail
ROOT=/Users/jmandel1/fhir/jekyll-surface
. "$ROOT/scripts/lib/vmdata.sh"
NAME="$1"; MODE="$2"; XMX="${3:-8g}"
SRC="$ROOT/repos/$NAME"; W="$VMDATA/pubint/runs/$NAME/$MODE${TAG:-}"
LOG="$ROOT/work/pubint/runs/$NAME.$MODE${TAG:-}.log"
igini=$(cd "$SRC" && find . -maxdepth 3 -name ig.ini -not -path '*/node_modules/*' | head -1); igsub=$(dirname "$igini")
to_vm "$SRC" "$W" --exclude .git
vol="pubint-cache-$NAME-$MODE${TAG:-}"; up="$VMDATA/tmp/pubint-cache-$NAME-$MODE${TAG:-}"
docker volume rm -f "$vol" >/dev/null 2>&1 || true
vm "rm -rf '$up' && mkdir -p '$up/upper/packages' '$up/work' && chown 502:50 '$up/upper' '$up/upper/packages' && chmod 755 '$up/upper/packages'"
docker volume create --driver local --opt type=overlay --opt device=overlay \
  --opt "o=lowerdir=${CACHE_LOWER:-$VMDATA/pubint/fhir-cache-snap},upperdir=$up/upper,workdir=$up/work" "$vol" >/dev/null
args=""; [ "$MODE" != jekyll ] && args="-jekyll-engine $MODE"
docker run --rm -v "$W":/home/publisher/ig -v "$vol":/home/publisher/.fhir -v "$VMDATA/pubint":"$VMDATA/pubint" \
  -v "$ROOT/work/pubint/scripts":/opt/s -w "/home/publisher/ig/$igsub" "$IMAGE" \
  bash -c "/opt/s/treemem.sh java -Xmx$XMX ${JPROPS:-} -jar $VMDATA/pubint/${PUBJAR:-publisher-pubint2.jar} -ig . -no-network-check $args > /home/publisher/ig/publisher.log 2>&1; tail -n 3 /home/publisher/ig/publisher.log" \
  > "$LOG.mem" 2>&1 || true
docker volume rm -f "$vol" >/dev/null 2>&1 || true
vm "rm -rf '$up'; cat '$W/publisher.log'" > "$LOG" || true
cat "$LOG.mem"
grep -E 'Run jekyll:|Jekyll \(in process\)|Fragments in memory|Jekyll has failed|Publishing Content Failed|Finished @|Times:' "$LOG" | cut -c1-400 | tail -8 || true
