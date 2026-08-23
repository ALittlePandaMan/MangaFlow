#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$repo_dir/.venv/bin/python" ]]; then
  python3 -m venv "$repo_dir/.venv"
  "$repo_dir/.venv/bin/pip" install -r "$repo_dir/backend/requirements/dev.txt"
fi

cleanup() {
  kill "${backend_pid:-}" "${frontend_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd "$repo_dir/backend" && "$repo_dir/.venv/bin/uvicorn" app.main:app --reload --port 8000) &
backend_pid=$!
(cd "$repo_dir/frontend" && npm install && npm run dev) &
frontend_pid=$!
wait

