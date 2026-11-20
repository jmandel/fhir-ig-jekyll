#!/bin/bash
# Determinism harness (P7.1): build one IG several times with the same publisher jar, the same IG source
# and the same frozen package cache, perturbing things that should not matter, and compare every
# build's output/ with build 1's (scripts/detcmp.py). See work/determinism/README.md.
#
# usage: determinism.sh [options] <repos-name>
#   -p JAR    publisher jar on the Mac (default $ROOT/work/bin/publisher.jar), or vm:<path> for a jar
#             on the VM disk (e.g. vm:$VMDATA/toolchain/publisher-master.jar from scripts/toolchain.sh)
#   -b PLAN   one word per build, space separated: "none" or a comma list of perturbations
#             (default "none all"). Build 1 is the reference; every other build is compared with it.
#             A second "none" build is a control: it shows run-to-run noise with nothing perturbed.
#             Perturbations (what they change for that build):
#               threads  publisher -threads 4 (reference: no -threads)
#               hash     identity hash codes: scripts/lib/HashShift.java draws K identity hashes on the
#                        main thread before calling Publisher.main (K = 7919 * build number; ref K = 0)
#               fsorder  directory listing order: the build copy lives on a fresh ext4 file system
#                        (loop image) with a random htree hash seed, so readdir order differs
#               tz       TZ and -Duser.timezone = Asia/Kathmandu (+05:45), or America/St_Johns
#                        (-03:30) after 17:00 UTC, so the local date stays the UTC date; ref UTC
#               path     the IG at /work/alt/b<n>/checkout/ig instead of /home/publisher/ig, and a
#                        different java.io.tmpdir
#               locale   -Duser.language=de -Duser.country=DE (not part of "all")
#             all = threads,hash,fsorder,tz,path
#   -j N      concurrent builds (default: number of builds, at most 3)
#   -m XMX    Java heap per build (default 8g; at most 12g)
#   -S DIR    frozen package cache: an existing snapshot on the VM (default: a hard-link snapshot of
#             $VMDATA/fhir-cache taken now, kept under $VMDATA/determinism/snap/<run-id>)
#   -o DIR    results on the Mac (default $ROOT/work/determinism/<name>/<run-id>)
#   -k        keep the build copies on the VM. Default: each build tree is compared with build 1 as
#             soon as it is finished and then deleted, so a run holds at most -j + 1 trees at a time
#   -F GB     wait before starting a build while the VM disk has less than GB free (default 25; 0 = off)
#   -R RUNID  compare again: reuse the builds of an earlier run kept with -k (no new builds)
#   -x ARGS   extra publisher arguments for every build
#   -e ENV    extra environment for the publisher JVM of every build, space separated NAME=VALUE words
#             (e.g. -e SOURCE_DATE_EPOCH=1700000000: fixed build clock, R2/R3; recorded in run.json)
#   -s VER    SUSHI version (default 3.21.1), installed once under $VMDATA/determinism/tools
#   -T MODE   terminology cache: live | frozen | warm. Default: frozen if a snapshot for this IG exists
#             under $VMDATA/determinism/txsnap/<name>-<time>/ (newest), else live.
#               live    every build starts from the IG's own (checked-in or absent) tx cache and asks
#                       the terminology server whatever it is not cached
#               frozen  every build starts from a copy of the snapshot (the dir the publisher reports as
#                       "Terminology Cache is at ...", normally input-cache/txcache)
#               warm    first an extra unperturbed build b0 (not compared) whose tx cache becomes a new
#                       snapshot, then as frozen
#             Each build's meta.json counts the requests that still went to the server (qa-tx.html).
#   -t DIR    use this tx cache snapshot (implies -T frozen)
#
# Fixed for all builds: jar, IG source (one copy streamed from repos/<name>, .git excluded), the package
# cache snapshot (each build sees it through its own overlay, writes are discarded and listed in
# meta.json as "downloads"), SUSHI version, image, JVM. Not controlled: terminology servers (each build
# talks to tx.fhir.org as a normal build does, starting from the IG's own checked-in tx cache), and any
# package the cache lacks (downloaded per build; listed).
# Heavy data stays on the VM disk (D11): $VMDATA/determinism/runs/<run-id>/. The Mac gets small files.
# The whole script is one brace group, so bash parses it completely before running anything: editing
# this file while a run is in progress cannot change what that run executes. (Replace it with a rename
# anyway, as editors and `sed -i` do; an in-place truncating write is what broke runs before.)
{
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
. "$ROOT/scripts/lib/vmdata.sh"
JAR="$ROOT/work/bin/publisher.jar"; PLAN="none all"; JOBS=""; XMX=8g; SNAP=""; OUT=""; KEEP=0; RERUN=""
XARGS=""; XENV=""; SUSHI_VER=3.21.1; MINFREE=25; TXMODE=""; TXSNAP=""
while getopts "p:b:j:m:S:o:kR:x:e:s:F:T:t:h" o; do
  case "$o" in
    p) JAR="$OPTARG" ;; b) PLAN="$OPTARG" ;; j) JOBS="$OPTARG" ;; m) XMX="${OPTARG#-Xmx}" ;;
    S) SNAP="$OPTARG" ;; o) OUT="$OPTARG" ;; k) KEEP=1 ;; R) RERUN="$OPTARG" ;; x) XARGS="$OPTARG" ;; e) XENV="$OPTARG" ;; F) MINFREE="$OPTARG" ;; T) TXMODE="$OPTARG" ;; t) TXSNAP="$OPTARG"; TXMODE=frozen ;;
    s) SUSHI_VER="$OPTARG" ;; *) sed -n '2,54p' "$0"; exit 2 ;;
  esac
