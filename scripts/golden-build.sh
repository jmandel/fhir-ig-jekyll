#!/bin/bash
# Build one sampled IG in the autobuilder image and capture a Jekyll golden pair.
# usage: golden-build.sh <repos-dir-name> [java -Xmx, default 6g]
# Produces, on the VM disk (scripts/lib/vmdata.sh): $VMDATA/golden/<name>/{jekyll-in,jekyll-out} and the
# build copy $VMDATA/igs/<name>; on the Mac: work/golden/<name>/{publisher.log,jekyll.log,timing.txt,VALID}.
# Daemon: whatever the docker CLI resolves (DOCKER_CONTEXT=igbuild for the Lima VM, see lima/README.md).
# FHIR_CACHE=<vm-dir or docker volume> overrides the package cache mounted as ~/.fhir (default
# $VMDATA/fhir-cache; golden-batch.sh gives each job its own overlay of it).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/vmdata.sh"
NAME="$1"; XMX="${2:-6g}"
SRC="$ROOT/repos/$NAME"
GOLD="$ROOT/work/golden/$NAME"          # small files, on the Mac
VW="$VMDATA/igs/$NAME"                  # build copy, on the VM
VG="$VMDATA/golden/$NAME"               # golden trees, on the VM
CACHE="${FHIR_CACHE:-$VMDATA/fhir-cache}"

igini=$(cd "$SRC" && find . -maxdepth 3 -name ig.ini -not -path '*/node_modules/*' | head -1)
[ -n "$igini" ] || { echo "no ig.ini in $NAME"; exit 1; }
igsub=$(dirname "$igini")

rm -rf "$GOLD"; mkdir -p "$GOLD"
vm "rm -rf '$VG'"
to_vm "$SRC" "$VW" --exclude .git

start=$(date +%s)
docker run --rm \
  -v "$VW":/home/publisher/ig \
  -v "$CACHE":/home/publisher/.fhir \
  -v "$ROOT/work/bin":/opt/pub:ro \
  -w "/home/publisher/ig/$igsub" \
  "$IMAGE" \
  bash -c "java -Xmx$XMX -jar /opt/pub/publisher.jar -ig . -no-network-check 2>&1 | tail -n 4000" \
  > "$GOLD/publisher.log" 2>&1 || echo "publisher exited non-zero (see log)"
end=$(date +%s)

# A golden is only valid if the publisher actually reached its Jekyll step (otherwise temp/pages is partial).
if ! grep -q 'Run jekyll:' "$GOLD/publisher.log"; then
  echo "INVALID golden for $NAME: publisher never reached Jekyll"; grep -m3 -E 'Publishing Content Failed|Exception' "$GOLD/publisher.log" || true; exit 1
fi

# Capture temp/pages and re-run Jekyll alone on it for a clean golden output + timing, all on the VM disk.
rc=0
docker run --rm --entrypoint bash -v "$VMDATA:$VMDATA" "$IMAGE" -c "
  set -e
  pages='$VW/$igsub/temp/pages'
  [ -d \"\$pages\" ] || { echo 'no temp/pages produced'; exit 3; }
  mkdir -p '$VG/jekyll-in' '$VG/jekyll-out'
  cp -a \"\$pages/.\" '$VG/jekyll-in/'
  rm -rf '$VG/jekyll-in/.jekyll-cache' '$VG/jekyll-in/.jekyll-metadata'
  cd '$VG/jekyll-in'
  jekyll -v; TIMEFORMAT='Elapsed %R s (user %U, sys %S)'; time jekyll build --destination '$VG/jekyll-out' 2>&1 || echo 'standalone jekyll exited non-zero'
  rm -rf .jekyll-cache
  echo \"jekyll_in_files=\$(find '$VG/jekyll-in' -type f | wc -l)\"
  echo \"jekyll_out_files=\$(find '$VG/jekyll-out' -type f | wc -l)\"
" > "$GOLD/jekyll.log" 2>&1 || rc=$?
if [ "$rc" = 3 ]; then echo "no temp/pages produced for $NAME"; exit 1; fi
touch "$GOLD/VALID"

{
  echo "full_build_seconds=$((end-start))"
  echo "docker_daemon=${DOCKER_HOST:-${DOCKER_CONTEXT:-$(docker context show 2>/dev/null || echo default)}}"
  grep -E 'jekyll' "$GOLD/publisher.log" | grep -iE 'took|done in|time' | head -5 || true
  grep -E '^jekyll [0-9]|done in|Elapsed|Maximum resident|^jekyll_(in|out)_files=' "$GOLD/jekyll.log" || true
} > "$GOLD/timing.txt"
cat "$GOLD/timing.txt"
