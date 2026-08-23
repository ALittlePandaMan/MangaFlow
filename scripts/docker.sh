#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_dir/docker-compose.yml"

# Runtime configuration is intentionally ignored by Git. Seed it from the
# public, secret-free templates before Compose evaluates bind mounts/env_file.
if [[ ! -f "$repo_dir/.env" ]]; then
  cp "$repo_dir/.env.example" "$repo_dir/.env"
  chmod 600 "$repo_dir/.env"
fi
if [[ ! -f "$repo_dir/config.yaml" ]]; then
  cp "$repo_dir/config.example.yaml" "$repo_dir/config.yaml"
fi

# Docker Desktop does not translate /mnt/<drive> bind mounts when its Linux
# client is connected directly to the desktop daemon. In WSL, use docker.exe
# so relative data/model paths are resolved as real Windows paths.
if grep -qi microsoft /proc/version 2>/dev/null && command -v docker.exe >/dev/null 2>&1; then
  compose_windows="$(wslpath -w "$compose_file")"
  exec docker.exe compose --project-name mangaflow -f "$compose_windows" "$@"
fi

exec docker compose --project-name mangaflow -f "$compose_file" "$@"
