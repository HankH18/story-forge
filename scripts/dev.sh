#!/usr/bin/env bash
# Run the API and the web dev server together (§0: `make dev` boots both).
#
# Deliberately dependency-free bash rather than a task-runner package, and
# written for macOS's system bash 3.2: no `wait -n`, no associative arrays.
#
# We kill the recorded PIDs explicitly and never `kill 0` — under make, the
# process group includes make itself, so `kill 0` would take down the parent and
# orphan uvicorn holding port 8000.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

API_PID=""
WEB_PID=""

cleanup() {
  trap - INT TERM EXIT
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
  [ -n "$API_PID" ] && wait "$API_PID" 2>/dev/null || true
  [ -n "$WEB_PID" ] && wait "$WEB_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "==> api  http://localhost:8000  (docs at /docs)"
(
  cd "$ROOT/services/api"
  exec uv run uvicorn storyforge.main:app --reload --port 8000
) &
API_PID=$!

echo "==> web  http://localhost:5173"
(
  cd "$ROOT/apps/web"
  exec bun run dev
) &
WEB_PID=$!

# bash 3.2 has no `wait -n`, so poll instead. Surfaces a crash in either service
# rather than hanging on the survivor.
while kill -0 "$API_PID" 2>/dev/null && kill -0 "$WEB_PID" 2>/dev/null; do
  sleep 1
done

echo "!! a dev service exited — shutting the other down" >&2
exit 1
