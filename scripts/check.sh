#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export TMPDIR="${TMPDIR:-/tmp}"
if [[ ! -x "$repo_dir/.venv/bin/ruff" || ! -d "$repo_dir/frontend/node_modules" ]]; then
  printf 'Development dependencies are missing. Run ./scripts/bootstrap.sh first.\n' >&2
  exit 1
fi

"$repo_dir/.venv/bin/ruff" check "$repo_dir/backend/app" "$repo_dir/backend/tests" "$repo_dir/scripts"
bash "$repo_dir/scripts/test-backend.sh" -q
(cd "$repo_dir/frontend" && npm run lint && npm run i18n:check && npm run visual:typecheck && npm test && npm run build)
