# ai-sync

**One set of skills and shared configuration for Claude Code and Codex.**

Edit a skill in either assistant's directory and both see the same files. Add an
MCP server in either client's user config and it appears in the other. Run once,
or install a small background watcher to keep them aligned.

This repository contains the sync tool. Your skills, MCP definitions, credentials,
and sync history live in your own local data directory. It works without dotfiles.

## Install

Requires Python 3.11+ on macOS or Linux. With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/jeffdhooton/ai-sync
ai-sync status
ai-sync once
ai-sync service install
```

Or clone and install into a virtual environment:

```sh
git clone https://github.com/jeffdhooton/ai-sync.git
cd ai-sync
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/ai-sync status
.venv/bin/ai-sync once
```

`status` previews changes and conflicts without writing files. On first sync,
existing supported MCP servers and ordinary skill directories are merged into
the shared store. Different definitions under the same name stop the run so you
can choose the version to keep. No MCP servers are contacted or executed.

## Commands

| Command | Purpose |
| --- | --- |
| `ai-sync status` | Read-only preview |
| `ai-sync once` | Import edits and reconcile once |
| `ai-sync once --dry-run` | Same preview as `status` |
| `ai-sync watch --interval 5` | Keep syncing in the foreground |
| `ai-sync service install` | Sync once and start a user background watcher |
| `ai-sync service status` | Check whether the watcher is running |
| `ai-sync service uninstall` | Remove the watcher; retain data and links |

`daemon` is an alias for `watch`. The service uses launchd on macOS and a systemd
user unit on Linux. Linux needs an active user service manager. Reinstall the
service after moving or reinstalling the Python environment that runs it.
macOS watcher output is in the data directory at `.state/watch.log`; Linux output
is in `journalctl --user -u io.github.ai-sync.service`. Rotate logs and archive
old backup directories according to your own retention needs.

## The shared store

Default: `~/.local/share/ai-sync/`

```text
mcp-servers.json       Shared MCP definitions
skills/               One directory per shared skill
  example/
    SKILL.md
    scripts/...
