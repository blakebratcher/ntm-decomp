#!/usr/bin/env python3
"""Symbolize P1 assembly sources: raw address operands -> labels and equates.

Turns Ghidra-style listings whose operands are raw addresses

    jsr ($0000d0f6).l        bsr.w $000028ae        lea ($62e30,PC),a0
    move.w ($10fdb4).l,d0    move.l #$f08,(a6)      movea.l #$3c0000,a0

into symbolic form with byte-identical output:

    jsr (sprite_map_blit_wrap).l   bsr.w l_28ae   lea (stamp_ladders,PC),a0
    move.w (start_latch).l,d0      move.l #actor_draw,(a6)

Rules (the byte gate arbitrates; every rewrite preserves the operand size, so
the encoding never changes):

  * Branch/jump targets (bcc/bsr/bra/dbcc, jsr/jmp): a target inside the same
    source becomes a local label `l_<addr>` created at that address; a target
    that carries a global name (or the source's own entry) uses the name.
    Explicit .s/.w/.l suffixes are kept, so displacements do not change.
  * Absolute memory operands ($X).l / ($X).w / ($X,PC) / $X(PC): the address
    is replaced by its symbol.  Pure address substitution - no data-flow
    question, no size change.  An address with no symbol of its own that
    falls *inside* a classified region row (config/regions/p1/*.tsv or
    config/data_regions.tsv) becomes `row+$off` - the field of that row.
    A reference into a proven-unreferenced `fill` row is a contradiction and
    stays raw (reported as `abs-fill-target`).
  * movea.l/w #$X,aN: rewritten when $X is a known address (the opcode is an
    address load by construction).
  * movea #$X,aN with $X in the fix map (0x7000-0x74ff): named from
    config/vram/fix.tsv (FIX_Xxx_Yyy) only when VramFlow proves the value
    reaches REG_VRAMADDR unchanged; such a site never takes a ROM name, and
    ROM operands never take a FIX name.
  * move.l #$X,(aN): rewritten only when $X is a known address AND the same
    source jumps or calls $X as code - the state-machine install idiom
    (`move.l #next_state,(a6)` followed by `jmp (next_state).l`) - or when
    config/install_proofs.tsv proves that exact site (instruction address +
    immediate): the install was traced into an actor node and dispatched
    (tools/lane_syminstall.py, docs/lanes/sym-install.md).
  * Anything ambiguous (no symbol at the target, a branch landing inside an
    instruction, ordinary constants) is left raw and reported.

Global symbols come from the shared tables (config/names*.tsv, the
config/functions.tsv inventory, config/regions/<cpu>/*.tsv row names,
config/ram/*.tsv RAM variables) plus the built-in Neo Geo system-register
map.  They are emitted as `name equ $addr` into a generated include
(build/gen/p1/symbols.i) that each source includes, so vasm resolves them
identically in every file.

vasm rejects a label that redefines an equate, so the include and the labels
in the sources must not double-define a name: a source's top label is
commented out when the include already defines that name at the same
address; a label whose name belongs to a *different* address (or an interior
label shadowing a table name) is kept and its name is dropped from the
include with a warning.

Modes:
  --dry-run  rewrite a scratch copy of the tree, assemble every file,
             byte-gate it against the pristine ROM, print the counts.
  --write    run the dry-run gate first and only then apply the rewrite to
             the real tree (never leaves a broken tree behind).
"""
import argparse
import collections
import pathlib
import re
import shutil
import subprocess
import sys

import symbols

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmm68k_mot'
FUNC_DIR = REPO / 'src' / 'p1' / 'asm' / 'functions'
BLOCK_DIR = REPO / 'src' / 'p1' / 'asm' / 'blocks'
WORK_DIR = REPO / 'build' / 'work'
INCLUDE_NAME = 'symbols.i'

# Fallback Neo Geo register names, used only where config/ram/hardware.tsv
# (ngdevkit's canonical table) has no row; the table always wins.
SYSTEM_REGISTERS = {
    0x320000: 'REG_SOUND',       # audio command latch to the Z80
    0x3c0000: 'REG_VRAMADDR',    # LSPC VRAM address
    0x3c0002: 'REG_VRAMRW',      # VRAM read/write data
    0x3c0004: 'REG_VRAMMOD',     # VRAM address increment
    0x3c0006: 'REG_LSPCMODE',    # LSPC mode
    0x3c0008: 'REG_TIMERHIGH',   # timer high byte
    0x3c000a: 'REG_TIMERLOW',    # timer low byte
    0x3c000c: 'REG_IRQACK',      # IRQ acknowledge
    0x3c000e: 'REG_TIMERSTOP',   # timer stop flag
    0x400000: 'REG_PALETTERAM',  # palette RAM base
}

# vasm 68k directives - a table symbol with one of these names would shadow
# the directive and produce confusing warnings, so it is skipped.
RESERVED = {
    'org', 'equ', 'set', 'include', 'incdir', 'if', 'ifdef', 'ifndef', 'ifd',
    'ifnd', 'ifeq', 'ifne', 'ifgt', 'ifge', 'iflt', 'ifle', 'ifb', 'ifnb',
    'ifc', 'ifnc', 'else', 'elseif', 'endc', 'endif', 'macro', 'endm',
    'exitm', 'rept', 'endr', 'end', 'fail', 'error', 'warning', 'print',
    'dephase', 'phase', 'align', 'cnop', 'even', 'odd', 'section', 'rs',
    'rsreset', 'xdef', 'public', 'global', 'xref', 'external', 'struct',
    'endstruct', 'comment', 'list', 'nolist', 'opt', 'machine', 'clrmac',
    'local', 'dc', 'dcb', 'ds', 'blk', 'forever', 'while', 'wend', 'repeat',
    'until', 'switch', 'case', 'endswitch', 'function', 'endfunction',
}

ORG_RE = re.compile(r'^\s*org\s+\$([0-9a-fA-F]+)')
LABEL_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*):\s*(;.*)?$')
HEX = r'[0-9a-fA-F]+'

BCC = r'b(?:cc|cs|eq|ne|mi|pl|ge|lt|gt|le|hi|ls|hs|lo|vc|vs)'
DBCC = r'db(?:t|f|ra|cc|cs|eq|ne|mi|pl|ge|lt|gt|le|hi|ls|hs|lo|vc|vs)'
REL_BRANCH = re.compile(
    rf'^(?P<ind>[ \t]*)(?P<mn>bsr|bra|{BCC})(?P<sz>\.[sw])?'
    rf'[ \t]+\$(?P<tgt>{HEX})(?P<tail>[ \t]*)$')
DB_BRANCH = re.compile(
    rf'^(?P<ind>[ \t]*)(?P<mn>{DBCC})[ \t]+(?P<reg>d[0-7])[ \t]*,[ \t]*'
    rf'\$(?P<tgt>{HEX})(?P<tail>[ \t]*)$')
