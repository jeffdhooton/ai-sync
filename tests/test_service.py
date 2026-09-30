import plistlib
from pathlib import Path

from ai_sync.cli import Paths


def test_launchd_arguments_round_trip_with_spaces(tmp_path):
    from ai_sync.service import definition
    paths = Paths(tmp_path / 'my home', tmp_path / 'shared data', tmp_path / 'claude profile', tmp_path / 'codex profile')
    destination, content = definition(paths, 'darwin')
    config = plistlib.loads(content)
    args = config['ProgramArguments']
    assert args[args.index('--data-dir') + 1] == str(paths.data)
    assert args[-1] == 'watch'
    assert config['RunAtLoad'] and config['KeepAlive']
    assert destination.parent == paths.home / 'Library/LaunchAgents'


def test_systemd_escapes_paths_and_uses_no_shell(tmp_path):
    from ai_sync.service import definition
    paths = Paths(tmp_path / 'home', tmp_path / 'data%name$var', tmp_path / 'claude', tmp_path / 'codex')
    destination, content = definition(paths, 'linux')
    text = content.decode()
    assert 'data%%name$$var' in text
    assert 'Restart=on-failure' in text
    assert '/bin/sh' not in text
    assert destination.parent == paths.home / '.config/systemd/user'
