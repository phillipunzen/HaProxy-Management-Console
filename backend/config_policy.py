"""Directives that let a HAProxy configuration execute code on the host.

`program` sections start arbitrary processes, `lua-load` runs Lua already during
`haproxy -c`, and external checks fork commands. Whoever may add these lines has
root-equivalent access to the HAProxy host, so only admins may introduce them.
Lines that are already part of the active configuration stay editable for
operators (e.g. imported setups), as long as nothing new is added.
"""

import re

_KEYWORDS = re.compile(
    r'^(?:'
    r'program\b'
    r'|lua-load(?:-per-thread)?\b'
    r'|lua-prepend-path\b'
    r'|external-check\b'
    r'|option\s+external-check\b'
    r'|insecure-fork-wanted\b'
    r'|insecure-setuid-wanted\b'
    r')',
    re.IGNORECASE,
)


def _strip_comment(line: str) -> str:
    out = []
    quote = None
    escaped = False
    for char in line:
        if escaped:
            out.append(char)
            escaped = False
            continue
        if char == '\\':
            out.append(char)
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
        elif char in '"\'':
            quote = char
        elif char == '#':
            break
        out.append(char)
    return ''.join(out)


def privileged_directives(config: str) -> set[str]:
    """Normalized privileged lines of a configuration (whitespace collapsed)."""
    found = set()
    for raw in (config or '').splitlines():
        line = ' '.join(_strip_comment(raw).split())
        if line and _KEYWORDS.match(line):
            found.add(line)
    return found


def added_privileged_directives(new_config: str, active_config: str) -> list[str]:
    return sorted(privileged_directives(new_config) - privileged_directives(active_config))


def message(lines: list[str]) -> str:
    shown = ', '.join(lines[:5]) + (' …' if len(lines) > 5 else '')
    return (
        'Diese Direktiven können Programme auf dem HAProxy-Server ausführen und dürfen nur '
        'von Administratoren hinzugefügt werden: ' + shown
    )
