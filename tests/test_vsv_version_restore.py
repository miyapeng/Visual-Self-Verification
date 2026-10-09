import json
from pathlib import Path

import pytest

from multimodalcode.vsv_eval.version_restore import restore_versions


def record(tmp_path, events):
    path = tmp_path / 'run.json'
    path.write_text(json.dumps({'timeline': events}))
    return path


def action(ordinal, tool, payload):
    return {'ordinal': ordinal, 'kind': 'action', 'tool': tool,
            'tool_call_id': str(ordinal), 'payload': payload}


def reply(ordinal, call, error=False):
    return {'ordinal': ordinal, 'kind': 'observation',
            'payload': {'tool_use_id': str(call)}, 'is_error': error}


def test_failed_write_and_literal_shell_retry_preserve_original_ids(tmp_path):
    events = [action(2, 'Write', {'file_path': '/work/a.py', 'content': 'wrong'}),
              reply(3, 2, True),
              action(6, 'Bash', {'command': "cat > /work/a.py << 'EOF'\nprint('$HOME')\nEOF\necho done"}),
              reply(7, 6)]
    source = record(tmp_path, events)
    original = source.read_bytes()
    result = restore_versions(source, {'scope': 'recorded_files', 'source_root': '/work',
                                      'event_ids': [2, 6]}, tmp_path / 'versions')
    assert result['skipped_failed_event_ids'] == [2]
    assert len(result['versions']) == 1  # No invented empty baseline.
    version = result['versions'][0]
    assert version['after_event'] == 6
    assert version['edits'][0]['return_event_id'] == 7
    assert (Path(version['workspace']) / 'app/a.py').read_text() == "print('$HOME')\n"
    assert source.read_bytes() == original


def test_sdk_edit_uses_paired_return_and_keeps_before_after_versions(tmp_path):
    base = tmp_path / 'base'; base.mkdir(); (base / 'a.js').write_text('before')
    events = [action(4, 'file_editor', {'command': 'str_replace', 'path': '/repo/a.js',
                                     'old_str': 'before', 'new_str': 'after'}),
              reply(5, 999, True), reply(6, 4)]
    result = restore_versions(record(tmp_path, events), {'scope': 'repository',
        'source_root': '/repo', 'base_snapshot': str(base), 'event_ids': [4]}, tmp_path / 'versions')
    before, after = result['versions']
    assert [v['after_event'] for v in result['versions']] == [3, 4]
    assert before['code_manifest'] != after['code_manifest']
    assert (base / 'a.js').read_text() == 'before'
    assert (Path(after['workspace']) / 'app/a.js').read_text() == 'after'


@pytest.mark.parametrize('path,old', [('/repo/../escape', 'x'), ('/repo/a', 'absent'), ('/repo/a', 'x')])
def test_unsafe_paths_and_ambiguous_edits_are_rejected(tmp_path, path, old):
    base = tmp_path / 'base'; base.mkdir(); (base / 'a').write_text('xx')
    events = [action(1, 'Edit', {'file_path': path, 'old_string': old, 'new_string': 'z'}), reply(2, 1)]
    with pytest.raises(ValueError):
        restore_versions(record(tmp_path, events), {'scope': 'repository', 'source_root': '/repo',
                         'base_snapshot': str(base), 'event_ids': [1]}, tmp_path / 'out')
    assert (base / 'a').read_text() == 'xx'


def test_copy_preserves_the_original_missing_path_state(tmp_path):
    events = [action(1, 'Edit', {'file_path': '/repo/a.py', 'old_string': '', 'new_string': 'value'}),
              reply(2, 1), action(5, 'Bash', {'command': 'cp /repo/a.py /repo/nested/a.py && ls'}), reply(6, 5)]
    result = restore_versions(record(tmp_path, events), {'scope': 'recorded_files',
                             'source_root': '/repo', 'event_ids': [1, 5]}, tmp_path / 'out')
    assert [v['after_event'] for v in result['versions']] == [1, 4, 5]
    assert not (Path(result['versions'][1]['workspace']) / 'app/nested/a.py').exists()
    assert (Path(result['versions'][2]['workspace']) / 'app/nested/a.py').read_text() == 'value'
    assert result['versions'][2]['edits'][-1]['kind'] == 'shell_mutation'


def test_recorded_full_file_contents_reject_a_wrong_base(tmp_path):
    base = tmp_path / 'base'; base.mkdir(); (base / 'a').write_text('prefix old')
    events = [action(1, 'file_editor', {'command': 'str_replace', 'path': '/repo/a',
                                      'old_str': 'old', 'new_str': 'new'}), reply(2, 1)]
    events[1]['payload'].update(old_content='different old', new_content='different new')
    with pytest.raises(ValueError, match='old_content'):
        restore_versions(record(tmp_path, events), {'scope': 'repository', 'source_root': '/repo',
                         'base_snapshot': str(base), 'event_ids': [1]}, tmp_path / 'out')


def test_nonzero_shell_return_does_not_create_a_version(tmp_path):
    events = [action(1, 'Bash', {'command': "cat > /repo/a << 'EOF'\nx\nEOF"}), reply(2, 1)]
    events[1]['payload']['exit_code'] = 1
    result = restore_versions(record(tmp_path, events), {'scope': 'recorded_files',
                             'source_root': '/repo', 'event_ids': [1]}, tmp_path / 'out')
    assert result['versions'] == []
    assert result['skipped_failed_event_ids'] == [1]
