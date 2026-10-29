#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Read only the literal runtime selector; never evaluate the private .env.
runtime="${MANGAFLOW_RUNTIME-}"
if [[ ! ${MANGAFLOW_RUNTIME+x} ]]; then
  runtime=gpu
  if [[ -f "$repo_dir/.env" ]]; then
    runtime="$(awk '
      BEGIN { value = "gpu" }
      /^[[:space:]]*(export[[:space:]]+)?MANGAFLOW_RUNTIME[[:space:]]*=/ {
        value = $0
        sub(/^[^=]*=[[:space:]]*/, "", value)
        sub(/[[:space:]]+#.*$/, "", value)
        sub(/[[:space:]\r]*$/, "", value)
        if (value ~ /^".*"$/ || value ~ /^\047.*\047$/) value = substr(value, 2, length(value)-2)
      }
      END { print value }
    ' "$repo_dir/.env")"
  fi
fi
case "$runtime" in
  gpu|cpu) ;;
  *) printf 'MANGAFLOW_RUNTIME must be gpu or cpu.\n' >&2; exit 1 ;;
esac
export MANGAFLOW_RUNTIME="$runtime"
source "$repo_dir/scripts/prepare-config.sh"
compose_file="$repo_dir/docker-compose.yml"
cpu_file="$repo_dir/docker-compose.cpu.yml"
if [[ "$runtime" == cpu && ! -f "$cpu_file" ]]; then
  printf 'CPU deployment configuration is missing; update the deployment files.\n' >&2; exit 1
fi
project_name="${MANGAFLOW_COMPOSE_PROJECT_NAME:-mangaflow}"
if grep -qi microsoft /proc/version 2>/dev/null && command -v docker.exe >/dev/null 2>&1; then
  docker_command=docker.exe
  compose_file="$(wslpath -w "$compose_file")"
  cpu_file="$(wslpath -w "$cpu_file")"
  repo_dir="$(wslpath -w "$repo_dir")"
else
  docker_command=docker
fi
for argument in "$@"; do
case "$argument" in
  pull|up|build|create|run|start|restart|watch)
    platform="$("$docker_command" info --format '{{.OSType}}/{{.Architecture}}')"
    case "$platform" in
      linux/amd64|linux/x86_64) ;;
      *) printf 'MangaFlow Docker currently requires a Linux amd64 server; automatic architecture emulation is unsupported.\n' >&2; exit 1 ;;
    esac
    break
    ;;
esac
done
compose=("$docker_command" compose --project-name "$project_name" --project-directory "$repo_dir" --env-file "$repo_dir/.env" -f "$compose_file")
if [[ "$runtime" == cpu ]]; then
  compose+=(-f "$cpu_file")
fi
exec "${compose[@]}" "$@"