done
shift $((OPTIND-1))
NAME="${1:?usage: determinism.sh [options] <repos-name>}"
SRC="$ROOT/repos/$NAME"
[ -d "$SRC" ] || { echo "no such repo: repos/$NAME" >&2; exit 2; }
case "$XMX" in *g) [ "${XMX%g}" -le 12 ] || { echo "-m: at most 12g" >&2; exit 2; } ;; esac
read -r -a BUILDS <<< "$PLAN"
NB=${#BUILDS[@]}
[ "$NB" -ge 2 ] || { echo "-b: need at least two builds" >&2; exit 2; }
[ -n "$JOBS" ] || JOBS=$(( NB < 3 ? NB : 3 ))
expand() { local w="$1"; [ "$w" = none ] && { echo ""; return; }; w="${w//all/threads,hash,fsorder,tz,path}"
  local p; for p in ${w//,/ }; do case "$p" in threads|hash|fsorder|tz|path|locale) ;; *) echo "unknown perturbation: $p" >&2; exit 2 ;; esac; done
  echo "$w" | tr ',' '\n' | awk 'NF && !s[$0]++' | paste -sd, -; }
for w in "${BUILDS[@]}"; do expand "$w" >/dev/null; done
for e in $XENV; do case "$e" in [A-Za-z_]*=*) ;; *) echo "-e: not NAME=VALUE: $e" >&2; exit 2 ;; esac; done

