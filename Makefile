.PHONY: setup test test-api run run-copilot eval eval-copilot precommit lint clean help

# Gestor de dependencias por defecto: uv (https://docs.astral.sh/uv/).
# Si no tienes uv: curl -LsSf https://astral.sh/uv/install.sh | sh

help:
	@echo "Targets disponibles:"
	@echo "  make setup       - uv sync --extra dev (resuelve + crea .venv + genera uv.lock)"
	@echo "  make test        - corre todos los tests (pytest) con MOCK_LLM=1"
	@echo "  make test-api    - corre solo los tests de la API de reclamaciones"
	@echo "  make run-copilot - arranca el Merchant Intelligence Copilot (puerto 8001, MOCK_LLM=1)"
	@echo "  make run         - arranca la API de reclamaciones (Parte 4, puerto 8000, MOCK_LLM=1)"
	@echo "  make eval        - eval golden-set del clasificador de reclamaciones"
	@echo "  make eval-copilot- eval golden-set del copilot"
	@echo "  make precommit   - corre los hooks de pre-commit (ruff + gitleaks) sobre todo el repo"
	@echo "  make lint        - chequeos con ruff"
	@echo "  make clean       - elimina caches, .venv y artefactos build"

setup:
	uv sync --extra dev
	@echo "✓ Setup completo · venv en .venv/ · activa con: source .venv/bin/activate (opcional, uv run no lo requiere)"

test:
	MOCK_LLM=1 uv run pytest -v

test-api:
	MOCK_LLM=1 uv run pytest -v tests/test_api.py

run-copilot:
	MOCK_LLM=1 uv run uvicorn src.copilot.api:app --reload --port 8001

run:
	MOCK_LLM=1 uv run uvicorn src.parte4_api.main:app --reload --port 8000

eval:
	MOCK_LLM=1 uv run python -m scripts.evaluate_classifier

eval-copilot:
	MOCK_LLM=1 uv run python -m scripts.evaluate_copilot

precommit:
	uv run pre-commit run --all-files

lint:
	uv run ruff check src/ tests/ || true

clean:
	rm -rf .venv .pytest_cache .ruff_cache build dist *.egg-info
	find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
	@echo "✓ Caches eliminados (uv.lock y outputs/ conservados)"
