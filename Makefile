.PHONY: openapi test

openapi:
	cd backend && python scripts/export_openapi.py
	@if [ -d frontend ]; then mkdir -p frontend/src/api && cd frontend && npx openapi-typescript ../docs/openapi.json -o src/api/openapi-types.ts; fi

test:
	cd backend && PYTHONPATH=. pytest --cov=app --cov-report=term-missing --cov-fail-under=85
