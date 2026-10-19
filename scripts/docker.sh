#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_dir/docker-compose.yml"
postgres_file="$repo_dir/docker-compose.postgres.yml"

# Runtime configuration is intentionally ignored by Git. Seed it from the
# public, secret-free templates before Compose evaluates bind mounts/env_file.
if [[ ! -f "$repo_dir/.env" ]]; then
  cp "$repo_dir/.env.example" "$repo_dir/.env"
  chmod 600 "$repo_dir/.env"
fi
if [[ ! -f "$repo_dir/config.yaml" ]]; then
  cp "$repo_dir/config.example.yaml" "$repo_dir/config.yaml"
fi

# Keep an explicitly migrated local deployment on PostgreSQL when the user
# starts it again through this wrapper. Never source .env as shell code.
local_postgres="${MANGAFLOW_LOCAL_POSTGRES:-}"
if [[ -z "${MANGAFLOW_LOCAL_POSTGRES+x}" ]] && grep -Eiq '^[[:space:]]*MANGAFLOW_LOCAL_POSTGRES[[:space:]]*=[[:space:]]*(true|1)([[:space:]#]|$)' "$repo_dir/.env"; then
  local_postgres=true
fi

# Docker Desktop does not translate /mnt/<drive> bind mounts when its Linux
# client is connected directly to the desktop daemon. In WSL, use docker.exe
# so relative data/model paths are resolved as real Windows paths.
if grep -qi microsoft /proc/version 2>/dev/null && command -v docker.exe >/dev/null 2>&1; then
  docker_command=docker.exe
  compose_file="$(wslpath -w "$compose_file")"
  postgres_file="$(wslpath -w "$postgres_file")"
  repo_dir="$(wslpath -w "$repo_dir")"
else
  docker_command=docker
fi

compose_args=(--project-name mangaflow --project-directory "$repo_dir" -f "$compose_file")
if [[ "$local_postgres" == true || "$local_postgres" == 1 ]]; then
  compose_args+=(-f "$postgres_file")
fi
exec "$docker_command" compose "${compose_args[@]}" "$@"
