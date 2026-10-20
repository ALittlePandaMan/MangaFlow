#!/usr/bin/env bash
# Full PostgreSQL-backed suite, with an automatically removed test database.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
if [[ -n "${MANGAFLOW_TEST_POSTGRES_URL:-}" ]]; then
  "$repo_dir/.venv/bin/pytest" "$@"
  exit
fi
docker_command=docker
if grep -qi microsoft /proc/version 2>/dev/null && command -v docker.exe >/dev/null 2>&1; then
  docker_command=docker.exe
fi
password="$("$repo_dir/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(24))')"
container="$("$docker_command" run --rm -d -e POSTGRES_USER=mangaflow -e POSTGRES_DB=mangaflow_test_suite \
  -e "POSTGRES_PASSWORD=$password" -p 127.0.0.1::5432 postgres:18-bookworm | tr -d '\r')"
cleanup() { "$docker_command" rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT
for _ in {1..30}; do
  if "$docker_command" exec "$container" pg_isready -U mangaflow -d mangaflow_test_suite >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
port="$("$docker_command" inspect -f '{{(index (index .NetworkSettings.Ports "5432/tcp") 0).HostPort}}' "$container" | tr -d '\r')"
export MANGAFLOW_TEST_POSTGRES_URL="postgresql+psycopg://mangaflow:$password@127.0.0.1:$port/mangaflow_test_suite"
"$repo_dir/.venv/bin/pytest" "$@"
