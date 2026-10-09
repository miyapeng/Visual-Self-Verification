import copy
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from multimodalcode.vsv_eval.archive_replay import blocked_shell, plan_replay, sha256, load_reconstructed_images
from multimodalcode.vsv_eval.evaluation import build_packet

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/vision2web'))
import archive_replay_worker as worker
from reconstruct_leaderboard_images import existing_output, publish
from import_leaderboard_trace import import_trace


def action(eid, tool, **payload):
    return {'ordinal': eid, 'kind': 'action', 'tool': tool,
            'tool_call_id': str(eid), 'payload': payload,
            'browser_url_context': 'http://localhost:3000/'}


def reply(eid, call, **values):
    return {'ordinal': eid, 'kind': 'observation',
            'payload': {'tool_use_id': str(call)}, **values}


def test_plan_pairs_receipts_preserves_edits_and_omits_later_cleanup(tmp_path):
    events = [action(1, 'Write', file_path='/workspace/app.js', content='before'), reply(2, 1),
              action(3, 'Bash', command='playwright-cli screenshot --filename=/tmp/a.png'), reply(4, 3),
              action(5, 'Edit', file_path='/workspace/app.js', old_string='before', new_string='after'), reply(6, 5),
              action(7, 'Read', file_path='/tmp/a.png'),
              action(8, 'Read', file_path='/workspace/prototypes/desktop.png'),
              reply(9, 8, images=[{'available': False}]), reply(10, 7, images=[{'available': False}]),
              action(11, 'Bash', command='rm -rf /workspace/node_modules')]
    original = copy.deepcopy(events)
    plan = plan_replay({'timeline': events}, tmp_path)
    assert plan['status'] == 'ready'
    assert [s['event_id'] for s in plan['steps']] == [1, 3, 5, 7]
    assert plan['images'] == [{'read_event_id': 7, 'image_receipt_event_ids': [10],
                               'capture_event_id': 3, 'original_path': '/tmp/a.png'}]
    assert events == original
    events[5]['is_error'] = True
    assert 5 not in [s['event_id'] for s in plan_replay({'timeline': events}, tmp_path)['steps']]


@pytest.mark.parametrize('command', ['rm /tmp/a;', '/bin/rm /tmp/a', 'true;rm /tmp/a',
                                     'python -c "import shutil;shutil.rmtree(\'/tmp/a\')"'])
def test_destructive_commands_are_blocked(command):
    assert blocked_shell(command)


def test_edited_startup_script_is_reviewed(tmp_path):
    events = [action(1, 'Write', file_path='/workspace/start.sh', content='node app.js'), reply(2, 1),
              action(3, 'Edit', file_path='/workspace/start.sh', old_string='node app.js', new_string='rm -rf /tmp/a'), reply(4, 3),
              action(5, 'Bash', command='bash /workspace/start.sh'), reply(6, 5),
              action(7, 'Bash', command='playwright-cli screenshot --filename=/tmp/a.png'), reply(8, 7),
              action(9, 'Read', file_path='/tmp/a.png'), reply(10, 9, images=[{}])]
    plan = plan_replay({'timeline': events}, tmp_path)
    assert plan['status'] == 'blocked'
    assert plan['blocks'][0]['event_id'] == 5


def test_navigation_flags_do_not_replace_url_and_last_navigation_wins(tmp_path):
    source = tmp_path/'frontend/task/results/task.json'
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({'conversation': [{'message': {'content': [
        {'type': 'tool_use', 'id': 'a', 'name': 'Bash', 'input': {'command':
         'playwright-cli open --browser=chromium; playwright-cli goto http://localhost:3000/'}},
        {'type': 'tool_use', 'id': 'b', 'name': 'Read', 'input': {'file_path': '/tmp/a.png'}}]}}]}))
    run = import_trace(source, tmp_path/'imported')
    assert all(e['browser_url_context'] == 'http://localhost:3000/' for e in run['timeline'])


