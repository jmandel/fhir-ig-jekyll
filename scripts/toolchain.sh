#!/bin/bash
# From-source toolchain: org.hl7.fhir.core and fhir-ig-publisher built from git clones on the
# Docker host's disk (the Lima VM, see scripts/lib/vmdata.sh), with private Maven repositories.
#
#   $VMDATA/toolchain/{core,publisher}   main clones (hapifhir/org.hl7.fhir.core, HL7/fhir-ig-publisher)
#   $VMDATA/toolchain/m2                 Maven repository of the main clones (core SNAPSHOT installed here)
#   $VMDATA/toolchain/wt/<name>/         isolated worktree set: core/, publisher/ (git worktrees on the new
#                                        branch determinism/<name>), m2/ (its own Maven repo, a copy of the
#                                        main m2 made at creation), jars/, publisher.jar (latest build)
#
# usage: scripts/toolchain.sh <command> [args]
# TARGET below is a worktree name, or omitted (or "main") for the main clones.
#   clone                              clone the main repositories if missing
#   sync [core=REF] [publisher=REF]    main clones: git fetch; check out REF where given; a clone on master is
#                                      fast-forwarded to origin/master. Local branches are never reset.
#   checkout <core|publisher> REF      main clones: check out a branch/tag/commit (refuses with a dirty tree)
#   worktree <name> [core=REF] [publisher=REF]
#                                      create wt/<name>: fetch, then worktrees for core and publisher on a new
#                                      branch determinism/<name> from origin/master (or REF), and m2/
#   worktrees                          list worktree sets (branch, commit, dirty)
#   worktree-rm <name>                 remove wt/<name> (the determinism/<name> branches are kept)
#   status [TARGET]                    branch, commit, dirty state; core version; publisher's pinned core_version
#   build [TARGET] [core|publisher|all] [-- MAVEN_ARGS...]
#                                      core: mvn install (tests skipped) into TARGET's m2; publisher: mvn package
#                                      of the cli module against TARGET's core version (-Dcore_version).
#                                      Incremental where Maven allows (target/ is kept). Extra args go to Maven,
#                                      e.g. `build foo core -- -pl org.hl7.fhir.r5 -am`. Prints the jar path.
#   test [TARGET] <core|publisher> <module|all> [-- MAVEN_ARGS...]
#                                      unit tests of one module (`all`: every module, -fae), e.g.
#                                      `test foo core org.hl7.fhir.r5 -- -Dtest=ProfileUtilitiesTests`
#   txcache-reset [TARGET] <core|publisher>
#                                      undo the test runs' writes to checked-in terminology caches (*/txCache/*)
#   git [TARGET] <core|publisher> ARGS...   git in that tree (e.g. `git foo core diff`)
#   run [TARGET] <core|publisher> 'SHELL'   shell snippet in that tree (e.g. `run foo core 'grep -rn X org.hl7.fhir.r5/src'`)
#   get [TARGET] <core|publisher> PATH...   copy files from the tree to $ROOT/work/toolchain/src/<TARGET>/<repo>/PATH
#   put [TARGET] <core|publisher> PATH...   copy edited files from there back into the tree (prints git status)
#   shell                              interactive shell in the build container, in $VMDATA/toolchain
#
# Environment: VMDATA (default /srv/ig), IMAGE (default ig-publisher-localdev-ca: Temurin JDK 21 that
# trusts the extra root CA of the local network), MAVEN_OPTS (default -Xmx4g), MVN_THREADS (default 1C),
# JAR_LABEL (main clones only; default <publisher branch>-<short commit>).
# Logs: $ROOT/work/toolchain/logs/<command>-<target>-<repo>-<time>.log (also shown on the console).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/vmdata.sh"
TC="$VMDATA/toolchain"
MAVEN_VERSION=3.9.9
MVN="$TC/tools/apache-maven-$MAVEN_VERSION/bin/mvn"
LOGS="$ROOT/work/toolchain/logs"
MAVEN_OPTS="${MAVEN_OPTS:--Xmx4g}"
MVN_THREADS="${MVN_THREADS:-1C}"
url() { case "$1" in core) echo https://github.com/hapifhir/org.hl7.fhir.core ;; publisher) echo https://github.com/HL7/fhir-ig-publisher ;; esac; }
SKIP="-DskipTests -Dmaven.javadoc.skip=true -Dcheckstyle.skip=true -Djacoco.skip=true -Dspotbugs.skip=true -Dlicense.skip=true -Danimal.sniffer.skip=true -Dduplicate-finder.skip=true -Dgpg.skip=true"
POMVER="sed -n '0,/<artifactId>org.hl7.fhir.core<\\/artifactId>/d; s:.*<version>\\(.*\\)</version>.*:\\1:p' pom.xml | head -1"

