"""Six protocol metrics computed from evidence labels, never model scores."""
from __future__ import annotations


PROTOCOL_VERSION = "requirement-level-20261009"
BDA_DEFINITION = "macro_diagnosis_accuracy_over_present_classes"


def check_validity(method_ok, evidence_ok):
    if method_ok is False or evidence_ok is False:
        return False
    return True if method_ok is True and evidence_ok is True else None


def diagnosis_correct(actual_state, agent_state, issue_match):
    """A decidable observation requires a correct, definite diagnosis."""
    if actual_state == "unknown":
        return None
    if actual_state == "pass":
        return agent_state == "pass"
    if agent_state != "fail":
        return False
    return issue_match


def conjunction(values):
    values = list(values)
    if False in values:
        return False
    return True if values and all(value is True for value in values) else None


def proportion(values):
    values = list(values)
    success = sum(value is True for value in values)
    failure = sum(value is False for value in values)
    known = success + failure
    unknown = sum(value is None for value in values)
    total = len(values)
    known_score = 100 * success / known if known else None
    return {"score": known_score if not unknown else None, "known_score": known_score,
            "lower_bound": 100 * success / total if total else None,
            "upper_bound": 100 * (success + unknown) / total if total else None,
            "success_count": success, "failure_count": failure,
            "unknown_count": unknown, "denominator": known, "sample_count": total}


def diagnosis_bounds(targets, normal, error):
    """Bound the present-class mean without inventing unresolved verdicts."""
    unresolved = [t for t in targets if t['actual_state'] == 'unknown']
    positive = sum(t['agent_state'] == 'pass' for t in unresolved)
    negative = sum(t['agent_state'] == 'fail' for t in unresolved)
    missing = len(unresolved) - positive - negative
    lows, highs = [], []
    for assigned_normal in range(len(unresolved) + 1):
        n = normal['denominator'] + normal['unknown_count'] + assigned_normal
        e = error['denominator'] + error['unknown_count'] + len(unresolved) - assigned_normal
        if not n and not e:
            continue
        min_normal_correct = max(0, assigned_normal - negative - missing)
        max_normal_correct = min(assigned_normal, positive)
        max_error_correct = negative - max(0, assigned_normal - positive - missing)
        low = ([(normal['success_count'] + min_normal_correct) / n] if n else [])
        high = ([(normal['success_count'] + normal['unknown_count'] + max_normal_correct) / n] if n else [])
        if e:
            low.append(error['success_count'] / e)
            high.append((error['success_count'] + error['unknown_count'] + max_error_correct) / e)
        lows.append(100 * sum(low) / len(low))
        highs.append(100 * sum(high) / len(high))
    return (min(lows), max(highs)) if lows else (None, None)


def diagnosis_summary(targets):
    groups = {}
    for state in ("pass", "fail"):
        groups[state] = proportion(diagnosis_correct(t["actual_state"], t["agent_state"], t["issue_match"])
                                   for t in targets if t["actual_state"] == state)
    normal, error = groups["pass"], groups["fail"]
    available = [g['known_score'] for g in groups.values() if g['known_score'] is not None]
    known_score = sum(available) / len(available) if available else None
    unknown = normal['unknown_count'] + error['unknown_count'] + sum(t['actual_state'] == 'unknown' for t in targets)
    lower, upper = diagnosis_bounds(targets, normal, error)
    return {"score": known_score if not unknown else None, "known_score": known_score,
            "lower_bound": lower, "upper_bound": upper,
            "definition": BDA_DEFINITION,
            "normal": normal, "error": error,
            "absent_count": sum(t['agent_state'] == 'absent' for t in targets),
            "uncertain_count": sum(t['agent_state'] == 'uncertain' for t in targets),
            "success_count": normal["success_count"] + error["success_count"],
            "failure_count": normal["failure_count"] + error["failure_count"],
            "unknown_count": unknown}


