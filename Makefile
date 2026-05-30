.PHONY: setup test test-api run lint clean help

# Gestor de dependencias por defecto: uv (https://docs.astral.sh/uv/).
# Si no tienes uv: curl -LsSf https://astral.sh/uv/install.sh | sh

help:
	@echo "Targets disponibles:"
	@echo "  make setup     - uv sync --extra dev (resuelve + crea .venv + genera uv.lock)"
	@echo "  make test      - corre todos los tests (pytest) con MOCK_LLM=1"
	@echo "  make test-api  - corre solo los tests de la API"
	@echo "  make run       - arranca la API con uvicorn (MOCK_LLM=1 por defecto)"
	@echo "  make lint      - chequeos con ruff"
	@echo "  make clean     - elimina caches, .venv y artefactos build"

setup:
	uv sync --extra dev
	@echo "✓ Setup completo · venv en .venv/ · activa con: source .venv/bin/activate (opcional, uv run no lo requiere)"

test:
	MOCK_LLM=1 uv run pytest -v

test-api:
	MOCK_LLM=1 uv run pytest -v tests/test_api.py

run:
	MOCK_LLM=1 uv run uvicorn src.parte4_api.main:app --reload --port 8000

lint:
	uv run ruff check src/ tests/ || true

clean:
	rm -rf .venv **/__pycache__ .pytest_cache .ruff_cache build dist *.egg-info
	@echo "✓ Caches eliminados (uv.lock y outputs/ conservados)"
