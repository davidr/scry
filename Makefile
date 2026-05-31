.DEFAULT_GOAL := install

.PHONY: install reinstall uninstall test

install:
	uv tool install .

reinstall:
	uv tool install . --reinstall

uninstall:
	uv tool uninstall scry

test:
	uv run pytest -v