def coverage_summary(coverage):
    total = len(coverage)
    full = sum(v == 'full' for v in coverage.values())
    unknown = sum(v == 'unknown' for v in coverage.values())
    return {'score': 100 * full / total if total and not unknown else None,
            'lower_bound': 100 * full / total if total else None,
            'upper_bound': 100 * (full + unknown) / total if total else None,
            'success_count': full, 'failure_count': total - full - unknown,
            'unknown_count': unknown, 'denominator': total,
            'is_lower_bound': bool(unknown),
            'partial_count': sum(v == 'partial' for v in coverage.values()), 'goals': coverage}


def repair_diagnostics(catalogue, episodes, repairs, repair_gaps):
    """Join diagnosis, local acceptance and regressions on the same recorded transition."""
    by_episode = {e['episode_id']: e for e in episodes}
    goals = [c['check_id'] for c in catalogue['checks']]
    diagnosed, regressions, transitions = [], [], []
    unverified_baselines = 0
    joint = {k: 0 for k in ('local_success_preserved', 'local_success_regressed',
                            'local_failure_preserved', 'local_failure_regressed',
                            'unresolved', 'not_applicable')}
    def correct_visual(t):
        return (t['modality'] != 'text' and t['actual_state'] == 'fail'
                and diagnosis_correct(t['actual_state'], t['agent_state'], t['issue_match']) is True)
    for repair in repairs:
        states = {r['check_id']: r for r in repair['states']}
        preservation = {r['check_id']: r for r in repair.get('preservation_states', repair['states'])}
        target_ids = set(repair['target_ids'])
        diagnosed_goals = set()
        visual_goals = {c['check_id'] for c in catalogue['checks'] if 'visual' in c.get('requirement_types', [])}
        for eid in set(repair['episode_ids']):
            for target in by_episode.get(eid, {}).get('targets', []):
                tid = target['target_id']
                aliases = repair.get('target_aliases', {}).get(tid, [tid] if tid in target_ids else [])
                aliases = [aliases] if isinstance(aliases, str) else aliases
                if target['modality'] != 'text':
                    visual_goals.update(aliases)
                if correct_visual(target):
                    diagnosed_goals.update(aliases)
        for cid in diagnosed_goals:
            row = states.get(cid, {})
            if row.get('before', 'unknown') == 'unknown':
                unverified_baselines += 1
            if row.get('before') == 'fail':
                after = row.get('after', 'unknown')
                diagnosed.append(None if after == 'unknown' else after == 'pass')
        local = [None if states.get(cid, {}).get('before', 'unknown') == 'unknown'
                 or states.get(cid, {}).get('after', 'unknown') == 'unknown'
                 else states[cid]['after'] == 'pass'
                 for cid in sorted(target_ids) if states.get(cid, {}).get('before') != 'pass']
        elsewhere = [preservation.get(cid, {'check_id': cid, 'before': 'unknown', 'after': 'unknown'})
                     for cid in goals if cid not in target_ids and preservation.get(cid, {}).get('before') != 'fail']
        regressed = [r['check_id'] for r in elsewhere if r['before'] == 'pass' and r['after'] == 'fail']
        regression = (True if regressed else None if not elsewhere or any(
            r['before'] == 'unknown' or r['after'] == 'unknown' for r in elsewhere) else False)
        success = conjunction(local)
        if elsewhere:
            regressions.append(regression)
        if not local or not elsewhere:
            joint['not_applicable'] += 1
        elif success is None or regression is None:
            joint['unresolved'] += 1
        else:
            joint[f"local_{'success' if success else 'failure'}_{'regressed' if regression else 'preserved'}"] += 1
        transitions.append({**{k: repair[k] for k in ('before', 'after', 'repair_event_ids') if k in repair},
                            'visual_target_ids': sorted(visual_goals & target_ids),
                            'local_repair_success': success, 'elsewhere_regression': regression,
                            'preservation_sample_count': len(elsewhere), 'regressed_check_ids': regressed})
    diagnosed_summary = {**proportion(diagnosed), 'definition': 'post_repair_pass_given_correct_visual_diagnosis_and_confirmed_defect',
                         'unverified_baseline_count': unverified_baselines}
    gaps = {eid for gap in repair_gaps for eid in gap['episode_ids']}
    diagnosed_summary['unassessed_episode_count'] = sum(
        any(correct_visual(t) for t in by_episode.get(eid, {}).get('targets', [])) for eid in gaps)
    if diagnosed_summary['unassessed_episode_count']:
        diagnosed_summary['score'] = None
    regression_summary = proportion(regressions)
    regression_summary['unassessed_transition_count'] = len(repair_gaps)
    if repair_gaps:
        regression_summary['score'] = None
    return diagnosed_summary, {'regression_rate': regression_summary, 'joint_outcomes': joint,
                               'transitions': transitions}