# Run a snippet in the build container ($1), with git trusting the clones (they are owned by root).
tc() {
  docker run --rm -i -v "$VMDATA:$VMDATA" -w "$TC" \
    -e MAVEN_OPTS="$MAVEN_OPTS" -e GIT_CONFIG_GLOBAL="$TC/gitconfig" \
    --entrypoint bash "$IMAGE" -c "set -euo pipefail; $1"
}
tc_tty() { docker run --rm -it -v "$VMDATA:$VMDATA" -w "$TC" -e MAVEN_OPTS="$MAVEN_OPTS" -e GIT_CONFIG_GLOBAL="$TC/gitconfig" --entrypoint bash "$IMAGE"; }
logged() {  # logged <name> <cmd...>: run, tee to a log file on the Mac
  mkdir -p "$LOGS"
  local log="$LOGS/$1-$(date -u +%Y%m%dT%H%M%SZ).log"; shift
  echo "log: $log"
  "$@" 2>&1 | tee "$log"
  return "${PIPESTATUS[0]}"
}
repo_arg() { case "${1:-}" in core|publisher) ;; *) echo "repository must be core or publisher (got '${1:-}')" >&2; exit 2 ;; esac; }
name_ok() { case "$1" in ''|core|publisher|all|main|--|*/*|.*) echo "bad worktree name: '$1'" >&2; exit 2 ;; esac; }

# target <name|main>: sets T (label), BASE (dir holding core/ and publisher/), M2, JARS
target() {
  T="${1:-main}"
  if [ "$T" = main ]; then BASE="$TC"; M2="$TC/m2"; JARS="$TC/jars"
  else BASE="$TC/wt/$T"; M2="$BASE/m2"; JARS="$BASE/jars"; fi
}
# take_target "$@": if $1 is not a repository/what keyword, it is a target; sets SHIFT=1 then
take_target() {
  SHIFT=0
  case "${1:-}" in core|publisher|all|--|'') target main ;; *) target "$1"; SHIFT=1 ;; esac
  if [ "$T" != main ]; then
    tc "[ -d '$BASE/core' ]" || { echo "no worktree set '$T' (create it with: toolchain.sh worktree $T)" >&2; exit 2; }
  fi
}

setup() {
  tc "mkdir -p '$TC/m2' '$TC/jars' '$TC/tools' '$TC/wt'
    [ -f '$TC/gitconfig' ] || git config --file '$TC/gitconfig' --add safe.directory '*'
    if [ ! -x '$MVN' ]; then
      curl -fsSL https://archive.apache.org/dist/maven/maven-3/$MAVEN_VERSION/binaries/apache-maven-$MAVEN_VERSION-bin.tar.gz | tar -xz -C '$TC/tools'
    fi"
}

cmd_clone() {
  setup
  local r
  for r in core publisher; do
    tc "[ -d '$TC/$r/.git' ] && echo '$r: already cloned' || git clone -q '$(url $r)' '$TC/$r'"
  done
  cmd_status
}

parse_refs() {  # parse_refs core=REF publisher=REF -> REF_core REF_publisher
  REF_core=""; REF_publisher=""
  local a
  for a in "$@"; do
    case "$a" in core=*) REF_core="${a#*=}" ;; publisher=*) REF_publisher="${a#*=}" ;; *) echo "bad argument: $a (want core=REF or publisher=REF)" >&2; exit 2 ;; esac
  done
}

cmd_sync() {
  parse_refs "$@"
  local r ref
  for r in core publisher; do
    if [ "$r" = core ]; then ref="$REF_core"; else ref="$REF_publisher"; fi
    tc "cd '$TC/$r'
      git fetch -q --prune origin
      if [ -n '$ref' ]; then git checkout -q '$ref'; fi
      b=\$(git symbolic-ref -q --short HEAD || true)
      if [ \"\$b\" = master ]; then git merge -q --ff-only origin/master; fi
      echo \"$r: \${b:-(detached)} \$(git rev-parse HEAD)\""
  done
}

cmd_checkout() {
  repo_arg "${1:-}"; [ -n "${2:-}" ] || { echo "usage: checkout <core|publisher> REF" >&2; exit 2; }
  tc "cd '$TC/$1'; git diff --quiet && git diff --cached --quiet || { echo 'working tree not clean' >&2; exit 1; }; git checkout -q '$2'; echo \"$1: \$(git symbolic-ref -q --short HEAD || echo detached) \$(git rev-parse HEAD)\""
}

cmd_worktree() {
  local n="${1:-}"; name_ok "$n"; shift
  parse_refs "$@"
  target "$n"
  setup
  # A separate Maven repo per worktree set, so concurrent `mvn install`s never write to the same one.
  # Full copy (not hard links): Maven may rewrite metadata files in place.
  tc "[ ! -e '$BASE' ] || { echo 'wt/$n already exists' >&2; exit 1; }
    mkdir -p '$BASE/jars'
    for r in core publisher; do
      ref=; [ \$r = core ] && ref='$REF_core' || ref='$REF_publisher'
      cd '$TC/'\$r
      flock '$TC/.git-'\$r.lock git fetch -q --prune origin
      flock '$TC/.git-'\$r.lock git worktree add -q -b 'determinism/$n' '$BASE/'\$r \"\${ref:-origin/master}\"
      echo \"\$r: determinism/$n at \$(git -C '$BASE/'\$r rev-parse HEAD)\"
    done
    cp -a '$TC/m2' '$BASE/m2'
    echo \"m2 seeded from $TC/m2 (core snapshot built from main core \$(git -C '$TC/core' rev-parse --short HEAD)) on \$(date -u +%FT%TZ)\" > '$BASE/SEED'
    cat '$BASE/SEED'
    echo 'worktree set: $BASE'"
}

cmd_worktrees() {
  tc "cd '$TC/wt' 2>/dev/null || exit 0
    for d in */; do d=\${d%/}; [ -d \"\$d/core\" ] || continue
      for r in core publisher; do
        cd '$TC/wt/'\$d/\$r
        x=; git diff --quiet && git diff --cached --quiet || x=' (dirty)'
        printf '%-28s %-10s %-40s %s%s\n' \"\$d\" \$r \"\$(git symbolic-ref -q --short HEAD || echo detached)\" \"\$(git rev-parse --short HEAD)\" \"\$x\"
      done
    done"
}

cmd_worktree-rm() {
  local n="${1:-}"; name_ok "$n"; target "$n"
  tc "for r in core publisher; do
      [ -d '$BASE/'\$r ] && { cd '$TC/'\$r; flock '$TC/.git-'\$r.lock git worktree remove --force '$BASE/'\$r; } || true
    done
    rm -rf '$BASE'; for r in core publisher; do git -C '$TC/'\$r worktree prune; done
    echo 'removed $BASE (branches determinism/$n kept)'"
}

cmd_status() {
  take_target "$@"
  tc "for r in core publisher; do
        cd '$BASE/'\$r
        d=; git diff --quiet && git diff --cached --quiet || d=' (dirty)'
        echo \"\$r: \$(git symbolic-ref -q --short HEAD || echo detached) \$(git rev-parse HEAD) \$(git log -1 --format=%cI)\$d\"
      done
      echo \"core version: \$(cd '$BASE/core' && $POMVER)\"
      echo \"publisher pins core_version: \$(sed -n 's:.*<core_version>\\(.*\\)</core_version>.*:\\1:p' '$BASE/publisher/pom.xml')\"
      echo \"maven repo: $M2\""
}

build_core() {
  echo "== [$T] core: mvn install (tests skipped) into $M2 $*"
  tc "cd '$BASE/core'; echo \"core at \$(git symbolic-ref -q --short HEAD || echo detached) \$(git rev-parse HEAD)\"
    TIMEFORMAT='core build: %R s'; time '$MVN' -B -ntp -T $MVN_THREADS -Dmaven.repo.local='$M2' $SKIP install $*"
}

build_publisher() {
  echo "== [$T] publisher: mvn package against $M2 $*"
  tc "cv=\$(cd '$BASE/core' && $POMVER); csha=\$(git -C '$BASE/core' rev-parse --short HEAD)
    cd '$BASE/publisher'
    sha=\$(git rev-parse --short HEAD); br=\$(git symbolic-ref -q --short HEAD || echo detached)
    if [ '$T' = main ]; then label='${JAR_LABEL:-}'; [ -n \"\$label\" ] || label=\"\$(echo \"\$br\" | tr / _)-\$sha\"
    else label='$T'-p\$sha-c\$csha; fi
    echo \"publisher at \$br \$sha, core_version \$cv (core tree at \$csha; the jar embeds whatever core \$cv was last installed in $M2)\"
    TIMEFORMAT='publisher build: %R s'
    # The cli module rarely changes, so maven-jar-plugin keeps its previous (already shaded) jar and
    # maven-shade-plugin re-shades that, whose classes then win over the freshly built publisher.core:
    # a stale jar. Remove the cli jars first so the shaded jar is always rebuilt from current modules.
    rm -f '$BASE/publisher/org.hl7.fhir.publisher.cli/target/'*.jar
    time '$MVN' -B -ntp -T $MVN_THREADS -Dmaven.repo.local='$M2' $SKIP -Dcore_version=\"\$cv\" package -pl org.hl7.fhir.publisher.cli -am $*
    v=\$(sed -n '0,/<artifactId>org.hl7.fhir.publisher<\\/artifactId>/d; s:.*<version>\\(.*\\)</version>.*:\\1:p' pom.xml | head -1)
    jar='$BASE/publisher/org.hl7.fhir.publisher.cli/target/org.hl7.fhir.publisher.cli-'\$v'.jar'
    [ -f \"\$jar\" ] || { echo \"no jar at \$jar\" >&2; exit 1; }
    mkdir -p '$JARS'; cp \"\$jar\" '$JARS/publisher-'\$label'.jar'
    if [ '$T' = main ]; then [ \"\$br\" = master ] && cp \"\$jar\" '$TC/publisher-master.jar' || true
    else cp \"\$jar\" '$BASE/publisher.jar'; echo \"publisher jar (latest): $BASE/publisher.jar\"; fi
    echo \"publisher jar: $JARS/publisher-\$label.jar\""
}

cmd_build() {
  take_target "$@"; shift $SHIFT
  local what="${1:-all}"; [ $# -gt 0 ] && shift
  [ "${1:-}" = -- ] && shift
  case "$what" in
    core) logged "build-$T-core" build_core "$@" ;;
    publisher) logged "build-$T-publisher" build_publisher "$@" ;;
    all) logged "build-$T-core" build_core "$@" && logged "build-$T-publisher" build_publisher "$@" ;;
    --) logged "build-$T-core" build_core "$@" && logged "build-$T-publisher" build_publisher "$@" ;;
    *) echo "build what? core|publisher|all" >&2; exit 2 ;;
  esac
}

