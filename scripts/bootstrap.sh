#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

source "$repo_dir/scripts/prepare-config.sh"

if [[ ! -x "$repo_dir/.venv/bin/python" ]]; then
  python3 -m venv "$repo_dir/.venv"
fi

"$repo_dir/.venv/bin/pip" install -r "$repo_dir/backend/requirements/dev.txt"
(cd "$repo_dir/frontend" && npm ci)

printf 'MangaFlow development environment is ready.\n'
