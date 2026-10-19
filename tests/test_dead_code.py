"""Proven-dead code (`kind: dead-insn`) must pass the shape proof in tools/dead_code.py."""
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))
import dead_code  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (REPO / 'src').is_dir()
    or not (REPO / 'build' / 'p1-spans.json').exists(),
    reason='source corpus and build/p1-spans.json required')


@pytest.mark.parametrize('cpu', ['p1', 'm1'])
def test_every_dead_insn_source_passes(cpu):
    starts = dead_code.code_starts(cpu)
    spans = dead_code.classified_spans(cpu)
    ran = dead_code.executed(cpu)
    failed = {}
    for src in dead_code.dead_sources(cpu):
        r = dead_code.check(cpu, src, starts, spans, ran)
        if r['problems']:
            failed[r['source']] = r['problems']
    assert not failed, failed


def _fake(tmp_path, org, body):
    p = tmp_path / f'{org:08x}.s'
    p.write_text('; Root evidence: dead: test.\n; kind: dead-insn; range [$0, $0)\n'
                 f' org ${org:x}\n include "symbols.i"\n' + body)
    return p


def test_text_bytes_fail(tmp_path):
    # the "CUP IN" string decodes as illegal words
    p = _fake(tmp_path, 0x957a, '        dc.w $4355,$5020,$494e,$fe00\n')
    r = dead_code.check('p1', p)
    assert any('undecodable' in x for x in r['problems']), r


def test_mid_instruction_branch_fails(tmp_path):
    # bra.s to the middle of its own 4-byte move.w #imm,d1
    p = _fake(tmp_path, 0x540, '        move.w #$1234,d1\n        bra.s *-2\n')
    r = dead_code.check('p1', p)
    assert any('mid-instruction' in x for x in r['problems']), r


def test_open_tail_fails(tmp_path):
    # ends on a plain move whose next address (0x957c) is data, not code
    p = _fake(tmp_path, 0x957a, '        moveq #$0,d0\n')
    assert dead_code.check('p1', p)['problems']


def test_executed_code_is_not_unref(tmp_path):
    # 0x925a is a recorded MAME instruction start: executed code has a root
    p = _fake(tmp_path, 0x925a, '        rts\n')
    r = dead_code.check('p1', p, ran={0x925a})
    assert any('executed' in x for x in r['problems']), r


def test_m1_dead_code_has_no_reference():
    """M1 closed-world value scan: every Z80 transfer target is a 16-bit value
    (jp/call/jr operands, the jp (hl) dispatch word tables, pushed returns);
    no little-endian word at any offset outside the dead bytes themselves
    equals an instruction start of a dead source, and nothing falls in.
    The scan covers the fixed window 0x0000-0x7fff: every jump target comes
    from a code operand or a jp (hl) site there (0x2050, 0x2096, 0x4a30,
    0x4aa8, 0x4ac4 read fixed-ROM word tables; 0x5362 indexes the 0x53a3
    stub array, bytes >= 0xda excluded). Banked bytes are read
    only by the sequence engine as sequence data and sequence pointers
    (tools/semantic_m1_seqwalk.py decodes every reached banked byte), never
    as code addresses; their byte pairs coincide with dead starts by chance
    (seventeen note/operand pairs equal interior starts of 0x540f)."""
    img = (REPO / 'build' / 'work' / 'p1.m1.pristine.bin').read_bytes()
    starts, spans = set(), []
    for src in dead_code.dead_sources('m1'):
        org, data = dead_code._assemble('m1', src)
        spans.append((org, org + len(data)))
        starts |= {a for a, _, _ in dead_code.decode('m1', org, data)}
        # the byte before each source is a one-byte ret (0xc9) or ends a jp
        assert img[org - 1] == 0xc9 or img[org - 3] in (0xc3, 0x18) or img[org - 2] == 0x18, src
    hits = [off for off in range(0x7fff)
            if img[off] | (img[off + 1] << 8) in starts
            and not any(lo <= off < hi for lo, hi in spans)]
    assert not hits, [hex(h) for h in hits]
