# ai-sync implementation plan

Goal: extract the existing shared-source/symlink/reconciliation method into an
independent public tool limited to Claude Code and Codex.
Architecture: a filesystem change plan is constructed by MCP, skill, and optional
instruction planners; a common transaction layer applies it with backups.
Stack: Python 3.11+, tomlkit, pytest. Spec: [design.md](design.md).

1. Write CLI integration tests using temporary homes and literal JSON/TOML fixtures.
   Run the suite and establish failures before implementing the commands.
2. Implement paths and the transaction layer in `ai_sync/files.py`; implement
   MCP translation and baseline reconciliation in `ai_sync/mcp.py`.
3. Implement skill discovery/import/link ownership in `ai_sync/skills.py` and
   optional managed instruction blocks in `ai_sync/instructions.py`.
4. Wire read-only status, once, watch, and native per-user service installation
   through `ai_sync/cli.py` and `ai_sync/service.py`.
5. Run the suite; add regression coverage for issues found during review.
   Build/install the package and test its executable in a fresh temporary home.
6. Document setup, data ownership, conflicts, backups, and migration. Inspect the
   complete public file list for personal data; initialize and publish the repo.

Review focus: stale ownership records must not unlink external symlinks; first
run must not replace existing configs with an empty registry; independent edits
must merge; unsupported native options must survive; malformed input or a
conflict must prevent unrelated planned mutations.

Live migration must account for the previous daemon's extra assistant targets;
the public package itself has only the two requested adapters.
