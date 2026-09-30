from pathlib import Path

import pytest

from ai_sync.files import Plan, SyncError, lock


def test_apply_rejects_file_changed_after_preview(tmp_path):
    file = tmp_path / 'settings'
    file.write_text('original')
    plan = Plan(tmp_path / 'data')
    plan.write(file, 'planned')
    file.write_text('concurrent edit')
    with pytest.raises(SyncError, match='Changed during planning'):
        plan.apply()
    assert file.read_text() == 'concurrent edit'


def test_apply_rolls_back_prior_changes_on_io_error(tmp_path, monkeypatch):
    import ai_sync.files as files
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.write_text('old-a')
    b.write_text('old-b')
    plan = Plan(tmp_path / 'data')
    plan.write(a, 'new-a')
    plan.write(b, 'new-b')
    real_write = files.atomic_write

    def fail_second(path, content):
        if path == b:
            raise OSError('simulated disk error')
        real_write(path, content)

    monkeypatch.setattr(files, 'atomic_write', fail_second)
    with pytest.raises(OSError):
        plan.apply()
    assert a.read_text() == 'old-a'
    assert b.read_text() == 'old-b'


def test_concurrent_writer_is_rejected(tmp_path):
    with lock(tmp_path):
        with pytest.raises(SyncError, match='Another ai-sync'):
            with lock(tmp_path):
                pass


def test_rollback_skips_untouched_unwritable_destination(tmp_path):
    import os
    if os.getuid() == 0:
        pytest.skip('Root ignores directory write permissions')
    first = tmp_path / 'first'
    folder = tmp_path / 'readonly'
    folder.mkdir()
    second = folder / 'second'
    first.write_text('old-first')
    second.write_text('old-second')
    plan = Plan(tmp_path / 'data')
    plan.write(first, 'new-first')
    plan.write(second, 'new-second')
    folder.chmod(0o500)
    try:
        with pytest.raises(OSError):
            plan.apply()
        assert first.read_text() == 'old-first'
        assert second.read_text() == 'old-second'
    finally:
        folder.chmod(0o700)


def test_rollback_preserves_concurrent_edit_and_reports_recovery(tmp_path, monkeypatch):
    import ai_sync.files as files
    first, second = tmp_path / 'a', tmp_path / 'b'
    first.write_text('original-a')
    second.write_text('original-b')
    plan = Plan(tmp_path / 'data')
    plan.write(first, 'sync-a')
    plan.write(second, 'sync-b')
    real_write = files.atomic_write

    def external_edit_then_fail(path, content):
        if path == second:
            first.write_text('concurrent-user-edit')
            raise OSError('disk failure')
        return real_write(path, content)

    monkeypatch.setattr(files, 'atomic_write', external_edit_then_fail)
    with pytest.raises(SyncError, match='Rollback incomplete'):
        plan.apply()
    assert first.read_text() == 'concurrent-user-edit'
    assert second.read_text() == 'original-b'