ABS_BRANCH = re.compile(
    rf'^(?P<ind>[ \t]*)(?P<mn>jsr|jmp)[ \t]+'
    rf'\(\$(?P<tgt>{HEX})\)(?P<sz>\.l)?(?P<tail>[ \t]*)$')
PC_BRANCH = re.compile(
    rf'^(?P<ind>[ \t]*)(?P<mn>jsr|jmp)[ \t]+'
    rf'\(\$(?P<tgt>{HEX}),PC\)(?P<tail>[ \t]*)$')
IDX_BRANCH = re.compile(
    rf'^(?P<ind>[ \t]*)(?P<mn>jsr|jmp)[ \t]+'
    rf'\$(?P<tgt>{HEX})\(PC\)(?P<tail>[ \t]*)$')
BRANCH_RE = (REL_BRANCH, DB_BRANCH, ABS_BRANCH, PC_BRANCH, IDX_BRANCH)

# generic absolute memory operands (any instruction, possibly several per line)
ABS_OP = re.compile(rf'\(\$(?P<tgt>{HEX})\)(?P<sz>\.l|\.w)')
PC_OP = re.compile(rf'\(\$(?P<tgt>{HEX}),PC\)')
IDX_OP = re.compile(rf'\$(?P<tgt>{HEX})\(PC\)')
OP_RE = (ABS_OP, PC_OP, IDX_OP)

MOVEA_IMM = re.compile(
    rf'^(?P<ind>[ \t]*)movea\.(?P<sz>[wl])[ \t]+'
    rf'#\$(?P<val>{HEX}),(?P<areg>a[0-7])(?P<tail>[ \t]*)$')
INSTALL_IMM = re.compile(
    rf'^(?P<ind>[ \t]*)move\.l[ \t]+#\$(?P<val>{HEX})[ \t]*,'
    rf'(?P<dst>\((?:a[0-7]|d[0-7],a[0-7])\))(?P<tail>[ \t]*)$')

# vasm listing: `<section>:<addr> <hexbytes> <line>: <source text>`.  The hex
# bytes and the line number are both hex digits, so the hex run is only valid
# when it is followed by whitespace and the `NNN:` column - the regex engine's
# backtracking then yields exactly one split.  Long byte runs wrap onto
# continuation lines (`<section>:<addr> <hexbytes>`, no line column).
LIST_LINE = re.compile(
    r'^(?P<sec>\d+):(?P<addr>[0-9a-fA-F]+)\s+'
    r'(?P<hex>(?:[0-9a-fA-F]{2})+)\s+'
    r'(?P<line>\d+):\s*(?P<text>.*)$')
CONT_LINE = re.compile(
    r'^(?P<sec>\d+):(?P<addr>[0-9a-fA-F]+)\s+'
    r'(?P<hex>(?:[0-9a-fA-F]{2})+)$')


# --------------------------------------------------------------------------
# symbol tables

def load_tables(config_root=REPO):
    """Merged {addr: (name, source)} from every symbol source, first wins.

    Precedence: friendly names (config/names*.tsv) > inventory names
    (config/functions.tsv) > region row names > RAM variables > system
    registers.  Also returns the name->addr map and a list of conflicts
    (one name on two addresses; later definitions are dropped).
    """
    merged, name_addr, conflicts = {}, {}, []

    def add(addr, name, source):
        if name in name_addr and name_addr[name] != addr:
            conflicts.append(f'{source}: {name} names ${addr:x} and '
                             f'${name_addr[name]:x} (dropped)')
            return
        if addr in merged:
            if merged[addr][0] != name:
                # a higher-priority name (e.g. a friendly names.tsv row) is
                # the deliberate winner over the inventory FUN_ name; only
                # report two names from the *same* source as a conflict
                if merged[addr][1] == source:
                    conflicts.append(f'{source}: ${addr:x} named {name} and '
                                     f'{merged[addr][0]}')
            return
        merged[addr] = (name, source)
        name_addr[name] = addr

    for addr, (name, _note) in symbols.load_names(config_root).items():
        add(addr, name, 'names')
    functions = config_root / 'config' / 'functions.tsv'
    if functions.exists():
        for addr, name in _tsv_rows(functions.read_text(), 'functions.tsv', 3, 1):
            add(addr, name, 'functions')
    for path in sorted((config_root / 'config' / 'regions' / 'p1').glob('*.tsv')):
        for addr, name in _tsv_rows(path.read_text(),
                                    f'regions/p1/{path.name}', 5, 3):
            add(addr, name, f'regions/{path.name}')
    # the legacy data-section table names the same kind of claim as a regions
    # row (a classified data block at an exact address), so its row starts are
    # symbols too: <hex start> <hex size> <name> <evidence>
    data_regions = config_root / 'config' / 'data_regions.tsv'
    if data_regions.exists():
        for addr, name in _tsv_rows(data_regions.read_text(),
                                    'data_regions.tsv', 4, 2):
            add(addr, name, 'data_regions.tsv')
    for addr, (name, _size_note) in symbols.load_ram(config_root).items():
        add(addr, name, 'ram')
    for addr, name in SYSTEM_REGISTERS.items():
        add(addr, name, 'system')
    return merged, name_addr, conflicts


def _tsv_rows(text, origin, fields, name_field=1):
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) < fields:
            raise ValueError(f'{origin}:{n}: expected {fields} fields')
        yield int(parts[0].strip(), 16), parts[name_field].strip()


def name_of(addr, merged, excluded):
    """The usable global name for addr, or None."""
    if addr not in merged:
        return None
    name = merged[addr][0]
    return None if name in excluded else name


# --------------------------------------------------------------------------
# install proofs (state installs dispatched outside their own source)

def load_install_proofs(config_root=REPO):
    """{(site, target)} from config/install_proofs.tsv.

    Rows: `<hex site> <hex target> <idiom> <evidence>`.  A row proves that
    the `move.l #$target,(aN)` instruction at `site` is a state install of
    the code entry `target` (the evidence says how); symbolize rewrites only
    that instruction, and only when its immediate is `target`.
    """
    path = config_root / 'config' / 'install_proofs.tsv'
    proofs = set()
    if not path.exists():
        return proofs
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        p = line.split('\t')
        if len(p) < 4 or not p[2].strip() or not p[3].strip():
            raise ValueError(f'install_proofs.tsv:{n}: expected <site> '
                             f'<target> <idiom> <evidence>')
        proofs.add((int(p[0], 16), int(p[1], 16)))
    return proofs


# --------------------------------------------------------------------------
# region rows (base+offset operands)

def load_region_rows(config_root=REPO):
    """[(start, size, kind, name)] for every classified P1 region row.

    Reads config/regions/p1/*.tsv (`<hex start> <hex size> <kind> <name>
    <evidence>`) and config/data_regions.tsv (`<hex start> <hex size> <name>
    <evidence>`, kind `data`).
    """
    rows = []
    for path in sorted((config_root / 'config' / 'regions' / 'p1').glob('*.tsv')):
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            p = line.split('\t')
            if len(p) < 5:
                raise ValueError(f'{path.name}: expected 5 fields')
            rows.append((int(p[0], 16), int(p[1], 16), p[2].strip(),
                         p[3].strip()))
    path = config_root / 'config' / 'data_regions.tsv'
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith('#'):
                continue
            p = line.split('\t')
            if len(p) < 4:
                raise ValueError('data_regions.tsv: expected 4 fields')
            rows.append((int(p[0], 16), int(p[1], 16), 'data', p[2].strip()))
    return rows


