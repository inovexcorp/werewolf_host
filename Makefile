.PHONY: all lint test container help run run_container

ENGINE := $(shell command -v podman > /dev/null 2>&1 && echo podman || echo docker)

help:
	@echo "Available make commands:"
	@echo "  make build         - Run lint, test, and container build"
	@echo "  make lint          - Run ruff linter on src/ and tests/"
	@echo "  make test          - Run pytest"
	@echo "  make container     - Build container image"
	@echo "  make run           - Run lint, test, and start uvicorn server"
	@echo "  make run_container - Run lint, test, and start containerized server"
	@echo "  make help          - Show this help message"


build: lint test container

lint:
	ruff check src/ tests/

test:
	pytest

container:
	$(ENGINE) build -t docker.io/inovexis/werewolf_host:local .

run_container: lint test container
	$(ENGINE) run -p 8080:8080 --rm --name werewolf_host_local docker.io/inovexis/werewolf_host:local

run: lint test
	uvicorn main:app --host 0.0.0.0 --port 8080 --env-file .env