DET="$VMDATA/determinism"
RUNID="${RERUN:-$NAME-$(date -u +%Y%m%dT%H%M%SZ)-$$}"  # pid suffix: concurrent runs never share a dir
RUN="$DET/runs/$RUNID"
OUT="${OUT:-$ROOT/work/determinism/$NAME/$RUNID}"
mkdir -p "$OUT"
igini=$(cd "$SRC" && find . -maxdepth 3 -name ig.ini -not -path '*/node_modules/*' | head -1)
[ -n "$igini" ] || { echo "no ig.ini in $NAME" >&2; exit 2; }
IGSUB=$(dirname "$igini"); IGSUB="${IGSUB#./}"; [ "$IGSUB" = . ] && IGSUB=""
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
priv() { docker run --rm --privileged -v "$VMDATA:$VMDATA" --entrypoint bash "$IMAGE" -c "$1"; }
mnt_cmd() { echo "if [ -f '$RUN/b$1/fs.img' ]; then mkdir -p '$RUN/b$1/ig'; mountpoint -q '$RUN/b$1/ig' || mount -o loop,ro,noload '$RUN/b$1/fs.img' '$RUN/b$1/ig'; fi; "; }
umnt_cmd() { echo "mountpoint -q '$RUN/b$1/ig' && umount '$RUN/b$1/ig'; "; }
compare_pair() {  # compare_pair <n>: build 1 vs build n -> $RUN/cmp-b1-b<n>
  log "compare b1 vs b$1"
  priv "$(mnt_cmd 1)$(mnt_cmd "$1")
    python3 -I -W ignore '$RUN/scripts/detcmp.py' --a '$RUN/b1/ig/$IGSUB/output' --b '$RUN/b$1/ig/$IGSUB/output' \
      --meta-a '$RUN/b1/meta/meta.json' --meta-b '$RUN/b$1/meta/meta.json' --out '$RUN/cmp-b1-b$1' \
      --label '$NAME b1 vs b$1' --jobs 6; rc=\$?; $(umnt_cmd 1)$(umnt_cmd "$1") exit \$rc" || true
}
drop_tree() { vm "rm -rf '$RUN/b$1/ig' '$RUN/b$1/fs.img'"; }  # never mounted outside a container
wait_disk() {
  [ "$MINFREE" -gt 0 ] || return 0
  local free warned=0
  while free=$(vm "df -BG --output=avail '$VMDATA' | tail -1 | tr -dc 0-9") && [ "${free:-0}" -lt "$MINFREE" ]; do
    [ "$warned" = 1 ] || log "BLOCKED: $free GB free on the VM disk (< $MINFREE GB); waiting"
    warned=1; sleep 60
  done
}
copy_scripts() {
  docker run --rm -i -v "$VMDATA:$VMDATA" --entrypoint bash "$IMAGE" -c "mkdir -p '$RUN/scripts' && tar -xf - -C '$RUN/scripts'" \
    < <(COPYFILE_DISABLE=1 tar -C "$ROOT/scripts" --no-mac-metadata --no-xattrs -cf - detcmp.py domdiff.py)
}

if [ -z "$RERUN" ]; then
  # ---------------------------------------------------------------- inputs, once per run
  # time zone for the tz perturbation: one whose local date equals the UTC date for the next hours
  if [ "$(date -u +%H)" -lt 17 ]; then PTZ=Asia/Kathmandu; else PTZ=America/St_Johns; fi
  log "run $RUNID: $NB builds (${PLAN}), -j $JOBS, -Xmx$XMX, IG dir '${IGSUB:-.}'"
  wait_disk
  vm "rm -rf '$RUN' && mkdir -p '$RUN/pub' '$RUN/hs' '$DET/snap' '$DET/tools'"
  copy_scripts
  to_vm "$SRC" "$RUN/src" --exclude .git
  case "$JAR" in
    vm:*) vm "cp '${JAR#vm:}' '$RUN/pub/publisher.jar'" ;;
    *) docker run --rm -i -v "$VMDATA:$VMDATA" --entrypoint bash "$IMAGE" -c "cat > '$RUN/pub/publisher.jar'" < "$JAR" ;;
  esac
  docker run --rm -i -v "$VMDATA:$VMDATA" --entrypoint bash "$IMAGE" -c \
    "cat > '$RUN/hs/HashShift.java' && javac -d '$RUN/hs' '$RUN/hs/HashShift.java'" < "$ROOT/scripts/lib/HashShift.java"
  if [ -z "$SNAP" ]; then
    SNAP="$DET/snap/$RUNID"
    log "freezing package cache: hard-link snapshot $SNAP"
    vm "cp -al '$VMDATA/fhir-cache' '$SNAP'"
  fi
  vm "[ -d '$SNAP/packages' ]" || { echo "no package cache snapshot at $SNAP" >&2; exit 2; }
  SUSHI="$DET/tools/sushi-$SUSHI_VER"
  if ! vm "[ -x '$SUSHI/bin/sushi' ]"; then
    log "installing SUSHI $SUSHI_VER into $SUSHI"
    docker run --rm -v "$VMDATA:$VMDATA" -v "$SUSHI:/home/publisher/.node" --entrypoint bash "$IMAGE" -c \
      "chown publisher /home/publisher/.node && gosu publisher npm install -g --prefix /home/publisher/.node fsh-sushi@$SUSHI_VER >/dev/null 2>&1; /home/publisher/.node/bin/sushi --version" \
      || { echo "SUSHI install failed" >&2; exit 1; }
  fi
  # terminology cache snapshot
  if [ -z "$TXMODE" ]; then
    TXSNAP=$(vm "ls -d '$DET/txsnap/$NAME'-* 2>/dev/null | sort | while read -r d; do [ -f \"\$d/relpath\" ] && echo \"\$d\"; done | tail -1")
    if [ -n "$TXSNAP" ]; then TXMODE=frozen; else TXMODE=live; fi
  fi
  case "$TXMODE" in
    live) TXSNAP="" ;;
    frozen) [ -n "$TXSNAP" ] || TXSNAP=$(vm "ls -d '$DET/txsnap/$NAME'-* 2>/dev/null | sort | while read -r d; do [ -f \"\$d/relpath\" ] && echo \"\$d\"; done | tail -1")
            vm "[ -f '$TXSNAP/relpath' ]" || { echo "-T frozen: no tx cache snapshot for $NAME (use -T warm)" >&2; exit 2; } ;;
    warm) TXSNAP="" ;;
    *) echo "-T: live, frozen or warm" >&2; exit 2 ;;
  esac
  TXREL=""; [ -n "$TXSNAP" ] && TXREL=$(vm "cat '$TXSNAP/relpath'")
  igcommit=$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo unknown)
  vm "cd '$RUN' && python3 - <<'EOF'
