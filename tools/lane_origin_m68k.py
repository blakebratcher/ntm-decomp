#!/usr/bin/env python3
"""68000 instruction model for the origin census (asm-vs-C authorship).

Parses instruction text in either of two dialects into a common
representation and computes conservative per-instruction register
reads/writes plus flag effects.  The model is intentionally conservative:
a mnemonic outside the table falls back to "reads and writes every
register mentioned", so an unmodelled instruction can blur the
first-use-before-def statistics but never invents a clean ABI signal.

Both dialects are normalized:
  - repo .s sources (vasm mot): '        move.l d0,-(a7)', lowercase
  - unidasm (MAME): '2908: 4a80       tst.l   D0', uppercase

The unidasm wrapper also parses 'dc.w $xxxx; ILLEGAL' continuation lines
so inline data / desynced decodes are counted, not silently treated as
instructions.
"""
import re

DREGS = {f'd{i}' for i in range(8)}
AREGS = {f'a{i}' for i in range(8)}
REGS = DREGS | AREGS
SPECIAL = {'sr', 'ccr', 'usp', 'pc', 'sp'}


def norm_reg(name):
    """Normalize a register token to lowercase ('sp' -> 'a7')."""
    n = name.lower()
    return 'a7' if n == 'sp' else n


def regs_mentioned(operand):
    """All dN/aN registers named anywhere in one operand string."""
    found = set()
    for token in re.findall(r'[ad][0-7]', operand.lower()):
        found.add(token)
    return found


def split_operands(text):
    """Split on commas outside parentheses (stolen from unidasm_spans)."""
    ops, depth, cur = [], 0, ''
    for ch in text:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        if ch == ',' and depth == 0:
            ops.append(cur.strip())
            cur = ''
        else:
            cur += ch
    if cur.strip():
        ops.append(cur.strip())
    return ops


def parse_source_line(line):
    """One .s corpus line -> ('insn', mnemonic, operands, size) /
    ('label', name, None) / ('directive', word, rest) / None.  Mnemonic is
    lowercase, operands a list, size is the mnemonic's size suffix ('' for
    none; branch sources carry explicit '.s'/'.w' and are byte-exact)."""
    line = line.split(';', 1)[0].strip()
    if not line:
        return None
    if line.startswith('*'):
        return None
    # label (possibly with equ) at any indent
    m = re.match(r'^([a-zA-Z_.$][\w.$]*):\s*(.*)$', line)
    if m:
        rest = m.group(2).strip()
        if rest:
            return parse_source_line(rest)
        return ('label', m.group(1), None)
    word = line.split(None, 1)[0]
    rest = line[len(word):].strip()
    low = word.lower()
    if low in ('org', 'equ', 'section', 'align', 'cnop', 'end', 'include',
               'if', 'else', 'endif', 'endc', 'ifd', 'ifnd', 'ifne', 'ifeq',
               'macro', 'endm', 'rsreset', 'rsset', 'so', 'set', 'global',
               'xref', 'xdef', 'public', 'machine', 'opt', 'fail'):
        return ('directive', low, rest)
    if re.match(r'^(dc|ds|dcb|blk)\b', low):
        return ('directive', low, rest)
    parts = low.split('.')
    mnem = parts[0]
    size = parts[1] if len(parts) > 1 else ''
    return ('insn', mnem, split_operands(rest) if rest else [], size)


UDA_LINE = re.compile(r'^([0-9a-fA-F]+):\s+([0-9a-fA-F ]+?)\s{2,}(.*)$')


def parse_unidasm_line(line):
    """One unidasm output line -> dict(addr,length,mnemonic,ops,illegal).

    '2908: 4a80       tst.l   D0' -> mnemonic 'tst', ops ['d0'].
    '000: 00c0       dc.w    $00c0; ILLEGAL' -> illegal event (mnemonic
    'dc.w', ops ['$00c0'], illegal=True).  Returns None for blank lines."""
    line = line.rstrip()
    if not line:
        return None
    m = UDA_LINE.match(line)
    if m:
        addr = int(m.group(1), 16)
        byts = m.group(2).split()
        text = m.group(3).strip()
        illegal = 'ILLEGAL' in text
        if illegal:
            text = text.replace('; ILLEGAL', '')
        parts = text.split(None, 1)
        mnem = parts[0].lower().split('.')[0]
        ops = split_operands(parts[1]) if len(parts) > 1 else []
        ops = [norm_operand(o) for o in ops]
        return dict(addr=addr, length=len(byts), mnemonic=mnem, ops=ops,
                    illegal=illegal, raw=line)
    return None


