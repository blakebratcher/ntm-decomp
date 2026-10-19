#!/usr/bin/env python3
"""Verify the shared fixed-point divide in three owner-provided ROM sets.

Outputs hashes, boundaries and candidate call sites, never ROM bytes.
Offsets in direct_calls are CPU addresses in the fixed P1 window. Aligned
opcode scans can hit data; they are candidates, not a complete call graph.
P2 banks and indirect calls are deliberately outside this report's scope.
"""
import argparse
import hashlib
import json
import pathlib
import tempfile
import zipfile

from rom_fingerprint_scan import word_swap
from unidasm_spans import disassemble

# Observed layout, not a general Neo Geo ROM loader.
TARGETS = [
    ('turfmast', '200-p1.p1', 0x100000, 0x2908, 10, 792),
    ('mslug2', '241-p1.p1', 0, 0xb2a8e, 12, 794),
    ('mslugx', '250-p1.p1', 0, 0x4e648, 12, 794),
]


def direct_calls(blob, target):
    calls = []
    for addr in range(0, len(blob) - 1, 2):
        op = int.from_bytes(blob[addr:addr+2], 'big')
        dest = None
        kind = None
        if op == 0x4eb9 and addr + 6 <= len(blob):
            dest = int.from_bytes(blob[addr+2:addr+6], 'big')
            kind = 'jsr.l'
        elif op in (0x6100, 0x4eba) and addr + 4 <= len(blob):
            dest = addr + 2 + int.from_bytes(blob[addr+2:addr+4], 'big', signed=True)
            kind = 'bsr.w' if op == 0x6100 else 'jsr.pc'
        elif op & 0xff00 == 0x6100 and op & 0xff:
            dest = addr + 2 + int.from_bytes(blob[addr+1:addr+2], 'big', signed=True)
            kind = 'bsr.s'
        if dest == target:
            calls.append({'address': f'0x{addr:06x}', 'encoding': kind})
    return calls


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--zips', type=pathlib.Path, required=True)
    args = ap.parse_args()
    records = []
    reference_body = reference_wrapper = None
    with tempfile.TemporaryDirectory(prefix='ntm-contract-') as td:
        raw = pathlib.Path(td) / 'span.bin'
        for game, member, offset, entry, prefix, size in TARGETS:
            with zipfile.ZipFile(args.zips / (game + '.zip')) as zf:
                member_data = zf.read(member)
            blob = word_swap(member_data)[offset:offset + 0x100000]
            if len(blob) != 0x100000:
                raise SystemExit(f'{game}: truncated fixed window')
            body = blob[entry+prefix:entry+size]
            wrapper = blob[entry+size:entry+size+40]
            # The sole wrapper relocation is the signed bsr.w displacement.
            normalized_wrapper = wrapper[:26] + b'\0\0' + wrapper[28:]
            if reference_body is None:
                reference_body, reference_wrapper = body, normalized_wrapper
            if body != reference_body or normalized_wrapper != reference_wrapper:
                raise SystemExit(f'{game}: library body/wrapper does not match')
            raw.write_bytes(blob[entry:entry+size])
            events = disassemble(raw, entry)
            if len(events) != 347 or events[-1] != (entry+size-2, 'rts'):
                raise SystemExit(f'{game}: unexpected division instruction structure')
            # Entry tests a == 0 before b == 0; independently decoded targets.
            if [t.split()[0] for _, t in events[:4]] != ['tst.l', 'beq', 'tst.l', 'beq']:
                raise SystemExit(f'{game}: unexpected zero handling')
            trap = int(events[3][1].split()[-1].lstrip('$'), 16)
            raw.write_bytes(blob[trap:trap+2])
            trap_instruction = disassemble(raw, trap)[0][1]
            if trap_instruction.replace(' ', '') != 'trap#$f':
                raise SystemExit(f'{game}: expected TRAP #15 at divisor-zero target')
            records.append({
                'game': game, 'member': member,
                'member_sha256': hashlib.sha256(member_data).hexdigest(),
                'fixed_member_offset': f'0x{offset:x}',
                'entry': f'0x{entry:06x}', 'size': size,
                'body_size': len(body),
                'body_sha256': hashlib.sha256(body).hexdigest(),
                'zero_divisor_target': f'0x{trap:06x}',
                'signed_wrapper': f'0x{entry+size:06x}',
                'kernel_direct_call_candidates': direct_calls(blob, entry),
                'wrapper_direct_call_candidates': direct_calls(blob, entry+size),
            })
    print(json.dumps({'scope': 'fixed P1 only; static candidate call sites',
                      'targets': records}, indent=2))


if __name__ == '__main__':
    main()
