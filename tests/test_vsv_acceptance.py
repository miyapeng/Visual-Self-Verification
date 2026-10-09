import copy
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.acceptance import ground_step, validate_execution, validate_workflows
from multimodalcode.vsv_eval.states import state_packet, validate_states
from multimodalcode.vsv_eval.replay import replay_version
from multimodalcode.vsv_eval.repair_pilot import digest_tree
from test_vsv_protocol import catalogue


def workflow():
    return {'workflow_id': 'navigation', 'setup': {'route': '/', 'viewport': {'width': 800, 'height': 600}},
            'nodes': [{'source_ref': 'task:0', 'actions': [
                {'type': 'click', 'target': {'role': 'button', 'name': 'Open'}, 'description': 'Open the panel.'}],
                'checks': {'a': [{'type': 'text_visible', 'value': 'Panel opened'}]}}]}


def test_plans_require_review_preserve_global_ids_and_reject_unknown_rules():
    plan = workflow()
    with pytest.raises(ValueError, match='model review'):
        validate_workflows([plan], catalogue('a'))
    assert validate_workflows([plan], catalogue('a'), model_reviewed=True) == {'a'}
    duplicate = copy.deepcopy(plan); duplicate['workflow_id'] = 'another'
    with pytest.raises(ValueError, match='assigned exactly once'):
        validate_workflows([plan, duplicate], catalogue('a'), model_reviewed=True)
    plan['nodes'][0]['checks']['a'][0]['type'] = 'evaluate_script'
    with pytest.raises(ValueError, match='Unsupported'):
        validate_workflows([plan], catalogue('a'), model_reviewed=True)


def test_grounding_preserves_input_and_only_selects_current_elements(tmp_path):
    packet = {'guided_action': {'type': 'fill', 'value': 'fixed input'},
              'observation': {'evidence_id': 'current', 'screenshot': 'current.png'},
              'controls': [{'element_ref': 'e0'}]}
    value = {'action': 'fill', 'value': 'fixed input', 'element_ref': 'e0', 'observation_ids': ['current']}
    judge = SimpleNamespace(cache_root=tmp_path, judge=lambda *args, **kwargs: {'parsed': value, 'request_sha256': 'request'})
    original = copy.deepcopy(packet)
    assert ground_step(packet, judge, tmp_path/'selection.json')['element_ref'] == 'e0'
    assert packet == original
    for field, invalid in [('value', 'changed'), ('element_ref', 'invented'), ('observation_ids', ['old']), ('action', 'click')]:
        bad = {**value, field: invalid}
        judge.judge = lambda *args, **kwargs: {'parsed': bad, 'request_sha256': 'request'}
        with pytest.raises(ValueError):
            ground_step(packet, judge, tmp_path/'invalid.json')
    assert not (tmp_path/'invalid.json').exists()


def test_model_failure_is_not_an_accepted_blocked_action(tmp_path):
    judge = SimpleNamespace(judge=lambda *args, **kwargs: {'error': 'truncated'})
    with pytest.raises(ValueError, match='truncated'):
        ground_step({'observation': {'screenshot': 'image.png'}}, judge, tmp_path/'selection.json')


def test_execution_validation_rejects_omitted_assertions():
    plan = workflow()
    node = {'evidence_id': 'v:workflow:navigation:node:0', 'run_status': 'completed', 'check_ids': ['a'],
            'actions': [{'action': plan['nodes'][0]['actions'][0], 'run_status': 'completed'}],
            'checks': [{'check_id': 'a', 'assertions': []}]}
    result = {'workflows': [{'workflow_id': 'navigation', 'setup': plan['setup'], 'nodes': [node]}]}
    with pytest.raises(ValueError, match='assertions'):
        validate_execution([plan], result, 'v')
    node['run_status'] = 'blocked'
    node['checks'] = []
    validate_execution([plan], result, 'v')


def test_missing_screenshot_does_not_erase_completed_exact_assertions(tmp_path):
    node = {'evidence_id': 'v:node', 'run_status': 'evaluator_error', 'check_ids': ['a','b'], 'actions': [],
            'checks': [{'check_id': 'a', 'assertions': [{'passed': False}]}, {'check_id': 'b', 'assertions': []}]}
    replay = {'routes': {}, 'checks': [], 'workflows': [{'workflow_id': 'w', 'setup': {}, 'nodes': [node]}]}
    packet, images = state_packet(catalogue('a','b'), {'version':'v'}, replay, {}, tmp_path)
    assert not images
    assert {r['check_id']:r['state'] for r in packet['rule_facts']} == {'a':'fail','b':'unknown'}