def norm_operand(op):
    """Lowercase registers inside an operand, keep the rest."""
    return re.sub(r'\b([ADad][0-7]|SP|sp|SR|sr|CCR|ccr|USP|usp|PC|pc)\b',
                  lambda m: m.group(1).lower(), op)


def ea_class(op):
    """Addressing-mode class of one operand (normalized)."""
    o = op.strip()
    if re.fullmatch(r'd[0-7]', o):
        return 'dreg'
    if re.fullmatch(r'a[0-7]', o):
        return 'areg'
    if o.startswith('#'):
        return 'imm'
    if o.startswith('-('):
        return 'aind_pre'
    if o.endswith(')+'):
        return 'aind_post'
    m = re.fullmatch(r'\(\$([0-9a-f]+)\)\.(w|l)', o, re.I)
    if m:
        return 'abs' if m.group(2) == 'w' else 'abs_l'
    if o.startswith('(') and o.endswith(')'):
        inner = o[1:-1].strip()
        if re.fullmatch(r'a[0-7]', inner):
            return 'aind'
        parts = inner.split(',')
        # (d,An) / (An,d) small displacement, (An,Xn) indexed, (d,PC,Xn)
        if len(parts) == 2:
            a, b = parts[0].strip().lower(), parts[1].strip().lower()
            if a in AREGS and b in REGS:
                return 'aind_idx'
            if b in AREGS and a in REGS:
                return 'aind_idx'
            if a in AREGS and re.match(r'^[+-]?\$?[0-9a-f]+$', b):
                return 'aind_disp'
            if b in AREGS and re.match(r'^[+-]?\$?[0-9a-f]+$', a):
                return 'aind_disp'
            if b == 'pc' and re.match(r'^[+-]?\$?[0-9a-f]+$', a):
                return 'pcd'
            if a == 'pc' and re.match(r'^[+-]?\$?[0-9a-f]+$', b):
                return 'pcd'
            return 'idx'
        if len(parts) == 3 and parts[1].strip().lower() == 'pc':
            return 'pci'
        return 'idx'
    m = re.fullmatch(r'\$([0-9a-f]+)(\.w)?', o, re.I)
    if m:
        return 'abs'
    if re.match(r'^\$[0-9a-f]+\.l$', o, re.I):
        return 'abs_l'
    return 'other'


# mnemonics whose explicit or implicit operands are written/read only
_BINARY_EA_DN = {'add', 'sub', 'and', 'or', 'eor', 'cmp'}
_BINARY_DN_DST = {'add', 'sub', 'and', 'or', 'eor', 'move'}
_ADDQ = {'addq', 'subq'}
_SHIFTS = {'asl', 'asr', 'lsl', 'lsr', 'rol', 'ror', 'roxl', 'roxr'}
_BITS = {'btst', 'bchg', 'bclr', 'bset'}
_BRANCHES = {'bra', 'bsr', 'bhi', 'bls', 'bcc', 'bcs', 'bne', 'beq',
             'bvc', 'bvs', 'bpl', 'bmi', 'bge', 'blt', 'bgt', 'ble'}
_DBCC = {'dbra', 'dbf', 'dbt', 'dbeq', 'dbne', 'dbcc', 'dbcs', 'dbmi',
         'dbpl', 'dbge', 'dblt', 'dbgt', 'dble', 'dbhi', 'dbls',
         'dbvc', 'dbvs'}
_FLAG_WRITERS = {'add', 'addi', 'addq', 'addx', 'sub', 'subi', 'subq',
                 'subx', 'neg', 'negx', 'not', 'clr', 'tst', 'cmp', 'cmpa',
                 'cmpi', 'cmpm', 'eor', 'eori', 'and', 'andi', 'or', 'ori',
                 'move', 'moveq', 'asl', 'asr', 'lsl', 'lsr', 'rol', 'ror',
                 'roxl', 'roxr', 'ext', 'chk', 'btst', 'abcd', 'sbcd',
                 'divu', 'divs', 'nbcd', 'tas'}


