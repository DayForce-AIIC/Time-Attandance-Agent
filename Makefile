.PHONY: openapi

openapi:
	cd backend && python scripts/export_openapi.py
	@if [ -d frontend ]; then mkdir -p frontend/src/api && cd frontend && npx openapi-typescript ../docs/openapi.json -o src/api/openapi-types.ts; fi
