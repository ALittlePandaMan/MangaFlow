#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$repo_dir/scripts/prepare-config.sh"
compose_file="$repo_dir/docker-compose.yml"
project_name="${MANGAFLOW_COMPOSE_PROJECT_NAME:-mangaflow}"
if grep -qi microsoft /proc/version 2>/dev/null && command -v docker.exe >/dev/null 2>&1; then
  docker_command=docker.exe
  compose_file="$(wslpath -w "$compose_file")"
  repo_dir="$(wslpath -w "$repo_dir")"
else
  docker_command=docker
fi
exec "$docker_command" compose --project-name "$project_name" --project-directory "$repo_dir" --env-file "$repo_dir/.env" -f "$compose_file" "$@"
