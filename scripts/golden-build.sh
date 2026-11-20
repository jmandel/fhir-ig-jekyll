#!/bin/bash
# Build one sampled IG in the autobuilder image and capture a Jekyll golden pair.
# usage: golden-build.sh <repos-dir-name> [java -Xmx, default 6g]
# Produces work/golden/<name>/{jekyll-in,jekyll-out,publisher.log,jekyll.log,timing.txt}
# Daemon: uses whatever the docker CLI resolves. Set DOCKER_CONTEXT=igbuild (Lima VM, see lima/README.md)
# or DOCKER_HOST=unix://... to run elsewhere; unset = current context (Docker Desktop). The image
# ig-publisher-localdev-ca must exist on that daemon.
# FHIR_CACHE=<dir> overrides the package cache mounted as ~/.fhir (default work/fhir-cache; golden-batch.sh -i).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="$1"; XMX="${2:-6g}"
IMAGE=ig-publisher-localdev-ca
SRC="$ROOT/repos/$NAME"
WORK="$ROOT/work/igs/$NAME"
GOLD="$ROOT/work/golden/$NAME"
CACHE="${FHIR_CACHE:-$ROOT/work/fhir-cache}"

igini=$(cd "$SRC" && find . -maxdepth 3 -name ig.ini -not -path '*/node_modules/*' | head -1)
[ -n "$igini" ] || { echo "no ig.ini in $NAME"; exit 1; }
igsub=$(dirname "$igini")

rm -rf "$WORK" "$GOLD"; mkdir -p "$WORK" "$GOLD"
rsync -a --exclude .git "$SRC/" "$WORK/"

start=$(date +%s)
docker run --rm \
  -v "$WORK":/home/publisher/ig \
  -v "$CACHE":/home/publisher/.fhir \
  -v "$ROOT/work/bin":/opt/pub:ro \
  -w "/home/publisher/ig/$igsub" \
  "$IMAGE" \
  bash -c "java -Xmx$XMX -jar /opt/pub/publisher.jar -ig . -no-network-check 2>&1 | tail -n 4000" \
  > "$GOLD/publisher.log" 2>&1 || echo "publisher exited non-zero (see log)"
end=$(date +%s)

pages="$WORK/$igsub/temp/pages"
if [ ! -d "$pages" ]; then echo "no temp/pages produced for $NAME"; exit 1; fi
# A golden is only valid if the publisher actually reached its Jekyll step (otherwise temp/pages is partial).
if ! grep -q 'Run jekyll:' "$GOLD/publisher.log"; then
  echo "INVALID golden for $NAME: publisher never reached Jekyll"; grep -m3 -E 'Publishing Content Failed|Exception' "$GOLD/publisher.log" || true; exit 1
fi
touch "$GOLD/VALID"
rsync -a --exclude .jekyll-cache --exclude .jekyll-metadata "$pages/" "$GOLD/jekyll-in/"

# Re-run Jekyll alone on the captured input for a clean golden output + timing.
docker run --rm --entrypoint bash \
  -v "$GOLD":/g \
  -w /g/jekyll-in \
  "$IMAGE" \
  -c 'jekyll -v; TIMEFORMAT="Elapsed %R s (user %U, sys %S)"; time jekyll build --destination /g/jekyll-out 2>&1' \
  > "$GOLD/jekyll.log" 2>&1 || echo "standalone jekyll exited non-zero (see log)"

{
  echo "full_build_seconds=$((end-start))"
  echo "docker_daemon=${DOCKER_HOST:-${DOCKER_CONTEXT:-$(docker context show 2>/dev/null || echo default)}}"
  grep -E 'jekyll' "$GOLD/publisher.log" | grep -iE 'took|done in|time' | head -5 || true
  grep -E '^jekyll [0-9]|done in|Elapsed|Maximum resident' "$GOLD/jekyll.log" || true
  echo "jekyll_in_files=$(find "$GOLD/jekyll-in" -type f | wc -l | tr -d ' ')"
  echo "jekyll_out_files=$(find "$GOLD/jekyll-out" -type f 2>/dev/null | wc -l | tr -d ' ')"
} > "$GOLD/timing.txt"
cat "$GOLD/timing.txt"
