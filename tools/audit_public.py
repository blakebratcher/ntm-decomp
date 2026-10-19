#!/usr/bin/env python3
"""Fail if the public tree contains source corpus, ROM data, or personal paths.

The public repository is an allowlist export.  ``.public-allowlist`` is the
exact set of tracked files; any addition or removal is an error until the
allowlist is updated deliberately.
"""
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
ALLOW = REPO / '.public-allowlist'

FORBIDDEN_PREFIXES = ('src/', 'build/', 'docs/lanes/', 'docs/prompts/',
                      'docs/superpowers/')
FORBIDDEN_NAMES = {'AGENTS.md', 'CLAUDE.md'}
FORBIDDEN_EXT = re.compile(r'\.(p1|m1|c1|c2|s1|v1|v2|v3|v4|zip|neo|bin|raw)$',
                           re.I)
PERSONAL = re.compile(r'/home/|~/[.]local|~/[.]config|pixel-foundry')
LONG_B64 = re.compile(r'[A-Za-z0-9+/]{1000,}={0,2}')
LONG_HEX = re.compile(r'(?:[0-9a-fA-F]{2}[ :]?){128,}')
SHA256 = re.compile(r'\b[0-9a-fA-F]{64}\b')

# The audit script necessarily contains the patterns it searches for.
EXEMPT_TEXT = {'tools/audit_public.py'}


def tracked():
    out = subprocess.check_output(['git', '-C', str(REPO), 'ls-files'],
                                  text=True)
    return [line for line in out.splitlines() if line]


def main():
    files = tracked()
    errors = []

    if not ALLOW.exists():
        errors.append('.public-allowlist is missing')
    else:
        allow = sorted(line.strip() for line in ALLOW.read_text().splitlines()
                       if line.strip())
        actual = sorted(files)
        if actual != allow:
            missing = sorted(set(allow) - set(actual))
            extra = sorted(set(actual) - set(allow))
            if missing:
                errors.append('allowlist entries missing from tree: '
                              + ', '.join(missing[:10]))
            if extra:
                errors.append('unlisted tracked files: '
                              + ', '.join(extra[:10]))

    for name in files:
        if name.startswith(FORBIDDEN_PREFIXES):
            errors.append(f'forbidden path: {name}')
        elif name in FORBIDDEN_NAMES:
            errors.append(f'forbidden path: {name}')
        elif FORBIDDEN_EXT.search(name):
            errors.append(f'ROM/binary-like path: {name}')

    for name in files:
        if name in EXEMPT_TEXT:
            continue
        path = REPO / name
        if path.is_symlink():
            errors.append(f'symlink: {name}')
            continue
        try:
            text = path.read_text(errors='replace')
        except OSError:
            continue
        if PERSONAL.search(text):
            errors.append(f'personal path in {name}')
        for line_no, line in enumerate(text.splitlines(), 1):
            if 'sha256' in line.lower() or SHA256.search(line):
                continue
            if LONG_B64.search(line):
                errors.append(f'long base64-like run in {name}:{line_no}')
                break
            if LONG_HEX.search(line):
                errors.append(f'long hex run in {name}:{line_no}')
                break

    if errors:
        print('public-tree audit FAILED:', file=sys.stderr)
        for error in errors[:40]:
            print('  ' + error, file=sys.stderr)
        return 1

    print(f'public-tree audit OK: {len(files)} tracked files, '
          'no source-corpus/ROM/personal content')
    return 0


if __name__ == '__main__':
    sys.exit(main())
