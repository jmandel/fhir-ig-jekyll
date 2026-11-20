#!/bin/bash
# One-off: move the heavy golden trees and the package cache from the Mac's work/ onto the VM disk
# (scripts/lib/vmdata.sh). Each tree is streamed (tar over the Docker socket, so the Mac reads
# natively), the file count is verified on the VM, and only then is the Mac copy moved aside to
# work/golden-archive/<name>/ (nothing is deleted). Derived shim outputs are archived, not copied.
# usage: scripts/vm-migrate.sh [-j N]
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/vmdata.sh"
J=4; [ "${1:-}" = "-j" ] && J="$2"
vm_init

one() {
  n="$1"; g="$ROOT/work/golden/$n"; a="$ROOT/work/golden-archive/$n"
  for d in jekyll-in jekyll-out; do
    [ -d "$g/$d" ] || continue
    [ -n "$(ls -A "$g/$d" 2>/dev/null)" ] || continue          # empty dir = an existing mount point
    want=$(find "$g/$d" -type f | wc -l | tr -d ' ')
    to_vm "$g/$d" "$VMDATA/golden/$n/$d" || { echo "FAIL copy $n/$d"; return; }
    got=$(vm "find '$VMDATA/golden/$n/$d' -type f | wc -l" | tr -d ' ')
    if [ "$want" != "$got" ]; then echo "FAIL count $n/$d host=$want vm=$got"; return; fi
    mkdir -p "$a"; mv "$g/$d" "$a/$d"
  done
  for d in shim-out shim-out2 shim-mem; do
    [ -d "$g/$d" ] && [ -n "$(ls -A "$g/$d" 2>/dev/null)" ] && { mkdir -p "$a"; mv "$g/$d" "$a/$d"; }
  done
  echo "ok $n"
}
export -f one to_vm vm; export ROOT VMDATA IMAGE

ls "$ROOT/work/golden" | xargs -P "$J" -I{} bash -c 'one "$1"' _ {}

# package cache (kept on the Mac too)
if [ -d "$ROOT/work/fhir-cache/packages" ]; then
  want=$(find "$ROOT/work/fhir-cache" -type f | wc -l | tr -d ' ')
  to_vm "$ROOT/work/fhir-cache" "$VMDATA/fhir-cache"
  got=$(vm "find '$VMDATA/fhir-cache' -type f | wc -l" | tr -d ' ')
  echo "fhir-cache host=$want vm=$got"
fi
vm "du -sh '$VMDATA'/* ; df -h '$VMDATA' | tail -1"
