from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .files import Plan, SyncError, lock
from .instructions import plan_instructions
from .mcp import plan_mcp
from .skills import plan_skills


@dataclass
class Paths:
    home: Path
    data: Path
    claude_dir: Path
    codex_dir: Path

    @property
    def registry(self):
        return self.data / 'mcp-servers.json'

    @property
    def skills(self):
        return self.data / 'skills'

    @property
    def claude_config(self):
        return self.home / '.claude.json' if self.claude_dir == self.home / '.claude' else self.claude_dir / '.claude.json'

    @property
    def codex_config(self):
        return self.codex_dir / 'config.toml'


def make_plan(paths):
    plan = Plan(paths.data)
    state_file = paths.data / '.state/state.json'
    state = plan.json(state_file)
    identity = [str(paths.home), str(paths.claude_dir), str(paths.codex_dir)]
    if state and (state.get('version') != 1 or state.get('profile') != identity):
        raise SyncError('State belongs to another profile or version; use a separate data directory.')
    plan_mcp(plan, paths, state)
    plan_skills(plan, paths, state)
    plan_instructions(plan, paths, state)
    state.update(version=1, initialized=True, profile=identity)
    plan.write_json(state_file, state)
    return plan


def run_once(paths, dry_run=False, quiet=False):
    if dry_run:
        plan = make_plan(paths)
    else:
        plan = make_plan(paths)
        with lock(paths.home):
            plan.apply()
    visible = [op for op in plan.operations if op.path != paths.data / '.state/state.json']
    for op in visible:
        print(f'{"would " if dry_run else ""}{op.kind}: {op.path}', flush=True)
    if not visible and not quiet:
        print('In sync.', flush=True)
    return plan


def parser():
    result = argparse.ArgumentParser(description='Sync Claude Code and Codex skills, MCP servers, and shared instructions.')
    result.add_argument('--version', action='version', version=__version__)
    result.add_argument('--home', type=Path, help='User home (also isolates all default paths).')
    result.add_argument('--data-dir', type=Path, help='Shared data directory; default ~/.local/share/ai-sync.')
    result.add_argument('--claude-dir', type=Path, help='Claude config directory; defaults to CLAUDE_CONFIG_DIR or ~/.claude.')
    result.add_argument('--codex-dir', type=Path, help='Codex config directory; defaults to CODEX_HOME or ~/.codex.')
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Preview changes without writing anything.')
    once = commands.add_parser('once', help='Reconcile changes once.')
    once.add_argument('--dry-run', action='store_true', help='Preview without writes.')
    watch = commands.add_parser('watch', aliases=['daemon'], help='Keep the two clients synchronized.')
    watch.add_argument('--interval', type=float, default=5)
    service = commands.add_parser('service', help='Manage a user launchd/systemd watcher.')
    service.add_argument('action', choices=['install', 'uninstall', 'status'])
    return result


def main():
    args = parser().parse_args()
    home = (args.home or Path.home()).expanduser().absolute()
    # --home is an isolation boundary; do not inherit another user's profile env.
    claude = args.claude_dir or (None if args.home else os.environ.get('CLAUDE_CONFIG_DIR')) or home / '.claude'
    codex = args.codex_dir or (None if args.home else os.environ.get('CODEX_HOME')) or home / '.codex'
    data = args.data_dir or home / '.local/share/ai-sync'
    paths = Paths(home, Path(data).expanduser().absolute(), Path(claude).expanduser().absolute(), Path(codex).expanduser().absolute())
    try:
        if args.command == 'service':
            from .service import service
            return service(paths, args.action)
        if args.command in ('watch', 'daemon'):
            if args.interval < 0.1 or not math.isfinite(args.interval):
                raise SyncError('The watch interval must be finite and at least 0.1 seconds.')
            last_error = None
            while True:
                try:
                    run_once(paths, quiet=True)
                    last_error = None
                except (SyncError, OSError) as exc:
                    message = str(exc)
                    if message != last_error:
                        print(f'ai-sync: {message}', file=sys.stderr, flush=True)
                        last_error = message
                time.sleep(args.interval)
        else:
            run_once(paths, args.command == 'status' or args.dry_run)
        return 0
    except KeyboardInterrupt:
        return 0
    except (SyncError, OSError) as exc:
        print(f'ai-sync: {exc}', file=sys.stderr)
        return 1
