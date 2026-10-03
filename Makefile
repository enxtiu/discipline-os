# DisciplineOS — developer shortcuts.
# Everything here is meant to be run from the repository root.

SHELL := /bin/bash
API_DIR := api
WEB_DIR := web
PY := $(API_DIR)/.venv/bin/python
PIP := $(API_DIR)/.venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help venv install dev dev-db api web migrate migration fix-migration test lint fmt typecheck gen-api check-i18n ui-check seed-foods build up down logs psql backup restore seed clean

help: ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --------------------------------------------------------------------------- #
# Local development
# --------------------------------------------------------------------------- #
venv: ## create the python virtualenv
	python3.12 -m venv $(API_DIR)/.venv || python3 -m venv $(API_DIR)/.venv

install: venv ## install backend + frontend dependencies
	$(PIP) install -U pip
	$(PIP) install -r $(API_DIR)/requirements-dev.txt
	cd $(WEB_DIR) && npm install

dev-db: ## start postgres only (exposed on 127.0.0.1:5432)
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres

migrate: ## apply migrations
	cd $(API_DIR) && .venv/bin/alembic upgrade head

migration: ## autogenerate a migration: make migration m="add xp table"
	cd $(API_DIR) && .venv/bin/alembic revision --autogenerate -m "$(m)"
	cd $(API_DIR) && .venv/bin/python scripts/fix_migration.py alembic/versions/*.py

fix-migration: ## strip duplicated enum CHECK constraints from the newest migration
	cd $(API_DIR) && .venv/bin/python scripts/fix_migration.py "$$(ls -t alembic/versions/*.py | head -1)"

api: ## run the API with autoreload (http://localhost:8000/docs)
	cd $(API_DIR) && MEDIA_DIR=$${MEDIA_DIR:-$$PWD/var/media} .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

web: ## run the Vite dev server (http://localhost:5173)
	cd $(WEB_DIR) && npm run dev

seed-exercises: ## seed the exercise library (stage 4)
	cd $(API_DIR) && .venv/bin/python -m app.seeds.exercises

seed-foods: ## import the bundled RU/EN food library (~320 foods)
	cd $(API_DIR) && .venv/bin/python -m app.cli import-foods --source seed

# --------------------------------------------------------------------------- #
# Quality
# --------------------------------------------------------------------------- #
test: ## run the backend test suite (needs TEST_DATABASE_URL or local postgres)
	cd $(API_DIR) && .venv/bin/pytest -q

lint: ## ruff check
	cd $(API_DIR) && .venv/bin/ruff check .

fmt: ## ruff format
	cd $(API_DIR) && .venv/bin/ruff format .

typecheck: ## mypy (best effort)
	cd $(API_DIR) && .venv/bin/mypy app || true

gen-api: ## regenerate the TypeScript client from the running API's OpenAPI schema
	cd $(WEB_DIR) && npm run gen:api

check-i18n: ## fail if any UI string is untranslated or the locales drift apart
	node scripts/check-i18n.mjs

ui-check: ## browser acceptance run: PAIRING_CODE=XXXX-XXXX make ui-check
	cd $(WEB_DIR) && PAIRING_CODE="$(PAIRING_CODE)" SHOTS="$(SHOTS)" node ../scripts/ui-check.mjs

# --------------------------------------------------------------------------- #
# Deployment
# --------------------------------------------------------------------------- #
build: ## build both images
	docker compose build

up: ## start the full stack
	docker compose up -d

down: ## stop the stack
	docker compose down

logs: ## tail the api logs
	docker compose logs -f api

psql: ## psql into the database
	docker compose exec postgres psql -U $${POSTGRES_USER:-dsos} -d $${POSTGRES_DB:-dsos}

owner: ## create the owner + print the first pairing code
	docker compose exec api python -m app.cli create-owner

pair: ## print a fresh pairing code
	docker compose exec api python -m app.cli pairing-code

backup: ## run an encrypted backup now
	./deploy/backup/backup.sh

restore: ## restore from a dump: make restore f=backups/dsos-2026-10-01.sql.age
	./deploy/backup/restore.sh $(f)

clean: ## remove caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(API_DIR)/.pytest_cache $(API_DIR)/.ruff_cache $(WEB_DIR)/dist
