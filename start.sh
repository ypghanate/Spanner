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
HEALTH_PATH="/coach/health"   # adjust if your health route differs
VIEWER_PATH="/trainer"        # adjust to the route that shows the pairing QR
API_ENV_VAR="EXPO_PUBLIC_API_URL"  # adjust to the variable your app reads

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

# ---- Backend ----
if curl --silent --fail --max-time 3 "http://127.0.0.1:$BACKEND_PORT$HEALTH_PATH" >/dev/null 2>&1; then
  echo "Backend already running on http://127.0.0.1:$BACKEND_PORT"
else
  if lsof -nP -iTCP:"$BACKEND_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "port $BACKEND_PORT is occupied by a server that is not Spanner"
  fi
  if [[ ! -d "$ROOT/.venv" ]]; then
    echo "Installing Python dependencies..."
    (cd "$ROOT" && uv sync) || die "uv sync failed"
  fi
  echo "Starting backend..."
  (
    cd "$ROOT"
    exec uv run --no-sync uvicorn backend.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
  ) >"$BACKEND_LOG" 2>&1 &
  BACKEND_PID=$!
  wait_for_url "http://127.0.0.1:$BACKEND_PORT$HEALTH_PATH" "Backend"
  echo "Backend running on http://127.0.0.1:$BACKEND_PORT"
fi

# ---- Cloudflare tunnel (public URL for the backend) ----
echo "Starting Cloudflare tunnel..."
cloudflared tunnel --no-autoupdate --protocol http2 --url "http://127.0.0.1:$BACKEND_PORT" >"$TUNNEL_LOG" 2>&1 &
TUNNEL_PID=$!

TUNNEL_URL=""
for _ in $(seq 1 60); do
  TUNNEL_URL="$(sed -nE 's#.*(https://[a-z0-9-]+\.trycloudflare\.com).*#\1#p' "$TUNNEL_LOG" | head -n 1)"
  [[ -n "$TUNNEL_URL" ]] && break
  kill -0 "$TUNNEL_PID" 2>/dev/null || die "cloudflared stopped. See $TUNNEL_LOG."
  sleep 1
done
[[ -n "$TUNNEL_URL" ]] || die "no URL from Cloudflare tunnel. See $TUNNEL_LOG."
echo "Waiting for tunnel to register with Cloudflare..."
REGISTERED=""
for _ in $(seq 1 60); do
  if grep -q "Registered tunnel connection" "$TUNNEL_LOG"; then REGISTERED=1; break; fi
  kill -0 "$TUNNEL_PID" 2>/dev/null || die "cloudflared stopped. See $TUNNEL_LOG."
  sleep 1
done
[[ -n "$REGISTERED" ]] || die "cloudflared never registered a connection. See $TUNNEL_LOG."

# Reachability from this laptop is only a hint: local DNS often can't resolve fresh
# trycloudflare.com names for a while, even though phones and other networks can.
REACHABLE=""
for _ in $(seq 1 20); do
  if curl --silent --max-time 5 -o /dev/null "$TUNNEL_URL$HEALTH_PATH"; then REACHABLE=1; break; fi
  sleep 2
done
if [[ -z "$REACHABLE" ]]; then
  echo "Warning: this laptop can't resolve/reach $TUNNEL_URL yet (often local DNS caching or a VPN)."
  echo "The tunnel is registered, so your phone should still work. Continuing."
fi
VIEWER_URL="$TUNNEL_URL$VIEWER_PATH"

# ---- Expo ----
if [[ ! -d "$APP_DIR/node_modules" ]]; then
  echo "Installing app dependencies..."
  (cd "$APP_DIR" && npm install) || die "npm install failed"
fi
# Expo's --tunnel needs @expo/ngrok; install it up front so there is no interactive prompt.
if [[ ! -d "$APP_DIR/node_modules/@expo/ngrok" ]]; then
  echo "Installing Expo tunnel support..."
  (cd "$APP_DIR" && npm install --save-dev @expo/ngrok) || die "could not install @expo/ngrok"
fi

echo "Starting Expo (this can take up to a minute)..."
(
  cd "$APP_DIR"
  export "$API_ENV_VAR=$TUNNEL_URL"
  export EXPO_NO_TELEMETRY=1
  exec npx expo start --tunnel --clear </dev/null
) >"$EXPO_LOG" 2>&1 &
EXPO_PID=$!

EXPO_URL=""
for _ in $(seq 1 180); do
  EXPO_URL="$(strip_ansi <"$EXPO_LOG" | grep -Eo 'exp://[A-Za-z0-9._-]+\.exp\.direct[^[:space:]]*' | head -n 1 || true)"
  [[ -n "$EXPO_URL" ]] && break
  kill -0 "$EXPO_PID" 2>/dev/null || die "Expo stopped. See $EXPO_LOG."
  sleep 1
done
[[ -n "$EXPO_URL" ]] || die "could not determine Expo tunnel URL. See $EXPO_LOG."

# ---- Open laptop viewer ----
if command -v open >/dev/null 2>&1; then
  open "$VIEWER_URL" >/dev/null 2>&1 || true
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$VIEWER_URL" >/dev/null 2>&1 || true
fi

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
MSG

while kill -0 "$EXPO_PID" 2>/dev/null; do
  sleep 2
done
die "Expo stopped. See $EXPO_LOG."
