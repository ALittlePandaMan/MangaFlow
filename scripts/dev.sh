#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$repo_dir/.venv/bin/python" || ! -d "$repo_dir/frontend/node_modules" ]]; then
  "$repo_dir/scripts/bootstrap.sh"
else
  [[ -f "$repo_dir/.env" ]] || cp "$repo_dir/.env.example" "$repo_dir/.env"
  [[ -f "$repo_dir/config.yaml" ]] || cp "$repo_dir/config.example.yaml" "$repo_dir/config.yaml"
fi

cleanup() {
  kill "${backend_pid:-}" "${frontend_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

(cd "$repo_dir/backend" && "$repo_dir/.venv/bin/uvicorn" app.main:app --reload --port 8000) &
backend_pid=$!
(cd "$repo_dir/frontend" && npm run dev) &
frontend_pid=$!
wait
