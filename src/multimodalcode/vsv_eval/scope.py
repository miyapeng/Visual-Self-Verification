"""Locate the initial implementation without using task success as its definition."""
from __future__ import annotations

import json
from pathlib import Path

from multimodalcode.io import read_json, write_json
from .catalogue import content_hash


PROMPT = """Locate the earliest complete implementation attempt in this coding trajectory.
An implementation attempt addresses the requested deliverable as a whole and could be
submitted to task evaluation. It need not build, run, pass tests, or be correct. A scaffold,
plan, isolated component, or incomplete code write is not a complete attempt. For maintenance
tasks, use the first completed proposed change to the supplied project. Use recorded actions
and returns; a completion claim alone is insufficient. Return the original event ID at the
boundary immediately after that attempt has been produced, before its subsequent checks.
Return null if no complete implementation attempt exists. Do not use the first successful
check, first screenshot, or final submission as an automatic boundary. Do not judge quality.
Return only {"initial_implementation_event_id": integer or null}.
TRAJECTORY:
"""


def validate_scope(value, rounds):
    if not isinstance(value, dict) or set(value) != {'initial_implementation_event_id'}:
        raise ValueError('Scope must contain only initial_implementation_event_id')
    boundary = value['initial_implementation_event_id']
    ids = {e['ordinal'] for e in rounds['events']}
    if boundary is not None and (type(boundary) is not int or boundary not in ids):
        raise ValueError('Initial implementation boundary must reference an original event')
    return dict(value)


def scope_packet(rounds, task):
    from .checks import _call_id
    calls = {_call_id(e): e for e in rounds['events'] if e['kind'] == 'action' and _call_id(e)}
    events = []
    for event in rounds['events']:
        row = dict(event)
        source = calls.get(_call_id(event))
        if source and row.get('source_action_payload') == source.get('payload'):
            row.pop('source_action_payload', None)  # Already present verbatim in the paired call.
        events.append(row)
    return {'task': task, 'events': events}


def locate_scope(rounds, task, judge, output, *, max_input_chars=500000):
    """One cached annotation. Never truncate code when deciding completeness."""
    packet = scope_packet(rounds, task)
    prompt = PROMPT + json.dumps(packet, ensure_ascii=False)
    if len(prompt) > max_input_chars:
        raise ValueError('Initial-implementation input exceeds its budget; increase scope_max_input_chars '
                         'only with a judge context that fits the original trajectory')
    schema = {'type': 'object', 'properties': {'initial_implementation_event_id': {
        'type': ['integer', 'null'], 'enum': [e['ordinal'] for e in rounds['events']] + [None]}},
        'required': ['initial_implementation_event_id'], 'additionalProperties': False}
    client = judge('implementation_boundary', schema)
    record = client.judge('implementation_boundary', prompt, [],
                          context={'source_run_sha256': rounds['source_run_sha256']})
    if record.get('error'):
        raise ValueError(record['error'])
    result = {**validate_scope(record['parsed'], rounds), 'input_sha256': content_hash(packet),
              'request_sha256': record['request_sha256'],
              'response_path': str((Path(client.cache_root) / (record['request_sha256'] + '.json')).resolve())}
    write_json(Path(output) / 'scope.json', result)
    return result


def validate_saved_scope(scope, rounds, task):
    packet = scope_packet(rounds, task)
    record = read_json(scope['response_path'])
    if (content_hash(packet) != scope['input_sha256'] or record.get('error')
            or record['request_sha256'] != scope['request_sha256']
            or record.get('stage') != 'implementation_boundary'
            or record['prompt'] != PROMPT + json.dumps(packet, ensure_ascii=False)
            or validate_scope(record['parsed'], rounds)['initial_implementation_event_id']
               != scope['initial_implementation_event_id']):
        raise ValueError('Initial implementation scope differs from its original annotation')


def scoped_rounds(rounds, scope):
    """Retain pre-boundary events as context without counting their checks."""
    boundary = scope['initial_implementation_event_id']
    selected = []
    events = {e['ordinal']: e for e in rounds['events']}
    for episode in rounds['episodes']:
        actions = [eid for eid in episode['core_event_ids'] if events[eid]['kind'] == 'action']
        if boundary is None or not actions or max(actions) <= boundary:
            continue
        if min(actions) <= boundary:
            raise ValueError('A check round crosses the initial implementation boundary; re-extract that round')
        selected.append(episode)
    return {**rounds, 'episodes': selected}