def effects(mnemonic, ops):
    """Conservative (reads, writes, writes_flags) for one instruction.

    Registers are normalized ('a7' for sp).  Unmodelled mnemonics read and
    write every register mentioned."""
    m = mnemonic.lower().split('.')[0]
    reads, writes = set(), set()
    flags = m in _FLAG_WRITERS

    def regs(op):
        return regs_mentioned(op)

    if m == 'moveq':
        if len(ops) == 2:
            writes.add(norm_reg(ops[1]))
        return reads, writes, flags
    if m == 'move' or m == 'movea':
        if len(ops) == 2:
            if norm_reg(ops[1]) in ('sr', 'ccr'):  # move Dn/ea,SR/CCR
                reads |= regs(ops[0])
                writes.add(norm_reg(ops[1]))
                return reads, writes, flags
            if norm_reg(ops[0]) in ('sr', 'ccr', 'usp'):  # move SR/CCR,ea
                reads.add(norm_reg(ops[0]))
                writes |= regs(ops[1])
                return reads, writes, flags
            if norm_reg(ops[1]) == 'usp':  # move An,USP
                reads |= regs(ops[0])
                writes.add('usp')
                return reads, writes, flags
            reads |= regs(ops[0])
            dst = ops[1]
            writes |= regs(dst)
            for op in (ops[0], dst):
                if ea_class(op) == 'aind_pre':
                    writes.add(norm_reg(op[2:-1]))
                if ea_class(op) == 'aind_post':
                    reads.add(norm_reg(op[1:-2]))
                    writes.add(norm_reg(op[1:-2]))
        if m == 'movea':
            flags = False
        return reads, writes, flags
    if m == 'lea':
        if len(ops) == 2:
            reads |= regs(ops[0])
            writes.add(norm_reg(ops[1]))
        return reads, writes, False
    if m == 'pea':
        if ops:
            reads |= regs(ops[0])
            writes.add('a7')
        return reads, writes, False
    if m in ('add', 'sub', 'and', 'or', 'eor'):
        if len(ops) == 2:
            if ea_class(ops[1]) == 'dreg':
                reads |= regs(ops[0]) | regs(ops[1])
                writes.add(norm_reg(ops[1]))
            else:  # Dn,ea
                reads |= regs(ops[0]) | regs(ops[1])
                writes |= regs(ops[1])
        return reads, writes, flags
    if m in ('cmp', 'cmpa', 'cmpi'):
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
        return reads, writes, flags
    if m == 'cmpm':
        if len(ops) == 2:
            for op in ops:
                if ea_class(op) == 'aind_post':
                    a = norm_reg(op[1:-2])
                    reads.add(a)
                    writes.add(a)
        return reads, writes, flags
    if m in _ADDQ:
        if len(ops) == 2:
            reads |= regs(ops[0])
            dst = ops[1]
            reads |= regs(dst)
            writes |= regs(dst)
            if ea_class(dst) == 'aind_pre':
                writes.add(norm_reg(dst[2:-1]))
            if ea_class(dst) == 'aind_post':
                reads.add(norm_reg(dst[1:-2]))
                writes.add(norm_reg(dst[1:-2]))
        return reads, writes, flags
    if m in ('adda', 'suba'):
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
            writes.add(norm_reg(ops[1]))
        return reads, writes, False
    if m in ('addx', 'subx', 'abcd', 'sbcd'):
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
            writes |= regs(ops[1])
            for op in ops:
                if ea_class(op) == 'aind_pre':
                    a = norm_reg(op[2:-1])
                    reads.add(a)
                    writes.add(a)
        return reads, writes, flags
    if m == 'movem':
        # register list and memory EA: the list is whichever operand is
        # not the addressing-mode side (mot syntax puts it first, but the
        # load form reads 'movem (An)+,list' with the EA first)
        if len(ops) == 2:
            ea0, ea1 = ea_class(ops[0]), ea_class(ops[1])
            mem0 = ea0 in ('aind', 'aind_disp', 'aind_idx', 'aind_post',
                           'abs', 'abs_l')
            mem1 = ea1 in ('aind', 'aind_disp', 'aind_idx', 'aind_pre',
                           'abs', 'abs_l')
            list_op = ops[1] if mem0 else ops[0]
            listed = set()
            for part in re.split(r'[/,]', list_op):
                mm = re.fullmatch(r'\s*([ad])([0-7])\s*-\s*([ad])?([0-7])\s*',
                                  part)
                if mm:
                    for i in range(int(mm.group(2)), int(mm.group(4)) + 1):
                        listed.add(mm.group(1).lower() + str(i))
                    continue
                mm = re.fullmatch(r'\s*([ad][0-7])\s*', part)
                if mm:
                    listed.add(mm.group(1).lower())
            if mem0:                            # load: regs written
                if ea0 == 'aind_post':
                    a = norm_reg(ops[0][1:-2])
                    reads.add(a)
                    writes.add(a)
                else:
                    reads |= regs(ops[0])
                writes |= listed
            else:                               # store: regs read
                if ea1 == 'aind_pre':
                    a = norm_reg(ops[1][2:-1])
                    reads.add(a)
                    writes.add(a)
                else:
                    pass
                reads |= listed
        return reads, writes, False
    if m in ('mulu', 'muls'):
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
            writes.add(norm_reg(ops[1]))
        return reads, writes, False
    if m in ('divu', 'divs'):
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
            writes.add(norm_reg(ops[1]))
        return reads, writes, flags
    if m in ('ext', 'swap', 'exg', 'not', 'neg', 'negx', 'clr', 'nbcd'):
        if ops:
            reads |= regs(ops[0])
            writes |= regs(ops[0])
        if m in ('swap', 'exg'):
            flags = False
        return reads, writes, flags
    if m == 'tst':
        if ops:
            reads |= regs(ops[0])
        return reads, writes, flags
    if m in ('chk', 'tas'):
        if ops:
            reads |= regs(ops[0])
            if m == 'tas':
                writes |= regs(ops[0])
        return reads, writes, flags
    if m in _SHIFTS:
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
            writes |= regs(ops[1])
        elif ops:
            reads |= regs(ops[0])
            writes |= regs(ops[0])
        return reads, writes, flags
    if m in _BITS:
        if len(ops) == 2:
            reads |= regs(ops[0]) | regs(ops[1])
            if m != 'btst':
                writes |= regs(ops[1])
        return reads, writes, flags
    if m in _BRANCHES:
        reads.add('sr')
        if m == 'bsr':
            reads.add('a7')
            writes.add('a7')
        return reads, writes, False
    if m in ('jmp', 'jsr'):
        if ops:
            reads |= regs(ops[0])
        if m == 'jsr':
            reads.add('a7')
            writes.add('a7')
        return reads, writes, False
    if m in _DBCC:
        if len(ops) == 2:
            reads.add(norm_reg(ops[0]))
            writes.add(norm_reg(ops[0]))
        return reads, writes, False
    if m == 'scc' or m in ('seq', 'sne', 'sge', 'slt', 'sgt', 'sle',
                           'shi', 'sls', 'spl', 'smi', 'svc', 'svs', 'sf'):
        if ops:
            reads.add('sr')
            writes |= regs(ops[0])
        return reads, writes, False
    if m in ('rts', 'rte', 'rtr'):
        reads.add('a7')
        writes.add('a7')
        if m in ('rte', 'rtr'):
            reads.add('sr')
        return reads, writes, False
    if m == 'rtd':
        reads.add('a7')
        writes.add('a7')
        return reads, writes, False
    if m == 'link':
        if len(ops) == 2:
            a = norm_reg(ops[0])
            reads.add(a)
            writes.add(a)
            reads.add('a7')
            writes.add('a7')
        return reads, writes, flags
    if m == 'unlk':
        if ops:
            a = norm_reg(ops[0])
            reads.add(a)
            writes.add(a)
            reads.add('a7')
            writes.add('a7')
        return reads, writes, flags
    if m in ('move_to_sr', 'move_from_sr', 'move_to_ccr', 'move_from_ccr'):
        if ops:
            if 'from' in m:
                reads.add('sr')
                writes |= regs(ops[0])
            else:
                reads |= regs(ops[0])
                writes.add('sr')
        return reads, writes, flags
    if m in ('trap', 'trapv', 'illegal'):
        reads.add('a7')
        writes.add('a7')
        return reads, writes, False
    if m in ('nop', 'reset', 'stop', 'rte'):
        return reads, writes, False
    # fallback: conservative unknown
    for op in ops:
        reads |= regs(op)
        writes |= regs(op)
    return reads, writes, False


