import json
from pathlib import Path

import pytest

from multimodalcode.vsv_eval.repair_pilot import extract_run_code, reconstruct, repair_outcome, regression_result, functional_pass

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'data/vision2web/vsv_eval_fixtures/frontend__smartrecruiters__claude-opus-4-8__self_verify'


def test_real_reconstruction(tmp_path):
    spec = json.loads((ROOT / 'configs/vision2web/smartrecruiters_repair_pilot.json').read_text())
    result = reconstruct(FIXTURE, spec, tmp_path / 'versions')
    assert len(result['versions']) == 4
    assert len(result['final_touched_files_match']) == 6
    assert all(result['final_touched_files_match'].values())
    assert [len(v['edits']) for v in result['versions']] == [0, 4, 7, 8]
    assert len({v['code_manifest']['sha256'] for v in result['versions']}) == 4


def test_real_script_extraction():
    rows = json.loads((FIXTURE / 'trajectory/run.json').read_text())['timeline']
    for ordinal in [299,302,305,310,313]:
        body = extract_run_code(next(r for r in rows if r['ordinal']==ordinal)['payload']['command'])
        assert body.startswith('async page =>')
        assert r'\$' not in body
        assert 'return JSON.stringify' in body


def test_outcome_requires_before_failure():
    assert repair_outcome(False, True) == 'fixed'
    assert repair_outcome(True, True) == 'no_reproduced_failure'
    assert repair_outcome(False, False) == 'not_fixed'
    assert repair_outcome(True, False) == 'regression'
    assert repair_outcome(None, True) == 'insufficient_evidence'


def test_regressions_require_passing_baseline():
    assert regression_result({'a':False},{'a':False})['status']=='insufficient_evidence'
    assert regression_result({'a':True},{'a':False})['status']=='regression'
    assert regression_result({'a':True},{'a':None})['status']=='insufficient_evidence'
    assert regression_result({'a':True},{'a':True})['status']=='no_observed_regression'
    assert not functional_pass('careers_filter_search', {'engineeringCount':0,'allEngineering':True,'accountMatches':2})


def test_generic_replay_does_not_credit_already_working_target():
    from multimodalcode.vsv_eval.replay import compare_replays
    plans = [{'kind':'target','status':'pass','assertions':[{'rule':{'type':'text_visible','value':'Buy'},'passed':True}]}]
    value = {'plans':plans,'mechanical_execution_passed':True}
    assert compare_replays('x',value,value)['episodes']['x']['target']['fixed'] is None


def test_configured_assertions_are_not_tied_to_case_names():
    from multimodalcode.vsv_eval.repair_pilot import assess_output
    rules = [{'path': 'cart.count', 'op': 'gt', 'other_path': 'initial'}, {'path': 'url', 'op': 'endswith', 'value': '/cart'}]
    assert assess_output(rules, {'cart': {'count': 2}, 'initial': 1, 'url': '/cart'}) is True
    assert assess_output(rules, {'cart': {'count': 0}, 'initial': 1, 'url': '/cart'}) is False
    assert assess_output(rules, {'cart': {}, 'initial': 1, 'url': '/cart'}) is None
    assert assess_output(rules, {'cart': {'count': 'two'}, 'initial': 1, 'url': '/cart'}) is None
    assert assess_output([], {'ok': True}) is None


def test_new_assertions_match_historical_twenty_actual_execution_outputs():
    from multimodalcode.vsv_eval.repair_pilot import assess_output
    spec = json.loads((ROOT / 'configs/vision2web/smartrecruiters_repair_v2.json').read_text())
    for name in ('V0', 'V1', 'V2', 'V3'):
        replay = json.loads((ROOT / f'runs/vsv_eval/smartrecruiters-full-0907/repair/replays/{name}/result.json').read_text())
        for row in replay['checks']:
            rule = next(c for c in spec['functional_checks'] if c['name'] == row['name'])
            value = json.loads(row['output']) if isinstance(row['output'], str) else row['output']
            assert assess_output(rule['assertions'], value) is row['passed']