def region_field(addr, rows):
    """(start, offset, kind, name) when addr is *inside* a region row.

    Strictly interior: an address that is a row start is already a symbol and
    never needs the offset form.
    """
    for start, size, kind, name in rows:
        if start < addr < start + size:
            return start, addr - start, kind, name
    return None


# --------------------------------------------------------------------------
# per-file facts

def org_of(text):
    for line in text.splitlines():
        m = ORG_RE.match(line)
        if m:
            return int(m.group(1), 16)
    raise ValueError('missing org directive')


def labels_of(text):
    """[(line_no, name)] for every label line, 1-based."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = LABEL_RE.match(line)
        if m:
            out.append((i, m.group(1)))
    return out


def vasm_listing(src, work_dir):
    """Assemble one source and parse the -L listing.

    Returns (lines, span_end): lines maps the 1-based source line number of
    every byte-emitting line to (addr, size, text).  Raises SystemExit when
    vasm fails.  Scratch listing/bin files land in work_dir.
    """
    listing_path = work_dir / (src.name + '.lst')
    out_path = work_dir / (src.name + '.bin')
    # a symbolized source includes build/gen/p1/symbols.i; pass -I when the
    # directory exists so the listing also works after tools/symbolize --write
    cmd = [str(VASM), '-Fbin', '-no-opt', '-quiet']
    inc = REPO / 'build' / 'gen' / 'p1'
    if inc.exists():
        cmd.append(f'-I{inc}')
    cmd += ['-L', str(listing_path), '-o', str(out_path), str(src)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f'vasm failed on {src.name}:\n{result.stderr}')
    listing = listing_path.read_text(errors='replace')
    listing_path.unlink(missing_ok=True)
    lines, span_end, last = {}, 0, None
    for line in listing.splitlines():
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
            # wrapped continuation of the previous byte-emitting line
            addr, size, text = lines[last]
            extra = len(m.group('hex')) // 2
            lines[last] = (addr, size + extra, text)
            span_end = max(span_end, addr + size + extra)
    if out_path.exists() and out_path.stat().st_size != span_end - org_of(
            src.read_text()):
        raise SystemExit(f'{src.name}: listing size does not match the '
                         f'assembled output (unparsed listing line?)')
    return lines, span_end


def jump_targets(text):
    """Every address the source treats as code (branch/jsr/jmp targets)."""
    targets = set()
    for line in text.splitlines():
        code = code_of(line)
        for rx in BRANCH_RE:
            m = rx.match(code)
            if m:
                targets.add(int(m.group('tgt'), 16))
                break
    return targets


def code_of(line):
    """Instruction text with any trailing comment stripped."""
    return line.split(';', 1)[0]


# --------------------------------------------------------------------------
# include generation

def inner_label_addrs(src, labels):
    """{label line: address} via one vasm listing (label addr = next instruction)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix='ntm-symbolize-') as td:
        listing, _end = vasm_listing(src, pathlib.Path(td))
    insns = sorted(listing.items())
    return {line_no: next((a for n, (a, _s, _t) in insns if n > line_no), None)
            for line_no, _name in labels}


def plan_labels(src_roots, name_addr):
    """Cheap scan of every source's org and labels (no assembly).

    Returns (info, excluded): info maps path -> (org, [(line_no, name)]),
    excluded maps names to drop from the include to their reasons.  A top
    label whose name matches the table at the file's org is fine (the
    rewriter comments it out, the equate takes over); any other label
    shadowing a table name forces the name out of the include.
    """
    info, excluded = {}, {}
    for root in src_roots:
        for src in sorted(root.glob('*.s')):
            text = src.read_text()
            org = org_of(text)
            labels = labels_of(text)
            info[src] = (org, labels)
            top_line = labels[0][0] if labels else None
            inner = None
            for line_no, name in labels:
                if name not in name_addr:
                    continue
                if line_no == top_line and name_addr[name] == org:
                    continue  # stripped by the rewriter, equate takes over
                if inner is None:
                    inner = inner_label_addrs(src, labels)
                if inner.get(line_no) == name_addr[name]:
                    continue  # inner entry at its table address: stripped too
                reason = (f'{src.name}:{line_no} label {name} (file org '
                          f'${org:x}) vs table ${name_addr[name]:x}')
                excluded.setdefault(name, reason)
    return info, excluded


def generate_include(config_root, src_roots, out_path, reserved=RESERVED):
    """Write `name equ $addr` lines for every table symbol not shadowed by a
    source label.  Returns (count, excluded, reserved_names, conflicts)."""
    merged, name_addr, conflicts = load_tables(config_root)
    _info, excluded = plan_labels(src_roots, name_addr)
    reserved_names = [n for n in sorted(name_addr) if n in reserved]
    lines = ['; generated by tools/symbolize.py - do not edit',
             '; one equate per named address; operand sizes stay explicit']
    count = 0
    for addr, (name, source) in sorted(merged.items()):
        if name in excluded or name in reserved:
            continue
        lines.append(f'{name} equ ${addr:x}')
        count += 1
    lines += fix_equates(config_root, reserved)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(lines) + '\n')
    return count, excluded, reserved_names, conflicts


# --------------------------------------------------------------------------
# operand rewriting

def resolve(target, org, span_end, merged, excluded, sites, entry_symbol,
            kept_labels):
    """Symbol for a code target: (symbol, kind) or (None, reason).

    kind is 'entry', 'global' or 'local'.  A target inside the same source
    must land on an instruction boundary to get a local label; a target that
    already has a surviving label in the file reuses that label.
    """
    if target == org and entry_symbol:
        return entry_symbol, 'entry'
    name = name_of(target, merged, excluded)
    if name:
        return name, 'global'
    if target in kept_labels:
        return kept_labels[target], 'local'
    if org <= target < span_end:
        if target not in sites:
            return None, 'mid-instruction'
        return f'l_{target:x}', 'local'
    return None, 'no-name'