def recheck_status(episode, target, episodes, rounds, repairs, matches=None):
    """Require a scoped agent recheck, not just evaluator acceptance after editing."""
    if rounds is None:
        return None
    source = next(e for e in rounds['episodes'] if e['episode_id'] == episode['episode_id'])
    raw = {e['episode_id']: e for e in rounds['episodes']}
    scored = {e['episode_id']: e for e in episodes}
    relevant_edits = {tuple(r['repair_event_ids']) for r in repairs
                      if episode['episode_id'] in r['episode_ids']
                      and (target['target_id'] in r['target_ids']
                           or target['target_id'] in r.get('target_aliases', {}))}
    values = []
    for link in source.get('repair_links', []):
        if tuple(link['repair_event_ids']) not in relevant_edits:
            continue
        edited = max(link['repair_event_ids'])
        for eid in link.get('recheck_episode_ids', []):
            follow = scored.get(eid)
            if follow is None or follow['status'] == 'pending':
                values.append(None)
                continue
            if min(raw[eid]['core_event_ids']) <= edited:
                raise ValueError('A recheck must follow its linked modification')
            for check in follow['targets']:
                if not check.get('check_attempted', True):
                    continue
                same_condition = (check['target_id'] == target['target_id'] and
                                  check['target'].strip() == target['target'].strip())
                covers_goal = (target['check_id'] is not None and check['check_id'] == target['check_id']
                               and check['coverage'] == 'full')
                aligned = matches is not None and any(
                    m['episode_id'] == episode['episode_id'] and m['target_id'] == target['target_id']
                    and m['recheck_episode_id'] == eid and m['recheck_target_id'] == check['target_id']
                    for m in matches)
                if not (aligned if matches is not None else same_condition or covers_goal):
                    if matches is None:
                        values.append(None)  # Archived IDs alone cannot disprove semantic correspondence.
                    continue
                # A read after an edit can still show a screenshot captured before it.
                images = [v for v in raw[eid].get('evidence_states', [])
                          if v['event_id'] in check['evidence_ids']]
                if target['modality'] != 'text':
                    if not images or not any(v.get('producer_event_id', -1) is not None
                                             and v.get('producer_event_id', -1) > edited for v in images):
                        continue
                state = None if check['actual_state'] == 'unknown' else check['actual_state'] == 'pass'
                values.append(conjunction([state, check_validity(check['method_ok'], check['evidence_ok']),
                                           diagnosis_correct(check['actual_state'], check['agent_state'], check['issue_match'])]))
    if True in values:
        return True
    if None in values or not source.get('relation_annotation_complete', False):
        return None
    return False


