#!/bin/bash
# Compile the shim (no external deps, Java 17 language level) inside the reference image; the host has no JDK.
#
# usage: shim/build.sh [module]
#   no module : compile everything            -> shim/build/{classes,test-classes}, shim/build/shim.jar
#   module    : compile util + that module only -> shim/build-<module>/{classes,test-classes}
#               (module = liquid | kramdown | site | yaml | rouge). Lets parallel workers build without
#               tripping over each other's half-finished code.
# Uses a local JDK (17+) when `javac` is on PATH; otherwise the reference Docker image. Force Docker
# with SHIM_BUILD_DOCKER=1. Docker daemon: honors DOCKER_CONTEXT / DOCKER_HOST (e.g. DOCKER_CONTEXT=igbuild).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MOD="${1:-}"
SCRIPT='
set -e
P=org/hl7/fhir/jekyll
if [ -z "$MOD" ]; then OUT=build; MAIN="src/main/java"; TEST="src/test/java";
else OUT=${BUILD_OUT:-build-$MOD}; MAIN="src/main/java/$P/util src/main/java/$P/$MOD"; TEST="src/test/java/$P/test/DiffRunner.java src/test/java/$P/test/$MOD";
  # site (the Jekyll glue) depends on both ports
  if [ "$MOD" = site ]; then MAIN="$MAIN src/main/java/$P/liquid src/main/java/$P/kramdown src/main/java/$P/yaml"; fi
  # yaml (the SafeYAML/Psych/libyaml port) yields org.hl7.fhir.jekyll.liquid.RubyTime values
  if [ "$MOD" = yaml ]; then MAIN="$MAIN src/main/java/$P/liquid"; fi
  # rouge (the Rouge 4.7.0 port) is used by kramdown (fenced code) and site ({% highlight %})
  if [ "$MOD" = kramdown ] || [ "$MOD" = site ]; then MAIN="$MAIN src/main/java/$P/rouge"; TEST="$TEST src/test/java/$P/test/rouge"; fi; fi
rm -rf $OUT && mkdir -p $OUT/classes $OUT/test-classes
find $MAIN -name "*.java" 2>/dev/null > $OUT/main-sources.txt
javac -encoding UTF-8 -Xlint:-options --release 17 -d $OUT/classes @$OUT/main-sources.txt
find $TEST -name "*.java" 2>/dev/null > $OUT/test-sources.txt || true
if [ -s $OUT/test-sources.txt ]; then javac -encoding UTF-8 -Xlint:-options --release 17 -cp $OUT/classes -d $OUT/test-classes @$OUT/test-sources.txt; fi
if [ -z "$MOD" ]; then jar --create --file $OUT/shim.jar --main-class org.hl7.fhir.jekyll.site.Main -C $OUT/classes .; fi
echo "built $OUT"
'
if [ -z "${SHIM_BUILD_DOCKER:-}" ] && command -v javac >/dev/null 2>&1 && command -v jar >/dev/null 2>&1; then
  (cd "$HERE" && MOD="$MOD" BUILD_OUT="${BUILD_OUT:-}" bash -c "$SCRIPT")
else
  docker run --rm --entrypoint bash -v "$HERE":/shim -w /shim -e MOD="$MOD" -e BUILD_OUT="${BUILD_OUT:-}" ig-publisher-localdev-ca -c "$SCRIPT"
fi