def rewrite_source(text, listing, span_end, org, merged, excluded,
                   region_rows=(), install_proofs=(), vram=None):
    """Rewrite one source's operands.  Returns (new_text, stats, raw).

    region_rows (see load_region_rows) enables the base+offset form: an
    absolute/PC-relative operand that lands inside a classified data row but
    has no symbol of its own becomes `row_name+$off`.  A reference into a
    `fill` row is a contradiction and stays raw (`abs-fill-target`).
    install_proofs (see load_install_proofs) is a set of (site, target)
    pairs: the install immediate at a proven site is rewritten even when
    this source never branches to the target (`install-proven`).
    vram ({line_no: (value, name, evidence)}, see plan_vram) names the movea
    immediates proven to be fix-layer VRAM addresses.
    """
    lines = text.splitlines()
    stats = collections.Counter()
    raw = collections.Counter()
    jumps = jump_targets(text)
    label_lines = labels_of(text)

    # entry symbol: the top label if it survives, else the table name
    entry_symbol = None
    if label_lines:
        top_name = label_lines[0][1]
        entry_symbol = top_name

    sites = {addr for addr, _size, _text in listing.values()}

    # comment out the top label when the include already defines its name at
    # this file's org (vasm rejects a label that redefines an equate)
    top_line = label_lines[0][0] if label_lines else None
    strip = set()
    insns_by_line = sorted(listing.items())
    for line_no, name in label_lines:
        if line_no == top_line and name_of(org, merged, excluded) == name:
            strip.add(line_no)
            continue
        # an inner entry label whose address carries the same table name is
        # defined by the include; keep it visible as a comment marker
        addr = next((a for n, (a, _s, _t) in insns_by_line if n > line_no), None)
        if addr is not None and addr != org and name_of(addr, merged, excluded) == name:
            strip.add(line_no)

    # surviving labels by address (label addr = next instruction's addr)
    insns = sorted(listing.items())   # (line_no, (addr, size, text))
    kept_labels = {}
    for line_no, name in label_lines:
        if line_no in strip:
            continue
        addr = next((a for n, (a, _s, _t) in insns if n > line_no), None)
        if addr is not None:
            kept_labels.setdefault(addr, name)

    place = {}          # addr -> local label to insert
    for line_no, (site_addr, _size, _text) in insns:
        code = code_of(lines[line_no - 1])
        if code.strip().startswith(('dc.', 'dcb.', 'ds.')):
            continue
        replaced = False
        for rx, form in ((REL_BRANCH, 'rel'), (DB_BRANCH, 'db'),
                         (ABS_BRANCH, 'abs'), (PC_BRANCH, 'pc'),
                         (IDX_BRANCH, 'idx')):
            m = rx.match(code)
            if not m:
                continue
            target = int(m.group('tgt'), 16)
            symbol, kind = resolve(target, org, span_end, merged, excluded,
                                   sites, entry_symbol, kept_labels)
            if symbol is None:
                raw[(kind, target)] += 1
                replaced = True
                break
            if form == 'rel':
                new_code = (f"{m.group('ind')}{m.group('mn')}"
                            f"{m.group('sz') or ''} {symbol}")
            elif form == 'db':
                new_code = (f"{m.group('ind')}{m.group('mn')} "
                            f"{m.group('reg')},{symbol}")
            elif form == 'abs':
                new_code = (f"{m.group('ind')}{m.group('mn')} "
                            f"({symbol}){m.group('sz') or ''}")
            elif form == 'pc':
                new_code = f"{m.group('ind')}{m.group('mn')} ({symbol},PC)"
            else:  # idx
                new_code = f"{m.group('ind')}{m.group('mn')} {symbol}(PC)"
            lines[line_no - 1] = new_code
            stats[f'branch-{kind}'] += 1
            if kind == 'local' and target not in kept_labels:
                place[target] = symbol
            replaced = True
            break
        if replaced:
            continue

        m = MOVEA_IMM.match(code)
        if m:
            val = int(m.group('val'), 16)
            name = name_of(val, merged, excluded)
            if vram and line_no in vram:
                vram_operand(lines, line_no, m, val, vram, stats, raw)
            elif name:
                lines[line_no - 1] = (f"{m.group('ind')}movea.{m.group('sz')} "
                                      f'#{name},{m.group("areg")}')
                stats['movea-imm'] += 1
            else:
                raw[('movea-no-name', val)] += 1
            continue

        m = INSTALL_IMM.match(code)
        if m:
            val = int(m.group('val'), 16)
            name = name_of(val, merged, excluded)
            if name and val in jumps:
                lines[line_no - 1] = (f"{m.group('ind')}move.l "
                                      f'#{name},{m.group("dst")}')
                stats['install-imm'] += 1
            elif name and (site_addr, val) in install_proofs:
                lines[line_no - 1] = (f"{m.group('ind')}move.l "
                                      f'#{name},{m.group("dst")}')
                stats['install-proven'] += 1
            elif name:
                raw[('install-unproven', val)] += 1
            continue

        # generic absolute memory operands, several per line possible
        for rx in OP_RE:
            def sub(m, rx=rx):
                addr = int(m.group('tgt'), 16)
                name = name_of(addr, merged, excluded)
                offset = 0
                if not name and region_rows:
                    hit = region_field(addr, region_rows)
                    if hit:
                        start, offset, kind, _row_name = hit
                        if kind.startswith('fill'):
                            # a reference into proven-unreferenced padding is a
                            # contradiction: report it, never name it
                            raw[('abs-fill-target', addr)] += 1
                            return m.group(0)
                        name = name_of(start, merged, excluded)
                if not name:
                    raw[('abs-no-name', addr)] += 1
                    return m.group(0)
                if offset:
                    name = f'{name}+${offset:x}'
                    stats['abs-region-offset'] += 1
                else:
                    stats['abs-global'] += 1
                if rx is ABS_OP:
                    return f'({name}){m.group("sz")}'
                if rx is PC_OP:
                    return f'({name},PC)'
                return f'{name}(PC)'
            lines[line_no - 1] = rx.sub(sub, lines[line_no - 1])

    # comment out labels the include defines (the equate takes over)
    for line_no in strip:
        lines[line_no - 1] = f'; {lines[line_no - 1].strip()}'
        stats['label-stripped'] += 1
    stats['label-kept'] += len(label_lines) - len(strip)

    # local labels go in before the include so line numbers stay valid;
    # insert in descending line order so indices stay stable
    addr_line = {addr: line_no for line_no, (addr, _s, _t) in insns}
    for addr, symbol in sorted(place.items(), reverse=True):
        lines.insert(addr_line[addr] - 1, f'{symbol}:')

    org_line = next(i for i, l in enumerate(lines) if ORG_RE.match(l))
    # re-applying the rewrite to an already symbolized source must be a no-op:
    # keep the existing include instead of inserting a second one
    if not (org_line + 1 < len(lines)
            and 'include "symbols.i"' in lines[org_line + 1]):
        lines.insert(org_line + 1, ' include "symbols.i"')
        stats['include-added'] += 1

    return '\n'.join(lines) + '\n', stats, raw


# --------------------------------------------------------------------------
# fix-layer VRAM immediates
#
# `movea.w #$7063,a1` followed by `jsr (vram_tile_block_fill).l` loads a fix
# map address (LSPC VRAM 0x7000-0x74ff: 40 columns x 32 rows, column-major,
# address = 0x7000 + x*32 + y), not a ROM address - the two spaces overlap
# numerically, so such an immediate can never take a config/names symbol.
# The names live apart in config/vram/fix.tsv (`<hex addr> <name>
# <evidence>`) and are used only for a movea immediate that VramFlow proves
# reaches REG_VRAMADDR: some execution path from the movea carries the
# value, unmodified (register copies allowed, through calls and jumps into
# other sources), into the word written to $3c0000.  A movea whose value
# merely lies in the range (a coincidence, or a value parked in RAM) stays
# raw.

