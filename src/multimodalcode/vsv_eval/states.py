"""Shared acceptance judgments on audited versions."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from multimodalcode.io import read_json, write_json
from .catalogue import content_hash, criteria_hash, image_views, load_catalogue, validate_catalogue, model_review_response, REQUIREMENT_POLICY, validate_prepared_repair_bindings
from .evaluation import load_rounds
from .metrics import PROTOCOL_VERSION
from .judge import build_client, load_judge_config, rows_schema
from .repair_pilot import prepare_versions, digest_tree, extract_run_code, assess_output
from .replay import replay_version
from .acceptance import GUI_SCHEMA, validate_workflows, validate_execution


STATE_PROMPT = REQUIREMENT_POLICY + """Apply every supplied acceptance criterion to THIS artifact version.
Use recorded execution feedback and attached evaluator observations. Agent claims, code edits,
and other versions are not proof. Apply the same task-grounded tolerances before and after editing.
Accept minor presentation variants only when they preserve the specified meaning and functionality;
missing content, unreadable controls and broken interactions remain failures. Exact wording is
required when the frozen criterion explicitly requires it.
Static images cannot prove unexecuted behavior. Recorded actions, destinations and assertions
can establish interactions without a screenshot at every step. Check the specified object at
readable scale rather than substituting a nearby component. Reference images define requirements.
Cite the result node assigned to each requirement. Preceding nodes in the same workflow may supply
context; unrelated workflows and later nodes cannot. Preserve rule_facts exactly.
Use pass or fail when observations decide the criterion. Use unknown for genuinely insufficient
observations or evaluator/runtime faults. Blocked tool actions and evaluation_network_deadline
alone do not prove an application defect. They require completing the acceptance evidence.
Return all supplied IDs once, without repair plausibility, metric-specific labels, or scores.
Return only {"checks":[{"check_id":"...","state":"pass|fail|unknown","evidence_ids":["execution ID"]}]}.
EVIDENCE:
"""
STATE_SCHEMA = rows_schema('checks', {'check_id':{'type':'string'}, 'state':{'type':'string','enum':['pass','fail','unknown']},
                                    'evidence_ids':{'type':'array','items':{'type':'string'}}})


def state_evidence_scope(packet, check_id):
    """Allow an assigned node and its preceding workflow observations as context."""
    core, allowed, preceding = set(), set(), {}
    for row in packet['evidence']:
        eid = row['evidence_id']
        if 'check_ids' not in row:
            allowed.add(eid)
            continue
        workflow = row.get('workflow_id', eid)
        preceding.setdefault(workflow, set()).add(eid)
        if check_id in row['check_ids']:
            core.add(eid)
            allowed.update(preceding[workflow])
    # Additional static acceptance uses existing captures, not invented executions.
    assigned = set(packet.get('evidence_assignments', {}).get(check_id, []))
    core.update(assigned)
    allowed.update(assigned)
    return core, allowed


def acceptance_packet(packet, *, check_ids=None):
    """Select evidence once; acceptance never depends on its eventual metric role."""
    ids = {c['check_id'] for c in packet['checks']}
    if check_ids is not None:
        if len(check_ids) != len(set(check_ids)) or not set(check_ids) <= ids:
            raise ValueError('Acceptance input includes duplicate or unknown checks')
        ids = set(check_ids)
    checks = [c for c in packet['checks'] if c['check_id'] in ids]
    allowed = set().union(*(state_evidence_scope(packet, cid)[1] for cid in ids))
    evidence = [e for e in packet['evidence'] if e['evidence_id'] in allowed]
    references = {rid for c in checks for rid in c['reference_ids']}
    views = [v for v in packet['image_order']
             if (v['role'] == 'reference' and v['reference_id'] in references)
             or (v['role'] == 'evaluator_observation' and v['evidence_id'] in allowed)]
    return {**packet, 'checks': checks, 'evidence': evidence,
            'image_order': [{**v, 'attachment_index': i + 1} for i, v in enumerate(views)],
            'rule_facts': [f for f in packet['rule_facts'] if f['check_id'] in ids]}


def artifact_identity(version, runtime):
    workspace = Path(version['workspace'])
    code = digest_tree(workspace / runtime.get('code_subdir', 'app'))
    if code != version['code_manifest']:
        raise ValueError('Version bytes differ from their audited manifest')
    for relative, target in version.get('material_links', {}).items():
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Unsafe checkpoint material link')
        link = Path(version['workspace']) / runtime.get('code_subdir', 'app') / relative
        if not link.is_symlink() or link.resolve() != Path(target).resolve():
            raise ValueError('Checkpoint material link changed after reconstruction')
    resource_root = version.get('resource_root')
    if version.get('resource_path'):
        mount = workspace / runtime.get('code_subdir', 'app') / version['resource_path']
        if not mount.is_symlink() or mount.resolve() != Path(resource_root).resolve():
            raise ValueError('Resource placement differs from its audited manifest')
    resources = digest_tree(Path(resource_root)) if resource_root else None
    if resource_root:
        # A served file's bytes matter even when its resource path is a symlink.
        for path in Path(resource_root).rglob('*'):
            if path.is_symlink():
                if path.is_dir() or not path.is_file():
                    raise ValueError('Resource directory links need an explicitly resolved resource root')
                resources['files'][path.relative_to(resource_root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        resources['sha256'] = hashlib.sha256(json.dumps(resources['files'],sort_keys=True).encode()).hexdigest()
    return content_hash({'code': code, 'resources': resources, 'runtime': runtime,
                         **({'material_links': version['material_links']} if version.get('material_links') else {}),
                         **({'resource_path': version['resource_path']} if version.get('resource_path') else {})})


def state_packet(catalogue, version, replay, adapters, output):
    evidence, image_order, images, facts = [], [], [], []
    workflows = replay.get('workflows', [])
    visual_ids = {c['check_id'] for w in workflows for n in w['nodes']
                  if n['run_status'] == 'completed' for c in n['checks'] if not c['assertions']}
    reference_ids = {rid for check in catalogue['checks'] if not workflows or check['check_id'] in visual_ids
                     for rid in check['reference_ids']}

    def attach(observation, eid):
        paths = ([observation['screenshot']] if observation.get('screenshot') else []) + observation.get('screenshots', [])
        for path in paths:
            for view in image_views(path, Path(output) / 'image_views'):
                image_order.append({'role': 'evaluator_observation', 'origin': 'independent',
                                    'evidence_id': eid, **view, 'attachment_index': len(images) + 1})
                images.append(view['path'])
    for rid in sorted(reference_ids):
        path = catalogue['references'][rid]
        if not Path(path).is_file():
            continue
        for view in image_views(path, Path(output) / 'image_views'):
            image_order.append({'role': 'reference', 'reference_id': rid, **view, 'attachment_index': len(images) + 1})
            images.append(view['path'])
    for route, row in replay['routes'].items():
        eid = f"{version['version']}:route:{route}"
        evidence.append({'evidence_id': eid, 'route': route, **{k:v for k,v in row.items() if k != 'screenshot'}})
        if row.get('screenshot') and row['status'] == 'executed':
            for view in image_views(row['screenshot'], Path(output) / 'image_views'):
                image_order.append({'role': 'evaluator_observation', 'evidence_id': eid, 'route': route,
                                    **view, 'attachment_index': len(images) + 1})
                images.append(view['path'])
    checks = {row['name']: row for row in replay['checks']}
    for name, row in checks.items():
        eid = f"{version['version']}:check:{name}"
        evidence.append({'evidence_id': eid, 'status': row['status'], 'source_function': row['source_function'],
                         'output': row.get('output'), 'steps': row.get('steps', []), 'reset_route': row['reset_route']})
        for index, step in enumerate(row.get('steps', [])):
            if step.get('observation'):
                sid = f'{eid}:step:{index}'
                evidence.append({'evidence_id': sid, 'observation': step['observation'], 'action': step.get('action')})
                attach(step['observation'], sid)
    assigned = set()
    for workflow in workflows:
        if workflow.get('initial'):
            initial_id = f"{version['version']}:workflow:{workflow['workflow_id']}:initial"
            evidence.append({'evidence_id': initial_id, 'check_ids': [],
                             'workflow_id': workflow['workflow_id'], 'observation': workflow['initial']})
            attach(workflow['initial'], initial_id)
        for node in workflow['nodes']:
            eid = node['evidence_id']
            assigned.update(node['check_ids'])
            evidence.append({**node, 'origin': 'independent', 'workflow_id': workflow['workflow_id'],
                             'setup': workflow['setup']})
            labelled = {c['check_id']: c for c in node['checks']}
            for cid in node['check_ids']:
                assertions = labelled.get(cid, {}).get('assertions', [])
                if assertions:
                    if any(type(a.get('passed')) is not bool for a in assertions):
                        raise ValueError('Invalid executable assertion result')
                    facts.append({'check_id': cid, 'state': 'pass' if all(a['passed'] for a in assertions) else 'fail',
                                  'evidence_ids': [eid]})
                elif node['run_status'] != 'completed':
                    facts.append({'check_id': cid, 'state': 'unknown', 'evidence_ids': [eid]})
            if set(node['check_ids']) & visual_ids and node.get('observation'):
                attach(node['observation'], eid)
    if workflows and not replay['routes']:
        # A small pilot cannot stand in for acceptance of the entire fixed catalogue.
        facts.extend({'check_id': c['check_id'], 'state': 'unknown', 'evidence_ids': []}
                     for c in catalogue['checks'] if c['check_id'] not in assigned)
    for check_id, adapter in adapters.items():
        name = adapter['check']
        row = checks.get(name, {})
        if row.get('status') != 'executed':
            continue
        try:
            data = json.loads(row['output']) if isinstance(row['output'],str) else row['output']
        except (ValueError, KeyError):
            continue
        passed = assess_output(adapter['assertions'], data)
        if passed is not None:
            facts.append({'check_id': check_id, 'state': 'pass' if passed else 'fail',
                          'evidence_ids': [f"{version['version']}:check:{name}"]})
    unresolved = set(catalogue.get('unresolved_check_ids', []))
    facts = [f for f in facts if f['check_id'] not in unresolved]
    facts.extend({'check_id': cid, 'state': 'unknown', 'evidence_ids': []} for cid in sorted(unresolved))
    return {'checks': catalogue['checks'], 'criteria_sha256': catalogue['criteria_sha256'],
            'version': version['version'], 'evidence': evidence, 'image_order': image_order, 'rule_facts': facts}, images



def state_check_groups(packet, checks):
    """Keep each metric call within one independently executed workflow."""
    groups = {}
    for check in checks:
        cid = check['check_id']
        workflows = {row.get('workflow_id', 'recorded') for row in packet['evidence']
                     if cid in row.get('check_ids', [])}
        if len(workflows) > 1:
            raise ValueError('A state check belongs to multiple workflows')
        groups.setdefault(next(iter(workflows), 'recorded'), []).append(cid)
    return list(groups.values())


def validate_states(value, packet):
    if not isinstance(value, dict) or set(value) != {'checks'} or not isinstance(value['checks'], list):
        raise ValueError('Invalid state response')
    allowed = {c['check_id'] for c in packet['checks']}
    evidence = {e['evidence_id'] for e in packet['evidence']}
    facts = {f['check_id']: f for f in packet['rule_facts']}
    seen, rows = set(), []
    for row in value['checks']:
        if not isinstance(row, dict) or set(row) != {'check_id','state','evidence_ids'}:
            raise ValueError('Invalid state fields')
        if row['check_id'] not in allowed or row['check_id'] in seen or row['state'] not in {'pass','fail','unknown'}:
            raise ValueError('Invalid or duplicate state check')
        ids = row['evidence_ids']
        if not isinstance(ids, list) or any(not isinstance(eid, str) or eid not in evidence for eid in ids) or len(ids) != len(set(ids)):
            raise ValueError('Unknown execution evidence ID')
        if row['state'] != 'unknown' and not ids:
            raise ValueError('Decidable state needs actual execution evidence')
        core, context = state_evidence_scope(packet, row['check_id'])
        if not set(ids) <= context:
            raise ValueError('State evidence belongs to a different workflow target')
        if row['state'] != 'unknown' and core and not core.intersection(ids):
            raise ValueError('Decidable state must cite its assigned workflow node')
        if row['check_id'] in facts and row['state'] != facts[row['check_id']]['state']:
            raise ValueError('State judge contradicted an exact assertion')
        rows.append(row)
        seen.add(row['check_id'])
    if seen != allowed:
        raise ValueError('State evaluation omitted fixed checks')
    return rows



def transition_mutations(before, after, events):
    shell = {r['ordinal'] for r in after.get('edits', []) if r.get('kind') in {'resource_move', 'shell_mutation'}}
    return {eid for eid, e in events.items() if before['after_event'] < eid <= after['after_event']
            and e['kind'] == 'action' and (e.get('category') == 'edit' or e.get('tool') in {'Edit', 'Write'} or eid in shell)}


def validate_transition(transition, versions, events, episodes, goals):
    before, after = versions[transition['before']], versions[transition['after']]
    edits = transition['repair_event_ids']
    if not edits or edits != sorted(set(edits)):
        raise ValueError('Repair edits must be ordered original IDs')
    if before['after_event'] >= min(edits) or after['after_event'] != max(edits):
        raise ValueError('Use the exact state before the first and after the last linked edit')
    between = transition_mutations(before, after, events)
    context = transition.get('context_edit_event_ids', [])
    if (not isinstance(context, list) or context != sorted(set(context)) or set(context) & set(edits)
            or between != set(edits) | set(context)):
        raise ValueError('A repair transition mixes in unrelated recorded edits without explicit context')
    if not set(transition['target_ids']) <= goals:
        raise ValueError('Repair targets require fixed, certified criteria')
    for episode_id in transition['episode_ids']:
        if episode_id not in episodes or not any(link['repair_event_ids'] == edits for link in episodes[episode_id]['repair_links']):
            raise ValueError('Transition does not match an extracted repair link')
    aliases = transition.get('target_aliases', {}).values()
    aliases = list(aliases)
    if any(not isinstance(value, str) and (not isinstance(value, list)
           or any(not isinstance(goal, str) for goal in value)) for value in aliases):
        raise ValueError('Repair aliases must contain criterion IDs')
    linked = [goal for value in aliases for goal in ([value] if isinstance(value, str) else value)]
    if not set(linked) <= set(transition['target_ids']):
        raise ValueError('Repair aliases must point to supplied fixed repair criteria')


def evaluate_states(rounds_path, catalogue_path, spec_path, config_path, output, *,
                    primary_profile=None, allow_draft=False, offline=False, port=18951, workers=1):
    rounds = load_rounds(rounds_path)
    catalogue = load_catalogue(catalogue_path, allow_draft=allow_draft)
    spec, output = read_json(spec_path), Path(output).resolve()
    if spec.get('review', {}).get('status') == 'model_reviewed':
        reviewed = model_review_response(spec['review'])
        validate_prepared_repair_bindings(spec, reviewed)
        if (reviewed['checks'] != catalogue['checks']
                or any(reviewed[key] != spec.get(key, []) for key in
                       ('repair_checks', 'acceptance_workflows', 'unresolved_check_ids'))):
            raise ValueError('Acceptance plan differs from its recorded model review')
    state_catalogue = {**catalogue, 'checks': catalogue['checks'] + spec.get('repair_checks', []),
                       'unresolved_check_ids': sorted(set(catalogue.get('unresolved_check_ids', []))
                                                    | set(spec.get('unresolved_check_ids', [])))}
    state_catalogue['criteria_sha256'] = criteria_hash(state_catalogue['checks'], state_catalogue['unresolved_check_ids'])
    if spec.get('repair_checks'):
        state_catalogue['review'] = spec.get('review', {'status': 'draft'})
        validate_catalogue(state_catalogue, allow_draft=allow_draft)
    output.mkdir(parents=True, exist_ok=True)
    previous = output / 'repair_results.json'
    if previous.is_file() and read_json(previous).get('protocol_version') != PROTOCOL_VERSION:
        raise ValueError('Acceptance output uses an older protocol; use a new output directory')
    frozen = output / 'spec.json'
    if frozen.exists() and read_json(frozen) != spec:
        raise ValueError('State specification changed; choose a fresh output directory')
    write_json(frozen, spec)
    manifest_path = output / 'versions/reconstruction.json'
    manifest = read_json(manifest_path) if manifest_path.exists() else prepare_versions(spec['fixture'], spec, output / 'versions')
    if manifest['source_run_sha256'] != rounds['source_run_sha256']:
        raise ValueError('Versions and rounds belong to different trajectories')
    versions = {v['version']: v for v in manifest['versions']}
    if 'version_ids' in spec:
        selected = spec['version_ids']
        if not isinstance(selected, list) or not selected or len(set(selected)) != len(selected) or not set(selected) <= set(versions):
            raise ValueError('version_ids must select existing, unique audited versions')
        versions = {name: versions[name] for name in selected}
        if any(t[v] not in versions for t in spec['transitions'] for v in ('before', 'after')):
            raise ValueError('Every repair transition must have both selected versions')
    events = {e['ordinal']: e for e in rounds['events']}
    definitions = []
    for check in spec.get('functional_checks', []):
        source = events[check['ordinal']]
        if source['kind'] != 'action' or source.get('tool') != 'Bash':
            raise ValueError('Functional probe must cite a reviewed original tool action')
        definitions.append({**check, 'body': extract_run_code(source['payload']['command'])})
    runtime = spec['runtime']
    model_config = load_judge_config(config_path)
    workflows = spec.get('acceptance_workflows')
    command_mode = runtime.get('kind') == 'command'
    gui_judge = None
    if command_mode:
        from .artifact_acceptance import validate_assignments, replay_commands
        validate_assignments(workflows, runtime, state_catalogue)
    elif workflows is not None:
        validate_workflows(workflows, state_catalogue,
                           model_reviewed=allow_draft or spec.get('review', {}).get('status') == 'model_reviewed')
        limits = spec.get('execution_limits', {'timeout_ms': 5000, 'max_ui_actions': 30})
        if set(limits) != {'timeout_ms', 'max_ui_actions'} or any(type(v) is not int or v <= 0 for v in limits.values()):
            raise ValueError('Execution limits must be positive integer budgets')
        if not offline:
            gui_judge = build_client(model_config, primary_profile or model_config['primary_stage_profiles']['gui_step'],
                                     output / 'judge_cache/gui_step', response_schema=GUI_SCHEMA)
    goals = {c['check_id'] for c in state_catalogue['checks']}
    if not set(spec.get('assertion_adapters', {})) <= goals:
        raise ValueError('Assertion adapters must use fixed catalogue targets')
    if workers < 1:
        raise ValueError('workers must be positive')
    records, execution_paths, identities, packets = {}, {}, {}, {}
    def replay_one(item):
        index, (name, version) = item
        version_port = port + index
        # JudgeClient keeps per-request counters; do not share an instance across replays.
        local_gui_judge = (build_client(model_config, primary_profile or model_config['primary_stage_profiles']['gui_step'],
                                      output / 'judge_cache/gui_step', response_schema=GUI_SCHEMA)
                           if gui_judge is not None else None)
        print('state', name, flush=True)
        identity = artifact_identity(version, runtime)
        identities[name] = identity
        browser_spec = {'base_url': f'http://127.0.0.1:{version_port}', 'routes': spec['routes'], 'checks': definitions,
                        'runtime': runtime, 'code_sha256': version['code_manifest']['sha256'],
                        'artifact_sha256': identity, 'criteria_sha256': state_catalogue['criteria_sha256']}
        if workflows is not None and not command_mode:
            browser_spec.update(acceptance_workflows=workflows, execution_limits=limits, version_id=name,
                                gui_profile=gui_judge.profile.generation_settings() if gui_judge else None,
                                gui_model=gui_judge.profile.model if gui_judge else None,
                                gui_endpoint=gui_judge.profile.base_url if gui_judge else None)
        if command_mode:
            replay = replay_commands(version, {'runtime': runtime, 'acceptance_workflows': workflows},
                                     output / 'replays' / name)
        else:
            replay = replay_version(version, browser_spec, output / 'replays' / name, version_port,
                                    **({'gui_judge': local_gui_judge} if workflows is not None else {}))
        if workflows is not None and not command_mode:
            validate_execution(workflows, replay, name)
        if artifact_identity(version, runtime) != identity:
            raise ValueError('Replay modified the evaluated artifact')
        execution_paths[name] = str(output / 'replays' / name / 'result.json')
        packet, images = state_packet(state_catalogue, version, replay, spec.get('assertion_adapters', {}), output)
        packet['artifact_sha256'] = identity
        write_json(output / 'inputs' / f'{name}.json', packet)
        packets[name] = (packet, images)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(replay_one, enumerate(versions.items())))

    def assess(name):
        print('acceptance judge', name, flush=True)
        packet, images = packets[name]
        exact = {f['check_id']: f for f in packet['rule_facts']}
        pending = [c for c in packet['checks'] if c['check_id'] not in exact]
        rows, requests, status = list(exact.values()), [], 'exact'
        for group_index, group in enumerate(state_check_groups(packet, pending)):
            supplied = acceptance_packet(packet, check_ids=group)
            images = [v['path'] for v in supplied['image_order']]
            input_path = output / 'inputs' / f'{name}-acceptance-{group_index}.json'
            write_json(input_path, supplied)
            stage = ('visual' if images else 'text') + '_acceptance'
            record = None
            cache_root = output / 'judge_cache' / stage
            if not offline or cache_root.is_dir():
                schema = rows_schema('checks', {**STATE_SCHEMA['properties']['checks']['items']['properties'],
                                     'check_id': {'type': 'string', 'enum': group}})
                judge = build_client(model_config, primary_profile or model_config['primary_stage_profiles'][stage],
                                     cache_root, response_schema=schema)
                prompt = STATE_PROMPT + json.dumps(supplied, ensure_ascii=False)
                cache = cache_root / (judge._key(stage, prompt, images)+'.json')
                record = read_json(cache) if offline and cache.is_file() else None if offline else judge.judge(
                    stage, prompt, images, context={'version': name, 'input_path': str(input_path), 'check_ids': group})
            if record:
                if record.get('error'):
                    raise ValueError(record['error'])
                rows += validate_states(record['parsed'], supplied)
                if status != 'unassessed':
                    status = 'evaluated'
                requests.append({'stage': stage, 'input_path': str(input_path),
                                 'request_sha256': record['request_sha256'],
                                 'response_path': str(cache_root / (record['request_sha256']+'.json'))})
            else:
                status = 'unassessed'
                rows += [{'check_id': cid, 'state': 'unknown', 'evidence_ids': []} for cid in group]
        write_json(output / 'state_results' / f'{name}.json', {
            'protocol_version': PROTOCOL_VERSION, 'artifact_sha256': identities[name],
            'checks': rows, 'requests': requests, 'status': status})
        return name, rows

    with ThreadPoolExecutor(max_workers=workers) as pool:
        records.update(pool.map(assess, versions))
    repairs = []
    episodes = {ep['episode_id']: ep for ep in rounds['episodes']}
    for transition in spec['transitions']:
        validate_transition(transition, versions, events, episodes, goals)
        left, right = [{r['check_id']: r for r in records[v]} for v in (transition['before'], transition['after'])]
        ids = set(transition['target_ids']) | {c['check_id'] for c in catalogue['checks']}
        states = [{'check_id': cid, 'before': left[cid]['state'], 'after': right[cid]['state'],
                   'before_evidence_ids': left[cid]['evidence_ids'], 'after_evidence_ids': right[cid]['evidence_ids']}
                  for cid in sorted(ids)]
        repairs.append({**transition, 'states': states})
    result = {'schema': 'self-verification-repair-results-2', 'source_run_sha256': rounds['source_run_sha256'],
              'protocol_version': PROTOCOL_VERSION, 'mode': 'offline' if offline else 'online',
              'status': 'incomplete' if any(r['state'] == 'unknown' for rows in records.values() for r in rows) else 'evaluated',
              'judgment_method': 'shared_acceptance',
              'criteria_sha256': catalogue['criteria_sha256'], 'spec': str(frozen), 'spec_sha256': content_hash(spec),
              'manifest': str(manifest_path), 'artifact_identities': identities, 'replays': execution_paths,
              'execution_sha256': {v: hashlib.sha256(Path(p).read_bytes()).hexdigest() for v,p in execution_paths.items()},
              'state_results': {v: str(output/'state_results'/f'{v}.json') for v in records}, 'repairs': repairs}
    result['state_result_sha256'] = {v: hashlib.sha256(Path(p).read_bytes()).hexdigest() for v,p in result['state_results'].items()}
    result['inputs'] = {v: str(output/'inputs'/f'{v}.json') for v in records}
    result['input_sha256'] = {v: hashlib.sha256(Path(p).read_bytes()).hexdigest() for v,p in result['inputs'].items()}
    write_json(output / 'repair_results.json', result)
    return result


def load_repair_results(path, rounds_path, catalogue_path):
    value = read_json(path)
    rounds = load_rounds(rounds_path)
    catalogue = load_catalogue(catalogue_path, allow_draft=True)
    if value.get('schema') != 'self-verification-repair-results-2' or value.get('protocol_version') != PROTOCOL_VERSION or value['source_run_sha256'] != rounds['source_run_sha256'] or value['criteria_sha256'] != catalogue['criteria_sha256']:
        raise ValueError('Repair results refer to different source data or criteria')
    spec = read_json(value['spec'])
    if content_hash(spec) != value['spec_sha256']:
        raise ValueError('Repair execution specification changed')
    if spec.get('review', {}).get('status') == 'model_reviewed':
        validate_prepared_repair_bindings(spec, model_review_response(spec['review']))
    manifest = read_json(value['manifest'])
    if manifest['source_run_sha256'] != rounds['source_run_sha256'] or len(value['repairs']) != len(spec['transitions']):
        raise ValueError('Repair manifest or transition count differs from the execution specification')
    expected_versions = set(spec.get('version_ids', [v['version'] for v in manifest['versions']]))
    if set(value['artifact_identities']) != expected_versions:
        raise ValueError('Repair results omitted an evaluated version')
    for version in manifest['versions']:
        name = version['version']
        if name not in value['artifact_identities']:
            if name in spec.get('version_ids', []):
                raise ValueError('An evaluated version is missing from repair results')
            continue
        if artifact_identity(version, spec['runtime']) != value['artifact_identities'][name]:
            raise ValueError('Version or resources changed after execution')
        if hashlib.sha256(Path(value['replays'][name]).read_bytes()).hexdigest() != value['execution_sha256'][name]:
            raise ValueError('Replay evidence changed')
        if hashlib.sha256(Path(value['state_results'][name]).read_bytes()).hexdigest() != value['state_result_sha256'][name]:
            raise ValueError('State labels changed after evaluation')
        if hashlib.sha256(Path(value['inputs'][name]).read_bytes()).hexdigest() != value['input_sha256'][name]:
            raise ValueError('State criteria or evidence packet changed')
        packet = read_json(value['inputs'][name])
        for image in packet['image_order']:
            if hashlib.sha256(Path(image['path']).read_bytes()).hexdigest() != image['sha256'] or hashlib.sha256(Path(image['source_path']).read_bytes()).hexdigest() != image['source_sha256']:
                raise ValueError('State image evidence changed')
        row = read_json(value['state_results'][name])
        exact = {f['check_id']: f for f in packet['rule_facts']}
        if row.get('protocol_version') != PROTOCOL_VERSION:
            raise ValueError('Acceptance labels use an older protocol')
        pending = {c['check_id'] for c in packet['checks']} - set(exact)
        labels, seen = list(exact.values()), set()
        for request in row['requests']:
            recorded_input = read_json(request['input_path'])
            selected = [c['check_id'] for c in recorded_input['checks']]
            if len(selected) != len(set(selected)) or set(selected) & seen or not set(selected) <= pending:
                raise ValueError('Acceptance requests duplicate or invent check IDs')
            expected = acceptance_packet(packet, check_ids=selected)
            record = read_json(request['response_path'])
            stage = ('visual' if expected['image_order'] else 'text') + '_acceptance'
            if (recorded_input != expected or record.get('error') or request['stage'] != stage
                    or record.get('stage') != stage or record['request_sha256'] != request['request_sha256']
                    or record['prompt'] != STATE_PROMPT + json.dumps(expected, ensure_ascii=False)):
                raise ValueError('Acceptance input differs from its original judge request')
            labels += validate_states(record['parsed'], expected)
            seen.update(selected)
        if pending - seen and row['status'] != 'unassessed':
            raise ValueError('Acceptance requests omitted fixed checks')
        labels += [{'check_id': c['check_id'], 'state': 'unknown', 'evidence_ids': []}
                   for c in packet['checks'] if c['check_id'] in pending - seen]
        if row['checks'] != labels:
            raise ValueError('State labels differ from exact assertions or original judge responses')
    for repair, transition in zip(value['repairs'], spec['transitions']):
        if any(repair.get(key) != expected for key,expected in transition.items()):
            raise ValueError('Repair relation differs from its executed transition')
        version_rows = [read_json(value['state_results'][v]) for v in (repair['before'],repair['after'])]
        states = [{r['check_id']: r for r in v['checks']} for v in version_rows]
        expected_ids = {c['check_id'] for c in catalogue['checks']} | set(transition['target_ids'])
        if {r['check_id'] for r in repair['states']} != expected_ids or len(repair['states']) != len(expected_ids):
            raise ValueError('Repair labels omitted or duplicated acceptance targets')
        for row in repair['states']:
            left, right = states[0][row['check_id']], states[1][row['check_id']]
            if (row['before'] != left['state'] or row['after'] != right['state']
                    or row.get('before_evidence_ids') != left['evidence_ids']
                    or row.get('after_evidence_ids') != right['evidence_ids']):
                raise ValueError('Repair label differs from shared version acceptance')
    return value['repairs']
