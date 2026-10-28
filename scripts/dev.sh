#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$repo_dir/.venv/bin/python" || ! -d "$repo_dir/frontend/node_modules" ]]; then
  "$repo_dir/scripts/bootstrap.sh"
else
  source "$repo_dir/scripts/prepare-config.sh"
fi

# Resolve only the development ports with the same dotenv rules as Vite.
# Do not source private .env content as shell code.
ports="$(node "$repo_dir/frontend/devServer.mjs")"
read -r api_port frontend_port <<< "$ports"

cleanup() {
  local pids=()
  for pid in "${backend_pid:-}" "${frontend_pid:-}"; do
    if [[ -n "$pid" ]]; then pids+=("$pid"); fi
  done
  if [[ ${#pids[@]} -gt 0 ]]; then
    if ! "$repo_dir/.venv/bin/python" "$repo_dir/scripts/dev_backend.py" --stop "${pids[@]}"; then
      # A broken Python environment must not leave the shell waiting forever.
      for pid in "${pids[@]}"; do kill -KILL "$pid" 2>/dev/null || true; done
    fi
  fi
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(cd "$repo_dir/backend" && exec "$repo_dir/.venv/bin/python" "$repo_dir/scripts/dev_backend.py" --port "$api_port") &
backend_pid=$!
(cd "$repo_dir/frontend" && exec node node_modules/vite/bin/vite.js --host 0.0.0.0 --port "$frontend_port" --strictPort) &
frontend_pid=$!
# A failed backend must not leave Vite proxying an unrelated service, and a
# failed frontend must not leave a new backend/reloader running unnoticed.
wait -n "$backend_pid" "$frontend_pid"