FIX_BASE, FIX_END = 0x7000, 0x7500
FIX_POS_RE = re.compile(r'^FIX_X(\d\d)_Y(\d\d)$')
VRAM_ADDR_OPS = {'(REG_VRAMADDR).l', '($3c0000).l', '(REG_VRAMADDR).w'}
VRAM_ADDR_IMMS = {'#REG_VRAMADDR', '#$3c0000', '#$003c0000'}
FLOW_ENDS = {'rts', 'rte', 'rtr', 'trap', 'stop', 'illegal', 'reset'}
NO_WRITE = {'cmp', 'cmpi', 'cmpa', 'cmpm', 'tst', 'btst', 'chk', 'pea',
            'nop'}
REG_ORDER = [f'd{i}' for i in range(8)] + [f'a{i}' for i in range(8)]


def fix_position_name(addr):
    """FIX_Xxx_Yyy for a fix map address (decimal column/row)."""
    if not FIX_BASE <= addr < FIX_END:
        raise ValueError(f'${addr:x} is outside the fix map')
    off = addr - FIX_BASE
    return f'FIX_X{off >> 5:02d}_Y{off & 0x1f:02d}'


def load_fix_names(config_root=REPO):
    """{addr: name} from config/vram/fix.tsv (empty when the file is absent).

    Rejects an address outside the fix map, two names for one address, one
    name for two addresses, and a positional FIX_Xxx_Yyy name that does not
    decode to its own address.
    """
    path = config_root / 'config' / 'vram' / 'fix.tsv'
    out, seen = {}, {}
    if not path.exists():
        return out
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        p = line.split('\t')
        if len(p) < 3 or not p[2].strip():
            raise ValueError(f'vram/fix.tsv:{n}: expected <addr> <name> '
                             f'<evidence>')
        addr, name = int(p[0], 16), p[1].strip()
        if not FIX_BASE <= addr < FIX_END:
            raise ValueError(f'vram/fix.tsv:{n}: ${addr:x} outside the fix map')
        if FIX_POS_RE.match(name) and fix_position_name(addr) != name:
            raise ValueError(f'vram/fix.tsv:{n}: {name} is not ${addr:x}')
        if addr in out or name in seen:
            raise ValueError(f'vram/fix.tsv:{n}: duplicate {addr:x}/{name}')
        out[addr], seen[name] = name, addr
    return out


def _split_ops(text):
    """Top-level comma split of an operand field (parentheses respected)."""
    ops, depth, cur = [], 0, ''
    for ch in text:
        if ch == ',' and depth == 0:
            ops.append(cur.strip())
            cur = ''
            continue
        depth += (ch == '(') - (ch == ')')
        cur += ch
    if cur.strip():
        ops.append(cur.strip())
    return ops


def _reg(op):
    op = op.strip().lower()
    if op == 'sp':
        return 'a7'
    return op if re.fullmatch(r'[da][0-7]', op) else None


def _reglist(op):
    """Registers of a movem list (d3-d4, d0/d4, d0-d7/a0-a6) or None."""
    regs = []
    for part in op.lower().split('/'):
        m = re.fullmatch(r'([da][0-7])(?:-([da][0-7]))?', part.strip())
        if not m:
            return None
        lo = REG_ORDER.index(m.group(1))
        hi = REG_ORDER.index(m.group(2)) if m.group(2) else lo
        regs += REG_ORDER[lo:hi + 1]
    return regs


