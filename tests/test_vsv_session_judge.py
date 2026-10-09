"""In-session annotations use the same request identity and stage validators."""
import json

import pytest

from multimodalcode.vsv_eval.judge import JudgeClient, JudgeProfile


def test_session_annotations_are_bound_to_inputs_and_never_use_http(tmp_path, monkeypatch):
    judge = JudgeClient(JudgeProfile('session', 'in-session', 'codex-in-session', '', ''), tmp_path)
    monkeypatch.setattr(judge, '_request', lambda *a: pytest.fail('Network transport invoked'))
    with pytest.raises(FileNotFoundError, match='annotation required'):
        judge.judge('verification_filter', 'packet', [])
    request_path = next(tmp_path.glob('*.request.json'))
    request = json.loads(request_path.read_text())
    answer_path = request_path.with_name(request_path.name.replace('.request', '.annotation'))
    answer = {k: request[k] for k in ('request_sha256', 'model')}
    answer['parsed'] = {'keep_ids': ['window-4']}
    answer_path.write_text(json.dumps(answer))
    response = judge.judge('verification_filter', 'packet', [])
    assert response['parsed'] == answer['parsed']
    assert response['api_requests'] == response['request_attempts'] == 0
    assert response['response_metadata']['provenance'] == 'in_session_annotation'
    assert response['response_metadata']['independent_validation'] is False
    assert judge.judge('verification_filter', 'packet', []) == response
    with pytest.raises(FileNotFoundError):
        judge.judge('verification_filter', 'different packet', [])


@pytest.mark.parametrize('field,value', [('request_sha256', 'other'), ('model', 'other'), ('parsed', [])])
def test_session_rejects_mismatched_or_invalid_annotations(tmp_path, field, value):
    judge = JudgeClient(JudgeProfile('session', 'in-session', 'codex-in-session', '', ''), tmp_path)
    with pytest.raises(FileNotFoundError):
        judge.judge('VC', 'packet', [])
    p = next(tmp_path.glob('*.request.json'))
    request = json.loads(p.read_text())
    answer = {k: request[k] for k in ('request_sha256', 'model')}
    answer['parsed'] = {'targets': []}
    answer[field] = value
    p.with_name(p.name.replace('.request', '.annotation')).write_text(json.dumps(answer))
    with pytest.raises(ValueError, match='Invalid in-session annotation'):
        judge.judge('VC', 'packet', [])