def test_workflow_pixels_and_program_states_are_separate_from_agent_observations(tmp_path):
    from PIL import Image
    image = tmp_path/'after-click.png'; Image.new('RGB', (20, 20), 'white').save(image)
    nodes = [
        {'evidence_id': 'v:node:0', 'run_status': 'completed', 'check_ids': ['a', 'b'], 'actions': [],
         'checks': [{'check_id': 'a', 'assertions': [{'passed': False}]}, {'check_id': 'b', 'assertions': []}],
         'observation': {'screenshot': str(image)}},
        {'evidence_id': 'v:node:1', 'run_status': 'blocked', 'check_ids': ['c'], 'actions': [], 'checks': []}]
    replay = {'routes': {}, 'checks': [], 'workflows': [{'workflow_id': 'test', 'setup': {}, 'nodes': nodes}]}
    packet, images = state_packet(catalogue('a', 'b', 'c', 'd'), {'version': 'v'}, replay, {}, tmp_path)
    assert images == [str(image)]
    assert packet['image_order'][0]['origin'] == 'independent'
    assert {r['check_id']: r['state'] for r in packet['rule_facts']} == {'a': 'fail', 'c': 'unknown', 'd': 'unknown'}
    bad = {'checks': [{'check_id': cid, 'state': 'unknown', 'evidence_ids': ['v:node:1'] if cid == 'b' else []}
                      for cid in ['a', 'b', 'c', 'd']]}
    with pytest.raises(ValueError, match='different workflow target'):
        validate_states(bad, {**packet, 'rule_facts': []})


def test_recorded_probe_step_pixels_are_attached(tmp_path):
    from PIL import Image
    image = tmp_path/'expanded.png'; Image.new('RGB', (20, 20), 'white').save(image)
    replay = {'routes': {}, 'checks': [{'name': 'probe', 'status': 'executed', 'source_function': 'original',
              'reset_route': '/', 'steps': [{'action': 'click', 'observation': {'screenshot': str(image)}}]}]}
    packet, images = state_packet(catalogue('a'), {'version': 'v'}, replay, {}, tmp_path)
    assert images == [str(image)] and packet['image_order'][0]['evidence_id'] == 'v:check:probe:step:0'


@pytest.mark.skipif(not os.environ.get('VSV_PLAYWRIGHT_PACKAGE') or not os.environ.get('VSV_CHROMIUM'),
                    reason='Requires the existing local Playwright browser runtime')
def test_browser_shared_state_reset_failures_and_action_budget(tmp_path):
    html = '<button onclick="document.querySelector(\'section\').insertAdjacentHTML(\'beforeend\',\'<article>new</article>\')">Add</button><section><article>first</article></section>'
    (tmp_path/'server.js').write_text("require('http').createServer((q,r)=>r.end(" + json.dumps(html) + ")).listen(process.env.PORT,'127.0.0.1');")
    action = {'type': 'click', 'description': 'Add a displayed item.', 'target': {'role': 'button', 'name': 'Add'}}
    initial = {'type': 'count', 'target': {'role': 'article'}, 'value': 1}
    growth = {'type': 'count_increases', 'target': {'role': 'article'}}
    setup = {'route': '/', 'viewport': {'width': 800, 'height': 600}}
    workflows = [
        {'workflow_id': 'shared', 'setup': setup, 'nodes': [
            {'source_ref': 'task:0', 'actions': [action], 'checks': {'growth': [growth]}},
            {'source_ref': 'task:0', 'actions': [], 'checks': {'wrong': [initial]}},
            {'source_ref': 'task:0', 'actions': [action], 'checks': {'budget': [growth]}}]},
        {'workflow_id': 'reset', 'setup': setup, 'nodes': [
            {'source_ref': 'task:0', 'actions': [], 'checks': {'initial': [initial]}},
            {'source_ref': 'task:0', 'actions': [{**action, 'target': {'role': 'button', 'name': 'Missing'}}], 'checks': {'missing': [initial]}},
            {'source_ref': 'task:0', 'actions': [], 'checks': {'downstream': [initial]}}]}]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    spec = {'base_url': f'http://127.0.0.1:{port}', 'runtime': {'command': ['node', 'server.js']},
            'routes': [], 'checks': [], 'version_id': 'v', 'acceptance_workflows': workflows,
            'execution_limits': {'timeout_ms': 200, 'max_ui_actions': 1}}
    version = {'workspace': str(tmp_path), 'code_manifest': digest_tree(tmp_path)}
    result = replay_version(version, spec, tmp_path/'execution', port)
    validate_execution(workflows, result, 'v')
    packet, _ = state_packet(catalogue('growth','wrong','budget','initial','missing','downstream'), {'version':'v'}, result, {}, tmp_path)
    assert {r['check_id']:r['state'] for r in packet['rule_facts']} == {
        'growth':'pass', 'wrong':'fail', 'budget':'unknown', 'initial':'pass', 'missing':'unknown', 'downstream':'unknown'}
    assert result['workflows'][1]['nodes'][2]['reason'] == 'prerequisite_blocked'


