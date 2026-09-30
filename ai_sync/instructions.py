from .files import SyncError
from .mcp import merge

START = '<!-- ai-sync:instructions:start -->'
END = '<!-- ai-sync:instructions:end -->'


def split(text, path):
    if START not in text and END not in text:
        return None
    if text.count(START) != 1 or text.count(END) != 1:
        raise SyncError(f'Malformed shared instruction markers: {path}')
    if text.index(END) < text.index(START):
        raise SyncError(f'Malformed shared instruction marker order: {path}')
    before, rest = text.split(START)
    middle, after = rest.split(END)
    if not middle.startswith('\n'):
        raise SyncError(f'Malformed shared instruction block: {path}')
    return before, middle[1:], after


def plan_instructions(plan, paths, state):
    source = paths.data / 'instructions.md'
    if not source.exists():
        if 'instructions' in state:
            raise SyncError(f'Shared instructions missing: {source}; restore the file or empty it deliberately.')
        return
    override = paths.codex_dir / 'AGENTS.override.md'
    if plan.read(override).strip():
        raise SyncError(f'Codex override shadows shared instructions: {override}')
    canonical = plan.read(source)
    canonical = canonical.rstrip('\n') + '\n' if canonical else ''
    files = [paths.claude_dir / 'CLAUDE.md', paths.codex_dir / 'AGENTS.md']
    docs = [(path, plan.read(path)) for path in files]
    blocks = [split(text, path) for path, text in docs]
    sources = [{'instructions': canonical}]
    for block in blocks:
        if block is not None:
            sources.append({'instructions': block[1]})
        elif 'instructions' in state:
            raise SyncError('A managed instruction block was removed; restore its markers before syncing.')
    base = {'instructions': state['instructions']} if 'instructions' in state else {}
    merged = merge(base, sources, initial=not base)['instructions']
    plan.write(source, merged)
    block_text = START + '\n' + merged + END
    for (path, text), existing in zip(docs, blocks):
        if existing:
            result = existing[0] + block_text + existing[2]
        else:
            separator = '' if not text else ('\n' if text.endswith('\n') else '\n\n')
            result = text + separator + block_text + '\n'
        plan.write(path, result)
    state['instructions'] = merged
