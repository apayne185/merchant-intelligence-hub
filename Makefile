.PHONY: help setup test run eval bench fixtures lint typecheck security style cpp-test check coverage \
	test-integration docker-build up down replay token k8s-validate clean

help:
	@echo "Development"
	@echo "  make setup            uv sync --extra dev (also compiles the C++ engine)"
	@echo "  make run              API on :8001 with MOCK_LLM=1"
	@echo "  make test             pytest, mock LLM"
	@echo "  make eval             golden-set eval + adversarial verifier test -> outputs/eval_report.json"
	@echo "  make bench            C++ riskcore vs NumPy -> outputs/benchmark_riskcore.json"
	@echo "  make cpp-test         C++ unit tests, release and ASan/UBSan builds"
	@echo "  make check            lint + style + typecheck + security + coverage (what CI runs)"
	@echo "  make fixtures         refresh data/ from SEC EDGAR (needs SEC_USER_AGENT)"
	@echo "Platform"
	@echo "  make up / down        docker-compose stack (API, ingest workers, Redis, Postgres, observability)"
	@echo "  make replay           publish the fixture filing index into the ingest stream"
	@echo "  make token            dev JWT for the compose stack"
	@echo "  make test-integration tests against the compose Redis/Postgres"
	@echo "  make k8s-validate     kustomize + kubeconform"

setup:
	uv sync --extra dev

run:
	MOCK_LLM=1 uv run uvicorn src.copilot.api:app --reload --port 8001

test:
	MOCK_LLM=1 uv run pytest -v

eval:
	MOCK_LLM=1 LOG_LEVEL=WARNING uv run python -m scripts.evaluate_copilot
	uv run python -m scripts.check_eval_floors

bench:
	uv run python -m scripts.benchmark_riskcore

fixtures:
	uv run python -m scripts.fetch_fixtures

cpp-test:
	cmake -S cpp/riskcore -B cpp/riskcore/build/release -DRISKCORE_PYTHON=OFF -DRISKCORE_TESTS=ON -DCMAKE_BUILD_TYPE=Release
	cmake --build cpp/riskcore/build/release -j
	ctest --test-dir cpp/riskcore/build/release --output-on-failure
	cmake -S cpp/riskcore -B cpp/riskcore/build/asan -DRISKCORE_PYTHON=OFF -DRISKCORE_TESTS=ON -DRISKCORE_SANITIZE=ON -DCMAKE_BUILD_TYPE=Debug
	cmake --build cpp/riskcore/build/asan -j
	ctest --test-dir cpp/riskcore/build/asan --output-on-failure

lint:
	uv run ruff check src/ tests/ scripts/

style:
	python3 scripts/check_no_em_dash.py

typecheck:
	uv run mypy

security:
	uv run bandit -r src scripts -q

coverage:
	MOCK_LLM=1 uv run pytest --cov --cov-report=term

check: lint style typecheck security coverage

test-integration:
	REDIS_URL=redis://localhost:6379/15 \
	AUDIT_DATABASE_URL=postgresql://copilot:copilot@localhost:5432/copilot \
	MOCK_LLM=1 uv run pytest -v -m integration

docker-build:
	docker buildx build --platform linux/amd64 --load -t filings-copilot:dev .

up:
	docker compose up -d --build

down:
	docker compose down

replay:
	docker compose run --rm poller-replay

# Mints with the secret the running API container was started with.
token:
	@docker compose exec -T copilot python -m scripts.mint_dev_token

k8s-validate:
	@for d in k8s/base k8s/overlays/local; do \
		kubectl kustomize $$d | docker run --rm -i ghcr.io/yannh/kubeconform:v0.7.0 -strict -summary - || exit 1; \
	done

clean:
	rm -rf .venv .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info cpp/riskcore/build
	find . -name __pycache__ -type d -not -path "./.venv/*" -exec rm -rf {} +
