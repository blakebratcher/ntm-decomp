#!/usr/bin/env python3
"""Form-agnostic view of a P1 assembly source.

A P1 source exists in two byte-identical forms:

  raw        - Ghidra listing style: plain labels and raw hex operands
               (``jsr ($0000d0f6).l``, ``bsr.w $28ae``)
  symbolized - tools/symbolize.py --write: `` include "symbols.i"`` right
               after ``org``, symbolic operands (``jsr
               (sprite_map_blit_wrap).l``, ``bsr.w l_28ae``) and entry or
               secondary-entry labels commented out as ``; name:`` markers
               when the generated include already defines the name.

Both forms assemble to identical bytes.  Tools and tests that read source
text for addresses, labels or per-instruction facts should go through this
module so they accept either form:

  org_of(text)             the org directive value
  labels(text)             [(line_no, name, commented)] - every label line,
                           ``; name:`` markers included
  entry_label(text)        the first label after org (marker or live label)
  assemble(src)            assembled bytes: vasm -Fbin with -I build/gen/p1,
                           generating symbols.i first when it is missing
  listing(src)             per-source-line (addr, size, text) plus span_end
                           and the file's own label->addr map from one
                           vasm -L run
  load_symbols()           name -> addr for every global symbol
                           (build/gen/p1/symbols.i, falling back to the
                           shared config tables)
  canonicalize(code, ...)  symbolic operands back to raw hex (``(name).l``
                           -> ``($73b66).l``); local labels ``l_XXXX`` decode
                           from their name
  line_addrs(code, ...)    the addresses one instruction line references
  iter_lines()             canonicalized code lines of every corpus source
  refs(addr)               [(path, lineno, text, kind)] - every source line
                           that references addr (kind: pcrel / abs / imm)
"""
import pathlib
import re
import subprocess
import tempfile

import symbolize

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'
SRC_DIRS = [REPO / 'src' / 'p1' / 'asm' / 'functions',
            REPO / 'src' / 'p1' / 'asm' / 'blocks']
INC_DIR = REPO / 'build' / 'gen' / 'p1'
INCLUDE = INC_DIR / 'symbols.i'

ORG_RE = re.compile(r'^\s*org\s+\$([0-9a-fA-F]+)')
# a live label (`name:`) or a commented marker (`; name:`); the optional
# trailing comment is only allowed right after the colon
LABEL_RE = re.compile(r'^\s*;?\s*([A-Za-z_][A-Za-z0-9_]*):\s*(;.*)?$')
EQU_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s+equ\s+\$([0-9a-fA-F]+)')
TOKEN = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
LOCAL = re.compile(r'^l_([0-9a-fA-F]{1,8})$')
ROW_OFF = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\+(?:\$([0-9a-fA-F]+)|(\d+))\b')

# vasm listing byte lines: `<section>:<addr> <hexbytes> <line>: <source text>`
LIST_LINE = re.compile(
    r'^(?P<sec>\d+):(?P<addr>[0-9a-fA-F]+)\s+'
    r'(?P<hex>(?:[0-9a-fA-F]{2})+)\s+'
    r'(?P<line>\d+):\s*(?P<text>.*)$')
CONT_LINE = re.compile(
    r'^(?P<sec>\d+):(?P<addr>[0-9a-fA-F]+)\s+'
    r'(?P<hex>(?:[0-9a-fA-F]{2})+)$')
# vasm listing symbol table: labels are A:<addr>, equates E:<addr>
SYM_LINE = re.compile(r'^(\S+)\s+[AE]:([0-9a-fA-F]{1,8})\s*$')


def org_of(text):
    for line in text.splitlines():
        m = ORG_RE.match(line)
        if m:
            return int(m.group(1), 16)
    raise ValueError('missing org directive')


def labels(text):
    """[(line_no, name, commented)] for every label line, 1-based.

    Commented lines are the ``; name:`` markers symbolize.py leaves for
    entry labels the generated include already defines - they carry the
    same name and address as a live label.
    """
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = LABEL_RE.match(line)
        if m:
            out.append((i, m.group(1), line.lstrip().startswith(';')))
    return out