def test_verified_version_copies_and_explicit_repair_records(tmp_path):
    from multimodalcode.vsv_eval.repair_pilot import prepare_versions, repair_records, digest_tree
    spec = json.loads((ROOT / 'configs/vision2web/smartrecruiters_repair_v2.json').read_text())
    timeline = json.loads((FIXTURE / 'trajectory/run.json').read_text())['timeline']
    if not Path(spec['version_source']['path']).is_file():
        pytest.skip('Archived verified version manifest unavailable on this machine')
    result = prepare_versions(FIXTURE, spec, tmp_path / 'versions')
    records = repair_records(spec, result, timeline)
    assert len(result['versions']) == 4 and len(records) == 5
    assert all(digest_tree(Path(r['workspace']) / 'app') == r['code_manifest'] for r in result['versions'])
    assert all(Path(r['workspace']).is_relative_to(tmp_path) for r in result['versions'])
    assert all(r['source_action_ordinals'] and r['source_observation_ordinals'] and r['regression_checks'] for r in records)
    assert next(r for r in records if r['policy_event'] == 267)['trigger_kind'] == 'action'
    assert next(r for r in records if r['policy_event'] == 307)['trigger_kind'] == 'model_text'


def test_repair_records_reject_reversed_versions_and_future_feedback():
    import copy
    import pytest
    from multimodalcode.vsv_eval.repair_pilot import repair_records
    spec = json.loads((ROOT / 'configs/vision2web/smartrecruiters_repair_v2.json').read_text())
    if not Path(spec['version_source']['path']).is_file():
        pytest.skip('Archived verified version manifest unavailable on this machine')
    versions = json.loads(Path(spec['version_source']['path']).read_text())
    timeline = json.loads((FIXTURE / 'trajectory/run.json').read_text())['timeline']
    bad = copy.deepcopy(spec)
    bad['targets'][0].update(before='V3', after='V0')
    with pytest.raises(ValueError, match='reversed'):
        repair_records(bad, versions, timeline)
    bad = copy.deepcopy(spec)
    bad['targets'][0]['source_observation_ordinals'] = [252]
    with pytest.raises(ValueError, match='precede'):
        repair_records(bad, versions, timeline)
    bad = copy.deepcopy(spec)
    bad['targets'][0].update(before='V1', after='V1')
    with pytest.raises(ValueError, match='changed code'):
        repair_records(bad, versions, timeline)


def test_reverse_edits_and_audited_resource_placement(tmp_path):
    import hashlib
    from multimodalcode.vsv_eval.states import artifact_identity
    for folder in ('trajectory/development/versions/P_first/app',
                   'trajectory/development/versions/P_final/app', 'agent_visible/resources'):
        (tmp_path/folder).mkdir(parents=True)
    for name in ('P_first', 'P_final'):
        (tmp_path/f'trajectory/development/versions/{name}/app/index.html').write_text('new')
    command = 'move resources'
    timeline = [
        {'ordinal': 1, 'kind': 'action', 'tool': 'Bash', 'tool_call_id': 'move', 'payload': {'command': command}},
        {'ordinal': 2, 'kind': 'observation', 'payload': {'tool_use_id': 'move'}},
        {'ordinal': 3, 'kind': 'action', 'tool': 'Edit', 'tool_call_id': 'edit',
         'payload': {'file_path': '/workspace/app/index.html', 'old_string': 'old', 'new_string': 'new'}},
        {'ordinal': 4, 'kind': 'observation', 'payload': {'tool_use_id': 'edit'}}]
    (tmp_path/'trajectory/run.json').write_text(json.dumps({'timeline': timeline}))
    spec = {'base_before_event': 1, 'edit_ordinals': [1, 3], 'boundaries': [1, 3], 'version_source': {
        'reverse_edit_ordinals': [3], 'resource_path': 'public/resources', 'resource_moves': [
            {'ordinal': 1, 'from': 'public/resources', 'to': 'resources',
             'command_sha256': hashlib.sha256(command.encode()).hexdigest()}]}}
    result = reconstruct(tmp_path, spec, tmp_path/'reconstructed')
    assert [(Path(v['workspace'])/'app/index.html').read_text() for v in result['versions']] == ['old', 'old', 'new']
    assert [v['resource_path'] for v in result['versions']] == ['public/resources', 'resources', 'resources']
    assert (tmp_path/'trajectory/development/versions/P_first/app/index.html').read_text() == 'new'
    assert result['versions'][0]['code_manifest'] == result['versions'][1]['code_manifest']
    assert artifact_identity(result['versions'][0], {}) != artifact_identity(result['versions'][1], {})
    spec['version_source']['resource_moves'][0]['command_sha256'] = 'invalid'
    with pytest.raises(ValueError, match='audited command'):
        reconstruct(tmp_path, spec, tmp_path/'invalid')
