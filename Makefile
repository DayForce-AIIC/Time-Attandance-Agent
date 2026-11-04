PYTHON ?= python3
VENV ?= .venv
VENV_PYTHON := $(VENV)/bin/python
ACTIVE_PYTHON := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),$(PYTHON))
PYTHON_BIN := $(abspath $(shell command -v $(ACTIVE_PYTHON)))

.PHONY: all setup seed backend frontend eval backend-ci eval-ci frontend-ci openapi test

all: backend-ci eval-ci frontend-ci

setup:
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm ci

seed:
	cd backend && PYTHONPATH=. $(PYTHON_BIN) scripts/seed_db.py

backend:
	cd backend && PYTHONPATH=. $(PYTHON_BIN) -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

frontend:
	cd frontend && npm run dev

eval:
	LLM_PROVIDER=mock $(PYTHON_BIN) eval/run_eval.py

backend-ci:
	$(PYTHON_BIN) -m ruff check backend
	$(PYTHON_BIN) -m ruff format --check backend
	$(PYTHON_BIN) -m mypy backend/app
	cd backend && PYTHONPATH=. $(PYTHON_BIN) -m pytest --cov=app --cov-fail-under=85
	cd backend && PYTHONPATH=. $(PYTHON_BIN) scripts/export_openapi.py
	git diff --exit-code -- docs/openapi.json

eval-ci:
	$(PYTHON_BIN) eval/generate_cases.py
	git diff --exit-code -- eval/cases.jsonl
	LLM_PROVIDER=mock $(PYTHON_BIN) eval/run_eval.py

frontend-ci:
	cd frontend && npm run gen:api
	git diff --exit-code -- frontend/src/api/openapi-types.ts
	cd frontend && npm run lint
	cd frontend && npm run typecheck
	cd frontend && npm test
	cd frontend && npm run build

openapi:
	cd backend && PYTHONPATH=. $(PYTHON_BIN) scripts/export_openapi.py
	cd frontend && npm run gen:api

test:
	cd backend && PYTHONPATH=. $(PYTHON_BIN) -m pytest --cov=app --cov-report=term-missing --cov-fail-under=85
	cd frontend && npm test
