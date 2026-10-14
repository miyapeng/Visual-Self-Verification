"""Integration boundaries for the single-command scoring system."""
import hashlib
from pathlib import Path

import pytest

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval import system
from multimodalcode.vsv_eval.catalogue import content_hash
from multimodalcode.vsv_eval.metrics import summarize


def inputs(tmp_path):
    task = tmp_path / 'task'
    task.mkdir()
    (task / 'prompt.txt').write_text('Render a working page.')
    run = tmp_path / 'run.json'
    write_json(run, {'timeline': [{'ordinal': 1, 'kind': 'action', 'tool': 'Bash',
                                 'payload': {'command': 'curl localhost'}, 'text': ''}]})
    rounds = {'schema': 'multimodalcode-verification-rounds-1', 'case_id': 'test',
              'source_run': str(run), 'source_run_sha256': system.file_hash(run),
              'events': read_json(run)['timeline'],
              'episodes': [{'episode_id': 'e1', 'core_event_ids': [1], 'context_event_ids': [],
                            'judgment_event_ids': [], 'evidence_states': [], 'repair_links': [],
                            'relation_annotation_complete': True}]}
    checks = [{'check_id': 'page', 'source_ref': 'task:0', 'setup': 'Open the page.',
               'criterion': 'Page works.', 'required_evidence': 'HTTP response.', 'reference_ids': []}]
    catalogue = {'schema': 'self-verification-catalogue-1', 'checks': checks,
                 'criteria_sha256': content_hash(checks), 'references': {}, 'source_ids': ['task:0'],
                 'task_sha256': hashlib.sha256((task / 'prompt.txt').read_bytes()).hexdigest(),
                 'review': {'status': 'draft'}}
    write_json(tmp_path / 'rounds.json', rounds)
    write_json(tmp_path / 'catalogue.json', catalogue)
    write_json(tmp_path / 'config.json', {'schema': 'multimodalcode-vsv-judge-config-1'})
    manifest = {'config': 'config.json', 'allow_draft': True,
                'cases': [{'id': 'model-task', 'model': 'test-model', 'task_root': 'task',
                           'catalogue': 'catalogue.json', 'rounds_json': 'rounds.json'}]}
    write_json(tmp_path / 'experiment.json', manifest)
    return manifest, rounds, catalogue


def test_preflight_never_calls_api_and_reports_all_cases(tmp_path, monkeypatch):
    manifest, rounds, _ = inputs(tmp_path)
    rounds['episodes'][0]['repair_links'] = [{'repair_event_ids': [1]}]
    write_json(tmp_path / 'with_repair.json', rounds)
    manifest['cases'].append({**manifest['cases'][0], 'id': 'blocked', 'rounds_json': 'with_repair.json'})
    write_json(tmp_path / 'experiment.json', manifest)
    monkeypatch.setattr(system, 'run_case', lambda *a: pytest.fail('Preflight ran scoring'))
    output = tmp_path / 'out'
    assert system.run_experiment(tmp_path / 'experiment.json', output, check_only=True) == 2
    rows = read_json(output / 'summary.json')['cases']
    assert [r['status'] for r in rows] == ['ready', 'blocked']
    assert 'acceptance bindings' in rows[1]['issues'][0]
    assert read_json(output / 'model_calls.json')['api_requests'] == 0


def test_missing_images_block_before_scoring(tmp_path):
    _, rounds, _ = inputs(tmp_path)
    rounds['events'].append({'ordinal': 2, 'kind': 'observation', 'images': ['absent-image.png']})
    write_json(rounds['source_run'], {'timeline': rounds['events']})
    rounds['source_run_sha256'] = system.file_hash(rounds['source_run'])
    rounds['episodes'][0]['core_event_ids'].append(2)
    write_json(tmp_path / 'rounds.json', rounds)
    config = system.load_experiment(tmp_path / 'experiment.json')
    result = system.preflight(config['cases'][0], config, tmp_path / 'preflight')
    assert any('images at events: 2' in issue for issue in result['issues'])


def test_blocked_reconstruction_is_not_reported_as_original_evidence(tmp_path):
    manifest, _, _ = inputs(tmp_path)
    manifest['cases'][0]['reconstructed_images'] = 'missing-index.json'
    write_json(tmp_path/'experiment.json', manifest)
    assert system.run_experiment(tmp_path/'experiment.json', tmp_path/'out', check_only=True) == 2
    saved = read_json(tmp_path/'out/summary.json')
    assert saved['cases'][0]['evidence_mode'] == 'reconstructed_observations'
    assert set(saved['model_macro_averages']) == {'reconstructed_observations'}


