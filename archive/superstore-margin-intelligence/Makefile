# MandiIQ — Makefile
# Quick commands for local development, testing, and deployment.

.PHONY: help install test test-watch coverage lint dev-api dev-dash docker-build docker-up docker-down docker-logs clean

.DEFAULT_GOAL := help

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install:  ## Install package in editable mode
	pip install -e .

test:  ## Run test suite
	python -m pytest mandi_rdd/tests/ -v --tb=short

coverage:  ## Run tests with coverage report
	python -m pytest mandi_rdd/tests/ --cov=mandi_rdd --cov-report=term --cov-report=html -v --tb=short

dev-dash:  ## Start Streamlit dashboard locally
	streamlit run mandi_rdd/dashboard/app.py --server.port 8501

dev-api:  ## Start FastAPI server locally
	python -m uvicorn mandi_rdd.api.main:app --host 0.0.0.0 --port 8000

pipeline:  ## Run nightly data pipeline manually
	python -m mandi_rdd.cli pipeline

docker-build:  ## Build Docker images
	docker compose build

docker-up:  ## Start all services
	docker compose up -d
	@echo "Dashboard: http://localhost:8501"
	@echo "API:       http://localhost:8000"

docker-down:  ## Stop all services
	docker compose down

docker-logs:  ## View service logs
	docker compose logs -f

clean:  ## Remove Python/build artifacts
	find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	find . -type f -name '*.pyo' -delete 2>/dev/null || true
	rm -rf .pytest_cache .coverage htmlcov/ 2>/dev/null || true
