.PHONY: demo test report docker-demo
demo: ## Dashboard with the verified campaign data
	python3 frontend/server.py --port 8765
test: ## Full unit-test suite
	python3 -m pytest tests/ -q
report: ## Auto-generated campaign report (Markdown + JSON)
	python3 experiments/generate_campaign_report.py final-evaluation-artifacts/campaign-02 -o campaign-report.md
docker-demo: ## One-command container demo
	docker compose up --build