def test_execution_orders_checks_states_join_and_keeps_scores(tmp_path, monkeypatch):
    _, rounds, catalogue = inputs(tmp_path)
    calls = []
    config = system.load_experiment(tmp_path / 'experiment.json')
    case = {**config['cases'][0], 'state_spec': 'prepared.json'}
    target = {'check_id': 'page', 'target_id': 't1', 'coverage': 'full', 'method_ok': True,
              'evidence_ok': True, 'actual_state': 'pass', 'agent_state': 'pass',
              'issue_match': None, 'modality': 'text', 'evidence_origin': 'original'}
    episodes = [{'episode_id': 'e1', 'status': 'evaluated', 'targets': [target], 'assessments': [target],
                 'repair_event_ids': [], 'relation_annotation_complete': True}]
    result = {'status': 'evaluated', 'episodes': episodes, 'repair_gaps': [],
              'scope': {'initial_implementation_event_id':0},
              'metrics': summarize(catalogue, episodes, scope={'initial_implementation_event_id':0})}
    monkeypatch.setattr(system, 'evaluate_rounds', lambda *a, **k: calls.append('checks') or result)
    monkeypatch.setattr(system, 'evaluate_states', lambda *a, **k: calls.append('states'))
    def finalize(checks, output, repairs):
        calls.append('join')
        assert Path(checks).name == 'scores.json'
        assert repairs is None
        return result
    monkeypatch.setattr(system, 'finalize_evaluation', finalize)
    row = system.run_case(case, config, tmp_path / 'out')
    assert calls == ['checks', 'join']
    assert row['status'] == 'completed'
    assert row['metrics']['VC']['score'] == 100
    assert row['metrics']['CAR-T']['score'] == 100
    assert row['metrics']['RSR']['assessment_status'] == 'not_applicable'
    assert row['metrics']['CAR']['assessment_status'] == 'scored'
    assert row['metrics']['CAR']['score'] == 100


def test_failure_preserves_progress_and_same_command_can_resume(tmp_path, monkeypatch):
    manifest, _, _ = inputs(tmp_path)
    manifest['cases'].append({**manifest['cases'][0], 'id': 'second'})
    write_json(tmp_path / 'experiment.json', manifest)
    seen = []
    def run(case, experiment, output):
        seen.append(case['id'])
        if case['id'] == 'model-task':
            raise RuntimeError('Provider unavailable')
        return {'status': 'completed', 'metrics': summarize({'checks':[]}, [], scope={'initial_implementation_event_id':0})}
    monkeypatch.setattr(system, 'run_case', run)
    output = tmp_path / 'out'
    assert system.run_experiment(tmp_path / 'experiment.json', output) == 2
    assert seen == ['model-task', 'second']
    assert [r['status'] for r in read_json(output / 'summary.json')['cases']] == ['failed', 'completed']
    monkeypatch.setattr(system, 'run_case', lambda *a: {'status': 'completed', 'metrics': summarize({'checks':[]}, [], scope={'initial_implementation_event_id':0})})
    assert system.run_experiment(tmp_path / 'experiment.json', output) == 0
    (tmp_path / 'task/prompt.txt').write_text('Changed task')
    with pytest.raises(ValueError, match='inputs changed'):
        system.run_experiment(tmp_path / 'experiment.json', output)


def test_final_validation_rejects_missing_or_duplicate_rounds(tmp_path):
    inputs(tmp_path)
    row = {'episode_id': 'e1', 'status': 'evaluated'}
    for episodes in ([], [row, row], [{**row, 'status': 'pending'}]):
        with pytest.raises(ValueError, match='pending rounds'):
            system.verify_complete({'episodes': episodes, 'repair_gaps': [], 'scope': {'initial_implementation_event_id':0}}, tmp_path / 'rounds.json')
    with pytest.raises(ValueError, match='unassessed repair'):
        system.verify_complete({'episodes': [row], 'repair_gaps': [{}], 'scope': {'initial_implementation_event_id':0}}, tmp_path / 'rounds.json')


def test_incomplete_evaluation_cannot_display_a_subset_as_a_complete_score():
    row = system.metric_record('CPR', {'score': None, 'status':'incomplete', 'unassessed_count':1})
    assert system.display_metric(row) == 'incomplete'


