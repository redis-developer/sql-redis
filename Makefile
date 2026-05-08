.PHONY: install format lint test clean check-types check-format check-sort-imports sort-imports build docs-build docs-serve help
.DEFAULT_GOAL := help

# Allow passing arguments to make targets (e.g., make test ARGS="...")
ARGS ?=

install: ## Install the project and all dependencies
	@echo "🚀 Installing project dependencies with uv"
	uv sync

format: ## Format code with isort and black
	@echo "🎨 Formatting code"
	uv run isort ./sql_redis ./tests/ --profile black
	uv run black ./sql_redis ./tests/

check-format: ## Check code formatting
	@echo "🔍 Checking code formatting"
	uv run black --check ./sql_redis ./tests/

sort-imports: ## Sort imports with isort
	@echo "📦 Sorting imports"
	uv run isort ./sql_redis ./tests/ --profile black

check-sort-imports: ## Check import sorting
	@echo "🔍 Checking import sorting"
	uv run isort ./sql_redis ./tests/ --check-only --profile black

check-types: ## Run mypy type checking
	@echo "🔍 Running mypy type checking"
	uv run python -m mypy ./sql_redis

lint: format check-types ## Run all linting (format + type check)

test: ## Run tests (pass extra args with ARGS="...")
	@echo "🧪 Running tests"
	uv run python -m pytest $(ARGS)

test-verbose: ## Run tests with verbose output
	@echo "🧪 Running tests (verbose)"
	uv run python -m pytest -vv -s $(ARGS)

test-cov: ## Run tests with coverage report
	@echo "🧪 Running tests with coverage"
	uv run python -m pytest --cov=sql_redis --cov-report=term-missing --cov-report=html $(ARGS)

check: lint test ## Run all checks (lint + test)

build: ## Build wheel and source distribution
	@echo "🏗️ Building distribution packages"
	uv build

docs-build: ## Build documentation
	@echo "📚 Building documentation"
	DISABLE_MKDOCS_2_WARNING=true uv run --group docs mkdocs build --strict

docs-serve: ## Serve documentation locally at http://localhost:8000
	@echo "🌐 Serving documentation at http://localhost:8000"
	DISABLE_MKDOCS_2_WARNING=true uv run --group docs mkdocs serve --dev-addr 127.0.0.1:8000

clean: ## Clean up build artifacts and caches
	@echo "🧹 Cleaning up directory"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".coverage" -delete 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.log" -exec rm -rf {} + 2>/dev/null || true
	rm -rf site

help: ## Show this help message
	@echo "Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

