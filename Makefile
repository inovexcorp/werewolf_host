.PHONY: all lint test container help run run_container install

ENGINE := $(shell command -v podman > /dev/null 2>&1 && echo podman || echo docker)
IMAGE  := docker.io/inovexis/werewolf_host:local
VENV   := .venv
help:
	@echo "Available make commands:"
	@echo "  make build         - Run lint, test, and container build"
	@echo "  make lint          - Run ruff linter on src/ and tests/"
	@echo "  make test          - Run pytest"
	@echo "  make container     - Build container image"
	@echo "  make run           - Run lint, test, and start uvicorn server"
	@echo "  make run_container - Run lint, test, and start containerized server"
	@echo "  make venv          - Create .venv if it doesn't exist"
	@echo "  make help          - Show this help message"


build: install lint test

venv:
	python3 -m venv $(VENV)

install: venv
	$(VENV)/bin/pip install -e ".[dev]"

lint:
	$(VENV)/bin/python -m ruff check src/ tests/

test:
	$(VENV)/bin/python -m pytest

container: build
	$(ENGINE) build -t $(IMAGE) .

run_container: lint test container
	$(ENGINE) run -p 8000:8000 --rm --name werewolf_host_local $(IMAGE)

run: lint test
	uvicorn main:app --host 0.0.0.0 --port 8000 --env-file .env
