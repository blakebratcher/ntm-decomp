#!/usr/bin/env python3
"""Recover additional m68k blocks reached from existing instruction sources.

Vasm listings establish instruction starts (dc.b tails are NOT roots).
Direct flow and fallthrough seed unidasm; every published block must roundtrip.
Data-region entries, interior-instruction branches, illegal instructions and
indirect transfers are recorded, not guessed. Existing source roots inherit
their current classification; byte equality alone cannot prove executability.
"""
import argparse
import collections
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile

from build_p1 import REPO, VASM, SRC_DIR, BLOCK_DIR
from unidasm_spans import disassemble, convert, build_source

LISTING = re.compile(r'^\d+:([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s+\s*\d+:\s*(.*?)\s*$')
BRANCH = {'bra', 'bsr', 'bhi', 'bls', 'bcc', 'bcs', 'bne', 'beq', 'bvc',
          'bvs', 'bpl', 'bmi', 'bge', 'blt', 'bgt', 'ble'}
STOP = {'rts', 'rte', 'rtr', 'stop', 'illegal', 'reset'}


def flow(addr, length, text):
    """Return (successors, unresolved_indirect). Calls include their return site."""
    parts = text.lower().split(None, 1)
    op = parts[0].split('.')[0]
    operands = parts[1] if len(parts) > 1 else ''
    targets = []
    indirect = False
    if op in BRANCH or op.startswith('db') or op in {'jmp', 'jsr'}:
        # Unidasm resolves simple PC-relative targets; indexed PC remains dynamic.
        vals = re.findall(r'\$([0-9a-f]+)', operands)
        if vals and not re.search(r'\b[ad][0-7]\b', operands.split(',')[-1]) and not re.search(r',\s*[ad][0-7]', operands):
            targets.append(int(vals[-1], 16))
        else:
            indirect = True
    if op not in STOP | {'bra', 'jmp', 'trap', 'trapv'}:
        targets.append(addr + length)
    return sorted(set(targets)), indirect


