# gemini-delegate — convenience targets (CLAUDE.md §3, §9).
.PHONY: help install install-editable test lint install-agent smoke

help:
	@echo "gemini-delegate make targets:"
	@echo "  install-editable  pipx install --editable . (puts gemini-delegate on PATH)"
	@echo "  install           alias for install-editable"
	@echo "  test              run the offline unit tests (mocked Gemini client)"
	@echo "  lint              ruff check if available (not load-bearing)"
	@echo "  install-agent     copy the subagent to ~/.claude/agents/"
	@echo "  smoke             run the live API smoke test (needs RUN_LIVE=1 + GEMINI_API_KEY)"

install-editable:
	pipx install --editable .

install: install-editable

# Unit tests mock genai.Client — no network, no API key (CLAUDE.md §10).
test:
	python -m pytest

lint:
	@command -v ruff >/dev/null 2>&1 && ruff check src tests || echo "ruff not installed; skipping (not load-bearing)"

# User-scope install so every project sees the subagent (CLAUDE.md §7).
install-agent:
	mkdir -p ~/.claude/agents
	cp agents/gemini-delegate.md ~/.claude/agents/gemini-delegate.md
	@echo "installed subagent -> ~/.claude/agents/gemini-delegate.md"

# Live smoke test is gated and never part of `make test` (CLAUDE.md §10).
smoke:
	RUN_LIVE=1 python scripts/smoke_test.py