def findings(episodes, repairs=()):
    """Emit traceable failure instances; counts are computed from these ID lists."""
    result = {k: [] for k in ('invalid_method', 'insufficient_observation', 'missing_diagnosis',
                              'false_alarm', 'missed_defect', 'wrong_defect', 'failed_repair', 'regression')}
    for episode in episodes:
        for t in episode['targets']:
            ref = {'episode_id': episode['episode_id'], 'target_id': t['target_id']}
            flags = {'invalid_method': t.get('check_attempted', True) and t['method_ok'] is False,
                     'insufficient_observation': t.get('check_attempted', True) and t['evidence_ok'] is False,
                     'missing_diagnosis': t['agent_state'] == 'absent',
                     'false_alarm': t['actual_state'] == 'pass' and t['agent_state'] == 'fail',
                     'missed_defect': t['actual_state'] == 'fail' and t['agent_state'] == 'pass',
                     'wrong_defect': t['actual_state'] == t['agent_state'] == 'fail' and t['issue_match'] is False}
            for name, active in flags.items():
                if active:
                    result[name].append(ref)
    for repair in repairs:
        for kind, rows in (('failed_repair', repair['states']),
                           ('regression', repair.get('preservation_states', repair['states']))):
            for row in rows:
                eligible = (row['check_id'] in repair['target_ids'] and row['before'] != 'pass'
                            if kind == 'failed_repair' else row['before'] == 'pass')
                if eligible and row['after'] == 'fail':
                    result[kind].append({'repair_event_ids': repair.get('repair_event_ids', []),
                                         'check_id': row['check_id']})
    return result


