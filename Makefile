.PHONY: install run test clean

install:
	uv sync

run:
	uv run python main.py

test:
	uv run python test_jira_tasks_export.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
