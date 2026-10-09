import copy

import pytest

from multimodalcode.vsv_eval.text_proxy import validate, batch_payload, batches, repair_packets
from multimodalcode.vsv_eval.check_stages import validate_stage
from multimodalcode.vsv_eval.evaluation import finalize_evaluation, build_packet
from multimodalcode.io import write_json


def packet():
    return {'episode_id': 'e', 'core_event_ids': [1, 2, 3], 'context_event_ids': [],
            'recorded_image_event_ids': [2], 'eligible_diagnosis_event_ids': [3],
            'image_order': [], 'rule_facts': [],
            'events': [{'ordinal': 1, 'kind': 'action', 'tool': 'Read', 'payload': {'file_path': '/tmp/view.png'}},
                       {'ordinal': 2, 'kind': 'observation', 'images': [{'path': '/missing/view.png'}]},
                       {'ordinal': 3, 'kind': 'model_text', 'text': 'The button is obscured.'}]}


def test_proxy_missing_image_uses_report_but_strict_validator_still_requires_pixels():
    p = packet(); original = copy.deepcopy(p)
    cat = {'checks': [{'check_id': 'button', 'criterion': 'The button is visible.'}]}
    row = {'target': 'Button visibility', 'check_id': 'button', 'coverage': 'full',
           'check_event_id': '1',
           'evidence_ids': ['1', '2', '3'], 'evidence_basis': 'agent_report'}
    result = validate('vc', {'e': {'targets': [row]}}, {'e': p}, cat)['e'][0]
    assert result['modality'] == 'visual' and result['evidence_basis'] == 'agent_report'
    strict = {k: v for k, v in result.items() if k not in ('target_id', 'evidence_basis')}
    with pytest.raises(ValueError, match='pixels'):
        validate_stage('VC', {'targets': [strict]}, p, cat)
    assert p == original


def test_proxy_rejects_future_diagnosis_and_invented_evidence():
    p = packet()
    row = {'actual_state': 'fail', 'agent_state': 'fail', 'issue_match': True,
           'evidence_basis': 'agent_report', 'evidence_ids': ['2', '3'], 'diagnosis_ids': ['3']}
    assert validate('bda', {'t': row}, {'t': p}, {'checks': []})['t']['issue_match']
    p['events'].append({'ordinal': 4, 'kind': 'observation', 'text': 'Fixed later.'})
    row['evidence_ids'] = ['4']
    with pytest.raises(ValueError, match='Future'):
        validate('bda', {'t': row}, {'t': p}, {'checks': []})
    row['evidence_ids'] = ['999']
    with pytest.raises(ValueError, match='invented'):
        validate('bda', {'t': row}, {'t': p}, {'checks': []})
    with pytest.raises(ValueError, match='item keys'):
        validate('bda', {}, {'t': p}, {'checks': []})


def test_unavailable_does_not_default_to_success():
    p = packet()
    for stage, row in [('cv', {'method_ok': True, 'evidence_ok': True}),
                       ('rs', {'applicable': True, 'after': 'pass'}),
                       ('cp', {'before': 'pass', 'after': 'pass'})]:
        row.update(evidence_ids=[], evidence_basis='unavailable')
        with pytest.raises(ValueError, match='Unsupported'):
            validate(stage, {'t': row}, {'t': p}, {'checks': []})


@pytest.mark.parametrize('stage', ['vc', 'coverage'])
@pytest.mark.parametrize('coverage', ['full', 'partial', 'none'])
def test_unavailable_coverage_is_not_a_supported_positive_or_negative(stage, coverage):
    row = {'coverage': coverage, 'evidence_ids': ['1', '2'], 'evidence_basis': 'unavailable'}
    if stage == 'vc':
        row.update(target='Button visibility', check_id=None, check_event_id='1')
    value = {'t': {'targets': [row]} if stage == 'vc' else row}
    with pytest.raises(ValueError, match='unknown coverage'):
        validate(stage, value, {'t': packet()}, {'checks': []})
    row['coverage'] = 'unknown'
    assert validate(stage, value, {'t': packet()}, {'checks': []})


