#!/bin/bash
# Run many IGs through scripts/golden-build.sh in parallel.
#
# usage: golden-batch.sh [-j N] [-m HEAP] [-i] [-f LIST] [name ...]
#   -j N     parallel builds (default 4)
#   -m HEAP  per-build Java heap, "-Xmx6g" or "6g" (default -Xmx6g)
#   -i       isolated package caches: each job gets an APFS clone (cp -c, copy-on-write) of
#            work/fhir-cache under work/igs/<name>.fhir-cache; packages it downloads are merged back
#            into the shared cache afterwards. Use when another daemon (e.g. Docker Desktop) is
#            building against work/fhir-cache at the same time (file locks don't cross VMs).
#   -f LIST  file with one repo name per line (# comments and blank lines ignored); may be
#            combined with names given as arguments.
#
# Daemon: set DOCKER_CONTEXT=igbuild (Lima VM, see lima/README.md) or DOCKER_HOST; unset =
# current docker context. Example:
#   DOCKER_CONTEXT=igbuild scripts/golden-batch.sh -j 8 -f names.txt
#
# Outputs: work/logs/<name>.log (console of golden-build.sh), work/golden/<name>/ (as golden-build.sh),
#          work/batch-summary.tsv  (name, valid, full_build_seconds, jekyll_seconds, exit)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOBS=4; HEAP=-Xmx6g; ISOLATE=0; NAMES=()
while getopts "j:m:if:h" o; do
  case "$o" in
    j) JOBS="$OPTARG" ;;
    m) HEAP="$OPTARG" ;;
    i) ISOLATE=1 ;;
    f) while IFS= read -r l || [ -n "$l" ]; do
         l="${l%%#*}"; l="$(echo "$l" | tr -d '[:space:]')"; [ -n "$l" ] && NAMES+=("$l")
       done < "$OPTARG" ;;
    *) sed -n '2,20p' "$0"; exit 2 ;;
  esac
done
shift $((OPTIND-1))
NAMES+=("$@")
[ ${#NAMES[@]} -gt 0 ] || { echo "no IG names given" >&2; exit 2; }
XMX="${HEAP#-Xmx}"
LOGS="$ROOT/work/logs"; SUMMARY="$ROOT/work/batch-summary.tsv"
mkdir -p "$LOGS"
for n in "${NAMES[@]}"; do [ -d "$ROOT/repos/$n" ] || { echo "no such repo: repos/$n" >&2; exit 2; }; done
# duplicates would make two jobs share work/igs/<name>
dups=$(printf '%s\n' "${NAMES[@]}" | sort | uniq -d)
[ -z "$dups" ] || { echo "duplicate names: $dups" >&2; exit 2; }

daemon="${DOCKER_HOST:-${DOCKER_CONTEXT:-$(docker context show 2>/dev/null)}}"
docker image inspect ig-publisher-localdev-ca >/dev/null 2>&1 \
  || { echo "image ig-publisher-localdev-ca not found on daemon '$daemon'" >&2; exit 2; }
echo "batch: ${#NAMES[@]} IGs, -j $JOBS, -Xmx$XMX, daemon=$daemon, isolated_cache=$ISOLATE"

one() {  # one <name>  -> runs a build, writes $LOGS/<name>.log
  local n="$1" log="$LOGS/$1.log" rc t0 t1
  t0=$(date +%s)
  echo "[$(date +%H:%M:%S)] start $n"
  if [ "$ISOLATE" = 1 ]; then
    local jc="$ROOT/work/igs/$n.fhir-cache"
    rm -rf "$jc"; mkdir -p "$(dirname "$jc")"
    cp -cR "$ROOT/work/fhir-cache" "$jc" 2>/dev/null || cp -R "$ROOT/work/fhir-cache" "$jc"
    FHIR_CACHE="$jc" "$ROOT/scripts/golden-build.sh" "$n" "$XMX" > "$log" 2>&1; rc=$?
    # merge back complete packages this job downloaded (the publisher renames a package dir into
    # place only once it is fully extracted, so a present dir is complete). Never overwrite.
    for p in "$jc"/packages/*/; do
      p="${p%/}"; b="$(basename "$p")"
      [ -e "$ROOT/work/fhir-cache/packages/$b" ] && continue
      case "$b" in *.lock|*.tmp|*-tmp*) continue ;; esac
      cp -cR "$p" "$ROOT/work/fhir-cache/packages/.$b.merge.$$" 2>/dev/null \
        || cp -R "$p" "$ROOT/work/fhir-cache/packages/.$b.merge.$$"
      mv -n "$ROOT/work/fhir-cache/packages/.$b.merge.$$" "$ROOT/work/fhir-cache/packages/$b" 2>/dev/null \
        || rm -rf "$ROOT/work/fhir-cache/packages/.$b.merge.$$"
      rm -rf "$ROOT/work/fhir-cache/packages/.$b.merge.$$"
    done
    rm -rf "$jc"
  else
    "$ROOT/scripts/golden-build.sh" "$n" "$XMX" > "$log" 2>&1; rc=$?
  fi
  echo "exit=$rc" >> "$log"
  t1=$(date +%s)
  echo "[$(date +%H:%M:%S)] done  $n rc=$rc $((t1-t0))s"
}
export -f one; export ROOT LOGS XMX ISOLATE

printf '%s\n' "${NAMES[@]}" | xargs -P "$JOBS" -I{} bash -c 'one "$1"' _ {}

# summary
{
  printf 'name\tvalid\tfull_build_seconds\tjekyll_seconds\texit\n'
  for n in "${NAMES[@]}"; do
    g="$ROOT/work/golden/$n"; t="$g/timing.txt"
    valid=invalid; [ -f "$g/VALID" ] && valid=valid
    full=$(grep -m1 '^full_build_seconds=' "$t" 2>/dev/null | cut -d= -f2)
    jek=$(grep -m1 -oE 'Elapsed [0-9.]+' "$t" 2>/dev/null | awk '{print $2}')
    rc=$(grep -m1 -oE '^exit=[0-9]+' "$LOGS/$n.log" 2>/dev/null | cut -d= -f2)
    printf '%s\t%s\t%s\t%s\t%s\n' "$n" "$valid" "${full:--}" "${jek:--}" "${rc:--}"
  done
} > "$SUMMARY"
column -t -s $'\t' "$SUMMARY"
