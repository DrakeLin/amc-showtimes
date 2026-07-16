#!/bin/bash
# Odyssey 70mm middle-seat watch at AMC Metreon (theatre 2325).
#
# Finds every 70mm showtime of The Odyssey in the next $DAYS days via the AMC
# vendor API, scrapes each showtime's seat map from amctheatres.com, and
# prints one line per showtime:
#   <showtimeId> <local datetime> middle=<seats|none> free=<n>/<total>
# plus a "NOTIFY <when>: middle seats now available: ..." line for every show
# that gained center-block seats since the previous run. State lives in
# odyssey_state.json next to this script; if the file changed, it is
# committed and pushed so runs in fresh clones share a baseline.
#
# Requires: AMC_VENDOR_KEY env var (never committed — see CONTRIBUTING.md),
# node + npm, Chromium (PW_CHROMIUM, default /opt/pw-browsers/chromium).
set -uo pipefail
cd "$(dirname "$0")"
REPO="$(cd .. && pwd)"
DAYS="${DAYS:-7}"
STATE=odyssey_state.json
: "${AMC_VENDOR_KEY:?set AMC_VENDOR_KEY}"

# --- bootstrap (idempotent) ----------------------------------------------
[ -d node_modules/playwright-core ] || npm install --no-save --silent playwright-core

# Claude Code remote sessions: Chromium must trust the egress proxy's CA via
# the NSS store, which starts empty in a fresh container.
if [ -n "${HTTPS_PROXY:-}" ] && [ -f /root/.ccr/ca-bundle.crt ]; then
  command -v certutil >/dev/null 2>&1 || { apt-get update -q && apt-get install -y -q libnss3-tools; } >/dev/null 2>&1
  mkdir -p "$HOME/.pki/nssdb"
  [ -f "$HOME/.pki/nssdb/cert9.db" ] || certutil -d sql:"$HOME/.pki/nssdb" -N --empty-password
  if ! certutil -d sql:"$HOME/.pki/nssdb" -L 2>/dev/null | grep -q ccr-proxy-0; then
    tmpd=$(mktemp -d)
    csplit -z -f "$tmpd/ca-" /root/.ccr/ca-bundle.crt '/-----BEGIN CERTIFICATE-----/' '{*}' >/dev/null
    i=0; for f in "$tmpd"/ca-*; do certutil -d sql:"$HOME/.pki/nssdb" -A -t "C,," -n "ccr-proxy-$i" -i "$f"; i=$((i+1)); done
    rm -rf "$tmpd"
  fi
fi

# --- find 70mm Odyssey showtimes ------------------------------------------
SHOWS=$(REPO="$REPO" DAYS="$DAYS" python3 - <<'EOF'
import datetime, json, os, sys
sys.path.insert(0, os.environ["REPO"])
import amc
out = []
today = datetime.date.today()
for i in range(int(os.environ["DAYS"])):
    d = today + datetime.timedelta(days=i)
    try:
        raw = amc.fetch_showtimes(2325, d)
    except Exception:
        continue
    for s in raw:
        if "odyssey" in (s.get("movieName") or "").lower() and "70mm" in (s.get("premiumFormat") or ""):
            out.append({"id": str(s["id"]), "when": s["showDateTimeLocal"]})
print(json.dumps(out))
EOF
)
ids=$(echo "$SHOWS" | python3 -c "import json,sys; print(' '.join(x['id'] for x in json.load(sys.stdin)))")
[ -z "$ids" ] && { echo "no 70mm odyssey showtimes found in next $DAYS days"; exit 0; }

# --- scrape seat maps and diff against previous state ----------------------
SEATS=$(node seatgrab.js $ids 2>/dev/null)
echo "$SHOWS" > .shows.json; echo "$SEATS" > .seats.json

python3 - <<'EOF'
import json, os
shows = {x["id"]: x for x in json.load(open(".shows.json"))}
seats = json.load(open(".seats.json"))
prev = json.load(open("odyssey_state.json")) if os.path.exists("odyssey_state.json") else {}
cur = {}
for r in seats:
    sid, when = r["id"], shows.get(r["id"], {}).get("when", "?")
    if "error" in r:
        print(f"{sid} {when} ERROR {r['error']}")
        continue
    mid = sorted(r["middle"])
    cur[sid] = mid
    print(f"{sid} {when} middle={','.join(mid) or 'none'} free={len(r['available'])}/{r['total']}")
    gained = set(mid) - set(prev.get(sid, []))
    if gained:
        print(f"NOTIFY {when}: middle seats now available: {','.join(sorted(gained))}")
json.dump(cur, open("odyssey_state.json", "w"), indent=0, sort_keys=True)
EOF
rm -f .shows.json .seats.json

# --- persist state only when it changed ------------------------------------
if ! git -C "$REPO" diff --quiet -- watch/odyssey_state.json 2>/dev/null \
   || [ -n "$(git -C "$REPO" status --porcelain watch/odyssey_state.json | grep '^??')" ]; then
  git -C "$REPO" add watch/odyssey_state.json
  git -C "$REPO" -c user.email=odyssey-watch@localhost -c user.name=odyssey-watch \
    commit -qm "watch: odyssey seat state $(date -u +%FT%TZ)" || true
  git -C "$REPO" push -q origin HEAD:claude/amc-showtime-seats-jqqtbp || true
fi