class VramFlow:
    """Prove that a register value reaches REG_VRAMADDR ($3c0000).

    Works on the corpus sources (vasm listings give line <-> address) and
    walks every path forward from an instruction, depth-first and bounded:
    conditional branches and dbcc take both edges, bra/jmp follow their
    target, bsr/jsr descend into the callee (the path ends there unless the
    callee proves the write), rts/trap/dc end a path, and a
    `movea.l ($T,PC,dN.w*$1),aK` + `jmp (aK)` dispatch follows each long in
    the table that is an instruction of the same source (read from rom).
    The tracked set holds the registers that carry the value; copies
    (move/movea .w/.l between registers, exg) extend it and any other write
    to a register drops it.  A second set holds address registers loaded
    with REG_VRAMADDR.  Success: `move.w rX,<VRAMADDR>` or
    `movem.w <list>,<VRAMADDR>` whose first (lowest) register carries the
    value.
    """

    MAX_STATES = 20000
    MAX_DEPTH = 8

    def __init__(self, src_roots, name_addr, rom=None):
        import bisect
        self._bisect = bisect
        self.name_addr = name_addr
        self.rom = rom or {}
        self.files = sorted((org_of(p.read_text()), p) for root in src_roots
                            for p in root.glob('*.s'))
        self.orgs = [o for o, _p in self.files]
        self._info = {}
        self._calls = {}

    def info(self, path):
        if path not in self._info:
            import tempfile
            text = path.read_text().splitlines()
            with tempfile.TemporaryDirectory(prefix='ntm-vram-') as td:
                listing, span_end = vasm_listing(path, pathlib.Path(td))
            insns = sorted(listing)
            addr_line = {a: n for n, (a, _s, _t) in listing.items()}
            labels = {}
            for line_no, name in labels_of('\n'.join(text)):
                nxt = next((n for n in insns if n > line_no), None)
                if nxt is not None:
                    labels.setdefault(name, listing[nxt][0])
            self._info[path] = dict(text=text, insns=insns, listing=listing,
                                    addr_line=addr_line, labels=labels,
                                    org=org_of('\n'.join(text)),
                                    end=span_end)
        return self._info[path]

    def locate(self, addr):
        """(path, line_no) of the instruction at addr, or None."""
        i = self._bisect.bisect_right(self.orgs, addr) - 1
        if i < 0:
            return None
        inf = self.info(self.files[i][1])
        if addr in inf['addr_line']:
            return self.files[i][1], inf['addr_line'][addr]
        return None

    def _next(self, path, line_no):
        """(path, line) of the next instruction; past the last one, flow
        falls into the source that starts where this one ends."""
        inf = self.info(path)
        i = self._bisect.bisect_right(inf['insns'], line_no)
        if i < len(inf['insns']):
            return path, inf['insns'][i]
        return self.locate(inf['end']) or (None, None)

    def _resolve(self, path, sym):
        sym = sym.strip()
        m = re.fullmatch(r'\$([0-9a-fA-F]+)', sym)
        if m:
            return int(m.group(1), 16)
        inf = self.info(path)
        if sym in inf['labels']:
            return inf['labels'][sym]
        m = re.fullmatch(r'l_([0-9a-fA-F]+)', sym)
        if m:
            return int(m.group(1), 16)
        return self.name_addr.get(sym)

    @staticmethod
    def _flow_operand(op):
        """The target token of a branch/jump operand, or None (computed)."""
        m = re.fullmatch(r'\(([^(),]+)\)(?:\.[lw])?|\(([^(),]+),PC\)|'
                         r'([^(),]+)\(PC\)|([^(),\s]+)', op.strip())
        if not m:
            return None
        tok = next(g for g in m.groups() if g is not None)
        return None if _reg(tok) else tok

    def _rom_long(self, addr):
        for base, blob in self.rom.items():
            if base <= addr and addr + 4 <= base + len(blob):
                return int.from_bytes(blob[addr - base:addr - base + 4], 'big')
        return None

    def _table_targets(self, path, line_no, areg):
        """Entries of a `movea.l ($T,PC,dN.w*$1),aK; jmp (aK)` dispatch."""
        inf = self.info(path)
        i = inf['insns'].index(line_no)
        if i == 0:
            return []
        prev = code_of(inf['text'][inf['insns'][i - 1] - 1]).strip()
        m = re.fullmatch(r'movea\.l\s+\(\$([0-9a-fA-F]+),PC,d[0-7]\.w'
                         r'(?:\*\$?1)?\),(a[0-7])', prev)
        if not m or m.group(2) != areg:
            return []
        out, table = [], int(m.group(1), 16)
        for k in range(32):
            tgt = self._rom_long(table + 4 * k)
            if tgt is None or tgt not in inf['addr_line']:
                break
            out.append((path, inf['addr_line'][tgt]))
        return out

    def _vram_write(self, base, sz, ops, regs, ptrs):
        if sz != 'w' or len(ops) != 2:
            return False
        dst = ops[1].replace(' ', '')
        m = re.fullmatch(r'\((a[0-7]|sp)\)\+?', dst.lower())
        to_vram = dst in VRAM_ADDR_OPS or bool(m and _reg(m.group(1)) in ptrs)
        if not to_vram:
            return False
        if base == 'move':
            return _reg(ops[0]) in regs
        if base == 'movem':
            lst = _reglist(ops[0])
            return bool(lst) and sorted(lst, key=REG_ORDER.index)[0] in regs
        return False

    @staticmethod
    def _transfer(base, sz, ops, regs, ptrs):
        regs, ptrs = set(regs), set(ptrs)

        def kill(r):
            regs.discard(r)
            ptrs.discard(r)
        for op in ops:
            for m in re.finditer(r'\((a[0-7]|sp)\)\+|-\((a[0-7]|sp)\)',
                                 op.lower()):
                kill(_reg(m.group(1) or m.group(2)))
        if base in NO_WRITE or base.startswith('db') or not ops:
            return frozenset(regs), frozenset(ptrs)
        if base in ('move', 'movea') and len(ops) == 2:
            src, dst = _reg(ops[0]), _reg(ops[1])
            if dst:
                carried = src in regs and sz in ('w', 'l')
                pointer = sz == 'l' and (src in ptrs or
                                         ops[0].replace(' ', '') in VRAM_ADDR_IMMS)
                kill(dst)
                if carried:
                    regs.add(dst)
                if pointer:
                    ptrs.add(dst)
            return frozenset(regs), frozenset(ptrs)
        if base == 'lea' and len(ops) == 2:
            dst = _reg(ops[1])
            if dst:
                kill(dst)
                if ops[0].replace(' ', '') in VRAM_ADDR_OPS:
                    ptrs.add(dst)
            return frozenset(regs), frozenset(ptrs)
        if base == 'exg' and len(ops) == 2:
            x, y = _reg(ops[0]), _reg(ops[1])
            for s in (regs, ptrs):
                hx, hy = x in s, y in s
                s.discard(x)
                s.discard(y)
                if hx:
                    s.add(y)
                if hy:
                    s.add(x)
            return frozenset(regs), frozenset(ptrs)
        if base == 'movem' and len(ops) == 2:
            for r in _reglist(ops[1]) or []:
                kill(r)
            return frozenset(regs), frozenset(ptrs)
        if base == 'link':
            kill(_reg(ops[0]))
            return frozenset(regs), frozenset(ptrs)
        dst = _reg(ops[-1])
        if dst:
            kill(dst)
        return frozenset(regs), frozenset(ptrs)

    @staticmethod
    def _stack_op(base, sz, ops, regs, ptrs, frame):
        """(regs, ptrs, frame) for a push/pop/adjust of a7, else None.

        frame models the words this routine pushed: ('r', bytes, carried)
        for move/pea, ('m', bytes, reglist, carried regs) for movem.  A pop
        that does not mirror its push (size or register list) forgets the
        frame; so does any other write to a7.
        """
        size = 4 if sz == 'l' else 2
        if base == 'pea':
            return regs, ptrs, frame + (('r', 4, False),)
        if len(ops) != 2:
            return None
        src, dst = ops[0].replace(' ', '').lower(), ops[1].replace(' ', '').lower()
        if dst in ('-(a7)', '-(sp)'):
            if base == 'movem':
                lst = tuple(sorted(_reglist(ops[0]) or [], key=REG_ORDER.index))
                slot = ('m', size * len(lst), lst,
                        frozenset(r for r in lst if r in regs))
            else:
                slot = ('r', size, base in ('move', 'movea') and sz != 'b'
                        and _reg(src) in regs)
            return regs, ptrs, frame + (slot,)
        if src in ('(a7)+', '(sp)+'):
            regs, ptrs = set(regs), set(ptrs)
            top = frame[-1] if frame else None
            if base == 'movem':
                lst = tuple(sorted(_reglist(ops[1]) or [], key=REG_ORDER.index))
                for r in lst:
                    regs.discard(r)
                    ptrs.discard(r)
                if top and top[0] == 'm' and top[1:3] == (size * len(lst), lst):
                    regs |= top[3]
                    frame = frame[:-1]
                else:
                    frame = ()
            else:
                d = _reg(dst)
                regs.discard(d)
                ptrs.discard(d)
                if top and top[0] == 'r' and top[1] == size:
                    if top[2] and d and sz != 'b':
                        regs.add(d)
                    frame = frame[:-1]
                else:
                    frame = ()
            return frozenset(regs), frozenset(ptrs), frame
        if _reg(dst) == 'a7' and base not in NO_WRITE:
            return regs, ptrs, ()
        return None

    def feeds(self, path, line_no, reg):
        """Evidence string when the value in reg after line_no reaches
        REG_VRAMADDR on some path, else None."""
        path, start = self._next(path, line_no)
        if start is None:
            return None
        return self._walk(path, start, frozenset([reg]), frozenset(), 0)

    def _call(self, tgt, regs, ptrs, depth):
        key = (tgt, regs, ptrs)
        if key not in self._calls:
            self._calls[key] = None     # recursion guard
            loc = self.locate(tgt)
            hit = None
            if loc and depth <= self.MAX_DEPTH:
                hit = self._walk(loc[0], loc[1], regs, ptrs, depth)
            self._calls[key] = hit
        return self._calls[key]

    def _walk(self, path, line_no, regs, ptrs, depth):
        """Depth-first over (line, regs, ptrs, frame) states.

        A call whose callee does not prove the write returns with every
        register forgotten but the frame intact (a returning subroutine
        leaves a7 where it found it), so a value saved across a call and
        restored by the mirroring pop is still tracked.
        """
        stack, seen = [(path, line_no, regs, ptrs, ())], set()
        while stack:
            path, line_no, regs, ptrs, frame = stack.pop()
            alive = regs or any(s[2] if s[0] == 'r' else s[3] for s in frame)
            if line_no is None or not alive or len(frame) > 16:
                continue
            key = (path, line_no, regs, ptrs, frame)
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > self.MAX_STATES:
                return None
            inf = self.info(path)
            code = code_of(inf['text'][line_no - 1]).strip()
            parts = code.split(None, 1)
            if not parts:
                continue
            mn = parts[0].lower()
            ops = _split_ops(parts[1]) if len(parts) > 1 else []
            base, _dot, sz = mn.partition('.')
            if base in ('dc', 'dcb', 'ds'):
                continue
            if self._vram_write(base, sz, ops, regs, ptrs):
                return f'{path.name}:{line_no}'
            if base in FLOW_ENDS:
                continue
            npath, nxt = self._next(path, line_no)
            if base in ('bra', 'jmp', 'bsr', 'jsr') or re.fullmatch(BCC, base) \
                    or re.fullmatch(DBCC, base):
                op = ops[-1] if ops else ''
                tok = self._flow_operand(op)
                tgt = self._resolve(path, tok) if tok else None
                if base in ('bsr', 'jsr'):
                    if tgt is not None and regs:
                        hit = self._call(tgt, regs, ptrs, depth + 1)
                        if hit:
                            return hit
                    stack.append((npath, nxt, frozenset(), frozenset(), frame))
                    continue
                n_regs, n_ptrs = self._transfer(base, sz, ops[:-1] or [], regs,
                                                ptrs)
                if base.startswith('db') and ops:
                    d = _reg(ops[0])
                    n_regs = frozenset(regs - {d})
                    n_ptrs = frozenset(ptrs - {d})
                targets = []
                if tgt is not None:
                    loc = self.locate(tgt)
                    if loc:
                        targets.append(loc)
                elif base == 'jmp':
                    m = re.fullmatch(r'\((a[0-7])\)', op.strip())
                    if m:
                        targets += self._table_targets(path, line_no,
                                                       m.group(1))
                for loc in targets:
                    stack.append((loc[0], loc[1], n_regs, n_ptrs, frame))
                if base not in ('bra', 'jmp'):
                    stack.append((npath, nxt, n_regs, n_ptrs, frame))
                continue
            step = self._stack_op(base, sz, ops, regs, ptrs, frame)
            if step is None:
                step = self._transfer(base, sz, ops, regs, ptrs) + (frame,)
            stack.append((npath, nxt) + step)
        return None


