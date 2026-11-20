#!/bin/bash
# Run many IGs through scripts/golden-build.sh in parallel.
#
# usage: golden-batch.sh [-j N] [-m HEAP] [-s] [-f LIST] [name ...]
#   -j N     parallel builds (default 4)
#   -m HEAP  per-build Java heap, "-Xmx6g" or "6g" (default -Xmx6g)
#   -s       share one package cache between jobs (default: isolated, see below)
#   -f LIST  file with one repo name per line (# comments and blank lines ignored); may be
#            combined with names given as arguments.
# Package caches: by default each job sees the shared cache ($VMDATA/fhir-cache, on the VM disk)
# through its own overlay volume (shared cache = read-only lower layer, the job's writes go to an
# upper layer), so concurrent builds can't collide on package locks or delete each other's
# packages. Complete packages a job downloaded are merged back afterwards (never overwriting).
#
# Daemon: set DOCKER_CONTEXT=igbuild (Lima VM, see lima/README.md) or DOCKER_HOST; unset =
# current docker context. Example:
#   DOCKER_CONTEXT=igbuild scripts/golden-batch.sh -j 8 -f names.txt
#
# Outputs: work/logs/<name>.log (console of golden-build.sh), work/golden/<name>/ (as golden-build.sh),
#          work/batch-summary.tsv  (name, valid, full_build_seconds, jekyll_seconds, exit)
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/vmdata.sh"
JOBS=4; HEAP=-Xmx6g; ISOLATE=1; NAMES=()
while getopts "j:m:sf:h" o; do
  case "$o" in
    j) JOBS="$OPTARG" ;;
    m) HEAP="$OPTARG" ;;
    s) ISOLATE=0 ;;
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
    local vol="igcache-$n" up="$VMDATA/tmp/cache-$n"
    docker volume rm -f "$vol" >/dev/null 2>&1 || true
    vm "rm -rf '$up' && mkdir -p '$up/upper' '$up/work'"
    docker volume create --driver local --opt type=overlay --opt device=overlay \
      --opt "o=lowerdir=$VMDATA/fhir-cache,upperdir=$up/upper,workdir=$up/work" "$vol" >/dev/null
    FHIR_CACHE="$vol" "$ROOT/scripts/golden-build.sh" "$n" "$XMX" > "$log" 2>&1; rc=$?
    docker volume rm -f "$vol" >/dev/null 2>&1 || true
    # merge back complete packages this job downloaded (the publisher renames a package dir into
    # place only once it is fully extracted, so a present dir is complete). Never overwrite.
    vm "cd '$up/upper/packages' 2>/dev/null || exit 0
        for b in *; do
          [ -d \"\$b\" ] || continue
          case \"\$b\" in *.lock|*.tmp|*-tmp*) continue ;; esac
          [ -e '$VMDATA/fhir-cache/packages/'\"\$b\" ] && continue
          t='$VMDATA/fhir-cache/packages/.'\"\$b\".merge.\$\$
          cp -a \"\$b\" \"\$t\" && { mv -nT \"\$t\" '$VMDATA/fhir-cache/packages/'\"\$b\" 2>/dev/null || true; }
          rm -rf \"\$t\"
        done" || true
    vm "rm -rf '$up'"
  else
    "$ROOT/scripts/golden-build.sh" "$n" "$XMX" > "$log" 2>&1; rc=$?
  fi
  echo "exit=$rc" >> "$log"
  t1=$(date +%s)
  echo "[$(date +%H:%M:%S)] done  $n rc=$rc $((t1-t0))s"
}
export -f one vm; export ROOT LOGS XMX ISOLATE VMDATA IMAGE

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