def test_batch_ledger_deduplicates_events_and_separates_diagnosis_rounds():
    p = packet(); q = {**p, 'episode_id': 'later'}
    common = {'task': 'Check the page.'}
    payload = batch_payload(common, {'t1': p, 't2': p})
    assert len(payload['events']) == 3 and 'events' not in payload['items']['t1']
    assert len(list(batches({'t1': p, 't2': p, 't3': q}, common, 10000, separate_rounds=True))) == 2
    with pytest.raises(ValueError, match='budget'):
        list(batches({'t1': p}, common, 10))


def test_shared_edit_and_recheck_are_counted_once():
    p = packet(); later = {'episode_id': 'later', 'core_event_ids': [5, 6],
                          'events': [{'ordinal': 5, 'kind': 'action'}, {'ordinal': 6, 'kind': 'observation'}]}
    episodes = [{'episode_id': name, 'core_event_ids': [1, 2, 3],
                 'repair_links': [{'repair_event_ids': [4], 'recheck_episode_ids': ['later'], 'evidence_event_ids': [3]}]}
                for name in ('a', 'b')]
    episodes.append({'episode_id': 'later', 'core_event_ids': [5, 6], 'repair_links': []})
    rounds = {'episodes': episodes, 'events': p['events'] + [{'ordinal': 4, 'kind': 'action', 'tool': 'Edit'}] + later['events']}
    targets = {name: [{'target_id': name, 'target': 'Button', 'check_id': 'button', 'modality': 'visual', 'actual_state': 'fail'}]
               for name in ('a', 'b')}
    rs, cp, transitions = repair_packets(rounds, {'a': p, 'b': p, 'later': later}, targets, {'checks': [{'check_id': 'button'}]})
    assert len(transitions) == len(cp) == 1 and len(rs) == 2
    assert transitions[0]['recheck_episode_ids'] == ['later']
    episodes[0]['repair_links'][0]['recheck_episode_ids'] = ['a']
    with pytest.raises(ValueError, match='post-edit'):
        repair_packets(rounds, {'a': p, 'b': p, 'later': later}, targets, {'checks': []})


def test_proxy_scores_cannot_enter_strict_finalize(tmp_path):
    source = tmp_path/'proxy.json'
    write_json(source, {'schema': 'self-verification-text-proxy-1', 'metrics': {'BDA': {'score': 100}}})
    with pytest.raises(ValueError, match='protocol check results'):
        finalize_evaluation(source, tmp_path/'strict')


def test_proxy_packet_does_not_load_image_pixels(tmp_path, monkeypatch):
    p = packet()
    image = tmp_path/'image.png'; image.write_bytes(b'not an image')
    p['events'][1]['images'][0]['path'] = str(image)
    rounds = {'source_run': str(tmp_path/'run.json'), 'events': p['events'], 'episodes': []}
    episode = {'core_event_ids': [1, 2, 3], 'context_event_ids': [], 'judgment_event_ids': [3], 'episode_id': 'e'}
    cat = {'checks': [], 'references': {}, 'criteria_sha256': 'hash'}
    result, images = build_packet(rounds, episode, 3, 'task', cat, [], tmp_path, attach_images=False)
    assert images == [] and result['recorded_image_event_ids'] == [2]
    assert result['missing_images'][0]['agent_received_image'] is True


def test_proxy_display_retains_unresolved_denominator():
    from multimodalcode.vsv_eval.text_proxy import metric_display
    from multimodalcode.vsv_eval.metrics import proportion, diagnosis_summary
    assert metric_display('CP', proportion([True, None])) == '50.00–100.00'
    one_class = diagnosis_summary([{'actual_state': 'pass', 'agent_state': 'pass', 'issue_match': None}])
    assert metric_display('BDA', one_class) == 'N/A (missing class)'


