#!/usr/bin/env python3
"""Control-flow-driven Z80 recovery for the Neo Turf Masters M1 sound driver.

Unlike tools/unidasm_import.py (one hand-picked address at a time) this walks
the driver's control flow: it seeds a worklist with the Z80 vectors (reset,
RST 0/8/..38, NMI), with every instruction/transfer already decoded in
src/m1/asm/functions/*.s, and with the direct call/jp/jr targets those
functions reach.  Each seed is decoded one instruction at a time with MAME's
unidasm until the flow ends:

  * `ret`/`retn`/`reti`/`halt` and unconditional `jp`/`jr` stop a block,
  * `jp cc`/`jr cc`/`djnz`/`ret cc` end the block but the fallthrough and the
    target both continue (the fallthrough stays inside the block so the
    emitted source round-trips),
  * `call`/`rst` keep decoding and enqueue the callee as a new seed,
  * indirect transfers (`jp (hl)`, `jp (ix)`/`(iy)`) end the block and are
    recorded as unresolved.  A `ld hl,<imm>`/`add hl,de`/`ld a,(hl)`/
    `inc hl`/`ld h,(hl)`/`ld l,a`/`jp (hl)` block is recognised as an indexed
    word dispatch: the ROM words at that constant are read and enqueued as
    *table-derived* seeds (recorded separately from direct flow).

Nothing is emitted that has not been byte-compared against
build/work/p1.m1.pristine.bin: every block is assembled with vasm z80/std and
only a byte-exact block is written to src/m1/asm/blocks/.  Blocks that
overlap an existing src/m1/asm/functions span are never produced (the walk
stops at the span edge and the existing file's own transfers supply the
continuation), so the recovery complements - never duplicates - the
inventory.

Only the fixed Z80 window 0x0000-0x7fff is decoded.  Addresses >= 0x8000 are
NEO-ZMC bank windows: the CPU address must not be read as a physical M1
offset, so such targets are recorded as banked/unresolved instead of decoded
(see docs/memory-map.md).

Outputs: src/m1/asm/blocks/*.s (verified blocks), build/analysis/m1-recovery.json
(full report: roots, blocks, coverage, indirect transfers, conflicts, data
candidates, unreached residue).  No raw ROM bytes are written.

Usage: python3 tools/m1_recover.py [--no-write] [--limit-blocks N] [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
from dataclasses import dataclass, field

REPO = pathlib.Path(__file__).resolve().parent.parent
VASM = REPO / 'tools' / 'vasm' / 'vasmz80_std'
PRISTINE = REPO / 'build' / 'work' / 'p1.m1.pristine.bin'
FUNC_DIR = REPO / 'src' / 'm1' / 'asm' / 'functions'
BLOCK_DIR = REPO / 'src' / 'm1' / 'asm' / 'blocks'
ANALYSIS_DIR = REPO / 'build' / 'analysis'

FIXED_TOP = 0x8000          # CPU addresses >= this are NEO-ZMC bank windows
IMAGE_SIZE = 0x20000
VECTORS = (0x0000, 0x0008, 0x0010, 0x0018, 0x0020, 0x0028, 0x0030, 0x0038,
           0x0066)          # reset, RST 0/1/2/3/4/5/6/7, NMI (IM 1 -> 0x38)
CONDITIONS = ('z', 'nz', 'c', 'nc', 'po', 'pe', 'p', 'm')
WINDOW = 0x800              # unidasm prefetch window per invocation
TABLE_MAX = 96              # hard cap on indexed-dispatch entries scanned
TABLE_UNBOUNDED = 32        # entries tried when no `cp n` guard is found
EXISTING_RECORD_LIMIT = 400  # bounded per-section record lists in the report

LISTING_LINE = re.compile(r'^([0-9a-f]+):(.*)$')
BYTE_FIELD = re.compile(r'^\s*(?:[0-9a-f]{2} )*[0-9a-f]{2}\s*$')
RELATIVE_OPS = ('jr', 'djnz')


# ----------------------------------------------------------------- decoding

@dataclass
class Insn:
    addr: int
    raw: bytes
    text: str               # vasm z80/std ready, lower case, single line
    kind: str = 'normal'    # normal/terminal/conditional/call/indirect/undecodable
    target: int | None = None

    @property
    def size(self):
        return len(self.raw)


def convert_dialect(text, base):
    """unidasm operand text -> vasm z80/std text.

    unidasm displays `jr`/`djnz` targets relative to the first decoded
    address (the -skip base, printed as offset 0), so those have to be made
    absolute or vasm would assemble a different displacement.
    """
    t = re.sub(r'\s+', ' ', text.strip().lower())
    parts = t.split(None, 1)
    if not parts:
        return ''
    op, rest = parts[0], parts[1] if len(parts) > 1 else ''
    if op in RELATIVE_OPS and rest:
        operands = [o.strip() for o in rest.split(',')]
        last = operands[-1]
        m = re.match(r'^\$([0-9a-f]+)$', last)
        if m:
            absolute = (base + int(m.group(1), 16)) & 0xffff
            operands[-1] = f'0x{absolute:x}'
            rest = ','.join(operands)
    rest = re.sub(r'\$([0-9a-f]+)', r'0x\1', rest)
    return f'{op} {rest}'.strip()


def classify(text):
    """(kind, target) for vasm-dialect text."""
    parts = text.split(None, 1)
    op = parts[0] if parts else ''
    operands = [o.strip() for o in parts[1].split(',')] if len(parts) > 1 else []

    def number(tok):
        try:
            return int(tok, 16 if tok.startswith('0x') else 10)
        except ValueError:
            return None

    if op == 'db':
        return 'undecodable', None
    if op in ('ret', 'retn', 'reti'):
        return ('conditional' if operands and operands[0] in CONDITIONS
                else 'terminal'), None
    if op == 'halt':
        return 'terminal', None
    if op in ('jp', 'jr'):
        if op == 'jp' and operands and operands[0].startswith('('):
            return 'indirect', None
        if operands and operands[0] in CONDITIONS:
            return 'conditional', number(operands[-1])
        return 'terminal', number(operands[-1]) if operands else None
    if op == 'djnz':
        return 'conditional', number(operands[-1]) if operands else None
    if op == 'call':
        return 'call', number(operands[-1]) if operands else None
    if op == 'rst':
        n = number(operands[0]) if operands else None
        return 'call', n           # RST n vectors at address n itself
    return 'normal', None


def parse_listing(text, base):
    """unidasm listing text -> [Insn] with base-relative lines resolved.

    unidasm pads the byte column to a fixed width, so the mnemonic always
    starts after the first run of two or more spaces; splitting there keeps a
    `db` fallback mnemonic (all hex digits) out of the byte column.
    """
    insns = []
    for raw_line in text.splitlines():
        m = LISTING_LINE.match(raw_line.strip())
        if not m:
            continue
        rest = m.group(2)
        split = re.search(r'  +', rest)
        if split is None:
            continue
        field, body = rest[:split.start()], rest[split.end():].strip()
        if not body or not BYTE_FIELD.match(field):
            continue
        addr = base + int(m.group(1), 16)
        data = bytes(int(b, 16) for b in field.split())
        source = convert_dialect(body, base)
        kind, target = classify(source)
        insns.append(Insn(addr, data, source, kind, target))
    return insns


def make_fetch(image_path, image, window=WINDOW):
    """unidasm-backed fetch(addr) -> [Insn]; one window sweep per miss."""
    def fetch(addr):
        count = min(window, IMAGE_SIZE - addr)
        if count <= 0:
            return []
        proc = subprocess.run(
            ['unidasm', str(image_path), '-arch', 'z80', '-basepc', '0x0',
             '-skip', str(addr), '-count', str(count)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f'unidasm failed at 0x{addr:x}: {proc.stderr}')
        # Drop entries that may straddle the window edge (a truncated
        # multi-byte instruction would be re-fetched as a `db` line).
        keep = [i for i in parse_listing(proc.stdout, addr)
                if i.addr + i.size <= addr + count]
        for insn in keep:
            if image[insn.addr:insn.addr + insn.size] != insn.raw:
                raise RuntimeError(f'unidasm/ROM mismatch at 0x{insn.addr:x}')
        return keep
    return fetch


class InsnIndex:
    """Per-address instruction cache; a sweep aligned on the address wins."""

    def __init__(self, fetch):
        self.fetch = fetch
        self.map = {}
        self.sweeps = 0

    def get(self, addr):
        if addr in self.map:
            return self.map[addr]
        self.sweeps += 1
        for insn in self.fetch(addr):
            self.map.setdefault(insn.addr, insn)
        return self.map.get(addr)


class StaticIndex:
    """Test double: instruction map handed over directly."""

    def __init__(self, insns):
        self.map = {i.addr: i for i in insns}
        self.sweeps = 0

    def get(self, addr):
        return self.map.get(addr)


# ----------------------------------------------------------------- recovery

@dataclass
class Block:
    start: int
    end: int
    insns: list
    origin: str          # direct / table / pointer / existing
    seed: int            # address that first made this block reachable
    via: str             # vector/call/jump/fallthrough/table/pointer/existing
    role: str            # reset/RST/NMI vector, callee, branch target, ...
    terminator: str
    exit_targets: list = field(default_factory=list)
    validated: bool = False
    validation: str = ''
    emitted: bool = False
    evidence: dict = field(default_factory=dict)


@dataclass
class Seed:
    addr: int
    via: str
    role: str
    origin: str = 'direct'
    evidence: dict = field(default_factory=dict)


class Recovery:
    """Reachability walk over the fixed Z80 window."""

    def __init__(self, index, top=FIXED_TOP, max_blocks=40000,
                 max_insns=400000, guard_bounded_tables=True):
        self.index = index
        self.top = top
        self.max_blocks = max_blocks
        self.max_insns = max_insns
        self.guard_bounded_tables = guard_bounded_tables
        self.blocks = {}                 # start -> Block
        self.code = bytearray(top)       # 1 = decoded instruction byte
        self.existing = bytearray(top)   # 1 = covered by an existing .s file
        self.existing_spans = []         # (start, end, source)
        self.insn_len = {}               # instruction start -> length
        self.insn_count = 0
        self.seeds = {}                  # addr -> Seed (first wins)
        self.conflicts = []              # roots landing mid-instruction
        self.undecodable = []            # reached addresses decoding to `db`
        self.boundary_hits = []          # flow stopped on an existing span edge
        self.banked = []                 # (from, target) >= top, not decoded
        self.row = {}                    # root kinds seen, for the report
        self.truncated = False

    # -- bookkeeping ------------------------------------------------------

    def mark_existing(self, start, end, source):
        self.existing_spans.append((start, end, str(source)))
        self.existing[start:end] = b'\x01' * (end - start)

    def note_banked(self, frm, target):
        if len(self.banked) < EXISTING_RECORD_LIMIT:
            self.banked.append({'from': frm, 'target': target})

    def note_undecodable(self, addr, seed):
        if len(self.undecodable) < EXISTING_RECORD_LIMIT:
            self.undecodable.append({'addr': addr, 'seed': seed})

    def note_conflict(self, addr, seed, reason):
        if len(self.conflicts) >= EXISTING_RECORD_LIMIT:
            return
        record = {'addr': addr, 'reason': reason,
                  'seed': getattr(seed, 'addr', seed),
                  'via': getattr(seed, 'via', '?'),
                  'from': getattr(seed, 'evidence', {}).get('from')}
        competing = self.insn_len.get(addr)
        if competing:                       # decoded from a sweep aligned here
            record['competing_decode'] = self.index.get(addr).text
        self.conflicts.append(record)

    # -- walk -------------------------------------------------------------

    def run(self, seeds):
        queue = list(seeds)
        while queue:
            seed = queue.pop(0)
            if seed.addr in self.seeds:
                continue
            self.seeds[seed.addr] = seed
            if seed.addr >= self.top:
                self.note_banked(seed.evidence.get('from'), seed.addr)
                continue
            if seed.addr in self.blocks:
                self.row['already_decoded'] = self.row.get('already_decoded', 0) + 1
                continue
            if self.existing[seed.addr]:
                self.row['inside_existing'] = self.row.get('inside_existing', 0) + 1
                continue
            if seed.addr in self.insn_len:
                self.row['joins_decoded'] = self.row.get('joins_decoded', 0) + 1
                continue
            if self.code[seed.addr]:
                self.note_conflict(seed.addr, seed, 'lands_inside_instruction')
                continue
            if len(self.blocks) >= self.max_blocks or self.insn_count >= self.max_insns:
                self.truncated = True
                break
            queue.extend(self.walk(seed))
        return self

    def walk(self, seed):
        """Decode one basic block; returns follow-up seeds."""
        insns = []
        follows = []
        addr = seed.addr
        target_exits = []
        while True:
            if addr >= self.top:
                terminator = 'crosses_fixed_top'
                break
            if addr != seed.addr and self.existing[addr]:
                terminator = 'existing_span_boundary'
                self.boundary_hits.append({'seed': seed.addr, 'stop': addr})
                break
            if addr != seed.addr and addr in self.blocks:
                terminator = 'joins_decoded_block'
                break
            if addr != seed.addr and addr in self.insn_len:
                terminator = 'joins_decoded_block'
                break
            if addr != seed.addr and self.code[addr]:
                terminator = 'joins_instruction_interior'
                self.note_conflict(addr, seed, 'fallthrough_inside_instruction')
                break
            insn = self.index.get(addr)
            if insn is None or insn.kind == 'undecodable':
                terminator = 'undecodable'
                self.note_undecodable(addr, seed.addr)
                break
            insns.append(insn)
            self.insn_len[addr] = insn.size
            self.code[addr:addr + insn.size] = b'\x01' * insn.size
            self.insn_count += 1
            if insn.kind == 'terminal':
                terminator = insn.text.split()[0]
                if insn.target is not None:
                    target_exits.append(insn.target)
                break
            if insn.kind == 'indirect':
                terminator = 'indirect'
                follows.extend(self.resolve_indirect(insn, insns))
                break
            if insn.kind == 'conditional':
                terminator = insn.text.split()[0]
                if insn.target is not None:
                    target_exits.append(insn.target)
                addr += insn.size
                continue
            if insn.kind == 'call':
                terminator = 'call'
                if insn.target is not None:
                    follows.append(Seed(insn.target, 'call', 'callee',
                                        evidence={'from': addr}))
                addr += insn.size
                continue
            addr += insn.size

        if not insns:
            self.note_undecodable(seed.addr, seed.addr)
            return follows

        block = Block(start=seed.addr, end=insns[-1].addr + insns[-1].size,
                      insns=insns, origin=seed.origin, seed=seed.addr,
                      via=seed.via, role=seed.role, terminator=terminator,
                      exit_targets=[t for t in target_exits if t is not None],
                      evidence=dict(seed.evidence))
        self.blocks[seed.addr] = block
        for target in block.exit_targets:
            follows.append(Seed(target, 'flow', 'branch_target',
                                evidence={'from': block.end - 1}))
        return follows

    # -- indirect transfers ----------------------------------------------

    def resolve_indirect(self, site_insn, insns):
        """Record the site; return evidence-derived seeds when the idiom fits.

        Recognised shapes, all requiring the whole shape in the same block so
        a target is only proposed when the ROM itself supplies it:

          * indexed word table (`ld hl/de,<base>` + `add hl,de` + the
            `ld a,(hl)` / `inc hl` / `ld h,(hl)` lookup + `jp (hl)`),
          * byte jump table (`ld bc,<base>` + `add hl,bc` + `jp (hl)`; each
            entry must itself decode as an unconditional `jp <addr>`),
          * a ROM-resident pointer (`ld hl,(<rom addr>)` + `jp (hl)`),
          * a pushed continuation (`ld hl,<addr>` + `push hl` before the
            dispatch - the dispatched handler's `ret` lands there).

        Resolved tables are recorded (self.tables) with their entry size and
        extent so the lane can emit data rows; banked targets are noted, not
        decoded.
        """
        site = site_insn.addr
        ops = [i.text for i in insns]
        entry = {'site': site, 'form': site_insn.text,
                 'block': insns[0].addr, 'kind': 'unresolved',
                 'targets': [], 'base': None}
        follows = []
        self.tables = getattr(self, 'tables', [])

        # last base load of each shape wins (dispatch idioms build one base)
        base = None          # (idx, register, value) or (idx, 'ptr', value)
        for i, text in enumerate(ops):
            for reg in ('hl', 'de', 'bc'):
                m = re.match(rf'^ld {reg},0x([0-9a-f]+)$', text)
                if m:
                    base = (i, reg, int(m.group(1), 16))
            m = re.match(r'^ld hl,\(0x([0-9a-f]+)\)$', text)
            if m:
                base = (i, 'ptr', int(m.group(1), 16))

        # pushed continuation: the dispatched handler's ret lands on it
        for i, text in enumerate(ops):
            m = re.match(r'^ld hl,0x([0-9a-f]+)$', text)
            if m and 'push hl' in ops[i + 1:] and ops[-1] == 'jp (hl)':
                cont = int(m.group(1), 16)
                if cont < self.top:
                    follows.append(Seed(cont, 'push_return',
                                        'pushed_continuation', origin='direct',
                                        evidence={'site': site, 'pushed': cont}))

        if base is not None and ops[-1] == 'jp (hl)':
            idx, reg, value = base
            after = ops[idx + 1:]
            # lookup: two 8-bit loads from (hl) with an inc hl between them
            # (`ld a,(hl)`/`ld h,(hl)` or `ld e,(hl)`/`ld d,(hl)`)
            lookup_lds = [t for t in after
                          if re.match(r'^ld [abcdehl],\(hl\)$', t)]
            has_lookup = 'inc hl' in after and len(lookup_lds) >= 2
            if reg in ('hl', 'de') and 'add hl,de' in after and has_lookup:
                # indexed word table
                if self.guard_bounded_tables:
                    bound, bound_site = self.index_bound(insns, idx)
                else:
                    bound, bound_site = None, None
                limit = bound if bound else TABLE_UNBOUNDED
                targets, used, tail_word = self.scan_word_table(value, limit)
                entry.update(kind='indexed_word_table', base=value,
                             entry_size=2, bound=bound,
                             bound_check_site=bound_site,
                             entries_scanned=used, targets=targets,
                             tail_word=tail_word)
                self.tables.append({
                    'kind': 'word_dispatch', 'base': value, 'entry_size': 2,
                    'entries': used, 'extent_end': value + 2 * used,
                    'guard_bound': bound, 'guard_site': bound_site,
                    'consumer_site': site, 'consumer_block': insns[0].addr,
                    'targets': targets,
                    'tail_word': tail_word,
                    'tail_addr': value + 2 * used if tail_word is not None
                    else None})
                # the tail word is recorded as evidence only: with no guard
                # bound it is usually the first bytes of the code after the
                # table (e.g. the pushed continuation), not an entry
                follows.extend(Seed(t, 'table', 'dispatch_table_target',
                                    origin='table',
                                    evidence={'site': site, 'table': value,
                                              'bounded': bound is not None})
                               for t in targets)
            elif reg == 'bc' and 'add hl,bc' in after:
                # byte table of 3-byte `jp` stubs; the cp/sub guard gives the
                # reachable entry count when both are present in the block
                guard_entries, guard_site = self.index_arith_bound(insns, idx)
                scanned, scanned_targets = self.scan_jump_table(value)
                entries = guard_entries if guard_entries else scanned
                entry.update(kind='byte_jump_table', base=value, entry_size=3,
                             entries=entries, guard_entries=guard_entries,
                             targets=scanned_targets[:entries])
                self.tables.append({
                    'kind': 'byte_jump_table', 'base': value, 'entry_size': 3,
                    'entries': entries, 'extent_end': value + 3 * entries,
                    'guard_bound': guard_entries, 'guard_site': guard_site,
                    'consumer_site': site, 'consumer_block': insns[0].addr,
                    'targets': scanned_targets[:entries],
                    'scanned_entries': scanned})
                follows.extend(Seed(t, 'table', 'dispatch_table_target',
                                    origin='table',
                                    evidence={'site': site, 'table': value,
                                              'bounded': True})
                               for t in scanned_targets[:entries])
                if guard_entries is not None and scanned > guard_entries:
                    # the first post-table stub is code, not an entry
                    overflow = value + 3 * guard_entries
                    follows.append(Seed(overflow, 'table_overflow',
                                        'post_table_code', origin='table',
                                        evidence={'site': site,
                                                  'table': value}))
            elif reg == 'ptr':
                word = self.read_word(value)
                entry.update(kind='rom_pointer', base=value,
                             targets=[word] if word is not None else [])
                if word is not None:
                    follows.append(Seed(word, 'pointer', 'rom_pointer_target',
                                        origin='pointer',
                                        evidence={'site': site,
                                                  'pointer': value}))
        self.indirect = getattr(self, 'indirect', [])
        if len(self.indirect) < EXISTING_RECORD_LIMIT:
            self.indirect.append(entry)
        return follows

    def index_bound(self, insns, lookup_index):
        """Nearest `cp <n>` guard before a table lookup that a conditional
        exit follows - the index range the dispatcher itself enforces."""
        for i in range(lookup_index - 1, -1, -1):
            m = re.match(r'^cp 0x([0-9a-f]+)$', insns[i].text)
            if not m:
                continue
            nxt = insns[i + 1].text if i + 1 < len(insns) else ''
            if re.match(r'^(ret|jr|jp) (z|nz|c|nc|po|pe|p|m)\b', nxt):
                return int(m.group(1), 16), insns[i].addr
        return None, None

    def index_arith_bound(self, insns, lookup_index):
        """`cp <hi>` + conditional exit ... `sub <lo>` before a byte table:
        the index is the value minus lo, so entries = hi - lo."""
        for i in range(lookup_index - 1, -1, -1):
            m = re.match(r'^cp 0x([0-9a-f]+)$', insns[i].text)
            if not m:
                continue
            nxt = insns[i + 1].text if i + 1 < len(insns) else ''
            if not re.match(r'^(ret|jr|jp) (z|nz|c|nc|po|pe|p|m)\b', nxt):
                continue
            hi = int(m.group(1), 16)
            for j in range(i + 1, lookup_index):
                m2 = re.match(r'^sub 0x([0-9a-f]+)$', insns[j].text)
                if m2:
                    lo = int(m2.group(1), 16)
                    if hi > lo:
                        return hi - lo, insns[i].addr
        return None, None

    def scan_word_table(self, base, limit):
        """Little-endian word run at `base`; stops at the first non-code word.

        Returns (targets, used, tail_word): the terminating word itself is
        reported (a banked pointer is a real table entry, only not decodable
        in the fixed window)."""
        targets, used = [], 0
        tail_word = None
        while used < limit:
            word = self.read_word(base + 2 * used)
            if word is None or not (0x0100 <= word < self.top):
                tail_word = word
                break
            targets.append(word)
            used += 1
        return targets, used, tail_word

    def scan_jump_table(self, base):
        """3-byte entries at `base` while each decodes as `jp <addr>`.

        Returns (count, targets).  The first entry that does not decode as an
        unconditional jp ends the table."""
        targets, used = [], 0
        while used < TABLE_MAX:
            addr = base + 3 * used
            insn = self.index.get(addr)
            if insn is None or insn.kind != 'terminal' or \
                    not insn.text.startswith('jp ') or insn.target is None:
                break
            targets.append(insn.target)
            used += 1
        return used, targets

    def read_word(self, addr):
        image = getattr(self.index, 'image', None)
        if image is None or not (0 <= addr < self.top - 1):
            return None
        return image[addr] | (image[addr + 1] << 8)

    # -- reporting --------------------------------------------------------

    def spans(self, mask, limit=None):
        """Merge a byte mask into [start, end) runs."""
        out = []
        start = None
        for addr in range(self.top):
            if mask[addr]:
                if start is None:
                    start = addr
            elif start is not None:
                out.append((start, addr))
                start = None
        if start is not None:
            out.append((start, self.top))
        if limit is not None and len(out) > limit:
            return out[:limit], len(out)
        return out, len(out)

    def coverage(self):
        reachable, _ = self.spans(self.code)
        existing, _ = self.spans(self.existing)
        return reachable, existing


# ------------------------------------------------------- existing inventory

def existing_spans():
    """Assemble src/m1/asm/functions/*.s: [(org, size, path, text)]."""
    spans = []
    for src in sorted(FUNC_DIR.glob('*.s')):
        text = src.read_text()
        m = re.search(r'\.org\s+(?:0x|\$)([0-9a-fA-F]+)', text)
        if not m:
            raise SystemExit(f'{src}: missing org directive')
        org = int(m.group(1), 16)
        out = REPO / 'build' / 'tmp' / 'm1-recover-span.bin'
        out.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run([str(VASM), '-Fbin', '-quiet', '-o', str(out),
                               str(src)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f'vasm failed on {src.name}:\n{proc.stderr}')
        spans.append((org, out.stat().st_size, src, text))
    return spans


def seed_existing(rec, spans):
    """Mark existing spans covered and lift their flow into the worklist.

    The instruction sequence of each existing file is re-walked with the same
    decoder so that its transfers, fallthrough and indirect sites are treated
    exactly like recovered blocks - but the bytes are recorded as `existing`
    and never re-emitted.
    """
    seeds = []
    for org, size, src, _text in spans:
        end = min(org + size, rec.top)
        rec.mark_existing(org, end, src.name)
        addr, insns = org, []
        while addr < end:
            insn = rec.index.get(addr)
            if insn is None or insn.size == 0 or addr + insn.size > end:
                break
            rec.insn_len[addr] = insn.size
            insns.append(insn)
            addr += insn.size
        walked_end = addr
        block = Block(start=org, end=end, insns=insns, origin='existing',
                      seed=org, via='inventory', role='existing span',
                      terminator='existing_end', validated=True,
                      validation=f'src/m1/asm/functions/{src.name}',
                      evidence={'instructions': len(insns),
                                'walked_to': walked_end})
        rec.blocks[org] = block
        if walked_end != end:
            block.validation += ' (instruction walk missed the span end)'
            continue
        for insn in insns:
            if insn.kind in ('terminal', 'conditional') and insn.target is not None:
                seeds.append(Seed(insn.target, 'existing_branch',
                                  'existing branch target',
                                  evidence={'from': insn.addr}))
            elif insn.kind == 'call' and insn.target is not None:
                seeds.append(Seed(insn.target, 'existing_call',
                                  'existing callee', evidence={'from': insn.addr}))
            elif insn.kind == 'indirect':
                seeds.extend(rec.resolve_indirect(insn, insns))
        if insns and insns[-1].kind in ('conditional', 'call'):
            seeds.append(Seed(end, 'existing_fallthrough',
                              'fallthrough past existing span',
                              evidence={'from': walked_end - 1}))
    return seeds


# ------------------------------------------------------------- validation

def assemble(text, tmp):
    src = tmp / 'block.s'
    out = tmp / 'block.bin'
    src.write_text(text)
    proc = subprocess.run([str(VASM), '-Fbin', '-quiet', '-o', str(out),
                           str(src)], capture_output=True, text=True)
    if proc.returncode != 0:
        return None, proc.stderr.strip().splitlines()[:3]
    return out.read_bytes(), None


def block_source(block):
    lines = [
        '; Neo Turf Masters M1 (Z80 sound driver) - recovered basic block',
        f'; origin: {block.origin} via {block.via} (seed 0x{block.seed:04x})',
        f'; terminator: {block.terminator}'
        + (f' -> {", ".join(f"0x{t:04x}" for t in block.exit_targets)}'
           if block.exit_targets else ''),
        '',
        f' .org 0x{block.start:x}',
        f'blk_{block.start:04x}:',
    ]
    lines += ['        ' + i.text for i in block.insns]
    return '\n'.join(lines) + '\n'


def localize_failure(block, pristine, tmp):
    """Find the first prefix of the block that no longer round-trips."""
    good = 0
    for n in range(1, len(block.insns) + 1):
        text = block_source(Block(block.start, 0, block.insns[:n],
                                  block.origin, block.seed, block.via,
                                  block.role, block.terminator))
        body, err = assemble(text, tmp)
        if body is None or body != pristine[block.start:block.start + len(body)]:
            return n, block.insns[n - 1], err
        good = n
    return good, None, None


def verify(blocks, pristine, tmp):
    ok = failed = 0
    for block in blocks:
        body, err = assemble(block_source(block), tmp)
        if body is None:
            block.validation = 'vasm: ' + '; '.join(err or [])
            failed += 1
            continue
        want = pristine[block.start:block.start + len(body)]
        if body != want:
            where = next((i for i in range(min(len(body), len(want)))
                          if body[i] != want[i]), min(len(body), len(want)))
            block.validation = f'byte mismatch at +0x{where:x}'
            failed += 1
            continue
        block.validated = True
        block.validation = 'byte-exact'
        ok += 1
    return ok, failed


# ------------------------------------------------------------------- report

def span_stats(pristine, start, end):
    chunk = pristine[start:end]
    return {'start': start, 'end': end, 'size': len(chunk),
            'distinct_values': len(set(chunk))}


def ambiguity_envelope(pristine, spans, top, strict):
    """Second walk with the indexed-table cp-guard ignored.

    The word right after a guard-bounded table is usually the first bytes of
    the code that follows it, and can look like a valid pointer; the strict
    walk refuses it, this one follows it.  The difference quantifies how much
    coverage depends on that reading - it is reported, never published.
    """
    index = InsnIndex(make_fetch(PRISTINE, pristine))
    index.image = pristine
    rec = Recovery(index, top=top, guard_bounded_tables=False)
    seeds = [Seed(addr, 'vector', 'vector') for addr in VECTORS]
    seeds.extend(seed_existing(rec, spans))
    rec.run(seeds)
    loose, _ = rec.coverage()
    loose_bytes = sum(b - a for a, b in loose)
    strict_bytes = sum(b - a for a, b in strict.coverage()[0])
    extra = sorted(b.start for b in rec.blocks.values()
                   if b.origin != 'existing' and not strict.code[b.start])
    return {'note': 'coverage if indexed-dispatch tables ignore their own '
                    'cp-guard; reported only, not published as blocks',
            'reachable_bytes': loose_bytes,
            'extra_bytes_over_strict': loose_bytes - strict_bytes,
            'extra_block_starts': extra[:EXISTING_RECORD_LIMIT],
            'extra_block_count': len(extra)}


def build_report(rec, pristine, validated, failed, roots, envelope=None):
    reachable, existing = rec.coverage()
    unreached_mask = bytearray(rec.top)
    for addr in range(rec.top):
        if not rec.code[addr] and not rec.existing[addr]:
            unreached_mask[addr] = 1
    unreached, unreached_count = rec.spans(unreached_mask, limit=40)
    reach_total = sum(b - a for a, b in reachable)
    exist_total = sum(b - a for a, b in existing)
    direct_bytes = sum(b.end - b.start for b in rec.blocks.values()
                       if b.origin == 'direct')
    table_bytes = sum(b.end - b.start for b in rec.blocks.values()
                      if b.origin in ('table', 'pointer'))
    failed_bytes = sum(b.end - b.start for b in rec.blocks.values()
                       if b.origin != 'existing' and not b.validated)
    return {
        'image': str(PRISTINE.relative_to(REPO)),
        'fixed_top': rec.top,
        'policy': 'only CPU addresses 0x0000-0x7fff decoded; >= 0x8000 recorded '
                  'as banked (no physical offset inferred)',
        'counts': {
            'blocks': len(rec.blocks),
            'recovered_blocks': sum(1 for b in rec.blocks.values()
                                    if b.origin != 'existing'),
            'existing_blocks': sum(1 for b in rec.blocks.values()
                                   if b.origin == 'existing'),
            'validated_blocks': validated,
            'failed_blocks': failed,
            'instructions_decoded': rec.insn_count,
            'unidasm_sweeps': getattr(rec.index, 'sweeps', 0),
            'reachable_bytes': reach_total,
            'reachable_bytes_direct': direct_bytes,
            'reachable_bytes_table_derived': table_bytes,
            'failed_validation_bytes': failed_bytes,
            'existing_bytes': exist_total,
            'unreached_bytes': rec.top - reach_total - exist_total,
            'reachable_spans': len(reachable),
            'unreached_spans': unreached_count,
        },
        'coverage_percent_of_fixed_window':
            round(reach_total * 100.0 / rec.top, 3),
        'roots': roots,
        'seeds_added': len(rec.seeds),
        'reachable_spans': [[a, b] for a, b in reachable],
        'existing_spans': [[a, b] for a, b in existing],
        'indirect_transfers': getattr(rec, 'indirect', []),
        'tables': getattr(rec, 'tables', []),
        'undecodable_reached': rec.undecodable,
        'instruction_overlaps': rec.conflicts,
        'existing_boundary_stops': rec.boundary_hits[:EXISTING_RECORD_LIMIT],
        'banked_targets_not_decoded': rec.banked,
        'unreached_residue': [dict(span_stats(pristine, a, b),
                                   note='no reachable evidence either way')
                              for a, b in unreached],
        'ambiguity_envelope': envelope,
        'truncated': rec.truncated,
        'blocks': [
            {'start': b.start, 'end': b.end, 'bytes': b.end - b.start,
             'insns': len(b.insns), 'origin': b.origin, 'seed': b.seed,
             'via': b.via, 'role': b.role, 'terminator': b.terminator,
             'exit_targets': b.exit_targets, 'validated': b.validated,
             'validation': b.validation, 'emitted': b.emitted,
             'evidence': b.evidence}
            for b in sorted(rec.blocks.values(), key=lambda b: b.start)
            if b.origin != 'existing'
        ],
    }


def summarize(report, stream=sys.stdout):
    c = report['counts']
    print(f"reachable code bytes   {c['reachable_bytes']:6d} "
          f"({report['coverage_percent_of_fixed_window']}% of 0x8000) "
          f"[direct {c['reachable_bytes_direct']}, "
          f"table-derived {c['reachable_bytes_table_derived']}, "
          f"unvalidated {c['failed_validation_bytes']}]")
    print(f"existing inventory     {c['existing_bytes']:6d} bytes, "
          f"{c['existing_blocks']} files")
    print(f"blocks                 {c['recovered_blocks']:6d} recovered "
          f"({c['validated_blocks']} validated, {c['failed_blocks']} failed)")
    print(f"instructions decoded   {c['instructions_decoded']:6d} "
          f"in {c['unidasm_sweeps']} unidasm sweeps")
    print(f"unreached residue      {c['unreached_bytes']:6d} bytes in "
          f"{c['unreached_spans']} spans")
    print(f"indirect transfers     {len(report['indirect_transfers'])}"
          f" (resolved by table: "
          f"{sum(1 for i in report['indirect_transfers'] if i['kind'] != 'unresolved')})")
    tables = report['tables']
    if tables:
        print(f"resolved tables        {len(tables)}"
              f" ({sum(t['entries'] for t in tables)} entries, "
              f"{sum(t['entry_size'] * t['entries'] for t in tables)} bytes)")
    print(f"undecodable reached    {len(report['undecodable_reached'])}"
          f"  overlaps {len(report['instruction_overlaps'])}"
          f"  banked targets {len(report['banked_targets_not_decoded'])}")
    env = report.get('ambiguity_envelope')
    if env:
        print(f"table-guard envelope   +{env['extra_bytes_over_strict']} bytes "
              f"({env['extra_block_count']} blocks) if dispatch tables are read "
              f"past their cp-guard")


# ---------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--no-write', action='store_true',
                    help='do not write src/m1/asm/blocks/*.s')
    ap.add_argument('--limit-blocks', type=int, default=0,
                    help='stop after N recovered blocks (debug)')
    ap.add_argument('--json', type=pathlib.Path,
                    default=ANALYSIS_DIR / 'm1-recovery.json')
    ap.add_argument('--top', type=lambda s: int(s, 0), default=FIXED_TOP,
                    help='fixed window size (default 0x8000)')
    args = ap.parse_args(argv)

    if not VASM.exists():
        raise SystemExit(f'{VASM} missing - run tools/setup_vasm.sh')
    if not PRISTINE.exists():
        raise SystemExit('build/work/p1.m1.pristine.bin missing - run make extract')
    pristine = PRISTINE.read_bytes()

    index = InsnIndex(make_fetch(PRISTINE, pristine))
    index.image = pristine                      # ROM words for table/pointer reads
    rec = Recovery(index, top=args.top)

    roots = []
    for addr in VECTORS:
        kind = {0x0000: 'reset vector', 0x0066: 'NMI vector'}.get(
            addr, f'RST {addr // 8} vector')
        roots.append(Seed(addr, 'vector', kind, evidence={}))
    seeds = list(roots)
    spans = existing_spans()
    seeds.extend(seed_existing(rec, spans))
    for s in seeds:
        rec.row[s.role] = rec.row.get(s.role, 0) + 1

    rec.run(seeds)
    if args.limit_blocks:
        for start in sorted(rec.blocks)[args.limit_blocks:]:
            if rec.blocks[start].origin != 'existing':
                del rec.blocks[start]

    envelope = ambiguity_envelope(pristine, spans, args.top, rec)

    tmp = REPO / 'build' / 'tmp' / 'm1-recover'
    tmp.mkdir(parents=True, exist_ok=True)
    recovered = [b for b in sorted(rec.blocks.values(), key=lambda b: b.start)
                 if b.origin != 'existing']
    validated, failed = verify(recovered, pristine, tmp)

    written = 0
    if not args.no_write:
        BLOCK_DIR.mkdir(parents=True, exist_ok=True)
        current = {p.name for p in BLOCK_DIR.glob('*.s')}
        for block in recovered:
            if not block.validated:
                continue
            name = f'{block.start:08x}.s'
            (BLOCK_DIR / name).write_text(block_source(block))
            block.emitted = True
            written += 1
            current.discard(name)
        for stale in current:      # keep the directory a faithful mirror
            (BLOCK_DIR / stale).unlink()

    failures = []
    for block in recovered:
        if block.validated:
            continue
        n, insn, err = localize_failure(block, pristine, tmp)
        failures.append({'start': block.start, 'validation': block.validation,
                         'good_prefix_insns': n,
                         'first_bad': insn.text if insn else None,
                         'vasm_error': err})

    report = build_report(rec, pristine, validated, failed, {
        'seed_sources': rec.row,
        'vectors': [hex(v) for v in VECTORS],
        'existing_inventory': [
            {'file': s.name, 'org': org, 'size': size, 'end': org + size}
            for org, size, s, _ in spans],
    }, envelope=envelope)
    report['validation_failures'] = failures[:EXISTING_RECORD_LIMIT]
    report['blocks_written'] = written
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=1) + '\n')

    summarize(report)
    print(f"wrote {args.json} and {written} block source(s) under "
          f"{BLOCK_DIR.relative_to(REPO)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
