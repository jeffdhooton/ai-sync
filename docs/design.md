# ai-sync design

A standalone, public Python tool for sharing skills, MCP configuration, and an
optional instruction block between Claude Code and Codex. The public repository
contains software and examples; user data lives outside the checkout. No dotfiles,
other assistants, service-specific policies, model routing, or plugin management.

## Interface

`ai-sync once` reconciles once; `ai-sync status` previews without writes;
`ai-sync watch --interval 5` polls. `ai-sync service install|uninstall` manages a
per-user macOS launchd job or Linux systemd unit. Global `--home`, `--data-dir`,
`--claude-dir`, and `--codex-dir` options support alternate profiles and testing.
Default data: `~/.local/share/ai-sync`. Optional config and source locations are
explicit CLI flags; no dotfiles lookup. Runtime: Python 3.11+, tomlkit. macOS/Linux.

## Shared data

- `skills/<name>/SKILL.md`: canonical skill directories. Both native skill roots
  link here. Existing non-symlink skills are imported on initial use; new skills
  added in either client are imported later. Different same-name contents conflict.
  External symlinks and hidden/plugin directories are never adopted or removed.
  A link is removed only if recorded as owned and still points at its recorded
  destination. Removing a canonical skill removes those links only.
- `mcp-servers.json`: server-name map. Common stdio and HTTP fields translate into
  native JSON/TOML. Optional `claude` and `codex` objects hold explicit native
  overrides. Native local options, OAuth, comments, and unrelated config survive.
- `instructions.md`: optional shared text copied into marked blocks within the
  two global instruction files. Existing text outside those blocks survives.

## Reconciliation and safety

Save a common MCP/instruction baseline after each completed sync. Merge edits to
independent servers; divergent changes to the same server conflict. Deletions
propagate after baseline creation. First run merges existing supported servers
and rejects different definitions under one name. Never resolve by timestamps.
Malformed data and conflicts stop the entire plan before application. A dry run
performs no filesystem writes. Only changed files are written. Back up replaced
files/directories outside skill discovery roots. Use atomic file replacement,
a process lock, pre-write snapshot checks, and rollback on ordinary apply errors.
Multi-file operations are not crash-atomic; retain backups for manual recovery.
Never log configuration values or contact MCP servers.

## Verification

Exercise real files in temporary homes: round trips, additions/deletions,
conflicts without writes, unrelated symlinks, duplicate skills, nested skill
assets, native config options and comments, optional instruction edits, read-only
status, repeat no-op sync, locking, rollback, custom paths, and daemon startup.
Verify a packaged installation and generated service definitions.