def test_helper_script_sources_and_recorded_cwd_reset(tmp_path):
    script = 'page.goto("http://localhost:3000/")\npage.screenshot(path=f"/tmp/view_{name}.png")'
    events = [action(1, 'Write', file_path='/tmp/capture.py', content=script), reply(2, 1),
              action(3, 'Bash', command='cd /tmp && python3 capture.py'),
              reply(4, 3, text='done\nShell cwd was reset to /workspace'),
              action(5, 'Read', file_path='/tmp/view_desktop.png'), reply(6, 5, images=[{}])]
    original = copy.deepcopy(events)
    plan = plan_replay({'timeline': events}, tmp_path)
    assert plan['status'] == 'ready'
    assert plan['images'][0]['capture_event_id'] == 3
    assert plan['steps'][1]['recorded_cwd_reset'] == '/workspace'
    assert plan['steps'][1]['payload']['command'] == 'cd /tmp && python3 capture.py'
    assert events == original
    events[0]['payload']['content'] += '\nimport shutil; shutil.rmtree("/tmp/old")'
    assert plan_replay({'timeline': events}, tmp_path)['status'] == 'blocked'


def test_javascript_helper_and_python_resize_keep_provenance(tmp_path):
    script = "page.goto('http://localhost:3000/');page.screenshot({path: `/tmp/${name}.png`});"
    events = [action(1, 'Write', file_path='/tmp/capture.cjs', content=script), reply(2, 1),
              action(3, 'Bash', command='NODE_PATH=/tmp/node_modules node /tmp/capture.cjs'), reply(4, 3),
              action(5, 'Bash', command='python3 -c \'im=Image.open(f"/tmp/{name}.png");im.save(f"/tmp/{name}_view.jpg")\''), reply(6, 5),
              action(7, 'Read', file_path='/tmp/home_view.jpg'), reply(8, 7, images=[{}])]
    for event in events:
        event.pop('browser_url_context', None)
    plan = plan_replay({'timeline': events}, tmp_path)
    assert plan['status'] == 'ready'
    assert plan['images'][0]['capture_event_id'] == 5


def test_sidecar_validates_source_receipts_and_keeps_original_events(tmp_path):
    raw = tmp_path/'raw.json'; raw.write_text('{}')
    run = tmp_path/'run.json'; run.write_text(json.dumps({'source': {'sha256': sha256(raw)}}))
    image = tmp_path/'image.png'; Image.new('RGB', (2, 2)).save(image)
    events = [action(1, 'Bash', command='capture'), action(2, 'Read', file_path='/tmp/page.png'),
              reply(3, 2, images=[{'available': False, 'path': 'missing.png'}])]
    rounds = {'source_run': str(run), 'events': events}
    index = {'source_trajectory': 'raw.json', 'source_trajectory_sha256': sha256(raw), 'captures': [
        {'image_path': 'image.png', 'image_sha256': sha256(image), 'read_event_id': 2,
         'image_receipt_event_ids': [3], 'capture_event_id': 1, 'original_path': '/tmp/page.png'}]}
    path = tmp_path/'index.json'; path.write_text(json.dumps(index))
    original = copy.deepcopy(events)
    loaded = load_reconstructed_images(path, rounds)
    assert loaded[3]['origin'] == 'reconstructed' and events == original
    index['captures'][0]['capture_event_id'] = 3
    path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match='producer'):
        load_reconstructed_images(path, rounds)
    index['source_trajectory_sha256'] = 'wrong'
    path.write_text(json.dumps(index))
    with pytest.raises(ValueError, match='different source'):
        load_reconstructed_images(path, rounds)


def test_reconstructed_packets_preserve_events_and_observation_cutoffs(tmp_path):
    source = tmp_path/'run.json'; source.write_text('{}')
    image = tmp_path/'restored.png'; Image.new('RGB', (2, 2)).save(image)
    events = [action(1, 'Bash', command='capture'), action(2, 'Read', file_path='/tmp/page.png'),
              reply(3, 2, images=[{'path': 'missing.png', 'available': False}]),
              {'ordinal': 4, 'kind': 'model_text', 'text': 'The heading is visible.'},
              action(5, 'Read', file_path='/tmp/later.png'), reply(6, 5, images=[{'path': 'later.png'}])]
    episode = {'episode_id': 'e', 'core_event_ids': [2, 3, 4], 'context_event_ids': [1],
               'judgment_event_ids': [4], 'evidence_states': []}
    rounds = {'events': events, 'source_run': str(source), 'episodes': [episode]}
    original = copy.deepcopy(rounds)
    catalogue = {'checks': [], 'references': {}, 'criteria_sha256': 'fixture'}
    mapping = {eid: {'path': str(image), 'sha256': sha256(image), 'origin': 'reconstructed',
                     'capture_event_id': 1} for eid in [3, 6]}
    packet, images = build_packet(rounds, episode, 4, 'task', catalogue, [], tmp_path,
                                  reconstructed_images=mapping)
    assert images and {v['event_id'] for v in packet['image_order']} == {3}
    assert all(v['origin'] == 'reconstructed' for v in packet['image_order'])
    assert packet['evidence_policy'].startswith('reconstructed_observations:')
    assert rounds == original and all(e['ordinal'] <= 4 for e in packet['events'])
    strict, images = build_packet(rounds, episode, 4, 'task', catalogue, [], tmp_path)
    assert not images and strict['missing_images'] and 'evidence_policy' not in strict


