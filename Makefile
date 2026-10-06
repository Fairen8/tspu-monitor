# =====================================================================
# TSPU Monitor 2 — Makefile
# =====================================================================
.PHONY: help install test integration lint fmt run check report package release protect docker-build docker-up docker-down clean

help:
	@echo "install       — установить пакет с dev-зависимостями (pip install -e .[dev])"
	@echo "test          — запустить pytest"
	@echo "integration   — локальные интеграционные тесты проб (мок-серверы)"
	@echo "lint          — ruff check"
	@echo "fmt           — ruff format"
	@echo "run           — запустить проверки (tspu-monitor check)"
	@echo "report        — сформировать отчёт"
	@echo "package       — собрать sdist и wheel (python -m build)"
	@echo "release       — подготовить релиз: make release VERSION=2.1.0"
	@echo "protect       — настроить защиту репозитория (gh, admin)"
	@echo "docker-build  — собрать Docker-образ"
	@echo "docker-up     — docker compose up -d --build"
	@echo "docker-down   — docker compose down"

install:
	pip install -e ".[dev]"

test:
	pytest -q

integration:
	pytest -q -m integration

lint:
	ruff check src tests

fmt:
	ruff format src tests

run:
	tspu-monitor check

report:
	tspu-monitor report

package:
	python -m build

release:
	@test -n "$(VERSION)" || (echo "Укажите версию: make release VERSION=2.1.0" && exit 1)
	bash scripts/release.sh "$(VERSION)"

protect:
	bash scripts/protect-repo.sh

docker-build:
	docker build -t tspu-monitor:2.0.0 .

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
