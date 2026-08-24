#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -f "$repo_dir/.env" ]]; then
  cp "$repo_dir/.env.example" "$repo_dir/.env"
  printf 'Created %s from .env.example\n' "$repo_dir/.env"
fi

if [[ ! -f "$repo_dir/config.yaml" ]]; then
  cp "$repo_dir/config.example.yaml" "$repo_dir/config.yaml"
  printf 'Created %s from config.example.yaml\n' "$repo_dir/config.yaml"
fi

if [[ ! -x "$repo_dir/.venv/bin/python" ]]; then
  python3 -m venv "$repo_dir/.venv"
fi

"$repo_dir/.venv/bin/pip" install -r "$repo_dir/backend/requirements/dev.txt"
(cd "$repo_dir/frontend" && npm ci)

printf 'MangaFlow development environment is ready.\n'
