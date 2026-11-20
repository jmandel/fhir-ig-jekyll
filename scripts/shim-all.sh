#!/bin/bash
# Run the shim on every VALID golden (optionally only ones newer than the last summary) and
# tabulate: name, files same/differ/missing/extra, shim seconds, jekyll seconds -> work/shim-summary.tsv
# usage: scripts/shim-all.sh [-j N] [build-dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
J=3
if [ "${1:-}" = "-j" ]; then J="$2"; shift 2; fi
B="${1:-build-site}"
OUT="$ROOT/work/shim-summary.tsv"
printf 'name\tsame\tdiffer\tmissing\textra\tshim_s\tjekyll_s\n' > "$OUT.tmp"
ls -d "$ROOT"/work/golden/*/ | while read -r g; do [ -f "$g/VALID" ] && [ -d "$g/jekyll-out" ] && basename "$g"; done > "$ROOT/work/shim-all-names.txt"
run_one() {
  n="$1"; ROOT="$2"; B="$3"
  "$ROOT/scripts/shim-golden.sh" "$n" "$B" > "$ROOT/work/golden/$n/shim-run.txt" 2>&1 || true
  python3 -I - "$ROOT/work/golden/$n" <<'EOF'
import json, re, sys, os
g = sys.argv[1]; n = os.path.basename(g)
try:
    d = json.load(open(os.path.join(g, 'shim-diff.json')))
    same, differ, missing, extra = d['same'], len(d['differ']), len(d['missing']), len(d['extra'])
except Exception:
    same = differ = missing = extra = -1
def secs(path, rx):
    try:
        m = re.search(rx, open(path, errors='replace').read())
        return m.group(1) if m else ''
    except OSError:
        return ''
print('\t'.join(map(str, [n, same, differ, missing, extra,
      secs(os.path.join(g, 'shim.log'), r'done in ([0-9.]+) seconds'),
      secs(os.path.join(g, 'jekyll.log'), r'done in ([0-9.]+) seconds')])))
EOF
}
export -f run_one
xargs -P "$J" -I{} bash -c 'run_one "$@"' _ {} "$ROOT" "$B" < "$ROOT/work/shim-all-names.txt" >> "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
awk -F'\t' 'NR>1{n++; if($3==0&&$4==0&&$5==0&&$2>0) ok++; files+=$2} END{printf "goldens=%d fully-identical=%d files-identical=%d\n", n, ok, files}' "$OUT"
awk -F'\t' 'NR>1 && ($3!=0||$4!=0||$5!=0||$2<=0)' "$OUT"
