# Project Makefile — small surface, opinionated.

.PHONY: demo test fmt clean

# `make demo` is your sanity check after opening the Codespace.
# Walks the stack and prints what's ready vs missing — does NOT run the
# evaluation rubric (use `make test` for that).
demo:
	@echo "──────────────────────────────────────────────────────────────"
	@echo "  Sobral ingestion stack — readiness check"
	@echo "──────────────────────────────────────────────────────────────"
	@printf "Docker daemon ........... "
	@docker info >/dev/null 2>&1 && echo "OK" || { echo "MISSING — démarre Docker"; exit 1; }
	@printf "LocalStack container .... "
	@docker compose ps --status running --services 2>/dev/null | grep -q '^localstack$$' \
		&& echo "OK" || { echo "DOWN — lance: docker compose up -d localstack"; exit 1; }
	@printf "LocalStack /_localstack/health .. "
	@curl -fsS http://localhost:4566/_localstack/health >/dev/null 2>&1 \
		&& echo "OK" || { echo "NOT READY — attends 10-30 s puis relance make demo"; exit 1; }
	@printf "Terraform binary ........ "
	@command -v terraform >/dev/null 2>&1 && echo "OK ($$(terraform -version | head -1))" || { echo "MISSING"; exit 1; }
	@printf "AWS CLI binary .......... "
	@command -v aws >/dev/null 2>&1 && echo "OK ($$(aws --version 2>&1 | head -1))" || { echo "MISSING"; exit 1; }
	@printf "Python venv ............. "
	@python -c "import boto3, pytest" 2>/dev/null && echo "OK (boto3 + pytest)" || { echo "MISSING — pip install -r requirements.txt"; exit 1; }
	@printf "Fixtures CSV ............ "
	@test -f fixtures/deliveries.csv && echo "OK ($$(wc -l < fixtures/deliveries.csv) lines)" || { echo "MISSING — python -m fixtures.generate_fixtures"; exit 1; }
	@echo ""
	@echo "✅ Stack ready. Ouvre src/lambda_handler.py et terraform/main.tf"
	@echo "   pour commencer. Lance \`make test\` quand tu veux faire tourner"
	@echo "   la rubric d'évaluation."

# `make test` runs the full rubric (the same checks the CI runs on push).
test:
	pytest tests/ -v

fmt:
	terraform fmt -recursive terraform/
	@command -v black >/dev/null 2>&1 && black src/ tests/ fixtures/ || echo "black not installed, skipping"

clean:
	rm -rf .terraform terraform.tfstate* .pytest_cache __pycache__ src/__pycache__ tests/__pycache__
