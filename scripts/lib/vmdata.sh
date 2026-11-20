# shellcheck shell=bash
# Heavy data lives on the Docker host's own disk (the Lima VM), not in the folder shared from the Mac.
#
# Why: Lima's virtiofs (Apple Virtualization.framework) keeps one macOS file handle open for every file
# the VM has cached, and macOS caps open files system-wide (kern.maxfiles = 491,520). IG builds,
# goldens and the package cache are millions of files, so doing that I/O through the shared folder
# eventually exhausts the Mac's file table (ENFILE) for every process on it. On the VM's own disk it
# costs the Mac nothing, so builds can run fully in parallel.
#
# Layout on the VM (VMDATA, default /srv/ig):
#   golden/<name>/{jekyll-in,jekyll-out,shim-out}   heavy halves of the goldens
#   igs/<name>, igs-shim/<name>                      publisher build copies
#   fhir-cache/                                      shared package cache (~/.fhir)
#   tmp/                                             per-job overlay layers etc.
# Small results (logs, VALID, timing.txt, *-diff.json, summaries) stay under $ROOT/work on the Mac.
# Containers mount $ROOT at its own path and the VM dirs over the usual paths, so paths don't change.
#
# Usage: . "$ROOT/scripts/lib/vmdata.sh"
VMDATA="${VMDATA:-/srv/ig}"
IMAGE="${IMAGE:-ig-publisher-localdev-ca}"

# Run a shell snippet in the reference image with VMDATA mounted.
vm() { docker run --rm -i -v "$VMDATA:$VMDATA" --entrypoint bash "$IMAGE" -c "$1"; }

# Create the layout (idempotent).
vm_init() {
  docker run --rm -v "$(dirname "$VMDATA"):$(dirname "$VMDATA")" --entrypoint mkdir "$IMAGE" -p \
    "$VMDATA/golden" "$VMDATA/igs" "$VMDATA/igs-shim" "$VMDATA/fhir-cache/packages" "$VMDATA/tmp"
}

# to_vm <host-dir> <vm-dir> [tar options...]: replace vm-dir with a copy of host-dir. The Mac reads
# the files natively and streams them over the Docker socket, so no virtiofs handles are involved.
to_vm() {
  local src="$1" dst="$2"; shift 2
  COPYFILE_DISABLE=1 tar -C "$src" --no-mac-metadata --no-xattrs "$@" -cf - . | docker run --rm -i -v "$VMDATA:$VMDATA" --entrypoint bash "$IMAGE" \
    -c "rm -rf '$dst' && mkdir -p '$dst' && tar -xf - -C '$dst'"
}

# from_vm <vm-dir> <host-dir>: copy a VM dir back to the Mac (for inspection).
from_vm() {
  mkdir -p "$2"
  docker run --rm -v "$VMDATA:$VMDATA" --entrypoint tar "$IMAGE" -C "$1" -cf - . | tar -xf - -C "$2"
}

# golden_mounts <name>: sets GM=(-v ... ) so the golden's heavy dirs appear under $ROOT/work/golden/<name>/.
golden_mounts() {
  local d
  GM=()
  for d in jekyll-in jekyll-out shim-out; do
    GM+=(-v "$VMDATA/golden/$1/$d:$ROOT/work/golden/$1/$d")
  done
}