cmd_test() {
  take_target "$@"; shift $SHIFT
  repo_arg "${1:-}"; [ -n "${2:-}" ] || { echo "usage: test [TARGET] <core|publisher> <module|all> [-- MAVEN_ARGS]" >&2; exit 2; }
  local r="$1" m="$2" extra=""; shift 2; [ "${1:-}" = -- ] && shift
  if [ "$r" = publisher ]; then extra="-Dcore_version=\$(cd '$BASE/core' && $POMVER)"; fi
  local pl="-pl '$m'"; if [ "$m" = all ]; then pl="-fae"; fi
  logged "test-$T-$r-$m" tc "cd '$BASE/$r'; TIMEFORMAT='tests: %R s'; time '$MVN' -B -ntp -Dmaven.repo.local='$M2' -Djacoco.skip=true -Dsurefire.failIfNoSpecifiedTests=false $extra test $pl $*"
}

cmd_txcache-reset() {
  take_target "$@"; shift $SHIFT
  repo_arg "${1:-}"
  tc "cd '$BASE/$1'
    git ls-files -m -o --exclude-standard | { grep -i '/txcache/' || true; } | while read -r f; do
      if git ls-files --error-unmatch \"\$f\" >/dev/null 2>&1; then git checkout -q -- \"\$f\"; else rm -f \"\$f\"; fi
      echo \"reset \$f\"
    done
    git status --short"
}

