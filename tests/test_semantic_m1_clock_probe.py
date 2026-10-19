import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import semantic_m1_clock_probe


def test_timer_a_period_uses_ten_bit_value_and_opn_prescaler():
    assert semantic_m1_clock_probe.timer_period() == pytest.approx(47952 / 8000000)
    assert 1 / semantic_m1_clock_probe.timer_period() == pytest.approx(166.8335001668)


def test_irq_sample_summary_preserves_gaps(tmp_path):
    path = tmp_path / 'irq.tsv'
    period = semantic_m1_clock_probe.timer_period()
    path.write_text('seconds\tpc\tvalue\n' +
                    '\n'.join(f'{time:.12f}\t1e98\t01' for time in (0, period, period * 2, 1)))
    result = semantic_m1_clock_probe.summarize(path)
    assert result['samples'] == 4
    assert result['long_gaps'] == 1
    assert result['median_interval_seconds'] == pytest.approx(period)
    assert result['total_span_seconds'] == 1


def test_insufficient_or_nonmonotonic_samples_are_not_evidence(tmp_path):
    path = tmp_path / 'irq.tsv'
    path.write_text('seconds\tpc\tvalue\n')
    with pytest.raises(ValueError, match='two IRQ'):
        semantic_m1_clock_probe.summarize(path)
    path.write_text('seconds\tpc\tvalue\n1.0\t1e98\t01\n0.0\t1e98\t02\n')
    with pytest.raises(ValueError, match='increasing'):
        semantic_m1_clock_probe.summarize(path)