def test_worker_keeps_overwritten_images_and_capture_versions(tmp_path, monkeypatch):
    root = tmp_path/'replay'
    root.mkdir()
    screenshot = tmp_path/'same.png'
    source = tmp_path/'app.js'
    source.write_text('before')
    steps = [
        {'event_id': 1, 'tool': 'Bash', 'payload': {'command': 'capture-red'},
         'recorded_cwd_reset': '/workspace'},
        {'event_id': 2, 'tool': 'Edit', 'payload': {'file_path': str(source), 'old_string': 'before', 'new_string': 'after'}},
        {'event_id': 3, 'tool': 'Read'},
        {'event_id': 4, 'tool': 'Bash', 'payload': {'command': 'capture-blue'}},
        {'event_id': 5, 'tool': 'Read'},
        {'event_id': 6, 'tool': 'Bash', 'payload': {'command': 'capture-failed'}},
        {'event_id': 7, 'tool': 'Read'},
    ]
    plan = {'task_timeout': 20, 'command_timeout': 5, 'steps': steps, 'images': [
        {'read_event_id': read, 'capture_event_id': capture, 'image_receipt_event_ids': [read+100],
         'original_path': str(screenshot)} for read, capture in [(3, 1), (5, 4), (7, 6)]]}
    (root/'plan.json').write_text(json.dumps(plan))
    monkeypatch.setattr(worker, 'ROOT', root)
    monkeypatch.setattr(worker, 'OUT', root/'output')
    monkeypatch.setattr(worker, 'manifest', lambda paths: sha256(source))

    def run(command, **kwargs):
        script = command[-1]
        if 'capture-red' in script:
            Image.new('RGB', (2, 2), 'red').save(screenshot)
            (root/'cwd').write_text('/tmp')
        elif 'capture-blue' in script:
            assert kwargs['cwd'] == '/workspace'
            Image.new('RGB', (2, 2), 'blue').save(screenshot)
        return SimpleNamespace(returncode=1 if 'capture-failed' in script else 0, stdout='fixture')

    monkeypatch.setattr(worker.subprocess, 'run', run)
    worker.main()
    result = json.loads((root/'output/result.json').read_text())
    assert result['status'] == 'partial'
    first, second = result['captures']
    assert first['image_sha256'] != second['image_sha256']
    assert first['code_sha256'] != second['code_sha256']  # Read after Edit still uses capture state
    assert result['failures'][0]['read_event_id'] == 7  # stale bytes are not a fresh screenshot
    assert (root/'output'/first['image_path']).is_file()


def test_publish_is_compact_and_refuses_to_overwrite(tmp_path):
    task = tmp_path/'task'
    source = task/'results/task.json'
    source.parent.mkdir(parents=True)
    source.write_text('{}')
    imported = tmp_path/'imported'
    imported.mkdir()
    (imported/'run.json').write_text('{}')
    attempt = tmp_path/'attempt'
    image = attempt/'output/images/a.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (2, 2)).save(image)
    result = {'status': 'completed', 'runtime': {}, 'failures': [], 'captures': [
        {'image_path': 'images/a.png', 'image_sha256': sha256(image)}]}
    plan = {'images': [{}], 'unresolved': [], 'limitations': []}
    publish(task, source, imported, attempt, plan, result, 'fixture')
    assert len([p for p in (task/'reconstructed').rglob('*') if p.is_file()]) == 2
    assert existing_output(task, source) == 'already_present'
    with pytest.raises(FileExistsError):
        publish(task, source, imported, attempt, plan, result, 'fixture')
    source.write_text('{"changed": true}')
    assert existing_output(task, source) == 'existing_output_requires_review'
