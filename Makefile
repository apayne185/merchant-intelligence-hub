.PHONY: setup test test-api run run-copilot eval eval-copilot eval-retrieval precommit lint clean help \
	coverage typecheck security check test-integration docker-build up down token k8s-validate train-mlflow

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
	@echo "  make eval-retrieval - benchmark recall@k/MRR del retriever (mock vs. real embeddings)"
	@echo "  make precommit   - corre los hooks de pre-commit (ruff + gitleaks) sobre todo el repo"
	@echo "  make lint        - chequeos con ruff"
	@echo "  make clean       - elimina caches, .venv y artefactos build"
	@echo ""
	@echo "Plataforma de producción (D51-D57):"
	@echo "  make check       - ruff + mypy + bandit + tests con gate de cobertura (lo mismo que CI)"
	@echo "  make test-integration - tests contra Redis/Postgres reales (make up antes)"
	@echo "  make docker-build - construye la imagen endurecida"
	@echo "  make up / down   - stack docker-compose (app, redis, postgres, otel, jaeger, prometheus, grafana)"
	@echo "  make token       - JWT de desarrollo para el stack de compose"
	@echo "  make k8s-validate - kustomize + kubeconform sobre k8s/"
	@echo "  make train-mlflow - reentrena el modelo de churn con tracking MLflow + drift"

setup:
	uv sync --extra dev --extra mlops --extra notebooks
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

eval-retrieval:
	MOCK_LLM=1 uv run python -m scripts.evaluate_retrieval

precommit:
	uv run pre-commit run --all-files

# Hard gate now, matching CI (D56) — no more `|| true`.
lint:
	uv run ruff check src/ tests/ scripts/

typecheck:
	uv run mypy

security:
	uv run bandit -r src scripts -q

coverage:
	MOCK_LLM=1 MLFLOW_DISABLE_AGENT_HINT=1 uv run pytest --cov --cov-report=term

check: lint typecheck security coverage

test-integration:
	REDIS_URL=redis://localhost:6379/15 \
	AUDIT_DATABASE_URL=postgresql://copilot:copilot@localhost:5432/copilot \
	MOCK_LLM=1 uv run pytest -v -m integration

docker-build:
	docker buildx build --platform linux/amd64 --load -t merchant-copilot:dev .

up:
	docker compose up -d --build

down:
	docker compose down

# Mints with the secret the running copilot container was started with.
token:
	@docker compose exec -T copilot python -m scripts.mint_dev_token

k8s-validate:
	@for d in k8s/base k8s/overlays/local; do \
		kubectl kustomize $$d | docker run --rm -i ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary - || exit 1; \
	done

train-mlflow:
	MLFLOW_DISABLE_AGENT_HINT=1 uv run python -m scripts.train_churn_mlflow

clean:
	rm -rf .venv .pytest_cache .ruff_cache build dist *.egg-info
	find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
	@echo "✓ Caches eliminados (uv.lock y outputs/ conservados)"
