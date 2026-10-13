import copy
from types import SimpleNamespace

import pytest

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.catalogue import content_hash
from multimodalcode.vsv_eval.scope import locate_scope, scope_packet, scoped_rounds, validate_saved_scope


def example():
    call = {'ordinal': 4, 'kind': 'action', 'tool_call_id': 'write',
            'payload': {'file_path': 'app.py', 'content': 'print(42)'}}
    result = {'ordinal': 5, 'kind': 'observation', 'payload': {'tool_use_id': 'write'},
              'text': 'File written.', 'source_action_payload': copy.deepcopy(call['payload'])}
    return {'source_run_sha256': 'source', 'events': [call, result], 'episodes': []}


def test_scope_removes_only_exact_duplicate_call_payloads():
    rounds = example()
    original = copy.deepcopy(rounds)
    packet = scope_packet(rounds, 'Write a program.')
    assert packet['events'][0] == rounds['events'][0]
    assert 'source_action_payload' not in packet['events'][1]
    assert rounds == original
    rounds['events'][1]['source_action_payload']['content'] = 'different source'
    assert 'source_action_payload' in scope_packet(rounds, 'task')['events'][1]


def test_boundary_annotation_is_cached_with_source_provenance(tmp_path):
    rounds = example()
    calls = []

    def client(stage, schema):
        def judge(actual_stage, prompt, images, **kwargs):
            assert actual_stage == stage == 'implementation_boundary'
            assert images == [] and schema['properties']['initial_implementation_event_id']['enum'] == [4, 5, None]
            calls.append(stage)
            key = content_hash(prompt)
            record = {'stage': stage, 'request_sha256': key, 'prompt': prompt,
                      'parsed': {'initial_implementation_event_id': 5}}
            write_json(tmp_path / f'{key}.json', record)
            return record
        return SimpleNamespace(judge=judge, cache_root=tmp_path)

    scope = locate_scope(rounds, 'task', client, tmp_path)
    validate_saved_scope(scope, rounds, 'task')
    assert calls == ['implementation_boundary']
    assert read_json(tmp_path / 'scope.json') == scope
    rounds['events'][0]['payload']['content'] = 'changed'
    with pytest.raises(ValueError, match='differs'):
        validate_saved_scope(scope, rounds, 'task')


def test_scope_budget_never_truncates_implementation_code(tmp_path):
    def client(*args):
        pytest.fail('Over-budget input reached the model')
    with pytest.raises(ValueError, match='budget'):
        locate_scope(example(), 'task', client, tmp_path, max_input_chars=10)


def test_absent_implementation_has_no_scored_rounds():
    rounds = example()
    rounds['episodes'] = [{'episode_id': 'construction', 'core_event_ids': [4, 5]}]
    result = scoped_rounds(rounds, {'initial_implementation_event_id': None})
    assert result['episodes'] == [] and result['events'] == rounds['events']
