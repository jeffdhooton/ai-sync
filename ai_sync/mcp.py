from __future__ import annotations

import re
from copy import deepcopy

import tomlkit

from .files import SyncError

COMMON = {'command', 'args', 'env', 'url', 'headers', 'env_headers', 'bearer_token_env_var'}
CLAUDE_FIELDS = {'command', 'args', 'env', 'url', 'headers', 'type'}
CODEX_FIELDS = {'command', 'args', 'env', 'url', 'http_headers', 'env_http_headers', 'bearer_token_env_var'}
VARIABLE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
MISSING = object()


def validate(entry, name):
    if not isinstance(entry, dict) or bool(entry.get('command')) == bool(entry.get('url')):
        raise SyncError(f'MCP {name}: provide exactly one command or URL.')
    for field in ('command', 'url', 'bearer_token_env_var'):
        if field in entry and (not isinstance(entry[field], str) or not entry[field]):
            raise SyncError(f'MCP {name}: invalid {field}.')
    if 'args' in entry and (not isinstance(entry['args'], list) or not all(isinstance(x, str) for x in entry['args'])):
        raise SyncError(f'MCP {name}: args must be strings.')
    for field in ('env', 'headers', 'env_headers'):
        if field in entry and (not isinstance(entry[field], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in entry[field].items())):
            raise SyncError(f'MCP {name}: {field} must map strings to strings.')
    if 'command' in entry and any(k in entry for k in ('headers', 'env_headers', 'bearer_token_env_var')):
        raise SyncError(f'MCP {name}: HTTP fields on a stdio server.')
    if 'url' in entry and any(k in entry for k in ('args', 'env')):
        raise SyncError(f'MCP {name}: stdio fields on an HTTP server.')
    variables = list(entry.get('env_headers', {}).values())
    if 'bearer_token_env_var' in entry:
        variables.append(entry['bearer_token_env_var'])
    if not all(VARIABLE.fullmatch(x) for x in variables):
        raise SyncError(f'MCP {name}: invalid environment variable name.')
    headers = entry.get('headers', {})
    env_headers = entry.get('env_headers', {})
    header_names = [k.lower() for k in headers] + [k.lower() for k in env_headers]
    if 'bearer_token_env_var' in entry:
        header_names.append('authorization')
    if len(set(header_names)) != len(header_names):
        raise SyncError(f'MCP {name}: overlapping authentication/header definitions.')
    # Claude expands these strings and Codex does not. Never silently change meaning.
    strings = [entry.get('command', ''), entry.get('url', ''), *entry.get('args', []), *entry.get('env', {}).values(), *headers.values()]
    if any('${' in value for value in strings):
        raise SyncError(f'MCP {name}: variable interpolation is only shared through env_headers or bearer_token_env_var.')


def normalize(entry, tool, name):
    if not isinstance(entry, dict):
        raise SyncError(f'MCP {name}: expected an object.')
    if tool == 'claude' and entry.get('type') not in (None, 'stdio', 'http', 'streamable-http'):
        return None  # Unsupported transports remain local.
    if not entry.get('command') and not entry.get('url'):
        return None  # Native policy-only entries remain local.
    out = {k: deepcopy(v) for k, v in entry.items() if k in {'command', 'args', 'env', 'url'}}
    if tool == 'codex':
        for native, shared in [('http_headers', 'headers'), ('env_http_headers', 'env_headers'), ('bearer_token_env_var', 'bearer_token_env_var')]:
            if native in entry:
                out[shared] = deepcopy(entry[native])
    else:
        if not isinstance(entry.get('headers', {}), dict):
            raise SyncError(f'MCP {name}: invalid headers.')
        for key, value in entry.get('headers', {}).items():
            if not isinstance(value, str):
                raise SyncError(f'MCP {name}: invalid header value.')
            bearer = re.fullmatch(r'Bearer \$\{([A-Za-z_][A-Za-z0-9_]*)\}', value)
            variable = re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', value)
            if key.lower() == 'authorization' and bearer:
                out['bearer_token_env_var'] = bearer[1]
            elif variable:
                out.setdefault('env_headers', {})[key] = variable[1]
            else:
                out.setdefault('headers', {})[key] = value
    # Empty optional collections have identical meaning in both clients.
    out = {k: v for k, v in out.items() if v != {} and v != []}
    validate(out, name)
    return out


def render(entry, tool):
    result = {k: deepcopy(v) for k, v in entry.items() if k in {'command', 'args', 'env', 'url'}}
    if tool == 'claude':
        if 'url' in entry:
            result['type'] = 'http'
        headers = dict(entry.get('headers', {}))
        headers.update({key: '${' + value + '}' for key, value in entry.get('env_headers', {}).items()})
        if 'bearer_token_env_var' in entry:
            headers['Authorization'] = 'Bearer ${' + entry['bearer_token_env_var'] + '}'
        if headers:
            result['headers'] = headers
    else:
        for shared, native in [('headers', 'http_headers'), ('env_headers', 'env_http_headers'), ('bearer_token_env_var', 'bearer_token_env_var')]:
            if shared in entry:
                result[native] = deepcopy(entry[shared])
    return result


