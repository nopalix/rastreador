## Comando Makefile

.PHONY: all help test lint format build up down doctor clean

all: help ## Muestra este help

help: ## Muestra los comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "} {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

test: ## Ejecuta todas las pruebas locales (sin red)
	python -m pytest -q tests/

test-integration: ## Ejecuta pruebas con servidor HTTP local (requiere contenedor)
	python -m pytest -q tests/test_platform_http.py

lint: ## Ejecuta el linter ruff sobre el código fuente
	ruff check src/rfe_rastreador tests/

format: ## Formatea el código con ruff (modo auto-fix)
	ruff format src/rfe_rastreador tests/

doctor: ## Diagnóstico rápido de dependencias y configuración
	python -m rfe_rastreador doctor

build: ## Construye la imagen Docker (con --pull para capas actualizadas)
	docker compose build --pull

up: ## Levanta los contenedores (rastreador + streamlit)
	docker compose up -d

down: ## Detiene y quita los contenedores (conserva los volúmenes data/)
	docker compose down

logs: ## Ve los logs en tiempo real del rastreador
	docker compose logs -f rfe-rastreador

privileges: ## Verifica y corrige permisos del volumen data/
	@echo "Verificando permisos..."
	@echo "Carpetas esperadas: 2770 (setgid)"
	@echo "Archivos esperados: 660"
	@ssh -o BatchMode=yes ubuntuserver 'cd /mnt/almacenamiento/Proyectos/GITHUB/rastreador && stat -c "%a %u:%g %n" data/output data/state data/output/*/*.xlsx 2>/dev/null | head -5'

clean: ## Limpia caches y archivos temporales (¡ojo! no borra data/ ni .env)
	rm -rf .pytest_cache __pycache__ src/rfe_rastreador/__pycache__ tests/__pycache__
	rm -rf .ruff_cache build/ dist/ *.egg-info
	@echo "Caches limpiados."