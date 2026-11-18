#!/usr/bin/env bash
# End-to-end test of the iOS app in a simulator against the real PC program (run on a Mac).
#
#   SIM_UDID=<booted simulator with the app installed> bash mobile/e2e/run_ios.sh [screenshot folder]
#
# Puts photos into the simulator's library, starts `tuckvault serve`, drives the app with
# Maestro (ios-flow.yaml) through setup, a backup and a restore on the phone, and then lets the
# PC program restore the phone's backup with the recovery key the app showed. What comes back
# must be, byte for byte, what was put into the library.
set -euo pipefail

: "${SIM_UDID:?set SIM_UDID to the id of the simulator}"
REPO=$(cd "$(dirname "$0")/../.." && pwd)
SHOTS=${1:-"$REPO/ios-shots"}
WORK=$(mktemp -d)
PY=${PYTHON:-python3}
mkdir -p "$SHOTS" "$WORK/home"
cd "$REPO"

echo "[e2e] test photos into the simulator's library"
"$PY" -c "
import sys
from pathlib import Path
sys.path.insert(0, 'mobile/e2e')
from run_android import make_media
make_media(Path('$WORK/media'))"
xcrun simctl addmedia "$SIM_UDID" "$WORK"/media/TVTEST_000{1,2,3}.jpg

echo "[e2e] the PC side"
export TUCKVAULT_HOME="$WORK/home" TUCKVAULT_SERVE_IDLE=2 PYTHONUNBUFFERED=1
printf '{"destination": {"type": "local", "folder": "%s"}}' "$WORK/repo" > "$TUCKVAULT_HOME/config.json"
"$PY" -m tuckvault serve --listen 127.0.0.1 > "$WORK/serve.log" 2>&1 &
SERVE=$!
trap 'kill $SERVE 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  "$PY" -m tuckvault phone status | grep -q "Accepting backups from phones: yes" && break
  sleep 0.2
done
CODE=$("$PY" -m tuckvault phone pair | awk '/Code:/ {print $2}')
test -n "$CODE"

echo "[e2e] the app: setup, backup, restore on the phone"
# If the flow stops, keep what explains it: the screen, what Maestro saw, the app's own log, the PC's log.
explain() {
  xcrun simctl io "$SIM_UDID" screenshot "$SHOTS/ios-failure.png" || true
  cp -R "$HOME/.maestro/tests" "$SHOTS/maestro-debug" 2>/dev/null || true
  xcrun simctl spawn "$SIM_UDID" log show --last 6m --style compact \
    --predicate 'process == "Tuckvault"' > "$SHOTS/app-log.txt" 2>&1 || true
  cp "$WORK/serve.log" "$SHOTS/serve-log.txt" 2>/dev/null || true
  cp "$TUCKVAULT_HOME/tuckvault.log" "$SHOTS/tuckvault-log.txt" 2>/dev/null || true
  echo "[e2e] the flow stopped; the last lines of the app's log:"
  grep -iE "error|exception|fatal|tuckvault" "$SHOTS/app-log.txt" | tail -40 || true
}
# On a busy machine Maestro's helper app can be slow to start inside the simulator; give it time,
# and if it still was not ready (the app was never touched), try once more with a fresh code.
export MAESTRO_DRIVER_STARTUP_TIMEOUT=${MAESTRO_DRIVER_STARTUP_TIMEOUT:-300000}
flow() {
  (cd "$SHOTS" && maestro --device "$SIM_UDID" test -e CODE="$CODE" "$REPO/mobile/e2e/ios-flow.yaml") 2>&1 | tee "$WORK/maestro.log"
  return "${PIPESTATUS[0]}"
}
if ! flow; then
  if grep -q "driver not ready in time" "$WORK/maestro.log"; then
    echo "[e2e] the test driver was not ready; trying once more"
    CODE=$("$PY" -m tuckvault phone pair | awk '/Code:/ {print $2}')
    flow || { explain; exit 1; }
  else
    explain
    exit 1
  fi
fi

# Maestro keeps its screenshots in its own folder; bring them over (all but the recovery key).
find "$HOME/.maestro/tests" -path "*takeScreenshot*" -name "ios-*.png" ! -name "*recovery-key*" \
  -exec cp {} "$SHOTS/" \; 2>/dev/null || true

echo "[e2e] the PC restores the phone's backup with the recovery key the app showed"
KEY=$(xcrun simctl pbpaste "$SIM_UDID")
# (kept simple on purpose: the bash that ships with macOS is old)
KEY_LENGTH=$(printf %s "$KEY" | tr -d - | wc -c | tr -d " ")
test "$KEY_LENGTH" = 56
TUCKVAULT_PHONE_KEY="$KEY" "$PY" -m tuckvault phone restore "$WORK/out"
"$PY" - "$WORK" <<'PYEOF'
import hashlib, sys
from pathlib import Path
work = Path(sys.argv[1])
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
put = {sha(p): p.name for p in (work / "media").glob("TVTEST_000[123].jpg")}
got = {sha(p): p for p in (work / "out").rglob("*") if p.is_file()}
print("[e2e] put into the library:", sorted(put.values()))
print("[e2e] restored by the PC:  ", sorted(str(p.relative_to(work / "out")) for p in got.values()))
missing = [name for digest, name in put.items() if digest not in got]
assert not missing, "not restored byte for byte: %s" % missing
stored = b"".join(p.read_bytes() for p in (work / "repo-phones").rglob("*")
                  if p.is_file() and p.parent.parent.name == "blobs")
assert b"Tuckvault test" not in stored, "plain data found in what is stored"
print("[e2e] ALL CHECKS PASSED: %d photos identical, nothing readable stored" % len(put))
PYEOF
