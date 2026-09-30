import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


class Box:
    def __init__(self, root):
        self.home = root / 'home'
        self.data = root / 'data'
        self.home.mkdir()
        self.data.mkdir()

    def run(self, *args, ok=True):
        result = subprocess.run(
            [sys.executable, '-m', 'ai_sync', '--home', str(self.home),
             '--data-dir', str(self.data), *args], capture_output=True, text=True,
        )
        if ok:
            assert result.returncode == 0, result.stderr
        else:
            assert result.returncode != 0, result.stdout
            assert 'ai-sync:' in result.stderr, result.stderr
            assert 'Traceback' not in result.stderr
        return result

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value))

    def skill(self, root, name, text='Shared skill\n'):
        path = root / name
        self.write(path / 'SKILL.md', text)
        self.write(path / 'scripts' / 'run.py', 'print(42)\n')
        return path

    @property
    def claude(self):
        return self.home / '.claude.json'

    @property
    def codex(self):
        return self.home / '.codex/config.toml'

    @property
    def registry(self):
        return self.data / 'mcp-servers.json'

    def snapshot(self):
        return {str(p.relative_to(self.home)): (os.readlink(p) if p.is_symlink() else p.read_bytes())
                for p in self.home.rglob('*') if p.is_file() or p.is_symlink()}


def test_first_run_imports_and_translates_both_clients(box):
    box.write(box.claude, {'mcpServers': {'alpha': {'command': 'alpha', 'args': ['serve']}}, 'theme': 'dark'})
    box.write(box.codex, '# keep comment\nmodel = "example"\n[mcp_servers.remote]\nurl = "https://example.com/mcp"\nstartup_timeout_sec = 30\n')
    box.run('once')
    registry = json.loads(box.registry.read_text())
    assert set(registry) == {'alpha', 'remote'}
    claude = json.loads(box.claude.read_text())
    assert claude['mcpServers']['remote'] == {'type': 'http', 'url': 'https://example.com/mcp'}
    assert claude['theme'] == 'dark'
    assert '# keep comment' in box.codex.read_text()
    assert tomllib.loads(box.codex.read_text())['mcp_servers']['remote']['startup_timeout_sec'] == 30


def test_canonical_fields_and_native_overrides(box):
    box.write(box.registry, {'x': {'command': 'x', 'args': ['a\nb'], 'env': {'KEY': 'value'}, 'codex': {'cwd': '/tmp', 'enabled': False}}})
    box.run('once')
    assert tomllib.loads(box.codex.read_text())['mcp_servers']['x']['enabled'] is False
    assert 'cwd' not in json.loads(box.claude.read_text())['mcpServers']['x']
    box.run('once')


def test_http_headers_translate_without_resolving_secrets(box):
    box.write(box.codex, '[mcp_servers.x]\nurl="https://example.com/mcp"\nbearer_token_env_var="EXAMPLE_TOKEN"\nhttp_headers={ Region="west" }\nenv_http_headers={ Tenant="TENANT" }\n')
    box.run('once')
    headers = json.loads(box.claude.read_text())['mcpServers']['x']['headers']
    assert headers == {'Region': 'west', 'Tenant': '${TENANT}', 'Authorization': 'Bearer ${EXAMPLE_TOKEN}'}
    before = box.snapshot()
    box.run('once')
    assert box.snapshot() == before


def test_repeated_sync_does_not_rewrite_configs(box):
    box.write(box.registry, {'x': {'command': 'x'}})
    box.run('once')
    paths = [box.registry, box.claude, box.codex]
    before = [p.stat().st_mtime_ns for p in paths]
    box.run('once')
    assert [p.stat().st_mtime_ns for p in paths] == before


def test_independent_edits_merge_and_deletion_propagates(box):
    box.write(box.registry, {'a': {'command': 'a'}, 'b': {'command': 'b'}})
    box.run('once')
    box.write(box.claude, {'mcpServers': {'a': {'command': 'new-a'}, 'b': {'command': 'b'}}})
    box.write(box.codex, '[mcp_servers.a]\ncommand="a"\n[mcp_servers.b]\ncommand="new-b"\n')
    box.run('once')
    assert json.loads(box.registry.read_text()) == {'a': {'command': 'new-a'}, 'b': {'command': 'new-b'}}
    box.write(box.claude, {'mcpServers': {}})
    box.run('once')
    assert json.loads(box.registry.read_text()) == {}
    assert tomllib.loads(box.codex.read_text()).get('mcp_servers', {}) == {}


def test_conflicting_edits_leave_all_files_untouched(box):
    box.write(box.registry, {'a': {'command': 'a'}})
    box.run('once')
    box.write(box.claude, {'mcpServers': {'a': {'command': 'left'}}})
    box.write(box.codex, '[mcp_servers.a]\ncommand="right"\n')
    box.skill(box.data / 'skills', 'new-skill')
    before = box.snapshot()
    result = box.run('once', ok=False)
    assert 'conflict' in result.stderr.lower()
    assert box.snapshot() == before
    assert json.loads(box.registry.read_text()) == {'a': {'command': 'a'}}