def summarize(catalogue, episodes, repairs=(), repair_gaps=(), rounds=None, recheck_matches=None):
    """Use a complete fixed catalogue; absent evaluation is an evidence gap."""
    goals = [c["check_id"] for c in catalogue["checks"]]
    targets = [t for e in episodes for t in e["targets"]]
    attempted = [t for t in targets if t.get('check_attempted', True)]
    coverage = {}
    for goal in goals:
        labels = [t["coverage"] for t in attempted if t["check_id"] == goal]
        coverage[goal] = ("full" if "full" in labels else "unknown" if "unknown" in labels
                          or any(e["status"] == "pending" for e in episodes)
                          else "partial" if "partial" in labels else "none")
    vc = coverage_summary(coverage)
    vc['unclassified_check_ids'] = [c['check_id'] for c in catalogue['checks'] if 'requirement_types' not in c]
    vc['by_requirement_type'] = {}
    for kind in ('visual', 'interactive', 'other'):
        selected = [c['check_id'] for c in catalogue['checks'] if 'requirement_types' in c
                    and (not c['requirement_types'] if kind == 'other' else kind in c['requirement_types'])]
        group = coverage_summary({cid: coverage[cid] for cid in selected})
        group['classification_complete'] = not vc['unclassified_check_ids']
        if not group['classification_complete']:
            group['score'] = None
        vc['by_requirement_type'][kind] = group
    repair_values, preservation_values, repair_by_episode = [], [], {}
    excluded_repair_count = 0
    for repair in repairs:
        state_by_id = {row["check_id"]: row for row in repair["states"]}
        preservation_by_id = {row['check_id']: row for row in repair.get('preservation_states', repair['states'])}
        fixed = {}
        for goal in repair["target_ids"]:
            row = state_by_id.get(goal, {})
            before, after = row.get("before", "unknown"), row.get("after", "unknown")
            if before == "pass":
                excluded_repair_count += 1
                fixed[goal] = None
                continue
            # RS is post-repair acceptance, not proof of a causal improvement.
            value = None if after == "unknown" else after == "pass"
            fixed[goal] = value
            repair_values.append(value)
        criterion_results = dict(fixed)
        for target, linked_goals in repair.get('target_aliases', {}).items():
            linked_goals = [linked_goals] if isinstance(linked_goals, str) else linked_goals
            if not linked_goals:
                fixed[target] = False  # Explicitly not addressed; no RS sample is added.
                continue
            if any(goal not in criterion_results for goal in linked_goals):
                raise ValueError('Repair target alias refers to an unassessed criterion')
            fixed[target] = conjunction(criterion_results[goal] for goal in linked_goals)
        preserved = []
        complete = True
        for goal in goals:
            row = preservation_by_id.get(goal, {})
            before, after = row.get("before", "unknown"), row.get("after", "unknown")
            if before == "unknown" or (before == "pass" and after == "unknown"):
                complete = False
            if before == "fail":
                continue
            value = None if before == "unknown" or after == "unknown" else after == "pass"
            preserved.append(value)
            preservation_values.append(value)
        preservation = False if False in preserved else True if complete else None
        for episode_id in repair["episode_ids"]:
            repair_by_episode.setdefault(episode_id, []).append((fixed, preservation))
    chain_values, chain_rows = [], []
    for episode in episodes:
        if episode['status'] == 'excluded':
            continue
        checks = episode["targets"]
        values = [check_validity(t["method_ok"], t["evidence_ok"]) for t in checks if t.get('check_attempted', True)]
        values += [diagnosis_correct(t["actual_state"], t["agent_state"], t["issue_match"]) for t in checks]
        if episode["status"] != "evaluated" or not checks:
            values.append(None)
        links = repair_by_episode.get(episode["episode_id"], [])
        rechecks = []
        for target in checks:
            if target["actual_state"] == "fail":
                attempts = [fixed[target["target_id"]] for fixed, _ in links if target["target_id"] in fixed]
                values.append(conjunction(attempts) if attempts else None
                              if episode.get("repair_event_ids") or not episode.get("relation_annotation_complete", False) else False)
                recheck = recheck_status(episode, target, episodes, rounds, repairs, recheck_matches)
                values.append(recheck)
                rechecks.append({'target_id': target['target_id'], 'verified': recheck})
        if episode.get("repair_event_ids") and not links:
            values.append(None)
        values.extend(preserved for _, preserved in links)
        value = conjunction(values)
        chain_values.append(value)
        chain_rows.append({"episode_id": episode["episode_id"],
                           "state": "pass" if value is True else "fail" if value is False else "unknown",
                           "rechecks": rechecks})
    rs = proportion(repair_values)
    rs['definition'] = 'post_repair_acceptance'
    rs["excluded_baseline_pass_count"] = excluded_repair_count
    rs['unassessed_episode_count'] = len({eid for gap in repair_gaps for eid in gap['episode_ids']})
    if repair_gaps:
        rs['score'] = None  # The target denominator is not yet known for missing groups.
        rs['lower_bound'] = rs['upper_bound'] = None
    # A missing artifact pair leaves every fixed baseline goal undecidable.
    preservation_values.extend([None] * (len(goals) * len(repair_gaps)))
    cp = proportion(preservation_values)
    cp['complete'] = not cp['unknown_count']
    cp['unassessed_transition_count'] = len(repair_gaps)
    rs['diagnosed_visual'], cp['repair_outcomes'] = repair_diagnostics(catalogue, episodes, repairs, repair_gaps)
    # Historical mixed labels belong to the visual group; new labels are binary.
    return {"VC": vc, "CV": proportion(check_validity(t["method_ok"], t["evidence_ok"]) for t in attempted),
            "BDA": diagnosis_summary(targets), "BDA-V": diagnosis_summary([t for t in targets if t["modality"] != "text"]),
            "BDA-T": diagnosis_summary([t for t in targets if t["modality"] == "text"]),
            "RS": rs, "CP": cp, "VCS": {**proportion(chain_values), "definition": "valid_diagnosis_repair_and_agent_recheck", "episodes": chain_rows}}


def _macro_rate(rows):
    scores = [r['score'] for r in rows if r['score'] is not None]
    known = sum(scores) / len(scores) if scores else None
    incomplete = any(r.get('unknown_count', 0) or r.get('unassessed_episode_count', 0)
                     or r.get('unassessed_transition_count', 0) or r.get('classification_complete') is False for r in rows)
    return {'score': None if incomplete else known, 'known_score': known,
            'task_count': len(scores), 'na_task_count': len(rows) - len(scores),
            'sample_count': sum(r.get('sample_count', r.get('denominator', 0)) for r in rows),
            'unknown_count': sum(r.get('unknown_count', 0) for r in rows)}


