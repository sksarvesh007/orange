.PHONY: setup test lint format eval robustness stress demo run clean

VENV ?= .venv
PYTHON ?= $(VENV)/bin/python
PYTEST ?= $(VENV)/bin/pytest
RUFF ?= $(VENV)/bin/ruff
MYPY ?= $(VENV)/bin/mypy
UVICORN ?= $(VENV)/bin/uvicorn

setup:
	@echo "Setting up virtual environment and dependencies using uv..."
	@command -v uv >/dev/null 2>&1 || (echo "uv is required. Install via 'curl -LsSf https://astral.sh/uv/install.sh | sh'" && exit 1)
	uv venv $(VENV)
	uv pip install -e ".[dev]"
	@echo "Setup complete. Virtual environment ready in $(VENV)."

test:
	$(PYTEST) -v

lint:
	$(RUFF) check .
	$(RUFF) format --check .
	$(MYPY) src/

format:
	$(RUFF) check --fix .
	$(RUFF) format .

eval:
	$(PYTEST) -v tests/evals/

robustness:
	$(PYTEST) -v tests/robustness/

stress:
	$(PYTHON) stress/runner.py

demo:
	$(PYTHON) -m errand_agent.cli chat

run:
	$(UVICORN) errand_agent.api.app:app --host 0.0.0.0 --port 8000

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -type d -name "__pycache__" -exec rm -rf {} +