def parse_listing(text):
    result = {}
    previous = None
    for line in text.splitlines():
        m = LISTING.match(line)
        if not m:
            continuation = re.match(r'^\d+:([0-9A-Fa-f]+)\s+([0-9A-Fa-f]+)\s*$', line)
            if continuation and previous is not None:
                addr, encoded = continuation.groups()
                length, ins = result[previous]
                if int(addr, 16) != previous + length:
                    raise ValueError('noncontiguous vasm listing continuation')
                result[previous] = (length + len(encoded)//2, ins)
            continue
        addr, encoded, ins = m.groups()
        token = ins.split()[0].lower() if ins else ''
        if token in {'dc.b', 'dc.w', 'dc.l', 'dcb.b', 'dcb.w', 'ds.b', 'ds.w'} or not token:
            previous = None
            continue
        previous = int(addr, 16)
        result[previous] = (len(encoded)//2, ins)
    return result


def assemble_source(path, raw, listing=None):
    cmd = [str(VASM), '-Fbin', '-no-opt', '-quiet']
    # a symbolized source includes build/gen/p1/symbols.i; pass -I when the
    # directory exists so recovery tools keep working after symbolize --write
    inc = REPO / 'build' / 'gen' / 'p1'
    if inc.exists():
        cmd.append(f'-I{inc}')
    cmd += ['-o', str(raw)]
    if listing:
        cmd += ['-L', str(listing)]
    result = subprocess.run(cmd + [str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f'{path.name}: {result.stderr[:400]}')
    return raw.read_bytes()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--write', action='store_true', help='publish verified blocks under src/p1/asm/blocks')
    args = ap.parse_args()
    out = REPO / 'build/analysis/p1-recovery'
    out.mkdir(parents=True, exist_ok=True)
    blobs = {0: (REPO / 'build/work/p1.fixed.cpu.bin').read_bytes(),
             0x200000: (REPO / 'build/work/p1.bank.cpu.bin').read_bytes()}
    def window(addr):
        return next((base for base,b in blobs.items() if base <= addr < base+len(b)), None)
    data_ranges = []
    for row in (REPO / 'config/data_regions.tsv').read_text().splitlines():
        if row and not row.startswith('#'):
            a, n = row.split('\t')[:2]
            data_ranges.append((int(a,16), int(a,16)+int(n,16)))
    known = {}
    rejected, indirect, provenance = [], [], {}
    with tempfile.TemporaryDirectory(prefix='ntm-p1-recover-') as td:
        temp = pathlib.Path(td)
        src, binary, listing, cut = [temp / n for n in ('block.s','block.bin','block.lst','cut.bin')]
        for path in sorted(SRC_DIR.glob('*.s')) + sorted(BLOCK_DIR.glob('*.s')):
            assemble_source(path, binary, listing)
            for addr, info in parse_listing(listing.read_text()).items():
                if addr in known and known[addr][0] != info[0]:
                    rejected.append({'address': hex(addr), 'reason': 'existing root length conflict'})
                known[addr] = info
        occupied = {}
        for addr,(length,_) in known.items():
            for a in range(addr,addr+length):
                occupied[a] = addr
        queue = collections.deque()
        for addr,(length,ins) in known.items():
            targets, dynamic = flow(addr,length,ins)
            if dynamic:
                indirect.append({'address': hex(addr), 'instruction': ins})
            for target in targets:
                if window(target) is not None:
                    queue.append((target,addr))
        recovered = {}
        attempted = set()
        while queue:
            start, parent = queue.popleft()
            if start in known or start in recovered or start in attempted:
                continue
            attempted.add(start)
            base = window(start)
            if base is None:
                continue
            if start & 1 or start in occupied:
                rejected.append({'address': hex(start), 'from': hex(parent), 'reason': 'odd/interior instruction target'})
                continue
            if any(a <= start < b for a,b in data_ranges):
                rejected.append({'address': hex(start), 'from': hex(parent), 'reason': 'target in named data region'})
                continue
            # Start decoding at the actual reachable target, not at an arbitrary earlier offset.
            rawbytes = blobs[base][start-base:min(len(blobs[base]),start-base+4096)]
            cut.write_bytes(rawbytes)
            decoded = disassemble(cut,start)
            for i,(addr,text) in enumerate(decoded[:-1]):
                length = decoded[i+1][0]-addr
                if addr in known or addr in recovered:
                    break
                if any(a in occupied for a in range(addr, addr+length)) or any(a < addr+length and addr < b for a,b in data_ranges):
                    rejected.append({'address':hex(addr),'from':hex(parent),'reason':'instruction/data overlap'})
                    break
                if 'illegal' in text or 'invalid' in text or text.startswith('dc.'):
                    rejected.append({'address':hex(addr),'from':hex(parent),'reason':'invalid reachable instruction'})
                    break
                # Only include an instruction after its exact encoding has been checked.
                converted = convert(addr,length,text)
                src.write_text(f' org ${addr:x}\n        {converted}\n')
                try:
                    got = assemble_source(src,binary)
                except ValueError as exc:
                    rejected.append({'address':hex(addr),'from':hex(parent),'reason':str(exc)})
                    break
                expect = blobs[base][addr-base:addr-base+length]
                if got != expect:
                    rejected.append({'address':hex(addr),'from':hex(parent),'reason':'encoding mismatch'})
                    break
                recovered[addr] = (length,text)
                provenance[addr] = parent
                for a in range(addr,addr+length):
                    occupied[a] = addr
                targets, dynamic = flow(addr,length,text)
                if dynamic:
                    indirect.append({'address':hex(addr),'instruction':text})
                for target in targets:
                    if window(target) is not None:
                        queue.append((target,addr))
                if addr+length not in targets:
                    break
                parent = addr
        groups = []
        for addr,(length,text) in sorted(recovered.items()):
            if groups and groups[-1][-1][0]+groups[-1][-1][1] == addr:
                groups[-1].append((addr,length,text))
            else:
                groups.append([(addr,length,text)])
        blocks = []
        for group in groups:
            start = group[0][0]; end = group[-1][0]+group[-1][1]
            body = build_source(start,[(a,t) for a,n,t in group],end)
            source = (f'; NTM reachable block; unidasm decode, vasm byte gate.\n'
                      f'; Root evidence: existing instruction flow from ${provenance[start]:x}.\n'
                      f'; kind: insn; range [${start:x}, ${end:x}); not a function boundary claim.\n'
                      f' org ${start:x}\nrecovered_{start:08x}:\n'+body)
            src.write_text(source)
            got = assemble_source(src,binary)
            base=window(start)
            if got != blobs[base][start-base:end-base]:
                raise SystemExit(f'block ${start:x}: final roundtrip failed')
            path = BLOCK_DIR / f'{start:08x}.s'
            if args.write:
                BLOCK_DIR.mkdir(parents=True,exist_ok=True)
                path.write_text(source)
            blocks.append({'start':hex(start),'end':hex(end),'size':end-start,
                           'from':hex(provenance[start]),'sha256':hashlib.sha256(got).hexdigest()})
        report={'root_instruction_count':len(known),'recovered_instructions':len(recovered),
                'recovered_bytes':sum(n for n,t in recovered.values()),'blocks':blocks,
                'rejected':rejected,'indirect_transfers':indirect,
                'limitation':'inherits existing instruction-root classifications; static flow, not execution proof'}
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(f"{len(known)} root instructions; recovered {len(recovered)} instructions / {report['recovered_bytes']} bytes / {len(blocks)} blocks")
        print(f'{len(rejected)} rejected targets; {len(indirect)} indirect transfers remain')


if __name__=='__main__':
    main()
