"""gemini-delegate — a thin, deterministic CLI over the Google Gemini API.

This package is the *mechanism* layer (CLAUDE.md §1): it builds requests,
transfers media, runs the call, persists multi-turn state, and prints a single
structured JSON envelope. All judgment (prompting, validation, structuring)
lives in the Claude Code subagent that drives this CLI.
"""

__version__ = "0.1.0"
