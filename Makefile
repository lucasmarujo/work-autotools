.PHONY: install run test clean

install:
	uv sync

run:
	uv run python main.py

test:
	uv run python tests/test_jira_tasks_export.py
	uv run python tests/test_github_yesterday_summary.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
