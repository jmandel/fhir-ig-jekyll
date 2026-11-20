#!/bin/bash
# Run a command and report its peak resident memory (VmHWM of the process and its children, polled).
# usage (inside a container): peakmem.sh <cmd> [args...]   -> prints "peak_rss_mb=<n> wall_s=<n> exit=<n>" on stderr
start=$(date +%s.%N)
"$@" &
pid=$!
peak=0
while kill -0 "$pid" 2>/dev/null; do
  for p in $pid $(pgrep -P "$pid" 2>/dev/null); do
    v=$(awk '/VmHWM/{print $2}' /proc/$p/status 2>/dev/null)
    [ -n "$v" ] && [ "$v" -gt "$peak" ] && peak=$v
  done
  sleep 0.2
done
wait "$pid"; rc=$?
end=$(date +%s.%N)
echo "peak_rss_mb=$((peak / 1024)) wall_s=$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.1f", b-a}') exit=$rc" >&2
exit $rc
