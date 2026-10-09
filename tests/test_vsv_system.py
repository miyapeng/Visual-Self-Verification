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


def test_execution_orders_checks_states_join_and_keeps_scores(tmp_path, monkeypatch):
    _, rounds, catalogue = inputs(tmp_path)
    calls = []
    config = system.load_experiment(tmp_path / 'experiment.json')
    case = {**config['cases'][0], 'state_spec': 'prepared.json'}
    target = {'check_id': 'page', 'target_id': 't1', 'coverage': 'full', 'method_ok': True,
              'evidence_ok': True, 'actual_state': 'pass', 'agent_state': 'pass',
              'issue_match': None, 'modality': 'text'}
    episodes = [{'episode_id': 'e1', 'status': 'evaluated', 'targets': [target],
                 'repair_event_ids': [], 'relation_annotation_complete': True}]
    result = {'status': 'evaluated', 'episodes': episodes, 'repair_gaps': [],
              'metrics': summarize(catalogue, episodes)}
    monkeypatch.setattr(system, 'evaluate_rounds', lambda *a, **k: calls.append('checks'))
    monkeypatch.setattr(system, 'evaluate_states', lambda *a, **k: calls.append('states'))
    def finalize(checks, output, repairs):
        calls.append('join')
        assert Path(checks).name == 'scores.json'
        assert Path(repairs).name == 'repair_results.json'
        return result
    monkeypatch.setattr(system, 'finalize_evaluation', finalize)
    row = system.run_case(case, config, tmp_path / 'out')
    assert calls == ['checks', 'states', 'join']
    assert row['status'] == 'completed'
    assert row['metrics']['VC']['score'] == 100
    assert row['metrics']['CV-T']['score'] == 100
    assert row['metrics']['RS']['assessment_status'] == 'not_applicable'
    assert row['metrics']['BDA']['assessment_status'] == 'scored'
    assert row['metrics']['BDA']['score'] == 100


def test_failure_preserves_progress_and_same_command_can_resume(tmp_path, monkeypatch):
    manifest, _, _ = inputs(tmp_path)
    manifest['cases'].append({**manifest['cases'][0], 'id': 'second'})
    write_json(tmp_path / 'experiment.json', manifest)
    seen = []
    def run(case, experiment, output):
        seen.append(case['id'])
        if case['id'] == 'model-task':
            raise RuntimeError('Provider unavailable')
        return {'status': 'completed', 'metrics': {}}
    monkeypatch.setattr(system, 'run_case', run)
    output = tmp_path / 'out'
    assert system.run_experiment(tmp_path / 'experiment.json', output) == 2
    assert seen == ['model-task', 'second']
    assert [r['status'] for r in read_json(output / 'summary.json')['cases']] == ['failed', 'completed']
    monkeypatch.setattr(system, 'run_case', lambda *a: {'status': 'completed', 'metrics': {}})
    assert system.run_experiment(tmp_path / 'experiment.json', output) == 0
    (tmp_path / 'task/prompt.txt').write_text('Changed task')
    with pytest.raises(ValueError, match='inputs changed'):
        system.run_experiment(tmp_path / 'experiment.json', output)


def test_final_validation_rejects_missing_or_duplicate_rounds(tmp_path):
    inputs(tmp_path)
    row = {'episode_id': 'e1', 'status': 'evaluated'}
    for episodes in ([], [row, row], [{**row, 'status': 'pending'}]):
        with pytest.raises(ValueError, match='pending rounds'):
            system.verify_complete({'episodes': episodes, 'repair_gaps': []}, tmp_path / 'rounds.json')
    with pytest.raises(ValueError, match='unassessed repair'):
        system.verify_complete({'episodes': [row], 'repair_gaps': [{}]}, tmp_path / 'rounds.json')


def test_unresolved_subset_is_not_displayed_as_a_complete_score():
    row = system.metric_record('CP', {'score': 100, 'success_count': 2, 'unknown_count': 1})
    assert row['score'] == 100  # Keep existing arithmetic available for audit.
    assert system.display_metric(row) == 'unresolved'
    gap = system.metric_record('Repair-regression-rate', {'score': None, 'unassessed_transition_count': 1})
    assert system.display_metric(gap) == 'unresolved'


def test_comparison_exports_requirement_and_repair_breakdowns(tmp_path):
    import csv
    rate = {'score': 50, 'success_count': 1, 'failure_count': 1, 'unknown_count': 0}
    metrics = {
        'VC': system.metric_record('VC', {**rate, 'by_requirement_type': {
            'visual': {**rate, 'classification_complete': True},
            'interactive': {**rate, 'score': None, 'classification_complete': False}}}),
        'BDA-V': system.metric_record('BDA-V', {**rate, 'error': rate}),
        'RS': system.metric_record('RS', {**rate, 'diagnosed_visual': rate}),
        'CP': system.metric_record('CP', {**rate, 'repair_outcomes': {'regression_rate': rate}}),
    }
    system.write_summary([{'id': 'case', 'model': 'model', 'status': 'completed', 'metrics': metrics}], tmp_path)
    with (tmp_path / 'comparison.csv').open() as handle:
        row = next(csv.DictReader(handle))
    assert row['VC-visual-requirements'] == '50.00%'
    assert row['VC-interactive-requirements'] == 'unclassified'
    assert row['BDA-visible-defects'] == row['RS-diagnosed-visual'] == row['Repair-regression-rate'] == '50.00%'


def test_manifest_rejects_case_collisions_and_conflicting_sources(tmp_path):
    manifest, _, _ = inputs(tmp_path)
    manifest['cases'].append(manifest['cases'][0])
    write_json(tmp_path / 'experiment.json', manifest)
    with pytest.raises(ValueError, match='unique'):
        system.load_experiment(tmp_path / 'experiment.json')
