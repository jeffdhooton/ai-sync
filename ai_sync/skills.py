from __future__ import annotations

import os
from pathlib import Path

from .files import SyncError, fingerprint


def discover(root: Path):
    if not root.exists():
        return {}
    if not root.is_dir():
        raise SyncError(f'Skill root is not a directory: {root}')
    return {p.name: p for p in sorted(root.iterdir())
            if not p.name.startswith('.') and not p.name.endswith('.pre-ai-sync')
            and p.is_dir() and (p / 'SKILL.md').is_file()}


def same_tree(a: Path, b: Path):
    # The legacy marker is bookkeeping, not skill content.
    def content(path):
        return tuple((p.name, fingerprint(p)) for p in sorted(path.iterdir())
                     if p.name != '.ai-sync-managed')
    return content(a) == content(b)


def validate_import(source: Path):
    for child in source.rglob('*'):
        if child.is_symlink():
            raw = Path(os.readlink(child))
            if raw.is_absolute() or not child.resolve().is_relative_to(source.resolve()):
                raise SyncError(f'Skill contains an external symlink: {child}; move it to the shared store explicitly.')
        elif not child.is_file() and not child.is_dir():
            raise SyncError(f'Unsupported skill entry: {child}')


def plan_skills(plan, paths, state):
    roots = [paths.claude_dir / 'skills', paths.home / '.agents/skills']
    shared_root = paths.skills.resolve()
    for root in roots:
        native_root = root.resolve()
        if shared_root.is_relative_to(native_root) or native_root.is_relative_to(shared_root):
            raise SyncError('Shared and native skill roots must not overlap (including through symlinks).')
    canonical = discover(paths.skills)
    for source in canonical.values():
        if any(source.resolve().is_relative_to(root.resolve()) for root in roots):
            raise SyncError(f'Canonical skill points into a native skill directory: {source}')
    plan.watch(paths.skills)
    if paths.skills.is_symlink():
        plan.watch(paths.skills.resolve())
    old_links = state.get('links', {})
    new_links = {}
    originals = {}
    for root in roots:
        plan.watch(root)
        if root.is_symlink():
            plan.watch(root.resolve())
        for name, source in discover(root).items():
            if source.is_symlink():
                continue
            originals.setdefault(name, []).append(source)
    for name, sources in sorted(originals.items()):
        reference = canonical.get(name, sources[0])
        for source in sources:
            if not same_tree(reference, source):
                raise SyncError(f'Skill conflict for {name}: different content; align or rename the skills and retry.')
        if name not in canonical:
            source = sources[0]
            validate_import(source)
            destination = paths.skills / name
            if destination.exists() or destination.is_symlink():
                raise SyncError(f'Non-skill entry blocks import: {destination}')
            plan.watch(source)
            plan.add('copy', destination, source)
            canonical[name] = destination
    for name, shared in sorted(canonical.items()):
        if shared.exists():
            plan.watch(shared.resolve())
        for root in roots:
            link = root / name
            expected_target = paths.skills / name
            if link.is_symlink():
                if link.resolve() != expected_target.resolve():
                    raise SyncError(f'Skill conflict: unrelated symlink at {link}')
            elif link.exists() and link not in originals.get(name, []):
                raise SyncError(f'Skill conflict: unrelated entry at {link}')
            target = str(expected_target)
            new_links[str(link)] = target
            if not link.is_symlink() or os.readlink(link) != target:
                plan.add('link', link, expected_target)
    for raw, target in old_links.items():
        link = Path(raw)
        # State does not grant authority outside this run's two skill roots.
        if link.parent not in roots or raw in new_links:
            continue
        if link.is_symlink() and os.readlink(link) == target:
            plan.add('unlink', link)
    state['links'] = new_links