def test_comparison_exports_six_rates_and_equal_task_model_means(tmp_path):
    import csv
    def scores(passed):
        return summarize({'checks':[{'check_id':'a'}]}, [{'status':'evaluated','targets':[
            {'check_id':'a','coverage':'full' if passed else 'none','method_ok':True,'evidence_ok':True}],
            'assessments':[]}], scope={'initial_implementation_event_id':0})
    rows=[{'id':str(i),'task_id':task,'model':model,'status':'completed', 'metrics':scores(passed)}
          for i,(task,model,passed) in enumerate([('a','model',True),('a','model',False),('b','model',True),
                                               ('a','other',True),('b','other',False)])]
    system.write_summary(rows,tmp_path)
    saved=read_json(tmp_path/'summary.json')
    assert saved['model_macro_averages']['original']['model']['metrics']['VC']['score']==75
    assert saved['model_macro_averages']['original']['other']['metrics']['VC']['score']==50
    with (tmp_path/'comparison.csv').open() as handle:
        row=next(csv.DictReader(handle))
    assert all(m in row for m in system.METRICS)
    assert 'CV' not in row and 'VCS' not in row
    assert row['evidence_mode'] == 'original'
    rows[-1]['status']='failed'
    means=system.model_averages(rows)
    assert means['original']['model']['metrics']['VC']['score']==50
    assert means['original']['model']['task_ids']==[['vision2web','a']]


def test_model_comparisons_do_not_pool_original_and_reconstructed_runs(tmp_path):
    def row(model, task, mode, passed):
        metrics = summarize({'checks':[{'check_id':'a'}]}, [{'status':'evaluated', 'targets':[
            {'check_id':'a','coverage':'full' if passed else 'none','method_ok':True,'evidence_ok':True}],
            'assessments':[]}], scope={'initial_implementation_event_id':0})
        return {'id':f'{model}-{task}', 'task_id':task, 'model':model, 'status':'completed',
                'evidence_mode':mode, 'metrics':metrics}
    original, replay = 'original', 'reconstructed_observations'
    rows = [row(model, 'original-task', original, True) for model in ('a','b')]
    rows += [row(model, 'replay-task', replay, False) for model in ('a','b')]
    rows += [row('a','mismatched',original,True), row('b','mismatched',replay,True)]
    system.write_summary(rows, tmp_path)
    means = read_json(tmp_path/'summary.json')['model_macro_averages']
    for model in ('a','b'):
        assert means[original][model]['task_ids'] == [['vision2web','original-task']]
        assert means[replay][model]['task_ids'] == [['vision2web','replay-task']]
        assert means[original][model]['metrics']['VC']['score'] == 100
        assert means[replay][model]['metrics']['VC']['score'] == 0
        assert ['vision2web','mismatched'] in means[original][model]['excluded_task_ids']
    report = (tmp_path/'comparison.md').read_text()
    assert 'Model averages: original' in report and 'Model averages: reconstructed_observations' in report
    assert 'Diagnosis by cited evidence origin' in report
    # Do not select just the convenient repetitions of a task.
    rows.append(row('a', 'original-task', replay, False))
    assert system.model_averages(rows)[original]['a']['task_ids'] == []
    with pytest.raises(ValueError, match='evidence mode'):
        system.model_averages([row('a', 'proxy-task', 'text_proxy', True)])


def test_manifest_rejects_case_collisions_and_conflicting_sources(tmp_path):
    manifest, _, _ = inputs(tmp_path)
    manifest['cases'].append(manifest['cases'][0])
    write_json(tmp_path / 'experiment.json', manifest)
    with pytest.raises(ValueError, match='unique'):
        system.load_experiment(tmp_path / 'experiment.json')


def test_same_task_cannot_use_different_criteria_for_different_models(tmp_path, monkeypatch):
    manifest, _, catalogue = inputs(tmp_path)
    catalogue['checks'][0]['criterion'] = 'Different acceptance condition.'
    catalogue['criteria_sha256'] = content_hash(catalogue['checks'])
    write_json(tmp_path / 'other_catalogue.json', catalogue)
    manifest['cases'].append({**manifest['cases'][0], 'id': 'other', 'model': 'other',
                              'catalogue': 'other_catalogue.json'})
    write_json(tmp_path / 'experiment.json', manifest)
    monkeypatch.setattr(system, 'run_case', lambda *args: pytest.fail('Inconsistent criteria reached scoring'))
    with pytest.raises(ValueError, match='same frozen catalogue'):
        system.run_experiment(tmp_path / 'experiment.json', tmp_path / 'out')
