.PHONY: install run demo test lint docker docker-nlp clean

install:
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

run:
	.venv/bin/uvicorn app.main:app --reload --port 8000

demo:
	.venv/bin/python scripts/seed_demo.py

test:
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check app tests

docker:
	docker compose up --build

docker-nlp:
	DOCKERFILE=Dockerfile.nlp docker compose up --build

clean:
	rm -rf var .pytest_cache **/__pycache__
