"""Bind recorded repairs to existing version manifests and model-reviewed tests."""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace

from multimodalcode.io import read_json, write_json
from .catalogue import load_catalogue, prepare_evaluation_plan, content_hash, model_review_response
from .evaluation import load_rounds
from .judge import build_client, load_judge_config
from .repair_pilot import digest_tree
from .states import validate_transition, transition_mutations, artifact_identity


def repair_groups(rounds):
    groups = {}
    events = {e['ordinal']: e for e in rounds['events']}
    for episode in rounds['episodes']:
        for link in episode['repair_links']:
            edits = tuple(link['repair_event_ids'])
            if not edits or list(edits) != sorted(set(edits)) or any(i not in events for i in edits):
                raise ValueError('Repair links require ordered original edit IDs')
            group = groups.setdefault(edits, {'id': 'repair-' + '-'.join(map(str, edits)),
                                               'repair_event_ids': list(edits), 'episode_ids': []})
            if episode['episode_id'] not in group['episode_ids']:
                group['episode_ids'].append(episode['episode_id'])
    return sorted(groups.values(), key=lambda g: g['repair_event_ids'])


def checkpoint_requests(rounds):
    boundaries = sorted({i for g in repair_groups(rounds)
                         for i in (min(g['repair_event_ids']) - 1, max(g['repair_event_ids']))})
    return [{'version': f'V{i}', 'after_event': eid} for i, eid in enumerate(boundaries)]


def load_acceptance_binding(path, rounds, *, require_versions=True):
    path = Path(path).resolve()
    binding = read_json(path)
    if set(binding) != {'fixture', 'version_manifest', 'runtime'}:
        raise ValueError('Acceptance binding requires fixture, version_manifest and runtime')
    for key in ('fixture', 'version_manifest'):
        binding[key] = str((path.parent / binding[key]).resolve())
    runtime = binding['runtime']
    if (not isinstance(runtime, dict) or not isinstance(runtime.get('command'), list)
            or not runtime['command'] or any(not isinstance(s, str) for s in runtime['command'])):
        raise ValueError('Runtime command must be a nonempty argument list')
    if runtime.get('kind') == 'command':
        from .artifact_acceptance import validate_runtime
        validate_runtime(runtime)
    for key in ('cwd', 'code_subdir'):
        relative = Path(runtime.get(key, '.' if key == 'cwd' else 'app'))
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Runtime paths must stay inside the version workspace')
    if require_versions:
        import hashlib
        source = Path(binding['fixture']) / 'trajectory/run.json'
        if hashlib.sha256(source.read_bytes()).hexdigest() != rounds['source_run_sha256']:
            raise ValueError('Acceptance fixture belongs to a different trajectory')
        manifest = read_json(binding['version_manifest'])
        if manifest['source_run_sha256'] != rounds['source_run_sha256']:
            raise ValueError('Version manifest belongs to a different trajectory')
        for version in manifest['versions']:
            artifact_identity(version, runtime)
    return binding


def build_transitions(rounds, manifest):
    if manifest['source_run_sha256'] != rounds['source_run_sha256']:
        raise ValueError('Version manifest belongs to a different trajectory')
    versions = manifest['versions']
    if len({v['version'] for v in versions}) != len(versions):
        raise ValueError('Duplicate version names')
    events = {e['ordinal']: e for e in rounds['events']}
    episodes = {e['episode_id']: e for e in rounds['episodes']}
    transitions = []
    for group in repair_groups(rounds):
        before = [v for v in versions if v['after_event'] == min(group['repair_event_ids']) - 1]
        after = [v for v in versions if v['after_event'] == max(group['repair_event_ids'])]
        if len(before) != 1 or len(after) != 1:
            raise ValueError(f"Missing exact before/after checkpoints for {group['id']}")
        row = {k: group[k] for k in ('episode_ids', 'repair_event_ids')}
        row.update(before=before[0]['version'], after=after[0]['version'], target_ids=[])
        context = sorted(transition_mutations(before[0], after[0], events) - set(group['repair_event_ids']))
        if context:
            row['context_edit_event_ids'] = context
        validate_transition(row, {v['version']: v for v in versions}, events, episodes, set())
        transitions.append(row)
    return transitions