def test_first_run_conflict_does_not_choose_a_winner(box):
    box.write(box.claude, {'mcpServers': {'a': {'command': 'left'}}})
    box.write(box.codex, '[mcp_servers.a]\ncommand="right"\n')
    before = box.snapshot()
    box.run('once', ok=False)
    assert box.snapshot() == before
    assert not box.registry.exists()


def test_invalid_toml_prevents_skill_changes(box):
    box.write(box.codex, '[broken')
    box.skill(box.data / 'skills', 'example')
    box.run('once', ok=False)
    assert not (box.home / '.claude/skills').exists()


def test_missing_previously_synced_config_is_not_a_delete_all(box):
    box.write(box.registry, {'x': {'command': 'x'}})
    box.run('once')
    box.claude.unlink()
    box.run('once', ok=False)
    assert json.loads(box.registry.read_text()) == {'x': {'command': 'x'}}


def test_skill_import_links_whole_tree_and_keeps_backup(box):
    original = box.skill(box.home / '.claude/skills', 'example')
    box.run('once')
    shared = box.data / 'skills/example'
    assert (shared / 'scripts/run.py').read_text() == 'print(42)\n'
    assert original.is_symlink() and original.resolve() == shared.resolve()
    assert (box.home / '.agents/skills/example').resolve() == shared.resolve()
    backups = list((box.data / '.state/backups').rglob('SKILL.md'))
    assert backups and backups[0].read_text() == 'Shared skill\n'
    (original / 'scripts/run.py').write_text('print(43)\n')
    assert (box.home / '.agents/skills/example/scripts/run.py').read_text() == 'print(43)\n'


def test_new_skill_imported_after_baseline(box):
    box.run('once')
    box.skill(box.home / '.agents/skills', 'fresh')
    box.run('once')
    assert (box.home / '.claude/skills/fresh').is_symlink()


def test_different_same_name_skills_conflict(box):
    box.skill(box.home / '.claude/skills', 'same', 'one')
    box.skill(box.home / '.agents/skills', 'same', 'two')
    box.run('once', ok=False)
    assert not (box.data / 'skills/same').exists()
    assert not (box.home / '.claude/skills/same').is_symlink()


def test_external_symlink_is_not_deleted_or_imported(box):
    external = box.skill(box.home / 'external', 'third-party')
    link = box.home / '.claude/skills/third-party'
    link.parent.mkdir(parents=True)
    link.symlink_to(external)
    box.run('once')
    assert link.is_symlink() and link.resolve() == external
    assert not (box.data / 'skills/third-party').exists()


def test_external_symlink_collision_stops_without_replacing(box):
    external = box.skill(box.home / 'external', 'example', 'third party')
    box.skill(box.data / 'skills', 'example')
    link = box.home / '.claude/skills/example'
    link.parent.mkdir(parents=True)
    link.symlink_to(external)
    box.run('once', ok=False)
    assert link.resolve() == external


def test_canonical_deletion_removes_only_owned_links(box):
    import shutil
    box.skill(box.data / 'skills', 'example')
    box.run('once')
    claude = box.home / '.claude/skills/example'
    claude.unlink()
    external = box.skill(box.home / 'elsewhere', 'example')
    claude.symlink_to(external)
    shutil.rmtree(box.data / 'skills/example')
    box.run('once')
    assert claude.resolve() == external
    assert not (box.home / '.agents/skills/example').is_symlink()


def test_status_is_read_only_even_on_first_run(box):
    box.skill(box.home / '.claude/skills', 'example')
    before = box.snapshot()
    result = box.run('status')
    assert 'example' in result.stdout
    assert box.snapshot() == before
    assert list(box.data.iterdir()) == []


def test_instruction_block_preserves_native_text_and_imports_edit(box):
    box.write(box.data / 'instructions.md', 'Use concise prose.\n')
    claude = box.home / '.claude/CLAUDE.md'
    codex = box.home / '.codex/AGENTS.md'
    box.write(claude, '# Claude only\n')
    box.write(codex, '# Codex only\n')
    box.run('once')
    assert claude.read_text().startswith('# Claude only\n')
    assert codex.read_text().startswith('# Codex only\n')
    box.write(claude, claude.read_text().replace('Use concise prose.', 'Use plain prose.'))
    box.run('once')
    assert box.data.joinpath('instructions.md').read_text() == 'Use plain prose.\n'
    assert 'Use plain prose.' in codex.read_text()


def test_instructions_remain_opt_in(box):
    box.run('once')
    assert not (box.home / '.claude/CLAUDE.md').exists()
    assert not (box.home / '.codex/AGENTS.md').exists()