import json, hashlib, os, subprocess
h = hashlib.sha256(open('pub/publisher.jar', 'rb').read()).hexdigest()
pk = sorted(os.listdir('$SNAP/packages'))
json.dump({'run_id': '$RUNID', 'ig': '$NAME', 'ig_commit': '$igcommit', 'ig_dir': '$IGSUB', 'plan': '$PLAN',
           'jar': os.path.basename('$JAR'), 'jar_sha256': h, 'image': '$IMAGE', 'xmx': '$XMX', 'extra_args': '$XARGS', 'extra_env': '$XENV',
           'package_cache_snapshot': '$SNAP', 'snapshot_packages': len(pk),
           'snapshot_listing_sha256': hashlib.sha256('\n'.join(pk).encode()).hexdigest(),
           'sushi': '$SUSHI_VER', 'tz_perturbation': '$PTZ', 'tx_mode': '$TXMODE', 'tx_snapshot': '$TXSNAP',
           'java': subprocess.run(['java', '-version'], capture_output=True, text=True).stderr.splitlines()[0]},
          open('run.json', 'w'), indent=1)
EOF"
  printf '%s\n' "$PTZ" "$SNAP" "$SUSHI" > "$OUT/.run-vars"
  vm "printf '%s\n' '$PTZ' '$SNAP' '$SUSHI' > '$RUN/run-vars'"

  # ---------------------------------------------------------------- builds
  one_build() {  # one_build <n> <perturbations>
    local n="$1" p="$2" B="$RUN/b$1" igp=/home/publisher/ig tmpd=/tmp tz=UTC jopts="" pargs="" k=0 privf="" mnt=""
    case ",$p," in *,path,*) igp="/work/alt/b$n/checkout/ig"; tmpd="/work/alt/b$n/tmp-java" ;; esac
    case ",$p," in *,tz,*) tz="$PTZ" ;; esac
    case ",$p," in *,hash,*) k=$((7919 * n)) ;; esac
    case ",$p," in *,threads,*) pargs="-threads 4" ;; esac
    case ",$p," in *,locale,*) jopts="-Duser.language=de -Duser.country=DE" ;; esac
    case ",$p," in *,fsorder,*) privf=--privileged ;; *) mnt="-v $B/ig:$igp" ;; esac
    local vol="det-$RUNID-b$n"; vol="${vol//[^A-Za-z0-9_.-]/_}"
    vm "mkdir -p '$B/meta' '$B/cache/upper/packages' '$B/cache/work' && uid=\$(stat -c %u '$SNAP/packages') && chown \$uid '$B/cache/upper' '$B/cache/upper/packages'
        $( [ -z "$privf" ] && echo "cp -a '$RUN/src' '$B/ig' && chown -R \$uid '$B/ig'" )
        $( [ -z "$privf" ] && [ -n "$TXSNAP" ] && echo "rm -rf '$B/ig/$TXREL' && mkdir -p \$(dirname '$B/ig/$TXREL') && cp -a '$TXSNAP/txcache' '$B/ig/$TXREL' && chown -R \$uid '$B/ig'" )"
    docker volume rm -f "$vol" >/dev/null 2>&1 || true
    docker volume create --driver local --opt type=overlay --opt device=overlay \
      --opt "o=lowerdir=$SNAP,upperdir=$B/cache/upper,workdir=$B/cache/work" "$vol" >/dev/null
    log "b$n start (${p:-none})"
    # shellcheck disable=SC2086
    docker run --rm $privf $mnt -v "$VMDATA:$VMDATA" -v "$vol:/home/publisher/.fhir" -v "$SUSHI:/home/publisher/.node:ro" \
      --name "$vol" --entrypoint bash "$IMAGE" -c "
      set -u; M='$B/meta'; uid=\$(stat -c %u '$SNAP/packages'); gid=\$(stat -c %g '$SNAP/packages')
      # run as the owner of the package cache, like the image's entrypoint does (but without usermod,
      # which would chown everything under the home dir, including the mounted cache)
      getent group \$gid >/dev/null || groupadd -g \$gid detgrp; useradd -o -u \$uid -g \$gid -M -d /home/publisher detpub
      chmod 755 /home/publisher
      if [ -n '$privf' ]; then
        seed=\$(cat /proc/sys/kernel/random/uuid); echo \$seed > \$M/fs_hash_seed
        truncate -s 80G '$B/fs.img' && mkfs.ext4 -q -F -E hash_seed=\$seed '$B/fs.img'
        mkdir -p '$igp' && mount -o loop '$B/fs.img' '$igp' && cp -a '$RUN/src/.' '$igp/' && chown -R \$uid '$igp'
        if [ -n '$TXSNAP' ]; then rm -rf '$igp/$TXREL'; mkdir -p \$(dirname '$igp/$TXREL'); cp -a '$TXSNAP/txcache' '$igp/$TXREL'; chown -R \$uid '$igp'; fi
      fi
      mkdir -p '$tmpd' && chown \$uid '$tmpd'
      cd '$igp/$IGSUB'
      date +%s.%N > \$M/start
      gosu detpub env TZ='$tz' $XENV java -Xmx$XMX -Duser.timezone='$tz' $jopts -Dhashshift=$k -Djava.io.tmpdir='$tmpd' \
        -cp '$RUN/pub/publisher.jar:$RUN/hs' HashShift org.hl7.fhir.igtools.publisher.Publisher \
        -ig . -no-network-check $pargs $XARGS > \$M/publisher.log 2>&1
      echo \$? > \$M/rc; date +%s.%N > \$M/end
      gosu detpub sushi --version > \$M/sushi-version 2>&1 || true
      find output -type f 2>/dev/null | wc -l > \$M/output-files
      # build metadata (window, zone, perturbations, packages the cache lacked), written here so that it
      # exists even if the harness process is killed while the container runs
      python3 - <<'EOF'
