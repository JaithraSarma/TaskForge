# =============================================================================
# TaskForge — developer/ops convenience targets
# =============================================================================

PYTHON := .venv/Scripts/python.exe

.DEFAULT_GOAL := help

.PHONY: help install up down logs ps test lint format typecheck precommit seed load-test k8s-validate clean

help: ## Show this help message
	@echo "TaskForge — available targets:"
	@grep -E '^[a-zA-Z0-9_-]+:.*##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*##"}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

install: ## Install Python dependencies
	$(PYTHON) -m pip install -r requirements.txt

up: ## Build and start all services in the background
	docker compose up -d --build

down: ## Stop and remove all services
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f --tail=100

ps: ## Show status of running services
	docker compose ps

test: ## Run the test suite
	$(PYTHON) -m pytest tests/ -q

lint: ## Check code style with ruff
	ruff check app/ worker/ tests/ scripts/ migrations/

format: ## Auto-format code with ruff
	ruff format app/ worker/ tests/ scripts/ migrations/

typecheck: ## Run static type checks with mypy
	mypy app/ worker/ --ignore-missing-imports

precommit: ## Run all pre-commit hooks against the whole tree
	pre-commit run --all-files

seed: ## Seed the database with sample jobs
	python scripts/seed_jobs.py

load-test: ## Run a concurrent load simulation (500 jobs, 50 concurrent)
	python scripts/load_test.py --jobs 500 --concurrency 50

k8s-validate: ## Validate Kubernetes manifests (client-side dry run)
	kubectl apply -f k8s/ --dry-run=client

clean: ## Remove local databases and Python/lint caches
	rm -f test.db
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache
