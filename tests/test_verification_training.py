"""Training contracts that do not require either distributed training framework."""
import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest

from multimodalcode.io import write_json
from multimodalcode.training.handoff import (
    export_rl, export_swift, load_checkpoint, make_checkpoint, validate_messages, validate_native_output, validate_splits,
)
from multimodalcode.training.reward import (
    VerificationReward, diagnosis_reward, evidence_reward, objective_weights, repair_reward, trajectory,
)
from multimodalcode.vsv_eval.catalogue import content_hash


def checkpoint(tmp_path, **kwargs):
    image = tmp_path / 'observation.png'
    image.write_bytes(b'fixture bytes')
    defaults = dict(task_id='training-fixture', benchmark='web-fixture', kind='diagnosis', split='train',
                    policy_revision='student-r0', messages=[{'role': 'user', 'content': 'Build the page.'},
                    {'role': 'assistant', 'content': 'I will inspect the implementation.'},
                    {'role': 'user', 'content': [{'type': 'image', 'image': str(image)},
                                                {'type': 'text', 'text': 'Observed implementation.'}]}])
    defaults.update(kwargs)
    return make_checkpoint(**defaults)


def audit(row, suffix, approved):
    return {'checkpoint_id': row['id'], 'suffix_sha256': content_hash(suffix), 'status': 'evaluated',
            'evidence_refs': ['fixture-evaluation.json'], 'image_sha256': {}, 'approved_message_indices': approved}


def test_suffix_mask_preserves_prefix_pixels_and_only_reviewed_turns(tmp_path):
    row = checkpoint(tmp_path)
    suffix = [{'role': 'assistant', 'content': 'The primary action is missing.'},
              {'role': 'user', 'content': 'Continue.'},
              {'role': 'assistant', 'content': 'An unreviewed claim.'}]
    original = copy.deepcopy((row, suffix))
    exported = export_swift(row, suffix, audit(row, suffix, [0]))
    assert [m['loss'] for m in exported['messages'] if m['role'] == 'assistant'] == [False, True]
    assert exported['images'] == [str(tmp_path / 'observation.png')]
    assert exported['messages'][2]['content'].startswith('<image>')
    assert exported['messages'][-1]['content'] == suffix[0]['content']
    assert (row, suffix) == original
    assert all('loss' not in m for m in exported['messages'] if m['role'] != 'assistant')


def test_handoff_requires_real_images_and_complete_tool_returns(tmp_path):
    row = checkpoint(tmp_path)
    path = tmp_path / 'handoff.json'
    write_json(path, row)
    assert load_checkpoint(path) == row
    (tmp_path / 'observation.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='image contents changed'):
        load_checkpoint(path)
    with pytest.raises(ValueError, match='pair'):
        validate_messages([{'role': 'tool', 'tool_call_id': 'invented', 'content': 'ok'}], handoff=True)
    with pytest.raises(ValueError, match='restorable'):
        checkpoint(tmp_path, kind='repair')
    with pytest.raises(ValueError, match='complete user/tool'):
        checkpoint(tmp_path, messages=[{'role': 'user', 'content': 'Task'}, {'role': 'assistant', 'content': 'Hypothesis'}])


def test_export_refuses_changed_review_and_split_leakage(tmp_path):
    row = checkpoint(tmp_path)
    suffix = [{'role': 'assistant', 'content': 'Correct acceptance.'}]
    review = audit(row, suffix, [0])
    suffix[0]['content'] = 'Changed claim'
    with pytest.raises(ValueError, match='does not match'):
        export_swift(row, suffix, review)
    with pytest.raises(ValueError, match='crosses'):
        validate_splits([row, {**row, 'split': 'test'}])
    with pytest.raises(ValueError, match='training tasks'):
        export_swift({**row, 'split': 'test'}, suffix, audit(row, suffix, [0]))
    path = tmp_path / 'checkpoint.json'
    write_json(path, row)
    with pytest.raises(ValueError, match='Refresh'):
        export_rl(path, expected_policy_revision='student-r1')
    exported = export_rl(path, expected_policy_revision='student-r0')
    assert json.loads(exported['prompt']) == row['messages']


def test_coverage_deduplicates_and_excludes_old_or_unattempted_conditions():
    target = {'check_id': 'new', 'coverage': 'full', 'method_ok': True, 'evidence_ok': True}
    result = evidence_reward([target, target, {**target, 'check_id': 'old'},
                              {**target, 'check_id': 'missed', 'check_attempted': False}], ['new', 'old', 'missed'], ['old'])
    assert result['score'] == .5
    assert result['new_check_ids'] == ['new']
    with pytest.raises(ValueError, match='incomplete'):
        evidence_reward([{**target, 'evidence_ok': None}], ['new'])
    assert evidence_reward([{**target, 'coverage': 'none', 'method_ok': False, 'evidence_ok': False}], ['new'])['score'] == 0
    with pytest.raises(ValueError, match='No uncovered'):
        evidence_reward([], ['new'], ['new'])


def test_diagnosis_uses_present_class_mean_and_scores_missing_judgments():
    normal = {'actual_state': 'pass', 'agent_state': 'pass', 'issue_match': None}
    missed = {'actual_state': 'fail', 'agent_state': 'absent', 'issue_match': None}
    assert diagnosis_reward([normal] * 9 + [missed])['score'] == .5
    assert diagnosis_reward([{**missed, 'agent_state': 'fail', 'issue_match': True}])['score'] == 1
    with pytest.raises(ValueError, match='decidable'):
        diagnosis_reward([{**normal, 'actual_state': 'unknown'}])


def test_repair_reward_measures_improvement_regression_and_correct_stop():
    before = {'target': 'fail', 'other': 'pass'}
    fixed = {'target': 'pass', 'other': 'pass'}
    assert repair_reward(before, fixed, ['target'], edited=True)['score'] == 1
    assert repair_reward(before, {**fixed, 'other': 'fail'}, ['target'], edited=True)['score'] == 0
    assert repair_reward(fixed, fixed, ['target'], edited=False)['score'] == 1
    assert repair_reward(fixed, fixed, ['target'], edited=True)['score'] == 0
    assert repair_reward(before, fixed, ['target'], edited=False)['score'] == 0
    with pytest.raises(ValueError, match='incomplete'):
        repair_reward(before, {**fixed, 'other': 'unknown'}, ['target'], edited=True)


def test_joint_weights_preserve_equal_kind_objective_after_grpo():
    kinds = ['task'] * 4 + ['evidence'] * 8 + ['diagnosis'] * 4 + ['repair'] * 4
    weights = objective_weights(kinds, local_weight=.6)
    totals = {k: sum(w for w, v in zip(weights, kinds) if v == k) / len(kinds) for k in set(kinds)}
    assert totals == pytest.approx({'task': 1, 'evidence': .2, 'diagnosis': .2, 'repair': .2})
    with pytest.raises(ValueError, match='every configured'):
        objective_weights(['task', 'diagnosis'])
    assert objective_weights(['diagnosis'] * 4, local_kinds=['diagnosis'], include_task=False) == [1] * 4


def test_native_call_and_return_record_retains_failed_attempt_and_version(tmp_path):
    row = checkpoint(tmp_path)
    suffix = [{'role': 'assistant', 'content': 'Inspect the page.', 'tool_calls': [
        {'id': 'call1', 'function': {'name': 'browser', 'arguments': '{"actions":[]}'}}]},
        {'role': 'tool', 'tool_call_id': 'call1', 'content': 'Timeout'},
        {'role': 'assistant', 'content': 'The check could not run.'}]
    record = {'tool_call_id': 'call1', 'tool': 'browser', 'arguments': {'actions': []}, 'text': 'Timeout',
              'images': [], 'is_error': True, 'program_before_sha256': 'same', 'program_after_sha256': 'same'}
    run, ids = trajectory(row, suffix, [record])
    assert [e['kind'] for e in run['timeline']] == ['model_text', 'action', 'observation', 'model_text']
    assert run['timeline'][1]['tool_call_id'] == run['timeline'][2]['tool_call_id'] == 'call1'
    assert run['timeline'][2]['is_error'] is True
    assert ids[0] == [1, 2]


def test_fixed_diagnosis_reuses_bda_and_rejects_unseen_pixels(tmp_path):
    row = checkpoint(tmp_path)
    packet = {'events': [{'ordinal': 1, 'kind': 'observation', 'text': 'rendered'}],
              'targets': [{'target_id': 'button', 'target': 'Primary action', 'check_id': 'button', 'modality': 'visual'}],
              'observation_verdicts': [{'target_id': 'button', 'actual_state': 'fail',
                    'actual_issue': {'object': 'Primary action', 'symptom': 'Missing'}, 'evidence_ids': [1]}],
              'image_order': [{'role': 'agent_observation', 'event_id': 1, 'path': str(tmp_path / 'observation.png')}],
              'rule_facts': [], 'recorded_image_event_ids': [1]}
    checks = [{'check_id': 'button', 'source_ref': 'task:0', 'setup': 'Inspect the page',
               'criterion': 'Primary action is present', 'required_evidence': 'Screenshot', 'reference_ids': []}]
    catalogue = {'schema': 'self-verification-catalogue-1', 'checks': checks,
                 'source_ids': ['task:0'], 'references': {}, 'review': {'status': 'draft'},
                 'criteria_sha256': content_hash(checks)}
    write_json(tmp_path / 'packet.json', packet)
    write_json(tmp_path / 'catalogue.json', catalogue)
    row['evaluation'] = {'diagnosis_packet': str(tmp_path / 'packet.json'), 'catalogue': str(tmp_path / 'catalogue.json'),
                         'observation_source': 'independent-fixture-labels', 'allow_draft': True}
    class Judge:
        cache_root = tmp_path
        def judge(self, stage, prompt, images, **kwargs):
            assert stage == 'visual_bda'
            assert images == [str(tmp_path / 'observation.png')]
            return {'request_sha256': 'fixture', 'parsed': {'targets': [{'target_id': 'button',
                       'agent_state': 'fail', 'issue_match': True, 'diagnosis_ids': [2]}]}}
    evaluator = VerificationReward(row, tmp_path / 'evaluation', lambda *a: Judge())
    result = asyncio.run(evaluator.score([{'role': 'assistant', 'content': 'The primary action is missing.'}], []))
    assert result['score'] == 1
    assert result['targets'][0]['actual_state'] == 'fail'
    assert len(result['requests']) == 1
    hidden = tmp_path / 'hidden.png'
    hidden.write_bytes(b'future image')
    packet['image_order'][0]['path'] = str(hidden)
    write_json(tmp_path / 'packet.json', packet)
    with pytest.raises(ValueError, match='absent from the student'):
        asyncio.run(evaluator.score([{'role': 'assistant', 'content': 'Fine'}], []))


def test_backend_native_history_and_images_use_existing_transport(tmp_path, monkeypatch):
    from multimodalcode.backends import OpenAICompatibleBackend
    import urllib.request
    image = tmp_path / 'image.png'
    image.write_bytes(b'image data')
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'Diagnosis'}}]}).encode()
    def open_request(request, **kwargs):
        captured.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(urllib.request, 'urlopen', open_request)
    messages = [{'role': 'user', 'content': [{'type': 'image', 'image': str(image)}]}]
    original = copy.deepcopy(messages)
    result = OpenAICompatibleBackend('fixture', 'http://unused').generate_messages(messages)
    assert result['choices'][0]['message']['content'] == 'Diagnosis'
    assert captured[0]['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
    assert messages == original


def test_native_tokens_are_checked_without_reencoding():
    from types import SimpleNamespace
    output = SimpleNamespace(response_ids=[11, 23, 88, 99], response_mask=[1, 1, 0, 1],
                             response_logprobs=[-.2, -.3, 0, -.4])
    before = copy.deepcopy(vars(output))
    validate_native_output(output)
    assert vars(output) == before
    output.response_logprobs.pop()
    with pytest.raises(ValueError, match='alignment'):
        validate_native_output(output)


def test_balanced_export_roundtrips_native_images_and_task_splits(tmp_path):
    pytest.importorskip('pandas')
    pytest.importorskip('pyarrow')
    import pandas as pd
    from multimodalcode.training.cli import export_datasets
    entries = []
    for i, split in enumerate(['train', 'validation']):
        row = checkpoint(tmp_path, task_id=f'fixture-{i}', split=split)
        path = tmp_path / f'checkpoint-{i}.json'
        write_json(path, row)
        entries.append({'checkpoint': str(path)})
    manifest = tmp_path / 'manifest.json'
    write_json(manifest, entries)
    counts = export_datasets(manifest, tmp_path / 'rl', mode='rl', policy_revision='student-r0')
    assert counts == {'train': 1, 'validation': 1}
    value = pd.read_parquet(tmp_path / 'rl/train.parquet').iloc[0].to_dict()
    assert json.loads(value['prompt'])[2]['content'][0]['image'] == str(tmp_path / 'observation.png')
    assert value['extra_info']['kind'] == 'diagnosis'


def test_web_restore_replays_interaction_and_keeps_source_isolated(tmp_path):
    pytest.importorskip('playwright')
    from multimodalcode.training.environment import WebEnvironment
    from playwright.sync_api import sync_playwright
    with sync_playwright() as browser:
        executable = Path(browser.chromium.executable_path)
    if not executable.is_file():
        pytest.skip('CPU browser smoke test requires the locally installed Chromium')
    source = tmp_path / 'source'
    source.mkdir()
    original = '<html><body><input id="value"><button>Submit</button></body></html>'
    (source / 'index.html').write_text(original)
    env = WebEnvironment(source, tmp_path / 'first', {'kind': 'static', 'executable_path': str(executable)})
    try:
        result = env.execute('browser', {'actions': [{'type': 'fill', 'target': {'selector': '#value'}, 'value': 'saved'}]}, 'fill')
        assert Path(result['images'][0]).is_file()
        state = env.snapshot(tmp_path / 'snapshot')
    finally:
        env.close()
    restored = WebEnvironment.restore(state, tmp_path / 'restored')
    try:
        assert restored.page.locator('#value').input_value() == 'saved'
        escaped = restored.execute('read_file', {'path': '../../source/index.html'}, 'escape')
        assert escaped['is_error']
        edited = restored.execute('edit_file', {'path': 'index.html', 'old_string': 'Submit', 'new_string': 'Done'}, 'edit')
        assert edited['program_before_sha256'] != edited['program_after_sha256']
        assert (source / 'index.html').read_text() == original
        assert (Path(state['workspace']) / 'index.html').read_text() == original
    finally:
        restored.close()
    with pytest.raises(ValueError, match='could not be reproduced'):
        WebEnvironment.restore({**state, 'state_sha256': 'wrong'}, tmp_path / 'wrong-state')