import json, os, re, datetime, zoneinfo
B = '$B'; M = B + '/meta'
r = lambda f, d='': open(os.path.join(M, f)).read().strip() if os.path.exists(os.path.join(M, f)) else d
start, end = float(r('start', '0')), float(r('end', '0'))
off = int(datetime.datetime.fromtimestamp(start, zoneinfo.ZoneInfo('$tz')).utcoffset().total_seconds()) if start else 0
up = B + '/cache/upper/packages'
def pkg(d):  # what a build fetched that the frozen cache lacked: version, date, content hash
    import hashlib
    h = hashlib.sha256(); pj = {}
    for dp, dn, fn in sorted(os.walk(os.path.join(up, d))):
        dn.sort()
        for f in sorted(fn):
            q = os.path.join(dp, f); h.update(os.path.relpath(q, up).encode())
            h.update(open(q, 'rb').read())
            if f == 'package.json' and dp.endswith('/package'):
                try: pj = json.load(open(q))
                except Exception: pass
    return '%s (version %s, date %s, sha256 %s)' % (d, pj.get('version'), pj.get('date'), h.hexdigest()[:12])
dl = sorted(pkg(d) for d in os.listdir(up) if os.path.isdir(os.path.join(up, d))) if os.path.isdir(up) else []
logt = open(os.path.join(M, 'publisher.log'), errors='replace').read() if os.path.exists(os.path.join(M, 'publisher.log')) else ''
tx = sorted(set(re.findall(r'https?://tx[\w.-]*\.(?:org|net|com)[\w/.-]*', logt)))[:10]
# requests that still went to a terminology server (the publisher logs each one in qa-tx.html)
qt = '$igp/$IGSUB/output/qa-tx.html'
ops = {}
if os.path.exists(qt):
    for mm in re.finditer(r'(?m)^(GET|POST|PUT) (\S+)', open(qt, errors='replace').read()):
        u = mm.group(2).split('?')[0]
        op = mm.group(1) + ' ' + ('/'.join(u.split('/')[-2:]) if '\$' in u else u.split('/')[-1] if u.endswith('metadata') else '/'.join(u.split('/')[4:5]) + '/<read>')
        ops[op] = ops.get(op, 0) + 1
