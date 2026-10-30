.PHONY: setup serve test app agent agent-remove db-up db-down db-logs adminer

setup:
	./scripts/setup.sh

serve:
	./scripts/start-server.sh

test:
	PYTHONPYCACHEPREFIX="$(PWD)/.pycache" .venv/bin/python -m unittest discover -s tests -v

app:
	./scripts/install-app.sh --no-open

agent:
	./scripts/install-agent.sh

agent-remove:
	./scripts/uninstall-agent.sh

db-up:
	docker compose up -d db

db-down:
	docker compose down

db-logs:
	docker compose logs -f db

adminer:
	docker compose --profile tools up -d adminer