def test_config_symlink_is_preserved(box):
    target = box.home / 'private/config.toml'
    box.write(target, '# original\nmodel="test"\n')
    box.codex.parent.mkdir(parents=True)
    box.codex.symlink_to(target)
    box.write(box.registry, {'x': {'command': 'x'}})
    box.run('once')
    assert box.codex.is_symlink()
    assert 'mcp_servers' in tomllib.loads(target.read_text())
    assert '# original' in target.read_text()


def test_sse_only_server_remains_local(box):
    box.write(box.claude, {'mcpServers': {'legacy': {'type': 'sse', 'url': 'https://example.com/events'}}})
    box.run('once')
    assert json.loads(box.claude.read_text())['mcpServers']['legacy']['type'] == 'sse'
    assert 'legacy' not in tomllib.loads(box.codex.read_text()).get('mcp_servers', {})


def test_instruction_conflict_blocks_mcp_changes(box):
    box.write(box.data / 'instructions.md', 'Original\n')
    box.run('once')
    claude = box.home / '.claude/CLAUDE.md'
    codex = box.home / '.codex/AGENTS.md'
    box.write(claude, claude.read_text().replace('Original', 'Left'))
    box.write(codex, codex.read_text().replace('Original', 'Right'))
    box.write(box.registry, {'new': {'command': 'new'}})
    before = box.snapshot()
    box.run('once', ok=False)
    assert box.snapshot() == before


def test_custom_profiles_are_isolated(box):
    box.write(box.registry, {'example': {'command': 'example'}})
    result = subprocess.run([sys.executable, '-m', 'ai_sync', '--home', str(box.home),
                             '--data-dir', str(box.data), '--claude-dir', str(box.home / 'claude profile'),
                             '--codex-dir', str(box.home / 'codex profile'), 'once'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (box.home / 'claude profile/.claude.json').exists()
    assert (box.home / 'codex profile/config.toml').exists()
    assert not box.claude.exists() and not box.codex.exists()
    box.run('once', ok=False)  # Same state must not be reused for another profile.


def test_empty_config_is_not_silently_treated_as_empty_servers(box):
    box.write(box.claude, '')
    box.run('once', ok=False)
    assert not box.codex.exists()


def test_daemon_picks_up_new_skill(box):
    import time
    process = subprocess.Popen([sys.executable, '-m', 'ai_sync', '--home', str(box.home),
                                '--data-dir', str(box.data), 'watch', '--interval', '0.1'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not box.registry.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert box.registry.exists()
        box.skill(box.home / '.claude/skills', 'after-start')
        link = box.home / '.agents/skills/after-start'
        while not link.is_symlink() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert link.is_symlink()
    finally:
        process.terminate()
        process.communicate(timeout=5)


def test_invalid_interval_rejected(box):
    box.run('watch', '--interval', '0', ok=False)


def test_external_relative_symlink_in_import_rejected(box):
    source = box.skill(box.home / '.claude/skills', 'unsafe')
    (source / 'external').symlink_to('../../../outside')
    box.run('once', ok=False)
    assert source.is_dir() and not source.is_symlink()


def test_unsupported_same_name_server_is_a_conflict(box):
    box.write(box.claude, {'mcpServers': {'legacy': {'type': 'sse', 'url': 'https://example.com/events'}}})
    box.write(box.codex, '[mcp_servers.legacy]\ncommand="legacy"\n')
    before = box.snapshot()
    box.run('once', ok=False)
    assert box.snapshot() == before
    assert not box.registry.exists()


def test_truncated_codex_file_does_not_delete_servers(box):
    box.write(box.registry, {'example': {'command': 'example'}})
    box.run('once')
    box.write(box.codex, '')
    box.run('once', ok=False)
    assert json.loads(box.registry.read_text()) == {'example': {'command': 'example'}}
    assert 'example' in json.loads(box.claude.read_text())['mcpServers']


def test_reversed_instruction_markers_report_error(box):
    box.write(box.data / 'instructions.md', 'shared\n')
    box.write(box.home / '.claude/CLAUDE.md', '<!-- ai-sync:instructions:end -->\n<!-- ai-sync:instructions:start -->\n')
    box.run('once', ok=False)
    assert not box.registry.exists()


def test_shared_skill_root_cannot_alias_native_root(box):
    original = box.skill(box.home / '.claude/skills', 'keep')
    (box.data / 'skills').symlink_to(box.home / '.claude/skills')
    box.run('once', ok=False)
    assert original.is_dir() and not original.is_symlink()
    assert (original / 'SKILL.md').read_text() == 'Shared skill\n'


def test_canonical_skill_cannot_point_back_into_native_skill(box):
    original = box.skill(box.home / '.claude/skills', 'keep')
    shared = box.data / 'skills/keep'
    shared.parent.mkdir()
    shared.symlink_to(original)
    box.run('once', ok=False)
    assert not original.is_symlink()
    assert (original / 'SKILL.md').read_text() == 'Shared skill\n'
