install:
	uv sync
	uv run playwright install

cities:
	uv run python3 main.py

completions:
	uv run python3 completions.py
	sh update-screenshot.sh