def prepare_case_acceptance(rounds_path, catalogue_path, checks_path, binding_path,
                            task_root, config_path, output, *, primary_profile=None):
    """Freeze actual transitions, then bind current target IDs and plan their tests."""
    rounds = load_rounds(rounds_path)
    catalogue = load_catalogue(catalogue_path, allow_draft=True)
    checks = read_json(checks_path)
    if (checks['source_run_sha256'] != rounds['source_run_sha256']
            or checks['criteria_sha256'] != catalogue['criteria_sha256']
            or [e['episode_id'] for e in checks['episodes']] != [e['episode_id'] for e in rounds['episodes']]
            or any(e['status'] not in {'evaluated', 'excluded'} for e in checks['episodes'])):
        raise ValueError('Repair planning requires complete current check labels')
    binding = load_acceptance_binding(binding_path, rounds)
    manifest = read_json(binding['version_manifest'])
    transitions = build_transitions(rounds, manifest)
    events = {e['ordinal']: e for e in rounds['events']}
    by_id = {e['episode_id']: e for e in checks['episodes']}
    targets = []
    by_edits = {tuple(t['repair_event_ids']): t for t in transitions}
    for group in repair_groups(rounds):
        targets.append({**group, 'edits': [events[i] for i in group['repair_event_ids']],
                        'context_edits': [events[i] for i in by_edits[tuple(group['repair_event_ids'])].get('context_edit_event_ids', [])],
                        'targets': [t for eid in group['episode_ids'] for t in by_id[eid]['targets']]})
    seed = {'fixture': binding['fixture'], 'runtime': binding['runtime'],
            'version_source': {'kind': 'manifest', 'path': binding['version_manifest']},
            'version_ids': sorted({t[k] for t in transitions for k in ('before', 'after')}),
            'transitions': transitions, 'repair_target_sha256': content_hash(targets),
            'criteria_sha256': catalogue['criteria_sha256']}
    output = Path(output).resolve()
    # Re-enter the existing cache on resume; never hand-mark a plan as reviewed.
    seed_path = output / 'binding_input.json'
    if seed_path.exists() and read_json(seed_path) != seed:
        raise ValueError('Acceptance inputs changed; use a fresh output directory')
    write_json(seed_path, seed)
    plan_path = output / 'plan/state_spec.json'
    if plan_path.exists():
        plan = read_json(plan_path)
        if any(plan.get(k) != v for k, v in seed.items() if k != 'transitions'):
            raise ValueError('Prepared acceptance binding changed')
        reviewed = model_review_response(plan['review'])
        if reviewed['checks'] != catalogue['checks']:
            raise ValueError('Prepared plan changed fixed criteria')
        from .catalogue import bind_plan_targets
        if bind_plan_targets(reviewed, targets, transitions) != plan['transitions']:
            raise ValueError('Prepared repair bindings changed')
        return plan_path
    config = load_judge_config(config_path)
    judge = build_client(config, primary_profile or config['primary_stage_profiles']['plan_review'],
                         output / 'judge_cache')
    settings = {}
    if 'plan_max_tokens' in config:
        budget = config['plan_max_tokens']
        if type(budget) is not int or budget <= 0:
            raise ValueError('plan_max_tokens must be a positive integer')
        settings['max_tokens'] = budget
    if 'plan_reasoning_effort' in config:
        effort = config['plan_reasoning_effort']
        if effort not in {None, 'low', 'medium', 'high'}:
            raise ValueError('Unsupported plan_reasoning_effort')
        settings['reasoning_effort'] = effort
    if settings:
        judge.profile = replace(judge.profile, **settings)
    prepare_evaluation_plan(task_root, judge, output / 'plan', catalogue_path=catalogue_path,
                                      state_spec_path=seed_path, repair_targets=targets)
    return output / 'plan/state_spec.json'


def publish_checkpoints(attempt, result, rounds, materials, binding_path):
    """Publish only complete, byte-verified exports through the existing manifest format."""
    binding = load_acceptance_binding(binding_path, rounds, require_versions=False)
    expected = checkpoint_requests(rounds)
    checkpoints = result.get('checkpoints', [])
    if [{k: v[k] for k in ('version', 'after_event')} for v in checkpoints] != expected:
        raise ValueError('Replay did not export all requested repair checkpoints')
    materials = Path(materials).resolve()
    versions = []
    for checkpoint in checkpoints:
        workspace = (Path(attempt) / 'output' / checkpoint['workspace']).resolve()
        if not workspace.is_relative_to((Path(attempt) / 'output').resolve()):
            raise ValueError('Checkpoint escaped replay output')
        root = workspace / 'app'
        if digest_tree(root) != checkpoint['code_manifest']:
            raise ValueError('Checkpoint bytes differ after container export')
        links = {'resources': 'resources', 'prototypes': 'prototypes', **checkpoint.get('material_links', {})}
        published_links = {}
        for relative, material in links.items():
            target = (materials / material).resolve()
            path = root / relative
            if (Path(relative).is_absolute() or '..' in Path(relative).parts
                    or not target.is_relative_to(materials)):
                raise ValueError('Material link escaped its roots')
            if target.exists() and not path.exists() and not path.is_symlink():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(target, target_is_directory=target.is_dir())
            if path.is_symlink():
                if path.resolve() != target:
                    raise ValueError('Checkpoint material link differs from its recorded source')
                published_links[relative] = str(target)
        versions.append({k: checkpoint[k] for k in ('version', 'after_event', 'code_manifest')} |
                        {'workspace': str(workspace), 'resource_root': str(materials / 'resources'), 'edits': checkpoint.get('edits', []),
                         'material_links': published_links})
    manifest = {'source_run_sha256': rounds['source_run_sha256'], 'versions': versions,
                'origin': 'recorded_action_replay', 'historical_equivalence': 'unverified'}
    build_transitions(rounds, manifest)
    destination = Path(binding['version_manifest'])
    if destination.exists():
        raise FileExistsError('Use a fresh acceptance binding for another checkpoint export')
    write_json(destination, manifest)
    return destination
