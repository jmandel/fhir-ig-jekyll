#!/bin/bash
# One full publisher build of an IG with the publisher-integration jar, in one -jekyll-engine mode.
# usage: pubint/run-mode.sh <repos-name> <jekyll|shim|shim-lazy> [Xmx, default 8g]
# Environment:
#   FHIR_IG_JEKYLL_ROOT  the fhir-ig-jekyll workspace (scripts/lib/vmdata.sh, repos/<name>)  [required]
#   PUBJAR               publisher jar, relative to $VMDATA/pubint (default publisher-pubint.jar)
#   TAG                  suffix for the build copy / log names (several runs of one mode)
#   JPROPS               extra JVM options, e.g. -Dig.fragments.drain=file
#   CACHE_LOWER          read-only package cache under the overlay (default $VMDATA/pubint/fhir-cache-snap)
#   PUBINT_LOGS          where logs go (default $FHIR_IG_JEKYLL_ROOT/work/pubint/runs)
#   PUBLISHER_UID/GID    owner of the overlay's upper packages dir (default: the caller's)
#   DOCKER_ENV           extra docker run options, e.g. "-e SHIM_RENDER_THREADS=1"
# Build copy on the Docker host's disk: $VMDATA/pubint/runs/<name>/<mode>$TAG; the package cache is an
# overlay of CACHE_LOWER (writes discarded). Log + memory line: $PUBINT_LOGS/<name>.<mode>$TAG.log
set -euo pipefail
ROOT="${FHIR_IG_JEKYLL_ROOT:?set FHIR_IG_JEKYLL_ROOT to the fhir-ig-jekyll workspace (scripts/, repos/)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
LOGS="${PUBINT_LOGS:-$ROOT/work/pubint/runs}"; mkdir -p "$LOGS"
. "$ROOT/scripts/lib/vmdata.sh"
NAME="$1"; MODE="$2"; XMX="${3:-8g}"
SRC="$ROOT/repos/$NAME"; W="$VMDATA/pubint/runs/$NAME/$MODE${TAG:-}"
LOG="$LOGS/$NAME.$MODE${TAG:-}.log"
igini=$(cd "$SRC" && find . -maxdepth 3 -name ig.ini -not -path '*/node_modules/*' | head -1); igsub=$(dirname "$igini")
to_vm "$SRC" "$W" --exclude .git
vol="pubint-cache-$NAME-$MODE${TAG:-}"; up="$VMDATA/tmp/pubint-cache-$NAME-$MODE${TAG:-}"
docker volume rm -f "$vol" >/dev/null 2>&1 || true
vm "rm -rf '$up' && mkdir -p '$up/upper/packages' '$up/work' && chown ${PUBLISHER_UID:-$(id -u)}:${PUBLISHER_GID:-$(id -g)} '$up/upper' '$up/upper/packages' && chmod 755 '$up/upper/packages'"
docker volume create --driver local --opt type=overlay --opt device=overlay \
  --opt "o=lowerdir=${CACHE_LOWER:-$VMDATA/pubint/fhir-cache-snap},upperdir=$up/upper,workdir=$up/work" "$vol" >/dev/null
args=""; [ "$MODE" != jekyll ] && args="-jekyll-engine $MODE"
docker run --rm ${DOCKER_ENV:-} -v "$W":/home/publisher/ig -v "$vol":/home/publisher/.fhir -v "$VMDATA/pubint":"$VMDATA/pubint" \
  -v "$HERE":/opt/s -w "/home/publisher/ig/$igsub" "$IMAGE" \
  bash -c "/opt/s/treemem.sh java -Xmx$XMX ${JPROPS:-} -jar $VMDATA/pubint/${PUBJAR:-publisher-pubint.jar} -ig . -no-network-check $args > /home/publisher/ig/publisher.log 2>&1; tail -n 3 /home/publisher/ig/publisher.log" \
  > "$LOG.mem" 2>&1 || true
docker volume rm -f "$vol" >/dev/null 2>&1 || true
vm "rm -rf '$up'; cat '$W/publisher.log'" > "$LOG" || true
cat "$LOG.mem"
grep -E 'Run jekyll:|Jekyll \(in process\)|Fragments in memory|Jekyll has failed|Publishing Content Failed|Finished @|Times:' "$LOG" | cut -c1-400 | tail -8 || true