def test_proxy_normalizes_duplicate_references_without_changing_labels():
    p = packet()
    row = {'method_ok': True, 'evidence_ok': None, 'evidence_ids': ['1', '1', '2'], 'evidence_basis': 'unavailable'}
    result = validate('cv', {'t': row}, {'t': p}, {'checks': []})['t']
    assert result['evidence_ids'] == [1, 2] and result['method_ok'] is True and result['evidence_ok'] is None
    assert row['evidence_ids'] == ['1', '1', '2']


def test_proxy_ignores_inapplicable_symptom_match_without_changing_states():
    p = packet()
    row = {'actual_state': 'pass', 'agent_state': 'pass', 'issue_match': True,
           'evidence_basis': 'agent_report', 'evidence_ids': ['2', '3'], 'diagnosis_ids': ['3']}
    result = validate('bda', {'t': row}, {'t': p}, {'checks': []})['t']
    assert result['issue_match'] is None and result['actual_state'] == result['agent_state'] == 'pass'
    assert row['issue_match'] is True


def test_proxy_preserves_known_baseline_when_post_edit_state_is_unavailable():
    row = {'before': 'pass', 'after': 'unknown', 'evidence_ids': ['2'], 'evidence_basis': 'unavailable'}
    result = validate('cp', {'c': row}, {'c': packet()}, {'checks': []})['c']
    assert result['before'] == 'pass' and result['after'] == 'unknown'


def test_request_accounting_follows_shared_cache_without_double_counting(tmp_path):
    import json
    from multimodalcode.vsv_eval.judge import write_model_call_report
    shared = tmp_path/'shared'; shared.mkdir()
    row = {'stage': 'proxy_cv', 'started_at': '2026-10-05T00:00:00Z', 'api_requests': 1,
           'cache_hit': False, 'status': 'response_received'}
    (shared/'model_calls.jsonl').write_text(json.dumps(row)+'\n')
    output = tmp_path/'new_run'; output.mkdir(); (output/'judge_cache').symlink_to(shared, target_is_directory=True)
    assert write_model_call_report(output)['api_requests'] == 1
    assert write_model_call_report(tmp_path)['api_requests'] == 1