def merge(base, sources, initial=False):
    merged = {}
    keys = set(base).union(*(set(source) for source in sources))
    for key in sorted(keys):
        old = base.get(key, MISSING)
        changes = []
        for source in sources:
            value = source.get(key, MISSING)
            if initial and value is MISSING:
                continue
            if value != old and not any(value == previous for previous in changes):
                changes.append(value)
        if len(changes) > 1:
            raise SyncError(f'Conflict for {key}: align the definitions in the source files and retry.')
        value = changes[0] if changes else old
        if value is not MISSING:
            merged[key] = value
    return merged


def plan_mcp(plan, paths, state):
    for path in (paths.registry, paths.claude_config, paths.codex_config):
        if state.get('initialized') and not path.exists():
            raise SyncError(f'Previously synced file missing: {path}; restore it (use an empty MCP object for deliberate deletion).')
    registry = plan.json(paths.registry)
    canonical, overrides = {}, {}
    for name, entry in registry.items():
        if not isinstance(entry, dict) or set(entry) - COMMON - {'claude', 'codex'}:
            raise SyncError(f'MCP {name}: unknown shared fields; put native options under claude or codex.')
        canonical[name] = {k: deepcopy(v) for k, v in entry.items() if k in COMMON and v != {} and v != []}
        validate(canonical[name], name)
        overrides[name] = {}
        for tool, managed in [('claude', CLAUDE_FIELDS), ('codex', CODEX_FIELDS)]:
            override = entry.get(tool, {})
            if not isinstance(override, dict) or set(override) & managed:
                raise SyncError(f'MCP {name}: native overrides cannot replace shared fields.')
            if override:
                overrides[name][tool] = override
    claude = plan.json(paths.claude_config)
    codex_text = plan.read(paths.codex_config)
    if state.get('initialized') and not codex_text.strip():
        raise SyncError(f'Empty previously synced TOML: {paths.codex_config}; restore it or use an explicit empty MCP table.')
    try:
        codex = tomlkit.parse(codex_text)
    except Exception:
        raise SyncError(f'Invalid TOML: {paths.codex_config}') from None
    native = {'claude': claude.get('mcpServers', {}), 'codex': codex.get('mcp_servers', {})}
    extracted = {}
    for tool, entries in native.items():
        if not isinstance(entries, dict):
            raise SyncError(f'Invalid {tool} MCP table.')
        extracted[tool] = {}
        for name, entry in entries.items():
            normalized = normalize(entry, tool, name)
            if normalized is not None:
                extracted[tool][name] = normalized
            elif name in state.get('mcp', {}) or name in canonical:
                raise SyncError(f'MCP {name}: shared server changed to an unsupported {tool} transport.')
    merged = merge(state.get('mcp', {}), [canonical, extracted['claude'], extracted['codex']], not state.get('initialized'))
    for tool, entries in native.items():
        for name in set(entries) - set(extracted[tool]):
            if name in merged:
                raise SyncError(f'MCP conflict for {name}: an unsupported {tool} entry already uses that name.')
    managed_names = set(state.get('mcp', {})) | set(merged)
    for tool, document, key in [('claude', claude, 'mcpServers'), ('codex', codex, 'mcp_servers')]:
        if key not in document:
            document[key] = {}
        table = document[key]
        for name in managed_names:
            if name not in merged:
                table.pop(name, None)
        for name, entry in merged.items():
            # Update individual fields so TOML comments/local keys survive.
            if name not in table:
                table[name] = {}
            target = table[name]
            fields = CLAUDE_FIELDS if tool == 'claude' else CODEX_FIELDS
            rendered = render(entry, tool)
            # Transport-specific native settings must not follow a changed endpoint.
            old_identity = (target.get('command'), target.get('url'))
            new_identity = (entry.get('command'), entry.get('url'))
            if old_identity != new_identity:
                target.pop('oauth', None)
            for field in fields - set(rendered):
                target.pop(field, None)
            for field, value in {**rendered, **overrides.get(name, {}).get(tool, {})}.items():
                if target.get(field, MISSING) != value:
                    target[field] = value
    plan.write_json(paths.registry, {name: {**entry, **overrides.get(name, {})} for name, entry in merged.items()})
    plan.write_json(paths.claude_config, claude)
    plan.write(paths.codex_config, tomlkit.dumps(codex))
    state['mcp'] = merged
