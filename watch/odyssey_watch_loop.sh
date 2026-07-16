#!/bin/bash
export AMC_VENDOR_KEY=0C7A7611-F8B0-4D1F-9D40-6980D83AB4BF
export JITTER=120
export WATCH_WHENS="143822251=2026-07-18T11:00:00 143822252=2026-07-18T15:00:00 143822250=2026-07-18T23:00:00 143822247=2026-07-19T23:00:00 143822246=2026-07-21T10:00:00"

# Run watch script every 5 minutes, exit if we hit the end date (2026-07-21 13:00 ET = 2026-07-21 17:00 UTC)
while true; do
  now=$(date -u +%s)
  cutoff=$(date -d "2026-07-21 17:00 UTC" +%s 2>/dev/null || echo 9999999999)
  if [ $now -ge $cutoff ]; then
    echo "Window closed: after 2026-07-21 1 PM ET"
    exit 0
  fi
  
  bash odyssey_watch.sh
  sleep 300
done
