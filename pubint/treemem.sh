#!/bin/bash
# Like scripts/peakmem.sh, but over the whole process tree (the publisher JVM and, in the default mode, the
# Jekyll process it forks through a shell): reports the peak of the tree's summed RSS (sampled every 0.2 s)
# and the largest single-process high-water mark (VmHWM).
# usage (inside a container): treemem.sh <cmd> [args...] -> "tree_peak_rss_mb=<n> max_proc_hwm_mb=<n> wall_s=<n> exit=<n>" on stderr
start=$(date +%s.%N)
"$@" &
pid=$!
desc() { local p; for p in $(pgrep -P "$1" 2>/dev/null); do echo "$p"; desc "$p"; done; }
tree=0; hwm=0
while kill -0 "$pid" 2>/dev/null; do
  sum=0
  for p in $pid $(desc $pid); do
    read -r r h < <(awk '/VmRSS/{r=$2} /VmHWM/{h=$2} END{print r+0, h+0}' /proc/$p/status 2>/dev/null)
    sum=$((sum + ${r:-0})); [ "${h:-0}" -gt "$hwm" ] && hwm=$h
  done
  [ "$sum" -gt "$tree" ] && tree=$sum
  sleep 0.2
done
wait "$pid"; rc=$?
end=$(date +%s.%N)
echo "tree_peak_rss_mb=$((tree / 1024)) max_proc_hwm_mb=$((hwm / 1024)) wall_s=$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.1f", b-a}') exit=$rc" >&2
exit $rc
