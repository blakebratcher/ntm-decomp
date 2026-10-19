"""Symbol tables shared by the pipeline.

Code/data names: config/names.tsv plus config/names/*.tsv (one file per lane),
rows `<hex addr>  <name>  <evidence note>`.
RAM variables: config/ram/*.tsv, rows `<hex addr>  <hex size>  <name>  <evidence note>`.
An address may carry one name and a name one address across all files.
M1 (Z80) symbols live apart in config/m1/{names,ram}.tsv: Z80 addresses overlap
P1 addresses numerically, so the namespaces must never mix (load_names(cpu='m1')).
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _rows(paths, fields):
    for path in paths:
        if not path.exists():
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip() or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < fields:
                raise ValueError(f'{path.name}:{n}: expected {fields} tab-separated fields')
            yield f'{path.name}:{n}', parts


def _unique(entries):
    by_addr, by_name = {}, {}
    for where, addr, name, rest in entries:
        if not NAME.match(name):
            raise ValueError(f'{where}: bad name {name!r}')
        if addr in by_addr and by_addr[addr][0] != name:
            raise ValueError(f'{where}: {addr:#x} named {name} and {by_addr[addr][0]}')
        if name in by_name and by_name[name] != addr:
            raise ValueError(f'{where}: {name} names {addr:#x} and {by_name[name]:#x}')
        by_addr[addr] = (name, rest)
        by_name[name] = addr
    return by_addr


def load_names(root=REPO, cpu='p1'):
    """{addr: (name, note)} for code and data labels."""
    if cpu == 'm1':
        paths = [root / 'config' / 'm1' / 'names.tsv']
    else:
        paths = [root / 'config' / 'names.tsv'] + sorted((root / 'config' / 'names').glob('*.tsv'))
    return _unique((w, int(p[0], 16), p[1].strip(), p[2].strip() if len(p) > 2 else '')
                   for w, p in _rows(paths, 2))


def load_ram(root=REPO, cpu='p1'):
    """{addr: (name, (size, note))} for work-RAM variables."""
    paths = ([root / 'config' / 'm1' / 'ram.tsv'] if cpu == 'm1'
             else sorted((root / 'config' / 'ram').glob('*.tsv')))
    return _unique((w, int(p[0], 16), p[2].strip(), (int(p[1], 16), p[3].strip() if len(p) > 3 else ''))
                   for w, p in _rows(paths, 3))


def check_namespaces(root=REPO):
    """Raise if a P1 name is used both as a code/data label and as a RAM variable."""
    code = {name: addr for addr, (name, _) in load_names(root).items()}
    clash = sorted((name, code[name], addr) for addr, (name, _) in load_ram(root).items()
                   if name in code and code[name] != addr)
    if clash:
        raise ValueError('names used for both code and RAM: ' +
                         ', '.join(f'{n} ({c:#x} / {r:#x})' for n, c, r in clash))
