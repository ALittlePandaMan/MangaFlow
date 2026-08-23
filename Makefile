.DEFAULT_GOAL := help

.PHONY: help bootstrap dev lint test build check docker-up docker-down docker-logs

help:
	@printf '%s\n' \
	  'MangaFlow development commands:' \
	  '  make bootstrap   Install local development dependencies' \
	  '  make dev         Start backend and frontend development servers' \
	  '  make lint        Run backend and frontend linters' \
	  '  make test        Run backend tests' \
	  '  make build       Build the frontend production bundle' \
	  '  make check       Run the complete local quality gate' \
	  '  make docker-up   Build and start the Docker stack' \
	  '  make docker-down Stop the Docker stack' \
	  '  make docker-logs Follow Docker logs'

bootstrap:
	./scripts/bootstrap.sh

dev:
	./scripts/dev.sh

lint:
	.venv/bin/ruff check backend/app backend/tests
	cd frontend && npm run lint

test:
	TMPDIR=$${TMPDIR:-/tmp} .venv/bin/pytest -q

build:
	cd frontend && npm run build

check:
	./scripts/check.sh

docker-up:
	./scripts/docker.sh up --build -d

docker-down:
	./scripts/docker.sh down

docker-logs:
	./scripts/docker.sh logs -f