tcl = re.search(r'Terminology Cache is at (\S+?)\.? (\d+) files in cache', logt)
home = sorted(e for e in os.listdir(B + '/cache/upper') if e != 'packages') if os.path.isdir(B + '/cache/upper') else []
m = {'label': 'b$n', 'perturbations': '${p:-none}', 'ig_path': '$igp', 'tmpdir': '$tmpd', 'tz': '$tz', 'tz_offset': off,
     'hashshift': $k, 'publisher_args': '$pargs', 'jvm_opts': '$jopts', 'fs_hash_seed': r('fs_hash_seed'),
     'start': start, 'end': end, 'seconds': round(end - start, 1), 'rc': r('rc', '?'), 'output_files': r('output-files'),
     'sushi': r('sushi-version'), 'downloads': dl, 'tx_servers_in_log': tx,
     'tx_mode': '$TXMODE' if '$n' != '0' else 'live (warm-up)', 'tx_snapshot': '$TXSNAP',
     'tx_cache_dir': tcl.group(1) if tcl else None, 'tx_cache_files_at_start': int(tcl.group(2)) if tcl else None,
     'tx_requests': sum(ops.values()) if os.path.exists(qt) else None, 'tx_requests_by_op': dict(sorted(ops.items(), key=lambda kv: -kv[1])),
     'fhir_home_writes': home,
     'finished': 'Finished' in logt[-20000:] or 'Done' in logt[-2000:],
     'failed': 'Publishing Content Failed' in logt}