cmd_git() { take_target "$@"; shift $SHIFT; repo_arg "${1:-}"; local r="$1"; shift; tc "cd '$BASE/$r'; git $(printf '%q ' "$@")"; }

cmd_run() { take_target "$@"; shift $SHIFT; repo_arg "${1:-}"; tc "cd '$BASE/$1'; $2"; }

cmd_get() {
  take_target "$@"; shift $SHIFT
  repo_arg "${1:-}"; local r="$1"; shift
  local dst="$ROOT/work/toolchain/src/$T/$r"; mkdir -p "$dst"
  tc "cd '$BASE/$r'; tar -cf - $(printf '%q ' "$@")" | tar -xf - -C "$dst"
  printf '%s\n' "${@/#/$dst/}"
}

cmd_put() {
  take_target "$@"; shift $SHIFT
  repo_arg "${1:-}"; local r="$1"; shift
  local src="$ROOT/work/toolchain/src/$T/$r"
  (cd "$src" && COPYFILE_DISABLE=1 tar --no-mac-metadata --no-xattrs --uid 0 --gid 0 -cf - "$@") \
    | tc "tar --no-same-owner -xf - -C '$BASE/$r' && cd '$BASE/$r' && git status --short"
}

c="${1:-}"; [ $# -gt 0 ] && shift
case "$c" in
  clone|sync|checkout|worktree|worktrees|worktree-rm|status|build|test|txcache-reset|git|run|get|put) "cmd_$c" "$@" ;;
  shell) tc_tty ;;
  *) sed -n "2,$(grep -n '^# Logs:' "$0" | cut -d: -f1)p" "$0"; exit 2 ;;
esac