def operand_roles(mnemonic, ops):
    """Per-operand memory roles for one instruction: list of
    ('r'|'w'|'rw', operand).  Used for stack-frame access accounting
    (e.g. which (d,An) operands are loads vs stores).  Conservative:
    unmodelled mnemonics mark every operand 'rw'."""
    m = mnemonic.lower().split('.')[0]
    if not ops:
        return []
    if m in ('move', 'movea') and len(ops) == 2:
        return [('r', ops[0]), ('w', ops[1])]
    if m == 'lea' and len(ops) == 2:
        return [('r', ops[0]), ('w', ops[1])]
    if m == 'pea':
        return [('r', ops[0])]
    if m in ('add', 'sub', 'and', 'or', 'eor') and len(ops) == 2:
        if ea_class(ops[1]) == 'dreg':
            return [('r', ops[0]), ('r', ops[1])]
        return [('r', ops[0]), ('rw', ops[1])]
    if m in ('cmp', 'cmpa', 'cmpi', 'tst', 'chk'):
        return [('r', o) for o in ops]
    if m in ('addq', 'subq') and len(ops) == 2:
        return [('r', ops[0]), ('rw', ops[1])]
    if m in ('adda', 'suba') and len(ops) == 2:
        return [('r', ops[0]), ('w', ops[1])]
    if m in ('addx', 'subx', 'abcd', 'sbcd') and len(ops) == 2:
        return [('rw', o) for o in ops]
    if m == 'movem' and len(ops) == 2:
        return [('r', ops[0]), ('w', ops[1])]
    if m in ('clr', 'neg', 'negx', 'not', 'nbcd', 'scc', 'seq', 'sne',
             'sge', 'slt', 'sgt', 'sle', 'shi', 'sls', 'spl', 'smi',
             'svc', 'svs', 'sf'):
        return [('w', ops[0])]
    if m in ('mulu', 'muls', 'divu', 'divs', 'ext', 'swap', 'exg') and \
            len(ops) == 2:
        return [('r', ops[0]), ('rw', ops[1])]
    if m == 'tas':
        return [('rw', ops[0])]
    if m in ('btst', 'bchg', 'bclr', 'bset') and len(ops) == 2:
        if m == 'btst':
            return [('r', ops[0]), ('r', ops[1])]
        return [('r', ops[0]), ('rw', ops[1])]
    if m in _SHIFTS and len(ops) == 2:
        return [('r', ops[0]), ('rw', ops[1])]
    return [('rw', o) for o in ops]


def base_of(op):
    """Frame-base analysis of one operand: ('a7'|'a6'|None, kind) where
    kind is 'indirect' for (An), 'disp' for (d,An), 'idx' for (An,Xn)."""
    def plain(tok):
        return tok.rsplit('.', 1)[0] if '.' in tok else tok
    o = op.strip()
    m = re.fullmatch(r'\(a([0-7])\)', o)
    if m:
        return ('a' + m.group(1), 'indirect')
    inner = o[1:-1] if o.startswith('(') and o.endswith(')') else ''
    parts = [p.strip() for p in inner.split(',')] if inner else []
    if len(parts) == 2:
        a, b = plain(parts[0]), plain(parts[1])
        if a in AREGS and (b in REGS or b == 'pc'):
            return (a, 'idx')
        if b in AREGS and (a in REGS or a == 'pc'):
            return (b, 'idx')
        if a in AREGS:
            return (a, 'disp')
        if b in AREGS:
            return (b, 'disp')
    return (None, None)