json.dump(m, open(os.path.join(M, 'meta.json'), 'w'), indent=1)
print('b$n', m['rc'], m['seconds'], 's, output files', m['output_files'], ', downloads', len(dl), ', tx requests', m['tx_requests'])
EOF
      cd /; [ -n '$privf' ] && umount '$igp'
      exit 0" > "$OUT/b$n.console" 2>&1 || true
    docker volume rm -f "$vol" >/dev/null 2>&1 || true
    vm "rm -rf '$B/cache'"
    grep -E '^b[0-9]+ ' "$OUT/b$n.console" || true
    log "b$n done"
    if [ "$KEEP" = 0 ] && [ "$n" -gt 1 ]; then  # compare now and free the tree
      until vm "[ -f '$RUN/b1/meta/meta.json' ]"; do sleep 10; done
      compare_pair "$n"; drop_tree "$n"
    fi
  }
  if [ "$TXMODE" = warm ]; then
    log "terminology warm-up build b0 (live; its tx cache becomes the snapshot)"
    wait_disk
    one_build 0 ""
    cpath=$(vm "grep -m1 -oE 'Terminology Cache is at [^ ]+' '$RUN/b0/meta/publisher.log'" || true)
    cpath="${cpath#Terminology Cache is at }"; cpath="${cpath%.}"
    TXREL="${cpath#/home/publisher/ig/}"
    [ -n "$cpath" ] && [ "$TXREL" != "$cpath" ] || { echo "warm-up: tx cache '$cpath' is not inside the IG; cannot freeze" >&2; exit 1; }
    TXSNAP="$DET/txsnap/$NAME-$(date -u +%Y%m%dT%H%M%SZ)"
    vm "mkdir -p '$TXSNAP' && cp -a '$RUN/b0/ig/$TXREL' '$TXSNAP/txcache' && echo '$TXREL' > '$TXSNAP/relpath' && echo '$RUNID' > '$TXSNAP/source' && cd '$RUN' && python3 -c \"import json; r = json.load(open('run.json')); r.update(tx_mode='frozen (warm-up b0)', tx_snapshot='$TXSNAP'); json.dump(r, open('run.json', 'w'), indent=1)\""
    log "tx cache snapshot $TXSNAP: $(vm "find '$TXSNAP/txcache' -type f | wc -l") files, $(vm "du -sh '$TXSNAP/txcache' | cut -f1")"
    drop_tree 0
    TXMODE=frozen
  fi
  i=0
  for w in "${BUILDS[@]}"; do
    i=$((i + 1))
    while [ "$(jobs -rp | wc -l)" -ge "$JOBS" ]; do sleep 5; done
    wait_disk
    one_build "$i" "$(expand "$w")" &
    sleep 2
  done
  wait
else
  { read -r PTZ; read -r SNAP; read -r SUSHI; } < <(vm "cat '$RUN/run-vars'")
  for v in $(docker volume ls -q | grep -F "det-${RUNID//[^A-Za-z0-9_.-]/_}-" || true); do docker volume rm -f "$v" >/dev/null 2>&1 || true; done
  log "re-comparing $RUNID"
fi

# ---------------------------------------------------------------- comparison
vm "for n in \$(seq 1 $NB); do [ -f '$RUN/b'\$n/meta/meta.json ] || { echo \"missing build b\$n in $RUN\"; exit 1; }; done"
[ -n "$RERUN" ] && copy_scripts
for n in $(seq 2 "$NB"); do  # pairs not compared yet (all of them with -k or -R)
  if [ -n "$RERUN" ] || ! vm "[ -f '$RUN/cmp-b1-b$n/report.json' ]"; then compare_pair "$n"; fi
done
priv "python3 -I -W ignore '$RUN/scripts/detcmp.py' --aggregate '$RUN' > '$RUN/summary.md'" || true

# ---------------------------------------------------------------- results to the Mac (small files only)
vm "cd '$RUN' && tar -cf - run.json summary.md cmp-* b*/meta/meta.json 2>/dev/null" | tar -xf - -C "$OUT"
for n in $(seq 1 "$NB"); do vm "tail -n 3000 '$RUN/b$n/meta/publisher.log'" > "$OUT/b$n.publisher.log.tail" 2>/dev/null || true; done
cat "$OUT/summary.md"
if [ "$KEEP" = 0 ]; then
  log "deleting build copies (results stay in $RUN: meta, comparisons)"
  vm "rm -rf '$RUN/src' '$RUN/pub' $(for n in $(seq 1 "$NB"); do printf "'%s' '%s' " "$RUN/b$n/ig" "$RUN/b$n/fs.img"; done)"
fi
log "results: $OUT"
exit 0
}
