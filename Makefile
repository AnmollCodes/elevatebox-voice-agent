# ============================================================
# ElevateBox Voice Agent — Makefile
# Common development tasks in one place.
# Usage: make <target>
# ============================================================

.DEFAULT_GOAL := help
.PHONY: help install dev test lint security demo demo-classifier demo-persona demo-diagram demo-full simulate dashboard clean

# ── Colours ──────────────────────────────────────────────────
BOLD  := \033[1m
RESET := \033[0m
GRN   := \033[92m
YEL   := \033[93m
BLU   := \033[94m

help:  ## Show this help message
	@echo ""
	@echo "$(BOLD)ElevateBox Voice Agent$(RESET)"
	@echo ""
	@echo "$(BOLD)Setup$(RESET)"
	@grep -E '^(install|dev):.*##' Makefile | awk -F ':.*## ' '{printf "  $(GRN)make %-20s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(BOLD)Development$(RESET)"
	@grep -E '^(test|lint|security):.*##' Makefile | awk -F ':.*## ' '{printf "  $(GRN)make %-20s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(BOLD)Demos (no API keys needed)$(RESET)"
	@grep -E '^demo.*:.*##' Makefile | awk -F ':.*## ' '{printf "  $(GRN)make %-20s$(RESET) %s\n", $$1, $$2}'
	@echo ""
	@echo "$(BOLD)Other$(RESET)"
	@grep -E '^(run|simulate|dashboard|clean):.*##' Makefile | awk -F ':.*## ' '{printf "  $(GRN)make %-20s$(RESET) %s\n", $$1, $$2}'
	@echo ""

install:  ## Install production dependencies
	pip install -r requirements.txt

dev:  ## Install all dependencies including dev/test tools
	pip install -r requirements-dev.txt

test:  ## Run all 320 tests
	pytest --tb=short -q

test-v:  ## Run tests with verbose output
	pytest -v

test-cov:  ## Run tests with coverage report
	pytest --cov=src --cov-report=term-missing --cov-fail-under=75

lint:  ## Run ruff linter
	ruff check src/ tests/ examples/

lint-fix:  ## Run ruff linter and auto-fix safe issues
	ruff check src/ tests/ examples/ --fix

security:  ## Run bandit security scan
	bandit -r src/ -ll --exclude src/__init__.py

run:  ## Start the server locally (requires .env file)
	uvicorn src.main:app --reload --port 8000

simulate:  ## Demo: run the pipeline via /simulate (server must be running)
	curl -s -X POST http://localhost:8000/simulate \
	  -H "Content-Type: application/json" \
	  -d '{"transcript":"Lead: Let'\''s do it! How do I pay? Abhi shuru karte hain. Send me details on WhatsApp."}' \
	  | python3 -m json.tool

dashboard:  ## Open the live dashboard in the browser (server must be running)
	@echo "$(GRN)Opening dashboard at http://localhost:8000/dashboard$(RESET)"
	@python3 -c "import webbrowser; webbrowser.open('http://localhost:8000/dashboard')" 2>/dev/null || true
	@echo "$(YEL)Or open: http://localhost:8000/dashboard$(RESET)"

# ── Demo targets (no API keys) ────────────────────────────────

demo: demo-full  ## Run the full pipeline demo (alias)

demo-classifier:  ## Demo: intent classification across EN/HI/TE
	python examples/demo_classifier.py

demo-persona:  ## Demo: buyer persona detection
	python examples/demo_persona.py

demo-diagram:  ## Demo: per-call SVG diagram generation
	python examples/demo_diagram.py

demo-full:  ## Demo: complete post-call pipeline simulation
	python examples/demo_full_pipeline.py

# ── Utilities ────────────────────────────────────────────────

clean:  ## Remove generated files and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .coverage htmlcov examples/output/*.svg
	@echo "$(GRN)Clean.$(RESET)"

check: lint security test  ## Run lint + security + tests (CI equivalent)
	@echo "$(GRN)All checks passed.$(RESET)"
