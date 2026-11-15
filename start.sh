#!/usr/bin/env bash
# Spanner: one command starts backend + public tunnel + Expo (phone) + opens the laptop viewer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$ROOT/app"
RUN_DIR="${TMPDIR:-/tmp}/spanner-run"
mkdir -p "$RUN_DIR"

BACKEND_LOG="$RUN_DIR/backend.log"
TUNNEL_LOG="$RUN_DIR/cloudflared.log"
EXPO_LOG="$RUN_DIR/expo.log"
: >"$BACKEND_LOG"; : >"$TUNNEL_LOG"; : >"$EXPO_LOG"

BACKEND_PORT=8000
HEALTH_PATH="/coach/health"
VIEWER_PATH="/training/viewer/"   # laptop viewer: hologram + pairing QR
API_ENV_VAR="EXPO_PUBLIC_BACKEND_URL"  # read by app/src/lib/config.ts

BACKEND_PID=""
TUNNEL_PID=""
EXPO_PID=""

kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

cleanup() {
  trap - EXIT INT TERM
  echo
  echo "Stopping Spanner..."
  local pid
  for pid in "$EXPO_PID" "$TUNNEL_PID" "$BACKEND_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill_tree "$pid"
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

die() {
  echo "Spanner could not start: $*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' is required. Install it, then run ./start.sh again."
}

wait_for_url() {
  local url="$1" label="$2" attempts=0
  until curl --silent --fail --max-time 3 "$url" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    (( attempts < 60 )) || die "$label did not become ready ($url). Logs: $RUN_DIR"
    sleep 1
  done
}

strip_ansi() { sed -E 's/\x1b\[[0-9;?]*[A-Za-z]//g'; }

for cmd in curl uv node npm npx python3 cloudflared lsof; do
  require_command "$cmd"
done
[[ -d "$APP_DIR" ]] || die "app directory not found: $APP_DIR"

# Everything slow runs in parallel: the tunnel only needs the backend's port (not a healthy backend), Expo only needs the
# tunnel's URL (not a registered tunnel), and none of them wait for the others to be ready until the very end.
# Flags: --clear  rebuild Expo's Metro cache (slow; only after odd bundler errors)
EXPO_CLEAR=""
for arg in "$@"; do [[ "$arg" == "--clear" ]] && EXPO_CLEAR="--clear"; done

# ---- Cloudflare tunnel (public URL for the backend): start first, it takes the longest to be reachable ----
echo "Starting Cloudflare tunnel..."
cloudflared tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:$BACKEND_PORT" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

# ---- Backend (in the background while the tunnel gets its URL) ----
BACKEND_STARTED=""
if curl --silent --fail --max-time 2 "http://127.0.0.1:$BACKEND_PORT$HEALTH_PATH" >/dev/null 2>&1; then
  echo "Backend already running on http://127.0.0.1:$BACKEND_PORT"
else
  if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "port $BACKEND_PORT is occupied by a server that is not Spanner"
  fi
  # Always sync: a no-op when up to date, and picks up dependencies added by a pull (e.g. qrcode for the pairing QR).
  (cd "$ROOT" && uv sync --quiet) || die "uv sync failed"
  echo "Starting backend..."
  (
    cd "$ROOT"
    exec uv run --no-sync uvicorn backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
  ) >"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  BACKEND_STARTED=1
fi

TUNNEL_URL=""
for _ in $(seq 1 60); do
  TUNNEL_URL="$(sed -nE 's#.*(https://[a-z0-9-]+\.trycloudflare\.com).*#\1#p' "$TUNNEL_LOG" | head -n 1)"
  [[ -n "$TUNNEL_URL" ]] && break
  kill -0 "$TUNNEL_PID" 2>/dev/null || die "cloudflared stopped. See $TUNNEL_LOG."
  sleep 0.5
done
[[ -n "$TUNNEL_URL" ]] || die "no URL from Cloudflare tunnel. See $TUNNEL_LOG."
echo "Tunnel: $TUNNEL_URL"

# ---- Expo (starts as soon as the tunnel URL exists) ----
if [[ ! -d "$APP_DIR/node_modules" ]]; then
  echo "Installing app dependencies..."
  (cd "$APP_DIR" && npm install) || die "npm install failed"
fi
# Expo's --tunnel needs @expo/ngrok (it is in app/package.json); only install if it is somehow missing.
if [[ ! -d "$APP_DIR/node_modules/@expo/ngrok" ]]; then
  echo "Installing Expo tunnel support..."
  (cd "$APP_DIR" && npm install --save-dev @expo/ngrok) || die "could not install @expo/ngrok"
fi

echo "Starting Expo..."
(
  cd "$APP_DIR"
  export "$API_ENV_VAR=$TUNNEL_URL"
  export EXPO_NO_TELEMETRY=1
  exec npx expo start --tunnel $EXPO_CLEAR </dev/null
) >"$EXPO_LOG" 2>&1 &
EXPO_PID=$!

# ---- Now wait for all three, in whatever order they finish ----
[[ -n "$BACKEND_STARTED" ]] && { wait_for_url "http://127.0.0.1:$BACKEND_PORT$HEALTH_PATH" "Backend"; echo "Backend ready"; }

for _ in $(seq 1 120); do
  grep -q "Registered tunnel connection" "$TUNNEL_LOG" && break
  kill -0 "$TUNNEL_PID" 2>/dev/null || die "cloudflared stopped. See $TUNNEL_LOG."
  sleep 0.5
done
grep -q "Registered tunnel connection" "$TUNNEL_LOG" || die "cloudflared never registered a connection. See $TUNNEL_LOG."
echo "Tunnel registered"
VIEWER_URL="$TUNNEL_URL$VIEWER_PATH"

# Open the laptop viewer now (the browser retries the name on its own); Expo may still be bundling.
if command -v open >/dev/null 2>&1; then
  open "$VIEWER_URL" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$VIEWER_URL" >/dev/null 2>&1 || true
fi

EXPO_URL=""
for _ in $(seq 1 480); do
  CLEAN="$(strip_ansi <"$EXPO_LOG")"
  # Older Expo prints exp://<id>.exp.direct; Expo 58 prints "Waiting on https://<host>.on.expo.app" (Expo Go uses exp://<host>).
  EXPO_URL="$(printf '%s\n' "$CLEAN" | grep -Eo 'exp://[A-Za-z0-9._-]+\.exp\.direct[^[:space:]]*' | head -n 1 || true)"
  if [[ -z "$EXPO_URL" ]]; then
    EXPO_HOST="$(printf '%s\n' "$CLEAN" | sed -nE 's#.*Waiting on https://([A-Za-z0-9.-]+\.expo\.app).*#\1#p' | head -n 1)"
    [[ -n "$EXPO_HOST" ]] && EXPO_URL="exp://$EXPO_HOST"
  fi
  [[ -n "$EXPO_URL" ]] && break
  kill -0 "$EXPO_PID" 2>/dev/null || die "Expo stopped. See $EXPO_LOG."
  sleep 0.5
done
[[ -n "$EXPO_URL" ]] || die "could not determine Expo tunnel URL. See $EXPO_LOG."

cat <<MSG

Spanner is running.

Laptop viewer:  $VIEWER_URL
Expo Go:        $EXPO_URL

On your phone:
1. Open Expo Go and enter the Expo Go address above.
2. Tap "Pair with laptop".
3. Scan the QR shown in the laptop viewer.

Logs:
$BACKEND_LOG
$TUNNEL_LOG
$EXPO_LOG

Keep this terminal open. Press Ctrl-C to stop everything started by this command.
(Slow or odd bundler errors? Run ./start.sh --clear to rebuild Expo's cache.)
MSG

while kill -0 "$EXPO_PID" 2>/dev/null; do
  sleep 2
done
die "Expo stopped. See $EXPO_LOG."
