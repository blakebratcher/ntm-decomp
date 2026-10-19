import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import semantic_match_probe


def test_probe_scenario_uses_inputs_only():
    text = semantic_match_probe.scenario_text()
    assert 'PLAY\t2\t60000\ttwop=1' in text
    assert 'PRESS' in text
    assert 'POKE' not in text
    assert 'WRITE' not in text


def test_zero_samples_do_not_confirm_bug(tmp_path):
    path = tmp_path / 'match-registers.tsv'
    path.write_text('pc\ta1\ta2\ta3\ta6\ta1_index\ta1_shot\ta2_done\n')
    assert semantic_match_probe.summarize(path)['stale_player_pointer_observed'] is False


def test_probe_requires_execution_sample_at_actor_entry(tmp_path):
    path = tmp_path / 'match-registers.tsv'
    path.write_text('pc\ta1\ta2\ta3\ta6\ta1_index\ta1_shot\ta2_done\n'
                    '06cb66\t007313\t10702a\t06cbae\t100760\t85\t85\tff\n')
    assert semantic_match_probe.summarize(path)['stale_player_pointer_observed'] is False
    with path.open('a') as stream:
        stream.write('06cb30\t007313\t10702a\t06cbae\t100760\t85\t85\tff\n')
    assert semantic_match_probe.summarize(path)['stale_player_pointer_observed'] is True