def vram_operand(lines, line_no, m, val, vram, stats, raw):
    """Rewrite one proven movea (MOVEA_IMM match m) to its fix.tsv name."""
    pval, name, _evidence = vram[line_no]
    if pval != val or not name:
        raw[('vram-no-name', val)] += 1
        return
    lines[line_no - 1] = (f"{m.group('ind')}movea.{m.group('sz')} "
                          f"#{name},{m.group('areg')}")
    stats['movea-vram'] += 1


def fix_equates(config_root=REPO, reserved=RESERVED):
    """`name equ $addr` lines for config/vram/fix.tsv (none when absent)."""
    rows = load_fix_names(config_root)
    if not rows:
        return []
    return (['; fix-layer VRAM addresses (config/vram/fix.tsv): used only for'
             ' movea immediates proven to feed REG_VRAMADDR'] +
            [f'{name} equ ${addr:x}' for addr, name in sorted(rows.items())
             if name not in reserved])


FIX_MOVEA = re.compile(r'^[ \t]*movea\.[wl][ \t]+#FIX_X(\d\d)_Y(\d\d),'
                       r'(a[0-7])[ \t]*$')


def vram_candidates(text):
    """[(line_no, value, areg)] for movea #imm,aN lines whose value lies in
    the fix map (whatever its proof status).  Already symbolized
    `movea.w #FIX_Xxx_Yyy,aN` lines count too, so the proof can be re-run
    on a written tree (rewrite_source leaves those lines alone)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = MOVEA_IMM.match(code_of(line))
        if m:
            val = int(m.group('val'), 16)
            if FIX_BASE <= val < FIX_END:
                out.append((n, val, m.group('areg')))
            continue
        m = FIX_MOVEA.match(code_of(line))
        if m:
            out.append((n, FIX_BASE + int(m.group(1)) * 32 + int(m.group(2)),
                        m.group(3)))
    return out


def plan_vram(config_root, src_roots, name_addr, rom=None):
    """{source path: {line_no: (value, name or None, evidence)}} for every
    movea immediate proven to feed REG_VRAMADDR (name from
    config/vram/fix.tsv; None when the table has no row for it)."""
    fix = load_fix_names(config_root)
    flow = VramFlow(src_roots, name_addr, rom)
    plan = {}
    for _org, path in flow.files:
        for line_no, val, areg in vram_candidates(path.read_text()):
            hit = flow.feeds(path, line_no, areg)
            if hit:
                plan.setdefault(path, {})[line_no] = (val, fix.get(val), hit)
    return plan


# --------------------------------------------------------------------------
# gate helpers

def pristine_blobs(work=WORK_DIR):
    fixed = (work / 'p1.fixed.cpu.bin').read_bytes()
    bank = (work / 'p1.bank.cpu.bin').read_bytes()
    return {0x000000: fixed, 0x200000: bank}


def span_window(org, size, pristine):
    for base, blob in pristine.items():
        if size > 0 and base <= org and org + size <= base + len(blob):
            return base
    raise ValueError(f'${org:x} +{size}: outside a P-ROM window')


def assemble_check(src, include_dir, pristine, work_dir):
    """Assemble one rewritten source and byte-gate it against the pristine ROM.

    Prints only failures; returns (ok, span_len).
    """
    out = work_dir / (src.name + '.bin')
    result = subprocess.run(
        [str(VASM), '-Fbin', '-no-opt', '-quiet', f'-I{include_dir}',
         '-o', str(out), str(src)],
        capture_output=True, text=True)
    if result.returncode != 0:
        print(f'  {src.name} ASSEMBLY FAILED')
        print(result.stderr)
        return False, 0
    span = out.read_bytes()
    org = org_of(src.read_text())
    base = span_window(org, len(span), pristine)
    offset = org - base
    ok = span == pristine[base][offset:offset + len(span)]
    if not ok:
        bad = next(i for i in range(min(len(span), len(pristine[base]) - offset))
                   if span[i] != pristine[base][offset + i])
        print(f'  {src.name} ${org:x} +{len(span):5d} DIFF at +${bad:x} '
              f'got {span[bad]:02x} want {pristine[base][offset + bad]:02x}')
    return ok, len(span)


# --------------------------------------------------------------------------
# CLI

def copy_tree(src_roots, scratch_root):
    out = []
    for root in src_roots:
        rel = root.relative_to(REPO) if root.is_relative_to(REPO) \
            else pathlib.Path(root.name)
        dst = scratch_root / rel
        dst.mkdir(parents=True, exist_ok=True)
        for src in root.glob('*.s'):
            shutil.copy2(src, dst / src.name)
        out.append(dst)
    return out


def run_gate(config_root, src_roots, min_org, max_org, include_dir,
             work_dir, report_path):
    """Rewrite the given tree in place and byte-gate every file.

    Returns (matched, failed, changed, files, stats, raw)."""
    merged, _name_addr, _conflicts = load_tables(config_root)
    region_rows = load_region_rows(config_root)
    install_proofs = load_install_proofs(config_root)
    count, excluded, reserved_names, conflicts = generate_include(
        config_root, src_roots, include_dir / INCLUDE_NAME)
    print(f'symbols.i: {count} equates; excluded {len(excluded)} label '
          f'shadows, {len(reserved_names)} reserved, '
          f'{len(conflicts)} name conflicts')
    for name, reason in sorted(excluded.items()):
        print(f'  excluded {name}: {reason}')
    for c in conflicts:
        print(f'  conflict: {c}')
    for name in reserved_names:
        print(f'  reserved name skipped: {name}')
    pristine = pristine_blobs()
    # proven fix-layer movea sites, planned on the tree before any rewrite
    vram_plan = plan_vram(config_root, src_roots, _name_addr, pristine)
    stats = collections.Counter()
    raw = collections.Counter()
    files = changed = matched = failed = 0
    for root in src_roots:
        for src in sorted(root.glob('*.s')):
            org = org_of(src.read_text())
            if not (min_org <= org < max_org):
                continue
            files += 1
            listing, span_end = vasm_listing(src, work_dir)
            new_text, s, r = rewrite_source(src.read_text(), listing,
                                            span_end, org, merged, excluded,
                                            region_rows, install_proofs,
                                            vram_plan.get(src))
            if new_text != src.read_text():
                changed += 1
                src.write_text(new_text)
            stats += s
            for k, v in r.items():
                raw[k] += v
            ok, _size = assemble_check(src, include_dir, pristine, work_dir)
            matched += ok
            failed += not ok
    print(f'  {changed}/{files} files rewritten; '
          f'byte gate: {matched} MATCH, {failed} DIFFER')
    for kind in sorted(stats):
        print(f'  rewritten: {kind}={stats[kind]}')
    by_reason = collections.defaultdict(int)
    samples = collections.defaultdict(list)
    for (reason, addr), n in raw.items():
        by_reason[reason] += n
        samples[reason].append((addr, n))
    for reason in sorted(by_reason):
        top = sorted(samples[reason], key=lambda p: -p[1])[:5]
        shown = ', '.join(f'${a:x}({n})' for a, n in top)
        print(f'  left raw ({reason}): {by_reason[reason]} [top: {shown}]')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        '\n'.join(f'{kind}\t{stats[kind]}' for kind in sorted(stats)) + '\n')
    return matched, failed, changed, files, stats, raw


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dry-run', action='store_true',
                    help='rewrite a scratch copy of the tree, assemble and '
                         'byte-gate it, print the counts')
    ap.add_argument('--write', action='store_true',
                    help='gate a scratch copy first, then apply the rewrite '
                         'to the real tree')
    ap.add_argument('--src', action='append', default=[],
                    help='extra source dir (defaults: functions and blocks)')
    ap.add_argument('--config-root', default=str(REPO),
                    help='tree holding config/ (default: repo)')
    ap.add_argument('--scratch', default=str(REPO / 'build' / 'tmp' / 'symbolize'),
                    help='scratch tree for --dry-run/--write')
    ap.add_argument('--min-org', type=lambda s: int(s, 16), default=0,
                    help='only rewrite files with org >= this (hex)')
    ap.add_argument('--max-org', type=lambda s: int(s, 16), default=0x300000,
                    help='only rewrite files with org < this (hex)')
    ap.add_argument('--include-dir', default=str(REPO / 'build' / 'gen' / 'p1'),
                    help='where symbols.i lives (also the vasm -I path)')
    args = ap.parse_args()

    config_root = pathlib.Path(args.config_root)
    src_roots = ([pathlib.Path(p) for p in args.src]
                 or [FUNC_DIR, BLOCK_DIR])
    scratch = pathlib.Path(args.scratch)
    work_dir = scratch / 'work'
    work_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        if scratch.exists():
            shutil.rmtree(scratch)
        work_dir.mkdir(parents=True, exist_ok=True)
        scratch_roots = copy_tree(src_roots, scratch)
        include_dir = scratch / 'build' / 'gen' / 'p1'
        matched, failed, changed, files, stats, raw = run_gate(
            config_root, scratch_roots, args.min_org, args.max_org,
            include_dir, work_dir, scratch / 'symbolize-report.txt')
        print(f'result: {changed}/{files} files rewritten, '
              f'{matched} MATCH, {failed} DIFFER')
        return 0 if failed == 0 else 1

    if args.write:
        if not (WORK_DIR / 'p1.fixed.cpu.bin').exists():
            raise SystemExit('pristine halves missing - run make extract')
        if scratch.exists():
            shutil.rmtree(scratch)
        work_dir.mkdir(parents=True, exist_ok=True)
        scratch_roots = copy_tree(src_roots, scratch)
        scratch_include = scratch / 'build' / 'gen' / 'p1'
        matched, failed, changed, files, _stats, _raw = run_gate(
            config_root, scratch_roots, args.min_org, args.max_org,
            scratch_include, work_dir, scratch / 'symbolize-report.txt')
        if failed:
            print('gate failed - the real tree was not touched')
            return 1
        # gate passed: publish the include and the rewritten sources
        include_dir = pathlib.Path(args.include_dir)
        merged, name_addr, conflicts = load_tables(config_root)
        count, excluded, reserved_names, _c2 = generate_include(
            config_root, src_roots, include_dir / INCLUDE_NAME)
        print(f'publishing symbols.i ({count} equates) to {include_dir}')
        for root, sroot in zip(src_roots, scratch_roots):
            for src in sorted(sroot.glob('*.s')):
                org = org_of(src.read_text())
                if not (args.min_org <= org < args.max_org):
                    continue
                real = root / src.name
                new_text = src.read_text()
                if new_text != real.read_text():
                    real.write_text(new_text)
        print(f'published {changed}/{files} rewritten sources; '
              f'byte gate {matched} MATCH')
        return 0

    ap.error('pick --dry-run or --write')


if __name__ == '__main__':
    main()
