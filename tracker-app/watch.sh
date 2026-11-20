#!/bin/bash
# Keep tracker.json in sync with TRACKER.md and progress/*.log: poll a content fingerprint every 5 s and,
# when it changes (after it has been stable for 2 s), regenerate with gen.py, self-check with check.py,
# and publish atomically. A failed run leaves the last good tracker.json in place.
# usage: tracker-app/watch.sh        (runs until killed; one log line per regeneration)
cd "$(dirname "$0")/.." || exit 1
shopt -s nullglob
fp() { cat TRACKER.md progress/*.log 2>/dev/null | shasum -a 256 | cut -c1-16; }
last=""; trigger=startup
while true; do
  f=$(fp)
  if [ "$f" != "$last" ]; then
    sleep 2; [ "$(fp)" = "$f" ] || continue          # still changing: wait for it to settle
    if /usr/bin/python3 -I tracker-app/gen.py "$trigger" >/dev/null 2>&1 \
       && out=$(/usr/bin/python3 -I tracker-app/check.py tracker-app/tracker.json.tmp 2>&1); then
      mv -f tracker-app/tracker.json.tmp tracker-app/tracker.json
      echo "$(date -u +%FT%TZ) regen ok ($trigger): ${out#self-check ok: }"
    else
      echo "$(date -u +%FT%TZ) regen FAILED ($trigger): ${out:-gen.py error}" | head -3
    fi
    last=$f; trigger=change
  fi
  sleep 5
done
