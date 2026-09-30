from __future__ import annotations

import os
import plistlib
import subprocess
import sys

from .files import Plan, SyncError

LABEL = 'io.github.ai-sync'


def definition(paths, platform):
    command = [sys.executable, '-m', 'ai_sync', '--home', str(paths.home),
               '--data-dir', str(paths.data), '--claude-dir', str(paths.claude_dir),
               '--codex-dir', str(paths.codex_dir), 'watch']
    if platform == 'darwin':
        log = str(paths.data / '.state/watch.log')
        content = plistlib.dumps({'Label': LABEL, 'ProgramArguments': command,
                                 'RunAtLoad': True, 'KeepAlive': True,
                                 'StandardOutPath': log, 'StandardErrorPath': log})
        return paths.home / f'Library/LaunchAgents/{LABEL}.plist', content
    if platform.startswith('linux'):
        def quote(value):
            return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$').replace('\n', '\\n').replace('\r', '\\r') + '"'
        content = ('[Unit]\nDescription=Sync Claude Code and Codex\n\n[Service]\nType=simple\n'
                   + 'ExecStart=' + ' '.join(quote(arg) for arg in command)
                   + '\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n')
        return paths.home / f'.config/systemd/user/{LABEL}.service', content.encode()
    raise SyncError('Background service installation supports macOS and Linux.')


def run(command, check=True):
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode:
        raise SyncError(f'Service command failed ({command[0]} {command[1]}); check your user service manager.')
    return result.returncode


def service(paths, action):
    path, content = definition(paths, sys.platform)
    mac = sys.platform == 'darwin'
    domain = f'gui/{os.getuid()}'
    unit = f'{LABEL}.service'
    if action == 'status':
        command = ['launchctl', 'print', f'{domain}/{LABEL}'] if mac else ['systemctl', '--user', 'is-active', unit]
        print('Running.' if run(command, check=False) == 0 else 'Not running.')
        return 0
    if action == 'install':
        from .cli import run_once
        run_once(paths)
        plan = Plan(paths.data)
        plan.write(path, content.decode())
        plan.apply()
        (paths.data / '.state').mkdir(parents=True, exist_ok=True, mode=0o700)
        if mac:
            if run(['launchctl', 'print', f'{domain}/{LABEL}'], check=False) == 0:
                run(['launchctl', 'bootout', f'{domain}/{LABEL}'])
            run(['launchctl', 'bootstrap', domain, str(path)])
        else:
            run(['systemctl', '--user', 'daemon-reload'])
            run(['systemctl', '--user', 'enable', '--now', unit])
            run(['systemctl', '--user', 'restart', unit])
        print(f'Installed watcher: {path}')
    else:
        if mac:
            if run(['launchctl', 'print', f'{domain}/{LABEL}'], check=False) == 0:
                run(['launchctl', 'bootout', f'{domain}/{LABEL}'])
        elif path.exists():
            run(['systemctl', '--user', 'disable', '--now', unit])
        path.unlink(missing_ok=True)
        if not mac:
            run(['systemctl', '--user', 'daemon-reload'])
        print('Watcher removed. Shared files and skill links remain.')
    return 0