def entry_label(text):
    """The first label name after org, live or commented marker."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if ORG_RE.match(line))
    for line in lines[start + 1:]:
        m = LABEL_RE.match(line)
        if m:
            return m.group(1)
    return None


def ensure_include(config_root=REPO):
    """Return the symbols.i path, generating it when it does not exist yet.

    Cheap when present (make check and make symbolize always generate it);
    the fallback lets a direct pytest run on a fresh tree work.
    """
    inc = config_root / 'build' / 'gen' / 'p1' / 'symbols.i'
    if not inc.exists():
        symbolize.generate_include(
            config_root,
            [config_root / 'src' / 'p1' / 'asm' / 'functions',
             config_root / 'src' / 'p1' / 'asm' / 'blocks'],
            inc)
    return inc


def vasm_include_args(config_root=REPO):
    """[-I dir] for vasm when the generated include directory exists."""
    d = config_root / 'build' / 'gen' / 'p1'
    return [f'-I{d}'] if d.exists() else []


def assemble(src, config_root=REPO):
    """Assemble one source and return its bytes (vasm -Fbin with the
    symbols.i include path)."""
    ensure_include(config_root)
    with tempfile.TemporaryDirectory(prefix='ntm-corpus-') as td:
        out = pathlib.Path(td) / 'o.bin'
        run = subprocess.run(
            [str(VASM), '-Fbin', '-no-opt', '-quiet']
            + vasm_include_args(config_root)
            + ['-o', str(out), str(src)],
            capture_output=True, text=True)
        if run.returncode != 0:
            raise SystemExit(f'vasm failed on {src}:\n{run.stderr}')
        return out.read_bytes()


def listing(src, config_root=REPO):
    """(lines, span_end, file_symbols) from one vasm -L run.

    lines maps the 1-based source line number of every byte-emitting line
    to (addr, size, text); file_symbols maps the file's own label names
    (local labels included) to addresses.
    """
    with tempfile.TemporaryDirectory(prefix='ntm-corpus-') as td:
        lst = pathlib.Path(td) / 'x.lst'
        out = pathlib.Path(td) / 'x.bin'
        ensure_include(config_root)
        run = subprocess.run(
            [str(VASM), '-Fbin', '-no-opt', '-quiet', '-L', str(lst)]
            + vasm_include_args(config_root)
            + ['-o', str(out), str(src)],
            capture_output=True, text=True)
        if run.returncode != 0:
            raise SystemExit(f'vasm failed on {src}:\n{run.stderr}')
        lines, span_end, last = {}, 0, None
        file_symbols = {}
        in_symbols = False
        for line in lst.read_text(errors='replace').splitlines():
            if line.startswith('Symbols by name:'):
                in_symbols = True
                continue
            if in_symbols:
                if not line.strip():
                    in_symbols = False
                    continue
                m = SYM_LINE.match(line)
                if m:
                    file_symbols[m.group(1)] = int(m.group(2), 16)
                continue
            m = LIST_LINE.match(line)
            if m:
                line_no = int(m.group('line'))
                addr = int(m.group('addr'), 16)
                size = len(m.group('hex')) // 2
                lines[line_no] = (addr, size, m.group('text'))
                span_end = max(span_end, addr + size)
                last = line_no
                continue
            m = CONT_LINE.match(line)
            if m and last is not None:
                addr, size, text = lines[last]
                extra = len(m.group('hex')) // 2
                lines[last] = (addr, size + extra, text)
                span_end = max(span_end, addr + size + extra)
    return lines, span_end, file_symbols


def load_symbols(config_root=REPO):
    """{name: addr} for every global symbol.

    Prefers the generated include (authoritative after a build or a
    symbolize --write); falls back to the shared config tables so the
    helper also works on a fresh tree.
    """
    inc = config_root / 'build' / 'gen' / 'p1' / 'symbols.i'
    if inc.exists():
        out = {}
        for line in inc.read_text().splitlines():
            m = EQU_RE.match(line)
            if m:
                out[m.group(1)] = int(m.group(2), 16)
        return out
    merged, _name_addr, _conflicts = symbolize.load_tables(config_root)
    return {name: addr for addr, (name, _src) in merged.items()}


def canonicalize(code, symbols):
    """Rewrite symbolic operands back to raw hex form.

    Every identifier token that names a global symbol becomes $<addr>;
    local labels ``l_XXXX`` decode from their own name.  The instruction
    mnemonic is never touched (it cannot be a symbol operand) and any
    comment is stripped.  Raw lines pass through unchanged.
    """
    code = code.split(';', 1)[0].rstrip()
    m = re.match(r'^(\s*\S+)(.*)$', code)
    if not m:
        return code

    def sub(tok):
        t = tok.group(0)
        if t in symbols:
            return f'${symbols[t]:x}'
        lm = LOCAL.match(t)
        if lm:
            return f'${int(lm.group(1), 16):x}'
        return t

    def fold(mm):
        # symbolize's row+$off form: an address inside a classified row
        base = symbols.get(mm.group(1))
        if base is None:
            return mm.group(0)
        off = int(mm.group(2), 16) if mm.group(2) else int(mm.group(3))
        return f'${base + off:x}'

    operands = ROW_OFF.sub(fold, m.group(2))
    return m.group(1) + TOKEN.sub(sub, operands)


def line_addrs(code, symbols):
    """Every address one instruction line references (canonical operand view).

    Covers the forms symbolize.py rewrites: branch/jump targets, absolute
    and PC-relative memory operands, movea immediates and install
    immediates.
    """
    c = canonicalize(code, symbols)
    addrs = set()
    for rx in symbolize.BRANCH_RE:
        m = rx.match(c)
        if m:
            addrs.add(int(m.group('tgt'), 16))
            break
    for rx in symbolize.OP_RE:
        for m in rx.finditer(c):
            addrs.add(int(m.group('tgt'), 16))
    m = symbolize.MOVEA_IMM.match(c)
    if m:
        addrs.add(int(m.group('val'), 16))
    m = symbolize.INSTALL_IMM.match(c)
    if m:
        addrs.add(int(m.group('val'), 16))
    return addrs


def iter_lines(dirs=None, symbols=None):
    """Yield (path, org, line_no, canonical_code) for every corpus source.

    canonical_code is the comment-stripped line with symbolic operands in
    raw hex form, so raw-hex scans work on either source form.
    """
    if symbols is None:
        symbols = load_symbols()
    for d in dirs or SRC_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.glob('*.s')):
            text = p.read_text(errors='replace')
            try:
                org = org_of(text)
            except ValueError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                code = line.split(';', 1)[0]
                if not code.strip():
                    continue
                try:
                    rel = str(p.relative_to(REPO))
                except ValueError:
                    rel = str(p)
                yield rel, org, n, canonicalize(code, symbols)


def refs(addr, dirs=None, symbols=None):
    """[(path, lineno, text, kind)] for lines referencing addr.

    kind is 'pcrel' for ($addr,PC), 'abs' for a 6-8 digit $addr operand,
    'imm' otherwise - the same classes the lane refscans report.  Works on
    raw and symbolized sources alike.
    """
    out = []
    hex8 = f'{addr:08x}'
    hex6 = f'{addr:06x}'
    h = f'{addr:x}'
    for path, _org, n, code in iter_lines(dirs, symbols):
        low = code.lower()
        if f'${h}' not in low and f'${hex8}' not in low:
            continue
        kind = 'imm'
        if (f'(${h},pc)' in low or f'(${hex8},pc)' in low
                or f'${h}(pc)' in low or f'${hex8}(pc)' in low):
            kind = 'pcrel'
        elif (f'(${h})' in low or f'(${hex8})' in low or f'(${hex6})' in low):
            kind = 'abs'
        out.append((path, n, code, kind))
    return out
