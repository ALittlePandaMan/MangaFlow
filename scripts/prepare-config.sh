#!/usr/bin/env bash
# Shared, secret-safe first-run configuration. Never source the user's .env.
set -euo pipefail

if [[ ! -f "$repo_dir/.env" ]]; then
  cp "$repo_dir/.env.example" "$repo_dir/.env"
  chmod 600 "$repo_dir/.env"
fi
if [[ ! -f "$repo_dir/config.yaml" ]]; then
  cp "$repo_dir/config.example.yaml" "$repo_dir/config.yaml"
fi
if [[ -z "${MANGAFLOW_POSTGRES_PASSWORD:-}" ]] && ! awk '
  /^[[:space:]]*MANGAFLOW_POSTGRES_PASSWORD[[:space:]]*=/ {
    value = $0
    sub(/^[^=]*=[[:space:]]*/, "", value)
    sub(/[[:space:]]+#.*$/, "", value)
    sub(/[[:space:]]*$/, "", value)
    gsub(/^[\047"]|[\047"]$/, "", value)
    found = length(value) > 0 && value !~ /^#/
  }
  END { exit !found }
' "$repo_dir/.env"; then
  if ! command -v openssl >/dev/null 2>&1; then
    printf 'Set a strong MANGAFLOW_POSTGRES_PASSWORD in the private .env file.\n' >&2
    exit 1
  fi
  password="$(openssl rand -hex 32)"
  sed -i '/^[[:space:]]*MANGAFLOW_POSTGRES_PASSWORD[[:space:]]*=/d' "$repo_dir/.env"
  printf '\nMANGAFLOW_POSTGRES_PASSWORD=%s\n' "$password" >> "$repo_dir/.env"
  unset password
fi
