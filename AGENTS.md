# ai-sync contributor guide

This tool has one job: synchronize Claude Code and Codex skills, shared MCP
configuration, and optional shared instructions. Keep user data outside the
software checkout. Do not add other assistants, product-specific integrations,
plugin management, or model routing.

Run `.venv/bin/python -m pytest` (or `python -m pytest` in an installed development
environment). Tests must use temporary homes and synthetic data. Never run a
mutating sync against a contributor's actual client configuration as a test.

Preserve unrelated native configuration and symlinks. Conflicts stop the entire
plan. Ownership records alone are insufficient: verify the destination still
matches before deletion or rollback. Do not log MCP values or resolve secrets.

Keep filesystem application in `files.py`, native MCP translation in `mcp.py`,
skill ownership in `skills.py`, and instruction blocks in `instructions.py`.