def test_scroll_direction_and_amount_are_explicit():
    plan = workflow()
    action = {'type': 'scroll', 'target': None, 'axis': 'x', 'amount': 500, 'description': 'Scroll horizontally.'}
    plan['nodes'][0]['actions'] = [action]
    validate_workflows([plan], catalogue('a'), model_reviewed=True)
    action['axis'] = 'diagonal'
    with pytest.raises(ValueError, match='axis'):
        validate_workflows([plan], catalogue('a'), model_reviewed=True)


def test_workflow_initial_view_is_context_not_assigned_result(tmp_path):
    from PIL import Image
    from multimodalcode.vsv_eval.states import state_evidence_scope
    image = tmp_path/'initial.png'; Image.new('RGB', (20,20), 'white').save(image)
    node = {'evidence_id': 'v:node', 'run_status': 'completed', 'check_ids': ['a'],
            'actions': [], 'checks': [{'check_id': 'a', 'assertions': []}]}
    replay = {'routes': {}, 'checks': [], 'workflows': [{'workflow_id': 'w', 'setup': {},
              'initial': {'screenshot': str(image)}, 'nodes': [node]}]}
    packet, images = state_packet(catalogue('a'), {'version':'v'}, replay, {}, tmp_path)
    core, allowed = state_evidence_scope(packet, 'a')
    assert images == [str(image)]
    assert core == {'v:node'} and allowed == {'v:node', 'v:workflow:w:initial'}
    with pytest.raises(ValueError, match='assigned workflow node'):
        validate_states({'checks':[{'check_id':'a','state':'pass','evidence_ids':['v:workflow:w:initial']}]}, packet)


@pytest.mark.skipif(not os.environ.get('VSV_PLAYWRIGHT_PACKAGE') or not os.environ.get('VSV_CHROMIUM'),
                    reason='Requires the existing local Playwright browser runtime')
def test_browser_uses_unique_exact_text_when_planned_role_differs(tmp_path):
    html = '<button onclick="document.querySelector(\'p\').textContent=\'Opened\'">Open panel</button><p>Closed</p>'
    (tmp_path/'server.js').write_text("require('http').createServer((q,r)=>r.end(" + json.dumps(html) + ")).listen(process.env.PORT,'127.0.0.1');")
    plan = workflow()
    plan['nodes'][0]['actions'][0]['target'] = {'role': 'link', 'name': 'Open panel'}
    plan['nodes'][0]['checks']['a'] = [{'type':'text_visible','value':'Opened'}]
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    spec = {'base_url':f'http://127.0.0.1:{port}', 'runtime':{'command':['node','server.js']},
            'routes':[], 'checks':[], 'version_id':'v', 'acceptance_workflows':[plan],
            'execution_limits':{'timeout_ms':200,'max_ui_actions':2}}
    version = {'workspace':str(tmp_path),'code_manifest':digest_tree(tmp_path)}
    result = replay_version(version, spec, tmp_path/'execution', port)
    node = result['workflows'][0]['nodes'][0]
    assert node['run_status'] == 'completed'
    assert node['checks'][0]['assertions'][0]['passed'] is True
    assert 'selection' not in node['actions'][0]