instructions.md       Optional shared instruction text
.state/               Private baseline and recovery backups
```

Choose another location with a global flag **before the command**:

```sh
ai-sync --data-dir ~/my-private-agent-config status
ai-sync --data-dir ~/my-private-agent-config once
ai-sync --data-dir ~/my-private-agent-config service install
```

The store can be separate from the tool checkout, including a private repository.
Keep `.state/` out of version control and protect any secrets in MCP definitions.
The tool never commits or pushes user data. A symlinked store or `skills/`
directory is supported.

`--claude-dir` and `--codex-dir` select custom client profiles. Without these
flags, `CLAUDE_CONFIG_DIR` and `CODEX_HOME` are respected. `--home` isolates the
whole setup and ignores those environment variables unless explicit profile
flags are also supplied. Each profile needs its own data directory/baseline.

## Skills

Shared directories are linked into `~/.claude/skills/` and `~/.agents/skills/`.
The complete directory is shared, including scripts, references, and assets.

- Add a directory containing `SKILL.md` in either client or the shared store.
- Edit through any of the links; all locations point to the same files.
- Remove a skill from the **shared store** to remove its owned links.
- Removing only a client link recreates it at the next sync.

Ordinary existing skill directories are backed up outside the discovery roots
before being replaced with links. Unrelated symlinks are never imported,
retargeted, or removed. Hidden directories, plugin caches, and legacy
`.pre-ai-sync` backup directories are excluded. A differing same-name skill or
an unrelated symlink at a required destination is a conflict. Imported skills
may contain relative symlinks within their own tree; external symlinks require
you to place that skill in the shared store explicitly.

## MCP configuration

The shared file is a server-name map:

```json
{
  "local-example": {
    "command": "example-mcp",
    "args": ["serve"],
    "env": {"EXAMPLE_MODE": "local"},
    "codex": {"startup_timeout_sec": 30}
  },
  "remote-example": {
    "url": "https://example.com/mcp",
    "headers": {"X-Region": "west"},
    "bearer_token_env_var": "EXAMPLE_API_TOKEN"
  }
}
```

Common fields are `command`, `args`, `env`, `url`, `headers`, `env_headers`, and
`bearer_token_env_var`. `env_headers` maps header names to environment variable
names. Tokens are referenced by name, never resolved by ai-sync. Clients still
need their own environment and OAuth sign-in.

Optional `claude` and `codex` objects supply native options, such as Codex's
`cwd` or `startup_timeout_sec`; they cannot override shared fields. Editing
native-only options in a client keeps them local. An explicit option in the
shared file takes precedence over that client's local value. Removing an override
stops enforcing it; it does not delete the client's existing setting.

MCP changes are synchronized through `~/.claude.json` and
`~/.codex/config.toml`. Other settings and Codex TOML comments are preserved.
With a custom Claude config directory, its `.claude.json` is used instead.
This tool does not mirror models, permissions, hooks, plugin installations,
OAuth tokens, project configuration, or application caches.

Only stdio and HTTP connections are shared. Unsupported transports, such as
explicit SSE/WebSocket entries, stay local. Arbitrary Claude `${...}` expansion
in commands, arguments, URLs, or environment values cannot be translated
faithfully; such definitions stop the run. Environment-backed HTTP headers
use the explicit shared fields above. Start a new client session if it has not
picked up a config change.

### Edits, deletions, and conflicts

After the first run, ai-sync compares the shared store and both clients against
a saved common baseline. Edits to different servers merge. Different edits to
the same server stop the entire run, without choosing a winner by timestamp.
Removing a previously shared server in any one location removes it everywhere.

To resolve a conflict, inspect the named server in all three files, then make its
shared fields agree on the version you want. Run `ai-sync status` and `once`
again. The watcher retries after you fix the files. It does not print config
values in errors. If an entire previously synchronized config disappears, sync
stops; restore it rather than interpreting an absent file as deleting everything.

## Optional shared instructions

Create `instructions.md` in the shared store to enable this feature:

```sh
mkdir -p ~/.local/share/ai-sync
printf '%s\n' 'Use concise, concrete explanations.' > ~/.local/share/ai-sync/instructions.md
ai-sync once
```

Its contents appear between `ai-sync:instructions` markers in
`~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md`. Text outside those markers stays
client-specific. Edits inside either block sync back to the source and the other
client; conflicting edits stop the run. No assistant names or prose are rewritten.

A nonempty Codex `AGENTS.override.md` shadows `AGENTS.md`, so ai-sync asks you to
resolve that before enabling shared instructions. To clear shared instructions,
empty `instructions.md`; deleting the file or its managed markers after adoption
stops sync to prevent an accidental loss.

## Backups and recovery

Each change batch has a private `.state/backups/<id>/` directory with original
files/directories and a `manifest.json` mapping their original locations. A
`complete` marker identifies a finished batch. Replaced config symlinks retain
their links: writes update their resolved target. New config/state files use
mode `0600`; private state/backup directories use `0700`.

Plans are validated before writes, checked against filesystem snapshots, and
serialized with a per-user process lock. Files are replaced atomically; an
ordinary apply error rolls back completed operations unless another process has
edited them since. Concurrent edits and unknown partial results are preserved,
with the backup manifest reported for manual recovery. A whole multi-file batch is
**not crash-atomic**. After a power loss or process kill, stop the watcher, inspect
the latest backup manifest, restore affected files as needed, and preview again.
Do not run two different sync tools against these same client files.

## Moving from a dotfiles script

1. Stop the old watcher before enabling this one.
2. Point `--data-dir` at a private store; copy or symlink your canonical skills
   into its `skills/` directory.
3. Put portable MCP fields in `mcp-servers.json`, moving native-only options into
   the appropriate `claude` or `codex` object. Or let the first run import the
   clients' supported connections, keeping their native options local.
4. Run `status`, resolve any differing definitions, then run `once`.
5. Install the new watcher and remove the old watcher's startup registration.

If the old tool served additional assistants, account for those separately.
This project synchronizes only Claude Code and Codex. Dotfiles may install or
symlink the CLI, but the executable never looks for or imports a dotfiles repo.

## Development

```sh
uv venv
uv pip install -e '.[test]'
.venv/bin/python -m pytest
uv build
```

Tests use temporary homes and synthetic configuration. CI covers Python 3.11 and
3.13 on Linux and macOS. There are no network calls in the sync engine.

Format references: [Claude Code MCP](https://code.claude.com/docs/en/mcp),
[Codex MCP](https://developers.openai.com/codex/mcp),
[Codex skills](https://developers.openai.com/codex/skills), and
[Codex instructions](https://developers.openai.com/codex/guides/agents-md).

MIT licensed. Inspired by a small personal dotfiles sync script, extracted with
explicit ownership, conflict detection, and separate user data.
