#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$repo_dir/.venv/bin/ruff" check "$repo_dir/backend/app" "$repo_dir/backend/tests"
"$repo_dir/.venv/bin/pytest" -q
(cd "$repo_dir/frontend" && npm run build)

