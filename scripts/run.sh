#!/usr/bin/env bash
# Power Peasant console control for Linux/macOS.
#
#   ./scripts/run.sh start            # background, logs + PID in data/
#   ./scripts/run.sh start --port 8800
#   ./scripts/run.sh status
#   ./scripts/run.sh logs --follow
#   ./scripts/run.sh stop
#   ./scripts/run.sh foreground       # Ctrl+C to stop
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/data"
PID_FILE="$DATA/monitor.pid"
LOG="$DATA/monitor.log"
ERR="$DATA/monitor.err"
ENTRY="$ROOT/scripts/pq_monitor.py"

ACTION="${1:-start}"
shift || true

PORT=8791
HOST=127.0.0.1
FOLLOW=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)   PORT="${2:?port value required}"; shift 2 ;;
    -H|--host)   HOST="${2:?host value required}"; shift 2 ;;
    -f|--follow) FOLLOW=1; shift ;;
    -h|--help)   echo "usage: $0 {start|stop|restart|status|logs|foreground} [--port N] [--host ADDR] [--follow]"; exit 0 ;;
    *)           echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

HEALTH_HOST="$HOST"
if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]]; then HEALTH_HOST=127.0.0.1; fi
URL="http://$HEALTH_HOST:$PORT/"

pick_python() {
  local cand
  for cand in "${PYTHON:-}" "$ROOT/.venv/bin/python" "$(command -v python3 2>/dev/null || true)" "$(command -v python 2>/dev/null || true)"; do
    [[ -n "$cand" ]] || continue
    if "$cand" -c 'import sys' >/dev/null 2>&1; then
      echo "$cand"
      return 0
    fi
  done
  echo "no working python3/python found (install Python 3 or set PYTHON=...)" >&2
  exit 1
}

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

wait_http() {
  command -v curl >/dev/null 2>&1 || { sleep 2; return 0; }
  local i
  for i in $(seq 1 30); do
    curl -fsS --max-time 1 "${URL}api/config" >/dev/null 2>&1 && return 0
    sleep 0.5
  done
  return 1
}

start() {
  mkdir -p "$DATA"
  if is_running; then
    echo "Already running (PID $(cat "$PID_FILE"))."
    echo "URL: $URL"
    return 0
  fi
  rm -f "$PID_FILE"
  local py
  py="$(pick_python)"
  echo "Starting with $py ..."
  nohup "$py" -u "$ENTRY" --no-browser --host "$HOST" --port "$PORT" >>"$LOG" 2>>"$ERR" &
  echo $! > "$PID_FILE"
  if wait_http; then
    echo "Started (PID $(cat "$PID_FILE"))."
    echo "URL: $URL"
    if [[ "$HOST" == "0.0.0.0" || "$HOST" == "::" ]]; then
      echo "Bound to $HOST - open http://<this-host-ip>:$PORT/ from other devices (no auth!)"
    fi
  else
    echo "Started (PID $(cat "$PID_FILE")), but HTTP is not answering yet."
    echo "URL: $URL"
    echo "Check: $ERR"
  fi
}

stop() {
  if ! is_running; then
    echo "Not running."
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  echo "Stopping PID $pid ..."
  kill "$pid" 2>/dev/null || true
  local i
  for i in $(seq 1 20); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  echo "Stopped."
}

status() {
  if is_running; then
    local code="n/a"
    if command -v curl >/dev/null 2>&1; then
      code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$URL" || true)"
    fi
    echo "Status: running (PID $(cat "$PID_FILE"))"
    echo "HTTP: $code"
    echo "URL: $URL"
  else
    echo "Status: not running"
    echo "URL: $URL (not reachable)"
  fi
}

logs() {
  if [[ ! -f "$LOG" ]]; then
    echo "No log yet: $LOG"
    return 0
  fi
  if [[ "$FOLLOW" == "1" ]]; then
    tail -n 40 -f "$LOG"
  else
    tail -n 40 "$LOG"
  fi
  if [[ -s "$ERR" ]]; then
    echo
    echo "--- stderr ---"
    tail -n 20 "$ERR"
  fi
}

foreground() {
  if is_running; then
    echo "Already running in the background. Run: $0 stop" >&2
    exit 1
  fi
  local py
  py="$(pick_python)"
  echo "URL: $URL"
  echo "Ctrl+C to stop."
  exec "$py" -u "$ENTRY" --no-browser --host "$HOST" --port "$PORT"
}

case "$ACTION" in
  start)      start ;;
  stop)       stop ;;
  restart)    stop; sleep 1; start ;;
  status)     status ;;
  logs)       logs ;;
  foreground) foreground ;;
  *)          echo "usage: $0 {start|stop|restart|status|logs|foreground} [--port N] [--follow]" >&2; exit 2 ;;
esac
