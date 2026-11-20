#!/bin/bash
# Run a Java main from the shim inside the reference image.
# usage: [BUILD=build-liquid] shim/run.sh <main-class> [args...]
#   Paths under the repo root are visible in the container as /w/<relative path>;
#   the working directory is /w, so relative paths from the root work unchanged.
# Daemon: honors DOCKER_CONTEXT / DOCKER_HOST (docker CLI env), e.g. DOCKER_CONTEXT=igbuild for the Lima VM; unset = as before.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
B="${BUILD:-build}"
CLS="$1"; shift
docker run --rm -i --entrypoint java -v "$ROOT":/w -w /w ig-publisher-localdev-ca \
  -Xss16m -Xmx4g -cp "/w/shim/$B/classes:/w/shim/$B/test-classes" "$CLS" "$@"