def test_proxy_augmentation_keeps_known_strict_labels_and_only_fills_gaps(tmp_path, monkeypatch):
    import hashlib
    import json
    import multimodalcode.vsv_eval.text_proxy as proxy
    p = packet()
    episode = {'episode_id': 'e', 'core_event_ids': [1,2,3], 'context_event_ids': [],
               'judgment_event_ids': [3], 'evidence_states': [], 'repair_links': [], 'relation_annotation_complete': True}
    rounds = {'source_run': str(tmp_path/'run.json'), 'events': p['events'], 'episodes': [episode],
              'case_id': 'case', 'source_run_sha256': 'source', 'annotation_status': 'llm_annotated'}
    catalogue = {'checks': [{'check_id': 'a', 'criterion': 'A'}, {'check_id': 'b', 'criterion': 'B'}],
                 'references': {}, 'criteria_sha256': 'criteria', 'task_sha256': hashlib.sha256(b'task').hexdigest()}
    target = {'target_id': 'a', 'target': 'A', 'check_id': 'a', 'modality': 'text', 'coverage': 'partial',
              'evidence_ids': [1,2], 'method_ok': True, 'evidence_ok': False,
              'actual_state': 'pass', 'agent_state': 'absent', 'issue_match': None, 'diagnosis_ids': []}
    unknown = {**target, 'target_id': 'b', 'target': 'B', 'check_id': 'b', 'coverage': 'unknown',
               'evidence_ok': None, 'actual_state': 'unknown', 'agent_state': 'fail', 'diagnosis_ids': [3]}
    baseline = tmp_path/'strict'; baseline.mkdir()
    write_json(baseline/'scores.json', {'schema': 'self-verification-evaluation-1',
                                     'episodes': [{'episode_id': 'e', 'targets': [target, unknown]}]})
    before = (baseline/'scores.json').read_bytes()
    (tmp_path/'prompt.txt').write_text('task')
    monkeypatch.setattr(proxy, 'load_rounds', lambda path: rounds)
    monkeypatch.setattr(proxy, 'load_catalogue', lambda *a, **k: catalogue)
    monkeypatch.setattr(proxy, 'load_judge_config', lambda path: {})
    monkeypatch.setattr(proxy, 'validate_saved_episode', lambda row, *a: row)
    def stage(name, packets, *args):
        if name in ('vc','rs','cp'):
            assert packets == {}; return {}, []
        assert set(packets) == {'e:b'}
        if name == 'coverage':
            return {'e:b': {'coverage': 'full', 'evidence_ids': [2,3], 'evidence_basis': 'agent_report'}}, []
        if name == 'cv':
            return {'e:b': {'method_ok': False, 'evidence_ok': True, 'evidence_ids': [2], 'evidence_basis': 'text_inferred'}}, []
        assert packets['e:b']['fixed_diagnosis'] == {'state': 'fail', 'event_ids': [3]}
        return {'e:b': {'actual_state': 'fail', 'agent_state': 'fail', 'issue_match': True,
                        'diagnosis_ids': [3], 'evidence_ids': [2,3], 'evidence_basis': 'agent_report'}}, []
    monkeypatch.setattr(proxy, 'judge_stage', stage)
    result = proxy.evaluate_text_proxy(tmp_path/'rounds.json', tmp_path/'catalogue.json', tmp_path,
                                      tmp_path/'config.json', tmp_path/'proxy', reuse_checks=baseline)
    known, filled = result['episodes'][0]['targets']
    assert known['coverage'] == 'partial' and known['evidence_ok'] is False and known['agent_state'] == 'absent'
    assert filled['coverage'] == 'full' and filled['method_ok'] is True and filled['actual_state'] == 'fail'
    assert result['metrics']['BDA']['score'] == 50
    assert (baseline/'scores.json').read_bytes() == before


def test_known_error_class_with_unresolved_match_still_has_bda_bounds():
    from multimodalcode.vsv_eval.text_proxy import metric_display
    from multimodalcode.vsv_eval.metrics import diagnosis_summary
    row = diagnosis_summary([{'actual_state': 'pass', 'agent_state': 'pass', 'issue_match': None},
                             {'actual_state': 'fail', 'agent_state': 'fail', 'issue_match': None}])
    assert metric_display('BDA', row) == '50.00–100.00'


def test_preservation_uses_prior_checks_outside_the_defect_round():
    p = packet()
    events = [{'ordinal': 0, 'kind': 'observation', 'text': 'Footer links present.'}] + p['events'] + [
        {'ordinal': 4, 'kind': 'action', 'tool': 'Edit', 'category': 'edit'}]
    earlier = {'episode_id': 'earlier', 'core_event_ids': [0], 'repair_links': []}
    defect = {'episode_id': 'e', 'core_event_ids': [1,2,3],
              'repair_links': [{'repair_event_ids': [4], 'recheck_episode_ids': [], 'evidence_event_ids': []}]}
    rounds = {'events': events, 'episodes': [earlier, defect]}
    targets = {'earlier': [{'check_id': 'footer'}], 'e': []}
    _, cp, _ = repair_packets(rounds, {'earlier': {'events': [events[0]]}, 'e': p}, targets,
                              {'checks': [{'check_id': 'footer'}]})
    entry = cp['repair-000:footer']
    assert entry['baseline_episode_id'] == 'earlier'
    assert 0 in [e['ordinal'] for e in entry['events']]
    assert entry['intervening_recorded_edit_ids'] == []