def macro_average(tasks):
    """Equal-weight task means; incomplete evidence never becomes a full score."""
    result = {}
    for metric in ("VC", "CV", "RS", "CP", "VCS"):
        rows = [task[metric] for task in tasks]
        available = [row["score"] for row in rows if row["score"] is not None]
        unknown = sum(row['unknown_count'] for row in rows)
        known_score = sum(available) / len(available) if available else None
        result[metric] = {"score": known_score if not unknown else None, "known_score": known_score,
                          "task_count": len(available), "na_task_count": len(rows) - len(available),
                          "unknown_count": sum(row["unknown_count"] for row in rows)}
    for metric in ("BDA", "BDA-V", "BDA-T"):
        if any(task[metric].get('definition') != BDA_DEFINITION for task in tasks):
            raise ValueError('Cannot aggregate BDA results with different diagnosis definitions')
        classes = {}
        for group in ("normal", "error"):
            rows = [task[metric][group] for task in tasks]
            scores = [row["score"] for row in rows if row["score"] is not None]
            classes[group] = {"score": sum(scores) / len(scores) if scores else None,
                              "task_count": len(scores), "target_count": sum(row["denominator"] for row in rows)}
        scores = [task[metric]['known_score'] for task in tasks if task[metric]['known_score'] is not None]
        known_score = sum(scores) / len(scores) if scores else None
        unknown = sum(task[metric]['unknown_count'] for task in tasks)
        bounds = [task[metric] for task in tasks]
        complete_bounds = bounds and all(row['lower_bound'] is not None and row['upper_bound'] is not None for row in bounds)
        result[metric] = {"score": known_score if not unknown else None, "known_score": known_score, **classes,
                          "lower_bound": sum(row['lower_bound'] for row in bounds) / len(bounds) if complete_bounds else None,
                          "upper_bound": sum(row['upper_bound'] for row in bounds) / len(bounds) if complete_bounds else None,
                          "definition": BDA_DEFINITION,
                          "unknown_count": sum(task[metric]["unknown_count"] for task in tasks),
                          **{key: sum(task[metric][key] for task in tasks)
                             for key in ('absent_count', 'uncertain_count')}}
    bounds = [task['VC'] for task in tasks if task['VC']['denominator']]
    result['VC'].update({'lower_bound': sum(row['lower_bound'] for row in bounds)/len(bounds) if bounds else None,
                         'upper_bound': sum(row['upper_bound'] for row in bounds)/len(bounds) if bounds else None,
                         'task_count': len(bounds), 'na_task_count': len(tasks)-len(bounds)})
    if any(row['is_lower_bound'] for row in bounds):
        result['VC']['score'] = None
    result['RS']['unassessed_episode_count'] = sum(task['RS'].get('unassessed_episode_count',0) for task in tasks)
    if result['RS']['unassessed_episode_count']:
        result['RS']['score'] = None
    result['CP']['unassessed_transition_count'] = sum(task['CP'].get('unassessed_transition_count',0) for task in tasks)
    result['VC']['by_requirement_type'] = {
        kind: _macro_rate([t['VC']['by_requirement_type'][kind] for t in tasks])
        for kind in ('visual', 'interactive', 'other')}
    result['RS']['diagnosed_visual'] = _macro_rate([t['RS']['diagnosed_visual'] for t in tasks])
    result['CP']['repair_outcomes'] = {
        'regression_rate': _macro_rate([t['CP']['repair_outcomes']['regression_rate'] for t in tasks]),
        'joint_outcomes': {key: sum(t['CP']['repair_outcomes']['joint_outcomes'][key] for t in tasks)
                          for key in (tasks[0]['CP']['repair_outcomes']['joint_outcomes'] if tasks else [])}}
    return result
