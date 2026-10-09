#!/usr/bin/env python3
"""Run a CPU Web smoke test with scripted continuations, without model API calls."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from multimodalcode.io import write_json
from multimodalcode.training.collector import collect, tool_message
from multimodalcode.training.environment import TOOL_SCHEMAS, WebEnvironment
from multimodalcode.training.handoff import export_rl, export_swift, make_checkpoint
from multimodalcode.vsv_eval.catalogue import content_hash


PAGE = """<!doctype html><html><head><title>Verification training fixture</title></head>
<body><h1>Task status</h1><p id="status">Incomplete</p>
<button onclick="document.querySelector('#details').hidden = false">Show details</button>
<p id="details" hidden>Saved details</p></body></html>"""


class ScriptedTeacher:
    model = 'scripted-smoke-fixture'
    def __init__(self):
        self.turn = 0

    def generate_messages(self, messages, **kwargs):
        turns = [
            {'role': 'assistant', 'content': 'The status is incorrect. I will update the displayed status.',
             'tool_calls': [{'id': 'repair', 'type': 'function', 'function': {'name': 'edit_file',
                'arguments': json.dumps({'path': 'index.html', 'old_string': '>Incomplete<', 'new_string': '>Complete<'})}}]},
            {'role': 'assistant', 'content': 'Reload and inspect the repaired state.',
             'tool_calls': [{'id': 'recheck', 'type': 'function', 'function': {'name': 'browser',
                'arguments': json.dumps({'actions': [{'type': 'navigate', 'url': '/'}]})}}]},
            {'role': 'assistant', 'content': 'The status now reads Complete.'},
        ]
        message = turns[self.turn]
        self.turn += 1
        return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}]}


def run(output, executable_path=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / 'source'
    source.mkdir()
    (source / 'index.html').write_text(PAGE)
    runtime = {'kind': 'static'}
    if executable_path:
        runtime['executable_path'] = executable_path
    env = WebEnvironment(source, output / 'student', runtime)
    try:
        inspection = env.execute('browser', {'actions': [{'type': 'click', 'target': {'selector': 'button'}}]}, 'student-inspect')
        assert not inspection['is_error']
        code = env.execute('read_file', {'path': 'index.html'}, 'student-read')
        state = env.snapshot(output / 'snapshot')
    finally:
        env.close()
    checks = [
        {'check_id': 'status', 'source_ref': 'task:0', 'setup': 'Open the page',
         'criterion': 'Status reads Complete', 'required_evidence': 'Rendered status text', 'reference_ids': []},
        {'check_id': 'details', 'source_ref': 'task:0', 'setup': 'Click Show details',
         'criterion': 'The button reveals Saved details', 'required_evidence': 'Interaction result', 'reference_ids': []},
    ]
    prompt = 'Display Complete as the task status and preserve the Show details interaction.'
    catalogue = {'schema': 'self-verification-catalogue-1', 'checks': checks, 'references': {},
                 'source_ids': ['task:0'], 'review': {'status': 'draft'}, 'criteria_sha256': content_hash(checks),
                 'task_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    write_json(output / 'catalogue.json', catalogue)
    messages = [
        {'role': 'user', 'content': prompt},
        {'role': 'assistant', 'content': 'Inspect the current implementation.', 'tool_calls': [
            {'id': 'student-inspect', 'type': 'function', 'function': {'name': 'browser',
                'arguments': json.dumps({'actions': [{'type': 'click', 'target': {'selector': 'button'}}]})}}]},
        tool_message(inspection),
        {'role': 'assistant', 'content': 'The displayed status is Incomplete, but the task requires Complete. Read the source before editing.',
         'tool_calls': [{'id': 'student-read', 'type': 'function', 'function': {'name': 'read_file',
                         'arguments': json.dumps({'path': 'index.html'})}}]},
        tool_message(code),
    ]
    evaluation = {'catalogue': str(output / 'catalogue.json'), 'allow_draft': True, 'target_ids': ['status'],
                  'acceptance_probes': [
                      {'check_id': 'status', 'assertions': [{'type': 'text_equals', 'target': {'selector': '#status'}, 'value': 'Complete'}]},
                      {'check_id': 'details', 'actions': [{'type': 'click', 'target': {'selector': 'button'}}],
                       'assertions': [{'type': 'text_visible', 'value': 'Saved details'}]},
                  ]}
    checkpoint = make_checkpoint(task_id='scripted-web-training-fixture', benchmark='smoke-fixture', kind='repair',
        messages=messages, policy_revision='scripted-student-r0', split='train', environment=state, evaluation=evaluation,
        source={'diagnosis_message_indices': [3]})
    path = output / 'checkpoint.json'
    write_json(path, checkpoint)
    suffix, records, result = asyncio.run(collect(checkpoint, ScriptedTeacher(), output / 'teacher'))
    assert result['score'] == 1 and result['fixed_check_ids'] == ['status']
    assert result['regressed_check_ids'] == []
    assert (source / 'index.html').read_text() == PAGE
    assert (Path(state['workspace']) / 'index.html').read_text() == PAGE
    # Fixture labels test data serialization; they are not model-reviewed research demonstrations.
    audit = {'status': 'evaluated', 'checkpoint_id': checkpoint['id'], 'suffix_sha256': content_hash(suffix),
             'approved_message_indices': [0, 2, 4], 'evidence_refs': [str(output / 'teacher/evaluation/reward.json')],
             'image_sha256': {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for r in records for p in r['images']},
             'provenance': 'scripted_test_fixture'}
    write_json(output / 'teacher/audit.json', audit)
    write_json(output / 'swift_row.json', export_swift(checkpoint, suffix, audit, tools=TOOL_SCHEMAS))
    write_json(output / 'verl_row.json', export_rl(path, expected_policy_revision='scripted-student-r0'))
    summary = {'status': 'passed', 'provenance': 'scripted_test_fixture', 'model_api_calls': 0,
               'restoration': 'matched code and replayed browser state', 'teacher_tool_calls': len(records),
               'repair_reward': result['score'], 'fixed_check_ids': result['fixed_check_ids'],
               'regressed_check_ids': result['regressed_check_ids'],
               'source_unchanged': True, 'swift_row': str(output / 'swift_row.json'), 'verl_row': str(output / 'verl_row.json')}
    write_json(output / 'summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--executable-path')
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.executable_path), indent=2))
