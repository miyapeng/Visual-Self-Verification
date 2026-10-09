import copy
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from multimodalcode.vsv_eval.catalogue import (content_hash, criteria_hash, validate_catalogue,
                                             prepare_evaluation_plan, load_catalogue)
from multimodalcode.vsv_eval.evaluation import build_packet, validate_targets, load_rounds, validate_saved_episode, unassessed_repairs, trailing_responses, round_cutoffs
from multimodalcode.vsv_eval.judge import JudgeClient, JudgeProfile
from multimodalcode.vsv_eval.metrics import summarize, macro_average
from multimodalcode.vsv_eval.states import (validate_states, state_packet, artifact_identity, validate_transition,
                                           evaluate_states, load_repair_results)
from multimodalcode.io import write_json, read_json
from multimodalcode.vsv_eval.repair_pilot import digest_tree


def catalogue(*ids):
    checks = [{'check_id': cid, 'source_ref': 'task:0', 'setup': 'Open the app.',
               'criterion': f'{cid} works.', 'required_evidence': 'Observed output.', 'reference_ids': []} for cid in ids]
    return {'schema': 'self-verification-catalogue-1', 'checks': checks,
            'criteria_sha256': content_hash(checks), 'source_ids': ['task:0'], 'references': {},
            'review': {'status': 'draft'}}


def test_continuous_response_survives_a_round_boundary_after_reasoning():
    rounds = {'events': [
        {'ordinal': 1, 'kind': 'observation'},
        {'ordinal': 2, 'kind': 'reasoning'},
        {'ordinal': 3, 'kind': 'model_text'},
        {'ordinal': 4, 'kind': 'action'},
        {'ordinal': 5, 'kind': 'model_text'},
    ]}
    episode = {'core_event_ids': [1, 2], 'judgment_event_ids': [2]}
    assert trailing_responses(rounds, episode) == [3]
    assert round_cutoffs(rounds, episode) == [2, 3]
    # A subsequent tool call or another session cannot extend the current judgment.
    rounds['events'][2]['scope'] = 'other_session'
    assert trailing_responses(rounds, episode) == []


def target(cid, actual='pass', agent=None, **kwargs):
    agent = agent or (actual if actual in {'pass','fail'} else 'uncertain')
    return {'target': cid, 'target_id': cid, 'check_id': cid, 'coverage': 'full',
            'method_ok': True, 'evidence_ok': True, 'actual_state': actual,
            'actual_issue': {'object': cid, 'symptom': 'missing'} if actual == 'fail' else None,
            'agent_state': agent, 'issue_match': True if actual == 'fail' else None,
            'evidence_ids': [1, 2], 'diagnosis_ids': [3] if agent != 'absent' else [], 'modality': 'text', **kwargs}


def episode(*targets, eid='e', **kwargs):
    return {'episode_id': eid, 'targets': list(targets), 'status': 'evaluated',
            'relation_annotation_complete': True, 'repair_event_ids': [], **kwargs}


def test_coverage_is_fixed_deduplicated_and_unknown_has_bounds():
    result = summarize(catalogue('a','b','c'), [episode(target('a'), target('b',coverage='partial')),
                                                episode(target('a'),target('c',actual='unknown',coverage='unknown'),eid='later')])
    assert result['VC']['score'] is None
    assert result['VC']['lower_bound'] == pytest.approx(100/3)
    assert result['VC']['upper_bound'] == pytest.approx(200/3)
    assert result['VC']['partial_count'] == 1
    assert result['VC']['unknown_count'] == 1


def test_absent_diagnosis_is_penalized_but_evaluator_uncertainty_remains_unknown():
    result = summarize(catalogue('a','b','c'), [episode(target('a',agent='absent'),
                         target('b',actual='fail',agent='fail',issue_match=None),
                         target('c',actual='unknown',agent='pass',coverage='unknown'))])
    assert result['BDA']['normal']['failure_count'] == 1
    assert result['BDA']['absent_count'] == 1
    assert result['BDA']['unknown_count'] == 2
    assert result['BDA']['score'] is None
    assert result['VCS']['failure_count'] == 1  # A known failure dominates unknowns.


def test_bda_penalizes_wrong_missing_and_uncertain_conclusions():
    rows = [target('a', agent='pass'), target('b', agent='fail'),
            target('c', actual='fail', agent='fail'), target('d', actual='fail', agent='pass'),
            target('e', agent='absent'), target('f', actual='fail', agent='uncertain')]
    result = summarize(catalogue('a','b','c','d','e','f'), [episode(*rows)])
    bda = result['BDA']
    assert bda['score'] == pytest.approx(100/3)
    assert bda['lower_bound'] == bda['upper_bound'] == pytest.approx(bda['score'])
    assert bda['success_count'] == 2 and bda['failure_count'] == 4
    assert bda['absent_count'] == bda['uncertain_count'] == 1
    assert bda['unknown_count'] == 0
    assert bda['definition'] == 'macro_diagnosis_accuracy_over_present_classes'
    macro = macro_average([result])['BDA']
    assert macro['score'] == pytest.approx(100/3) and macro['absent_count'] == macro['uncertain_count'] == 1
    result['BDA'].pop('definition')
    with pytest.raises(ValueError, match='different diagnosis definitions'):
        macro_average([result])


def test_bda_unknown_state_bounds_match_possible_completions():
    from itertools import product
    base = [target('normal'), target('error', actual='fail')]
    unresolved = [target(str(i), actual='unknown', agent=agent)
                  for i, agent in enumerate(['pass', 'fail', 'absent', 'uncertain'])]
    result = summarize(catalogue('normal','error'), [episode(*(base + unresolved))])
    bda = result['BDA']
    assert bda['score'] is None and bda['known_score'] == 100
    assert bda['unknown_count'] == 4  # Unknown evidence is retained even when a diagnosis is absent.
    possible = []
    for states in product(['pass', 'fail'], repeat=4):
        for matched in [False, True]:
            rows = [dict(t, actual_state=state, issue_match=matched if t['agent_state']=='fail' else None)
                    for t, state in zip(unresolved, states)]
            possible.append(summarize(catalogue('normal','error'), [episode(*(base + rows))])['BDA']['score'])
    assert bda['lower_bound'] == pytest.approx(min(possible))
    assert bda['upper_bound'] == pytest.approx(max(possible))
    # A known-subset score can exceed the full upper bound: silence never earns credit.
    assert bda['upper_bound'] < bda['known_score']
    assert macro_average([result])['BDA']['score'] is None


def test_single_class_tasks_contribute_to_macro_bda():
    normal = summarize(catalogue('a'),[episode(target('a'))])
    error = summarize(catalogue('a'),[episode(target('a',actual='fail'))])
    assert normal['BDA']['score'] == error['BDA']['score'] == 100
    result = macro_average([normal,error])
    assert result['BDA']['score'] == 100
    assert result['BDA']['normal']['task_count'] == result['BDA']['error']['task_count'] == 1


def test_diagnosis_groups_are_visual_and_text_and_partial_coverage_is_unchanged():
    rows=[target('a',modality='visual'), target('b',modality='visual',actual='fail',coverage='partial'),
          target('c',modality='text')]
    result=summarize(catalogue('a','b','c'),[episode(*rows)])
    assert 'BDA-M' not in result and 'BDA-M' not in macro_average([result])
    assert result['BDA-V']['normal']['denominator']==result['BDA-V']['error']['denominator']==1
    assert result['BDA-T']['normal']['denominator']==1
    assert result['VC']['score']==pytest.approx(200/3) and result['VC']['partial_count']==1
    historical=summarize(catalogue('a'),[episode(target('a',modality='mixed'))])
    assert historical['BDA-V']['normal']['denominator']==1


def test_repair_preservation_uses_all_fixed_goals_and_baseline_pass_is_not_rs():
    repairs = [{'episode_ids':['e'], 'target_ids':['a','b'], 'states':[
        {'check_id':'a','before':'fail','after':'pass'},
        {'check_id':'b','before':'pass','after':'pass'},
        {'check_id':'c','before':'pass','after':'fail'}]}]
    result = summarize(catalogue('a','b','c'),[episode(target('a',actual='fail'),repair_event_ids=[4])],repairs)
    assert result['RS']['score'] == 100 and result['RS']['excluded_baseline_pass_count'] == 1
    assert result['CP']['score'] == 50
    assert result['VCS']['failure_count'] == 1


def test_requirement_coverage_is_not_the_modality_of_the_check():
    cat = catalogue('layout', 'tabs', 'available')
    for check, kinds in zip(cat['checks'], [['visual'], ['interactive'], []]):
        check['requirement_types'] = kinds
    rows = [episode(target('layout', modality='visual'), target('available', modality='text'))]
    result = summarize(cat, rows)
    groups = result['VC']['by_requirement_type']
    assert groups['visual']['score'] == 100 and groups['interactive']['score'] == 0
    assert groups['other']['score'] == 100
    rows.append(episode(target('tabs', modality='text'), eid='interaction'))
    assert summarize(cat, rows)['VC']['by_requirement_type']['interactive']['score'] == 100
    # Missing historical labels must not be inferred from what the agent happened to do.
    del cat['checks'][0]['requirement_types']
    legacy = summarize(cat, rows)['VC']
    assert legacy['score'] == 100 and legacy['unclassified_check_ids'] == ['layout']
    assert all(r['score'] is None for r in legacy['by_requirement_type'].values())


def test_repair_attempts_preserve_failures_and_join_local_success_with_regression():
    cat = catalogue('mobile', 'desktop', *[f'stable-{i}' for i in range(4)])
    episodes = [episode(target('mobile', actual='fail', modality='visual'), eid='mobile'),
                episode(target('mobile', actual='fail', modality='visual'), eid='shared-source'),
                episode(target('desktop', actual='fail', modality='visual'), eid='desktop'),
                episode(target('desktop', actual='fail', modality='visual'), eid='retry')]
    repairs = []
    for i, (goal, sources, before, after) in enumerate([
            ('mobile', ['mobile', 'shared-source'], ('fail', 'pass'), ('pass', 'fail')),
            ('desktop', ['desktop'], ('pass', 'fail'), ('pass', 'fail')),
            ('desktop', ['retry'], ('pass', 'fail'), ('pass', 'pass'))]):
        states = [{'check_id': cid, 'before': a, 'after': b}
                  for cid, a, b in zip(['mobile', 'desktop'], before, after)]
        states += [{'check_id': f'stable-{j}', 'before': 'pass', 'after': 'pass'} for j in range(4)]
        repairs.append({'before': f'V{i}', 'after': f'V{i+1}', 'repair_event_ids': [10+i],
                        'episode_ids': sources, 'target_ids': [goal], 'states': states})
    result = summarize(cat, episodes, repairs)
    diagnosed = result['RS']['diagnosed_visual']
    assert diagnosed['sample_count'] == 3  # Two source rounds do not duplicate the shared attempt.
    assert diagnosed['score'] == pytest.approx(200 / 3)
    outcomes = result['CP']['repair_outcomes']
    assert [t['local_repair_success'] for t in outcomes['transitions']] == [True, False, True]
    assert outcomes['transitions'][0]['regressed_check_ids'] == ['desktop']
    assert outcomes['joint_outcomes'] == {'local_success_preserved': 1, 'local_success_regressed': 1,
                                        'local_failure_preserved': 1, 'local_failure_regressed': 0,
                                        'unresolved': 0, 'not_applicable': 0}
    assert outcomes['regression_rate']['score'] == pytest.approx(100 / 3)
    assert result['CP']['score'] == pytest.approx(1400 / 15)  # Many preserved checks do not hide the transition.
    macro = macro_average([result])
    assert macro['RS']['diagnosed_visual']['score'] == diagnosed['score']
    assert macro['CP']['repair_outcomes']['joint_outcomes'] == outcomes['joint_outcomes']


def test_conditional_repair_requires_a_real_baseline_defect():
    repairs = [{'episode_ids': ['e'], 'target_ids': ['a'],
                'states': [{'check_id': 'a', 'before': 'unknown', 'after': 'pass'}]}]
    result = summarize(catalogue('a'), [episode(target('a', actual='fail', modality='visual'))], repairs)
    assert result['RS']['score'] == 100  # The existing post-repair acceptance policy is unchanged.
    conditional = result['RS']['diagnosed_visual']
    assert conditional['sample_count'] == 0 and conditional['unverified_baseline_count'] == 1


def test_post_repair_acceptance_does_not_require_a_decidable_baseline():
    repairs = [{'episode_ids': [], 'target_ids': ['a', 'b', 'c'], 'states': [
        {'check_id': 'a', 'before': 'unknown', 'after': 'pass'},
        {'check_id': 'b', 'before': 'unknown', 'after': 'fail'},
        {'check_id': 'c', 'before': 'fail', 'after': 'unknown'}]}]
    rs = summarize(catalogue('a', 'b', 'c'), [], repairs)['RS']
    assert rs['definition'] == 'post_repair_acceptance'
    assert rs['score'] is None and rs['known_score'] == 50 and rs['success_count'] == rs['failure_count'] == rs['unknown_count'] == 1


def test_unexecuted_fixed_check_prevents_chain_success_despite_known_cp_pass():
    repairs = [{'episode_ids':['e'], 'target_ids':['a'], 'states':[
        {'check_id':'a','before':'fail','after':'pass'},
        {'check_id':'b','before':'pass','after':'pass'}]}]
    result = summarize(catalogue('a','b','c'),[episode(target('a',actual='fail'),repair_event_ids=[4])],repairs)
    assert result['RS']['score'] == 100
    assert result['CP']['unknown_count'] == 1
    assert result['VCS']['unknown_count'] == 1


def test_known_error_without_repair_fails_chain_unassessed_edit_is_unknown():
    goal = target('a',actual='fail')
    assert summarize(catalogue('a'),[episode(goal)])['VCS']['failure_count'] == 1
    assert summarize(catalogue('a'),[episode(goal,repair_event_ids=[4])])['VCS']['unknown_count'] == 1


def test_unrecoverable_shared_repairs_remain_visible_without_guessing_target_counts():
    rounds={'episodes':[{'episode_id':eid,'repair_links':[{'repair_event_ids':[4,6]}]} for eid in ['e','other']]}
    gaps=unassessed_repairs(rounds,[])
    assert len(gaps)==1 and gaps[0]['episode_ids']==['e','other']
    result=summarize(catalogue('a','b'),[episode(target('a',actual='fail'),repair_event_ids=[4,6])],repair_gaps=gaps)
    assert result['RS']['score'] is None and result['RS']['unassessed_episode_count']==2
    assert result['CP']['unknown_count']==2 and result['CP']['unassessed_transition_count']==1
    assert unassessed_repairs(rounds,[{'repair_event_ids':[4,6]}])==[]


def test_empty_trace_has_zero_coverage_and_na_conditional_scores():
    result = summarize(catalogue('a'),[])
    assert result['VC']['score'] == 0
    assert all(result[key]['score'] is None for key in ['CV','BDA','RS','CP','VCS'])
    assert summarize(catalogue(),[])['VC']['score'] is None


def test_catalogue_requires_review_and_detects_changed_criteria():
    value = catalogue('a')
    with pytest.raises(ValueError,match='model-reviewed'):
        validate_catalogue(value)
    validate_catalogue(value,allow_draft=True)
    value['checks'][0]['criterion'] = 'Different.'
    with pytest.raises(ValueError,match='hash'):
        validate_catalogue(value,allow_draft=True)


def packet_fixture(tmp_path):
    events = [
        {'ordinal':0,'kind':'action','tool':'Bash','tool_call_id':'capture','payload':{'command':'capture'}},
        {'ordinal':1,'kind':'observation','payload':{'tool_use_id':'capture'},'text':'ok'},
        {'ordinal':2,'kind':'observation','images':[{'path':'missing.png'}],'text':''},
        {'ordinal':3,'kind':'model_text','text':'It is broken.'},
        {'ordinal':4,'kind':'action','tool':'Edit','payload':{}},
        {'ordinal':5,'kind':'observation','text':'later fixed'}]
    source=tmp_path/'run.json'
    source.write_text(json.dumps({'timeline':events}))
    ep={'episode_id':'e','core_event_ids':[2,3],'context_event_ids':[0,1], 'judgment_event_ids':[3],
        'evidence_states':[{'event_id':2,'producer_event_id':0,'program_sha256':'capture', 'read_program_sha256':'later'}]}
    rounds={'schema':'multimodalcode-verification-rounds-1','source_run':str(source),
            'source_run_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'events':events,'episodes':[ep]}
    return rounds,ep


def test_packet_cutoff_keeps_missing_image_capture_state_and_original_events(tmp_path):
    rounds,ep=packet_fixture(tmp_path)
    original=copy.deepcopy(rounds)
    packet,images=build_packet(rounds,ep,3,'task',catalogue('a'),[],tmp_path)
    assert [e['ordinal'] for e in packet['events']]==[0,1,2,3]
    assert packet['missing_images'][0]['agent_received_image'] is True
    assert packet['evidence_states'][0]['program_sha256']=='capture'
    assert packet['recorded_image_event_ids']==[2] and not images
    assert packet['context_event_ids']==[0,1]
    assert packet['eligible_diagnosis_event_ids']==[3]
    assert rounds==original


def test_target_validator_rejects_future_citations_and_visual_state_without_pixels(tmp_path):
    rounds,ep=packet_fixture(tmp_path)
    packet,_=build_packet(rounds,ep,3,'task',catalogue('a'),[],tmp_path)
    row=target('a',actual='fail',agent='fail')
    row.pop('target_id')
    validate_targets({'targets':[row]},packet,catalogue('a'))
    row['evidence_ids']=[5]
    with pytest.raises(ValueError,match='future'):
        validate_targets({'targets':[row]},packet,catalogue('a'))
    row['evidence_ids']=[2]
    row['modality']='visual'
    with pytest.raises(ValueError,match='pixels'):
        validate_targets({'targets':[row]},packet,catalogue('a'))


def test_state_validation_requires_complete_fixed_catalogue_and_real_execution():
    packet={'checks':catalogue('a','b')['checks'],'evidence':[{'evidence_id':'v:check'}], 'rule_facts':[]}
    with pytest.raises(ValueError,match='omitted'):
        validate_states({'checks':[{'check_id':'a','state':'pass','evidence_ids':['v:check']}]},packet)
    with pytest.raises(ValueError,match='evidence'):
        validate_states({'checks':[{'check_id':'a','state':'pass','evidence_ids':['other']}]},packet)


def test_context_responses_and_tool_results_are_not_current_diagnoses(tmp_path):
    rounds,ep=packet_fixture(tmp_path)
    rounds['events'][0]={'ordinal':0,'kind':'model_text','text':'Earlier check passed.'}
    packet,_=build_packet(rounds,ep,3,'task',catalogue('a'),[],tmp_path)
    row=target('a'); row.pop('target_id')
    for diagnosis in [0,1]:
        row['diagnosis_ids']=[diagnosis]
        with pytest.raises(ValueError,match='responses in this round'):
            validate_targets({'targets':[row]},packet,catalogue('a'))


def test_exact_state_assertions_do_not_share_a_group_verdict(tmp_path):
    replay={'routes':{},'checks':[{'name':'probe','status':'executed','source_function':'probe()',
                                 'output':{'a':True,'b':False},'steps':[],'reset_route':'/'}]}
    adapters={cid:{'check':'probe','assertions':[{'path':cid,'op':'eq','value':True}]} for cid in ['a','b']}
    packet,_=state_packet(catalogue('a','b'),{'version':'v'},replay,adapters,tmp_path)
    assert {f['check_id']:f['state'] for f in packet['rule_facts']}=={'a':'pass','b':'fail'}


def test_artifact_identity_tracks_resources_and_rejects_changed_code(tmp_path):
    app=tmp_path/'app'; app.mkdir(); (app/'index.html').write_text('before')
    assets=tmp_path/'assets'; assets.mkdir(); (assets/'logo.svg').write_text('logo')
    version={'workspace':str(tmp_path),'code_manifest':digest_tree(app),'resource_root':str(assets)}
    first=artifact_identity(version,{'code_subdir':'app'})
    (assets/'logo.svg').write_text('changed logo')
    assert artifact_identity(version,{'code_subdir':'app'}) != first
    external=tmp_path/'linked.svg'; external.write_text('first linked bytes')
    (assets/'linked.svg').symlink_to(external)
    linked=artifact_identity(version,{'code_subdir':'app'})
    external.write_text('changed linked bytes')
    assert artifact_identity(version,{'code_subdir':'app'}) != linked
    (app/'index.html').write_text('after')
    with pytest.raises(ValueError,match='audited manifest'):
        artifact_identity(version,{'code_subdir':'app'})


def test_repair_transition_rejects_later_versions_and_unrelated_edits():
    versions={'before':{'after_event':1},'after':{'after_event':4}}
    events={2:{'kind':'action','tool':'Edit'},4:{'kind':'action','tool':'Edit'}}
    episodes={'e':{'repair_links':[{'repair_event_ids':[2,4]}]}}
    transition={'before':'before','after':'after','episode_ids':['e'],'repair_event_ids':[2,4],'target_ids':['a']}
    validate_transition(transition,versions,events,episodes,{'a'})
    versions['after']['after_event']=5
    with pytest.raises(ValueError,match='exact state'):
        validate_transition(transition,versions,events,episodes,{'a'})
    versions['after']['after_event']=4
    events[3]={'kind':'action','tool':'Write'}
    with pytest.raises(ValueError,match='unrelated'):
        validate_transition(transition,versions,events,episodes,{'a'})


def test_saved_labels_require_original_response_and_complete_boundaries(tmp_path):
    rounds,ep=packet_fixture(tmp_path)
    ep.update(relation_annotation_complete=True,repair_links=[])
    cat=catalogue('a'); cat['task_sha256']=hashlib.sha256(b'task').hexdigest()
    packet,_=build_packet(rounds,ep,3,'task',cat,[],tmp_path)
    input_path=tmp_path/'input.json'; input_path.write_text(json.dumps(packet))
    raw=target('a',agent='absent'); raw.pop('target_id')
    row=episode(*validate_targets({'targets':[raw]},packet,cat),requests=[{'input_path':str(input_path),'stage':'text_check',
                            'request_sha256':'request','cutoff':3}],repair_event_ids=[])
    cache=tmp_path/'judge_cache/text_check'; cache.mkdir(parents=True)
    response=cache/'request.json'
    response.write_text(json.dumps({'request_sha256':'request','prompt':'Prompt\n'+json.dumps(packet,ensure_ascii=False),'parsed':{'targets':[raw]}}))
    assert validate_saved_episode(row,tmp_path,rounds,cat)['targets']==row['targets']
    row['targets'][0]['actual_state']='fail'
    with pytest.raises(ValueError,match='differ'):
        validate_saved_episode(row,tmp_path,rounds,cat)


def test_offline_repair_runs_fixed_goals_and_rejects_unsubstantiated_labels(tmp_path, monkeypatch):
    events=[{'ordinal':0,'kind':'action','tool':'Bash','tool_call_id':'probe',
             'payload':{'command':'playwright run-code "async page => { return {}; }"'}},
            {'ordinal':1,'kind':'observation','payload':{'tool_use_id':'probe'},'text':'failed'},
            {'ordinal':2,'kind':'action','tool':'Edit'}, {'ordinal':3,'kind':'observation'},
            {'ordinal':4,'kind':'action','tool':'Edit'}, {'ordinal':5,'kind':'observation'}]
    source=tmp_path/'run.json'; write_json(source,{'timeline':events})
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    ep={'episode_id':'e','core_event_ids':[0,1],'context_event_ids':[],'judgment_event_ids':[],
        'evidence_states':[],'relation_annotation_complete':True,'repair_links':[{'repair_event_ids':[2,4]}]}
    rounds=tmp_path/'rounds.json'
    write_json(rounds,{'schema':'multimodalcode-verification-rounds-1','source_run':str(source),
                      'source_run_sha256':digest,'events':events,'episodes':[ep]})
    cat=tmp_path/'catalogue.json'; write_json(cat,catalogue('a','b'))
    versions=[]
    for name,event in [('V0',1),('V1',4)]:
        app=tmp_path/name/'app'; app.mkdir(parents=True); (app/'app.js').write_text(name)
        versions.append({'version':name,'workspace':str(app.parent),'after_event':event,'code_manifest':digest_tree(app)})
    manifest=tmp_path/'manifest.json'; write_json(manifest,{'source_run_sha256':digest,'versions':versions})
    config=tmp_path/'config.json'; write_json(config,{'schema':'multimodalcode-vsv-judge-config-1'})
    spec=tmp_path/'spec.json'
    write_json(spec,{'fixture':str(tmp_path),'version_source':{'kind':'manifest','path':str(manifest)},
                     'runtime':{'code_subdir':'app','command':['node','app.js']},'routes':[],
                     'functional_checks':[{'name':'probe','ordinal':0}],
                     'assertion_adapters':{'a':{'check':'probe','assertions':[{'path':'ok','op':'eq','value':True}]}},
                     'transitions':[{'before':'V0','after':'V1','episode_ids':['e'],'repair_event_ids':[2,4],'target_ids':['a']}]})
    def replay(version,spec,destination,port):
        value={'routes':{},'checks':[{'name':'probe','status':'executed','source_function':'synthetic probe',
                                     'output':{'ok':version['version']=='V1'},'reset_route':'/','steps':[]}]}
        write_json(destination/'result.json',value)
        return value
    # Version copying validates the fixture's source trajectory at its standard location.
    write_json(tmp_path/'trajectory/run.json',{'timeline':events})
    monkeypatch.setattr('multimodalcode.vsv_eval.states.replay_version',replay)
    out=tmp_path/'result'
    result=evaluate_states(rounds,cat,spec,config,out,offline=True,allow_draft=True,workers=2)
    repairs=load_repair_results(out/'repair_results.json',rounds,cat)
    metric=summarize(catalogue('a','b'),[episode(target('a',actual='fail'),repair_event_ids=[2,4])],repairs)
    assert metric['RS']['score']==100
    assert metric['CP']['unknown_count']==1 and metric['VCS']['unknown_count']==1
    state_path=out/'state_results/V1.json'; state=read_json(state_path)
    state['checks'][0]['state']='fail'; write_json(state_path,state)
    result['state_result_sha256']['V1']=hashlib.sha256(state_path.read_bytes()).hexdigest()
    write_json(out/'repair_results.json',result)
    with pytest.raises(ValueError,match='original judge responses'):
        load_repair_results(out/'repair_results.json',rounds,cat)


def test_profile_parameters_change_cache_and_unspecified_temperature_is_omitted(tmp_path):
    profile=JudgeProfile('judge','openai-compatible','model','http://localhost/v1','KEY',retries=0)
    client=JudgeClient(profile,tmp_path)
    assert 'temperature' not in profile.generation_settings()
    altered=JudgeClient(replace(profile,temperature=1,reasoning_effort='high'),tmp_path)
    assert client._key('check','prompt',[])!=altered._key('check','prompt',[])
    assert client._key('check','prompt',[])!=JudgeClient(replace(profile,base_url='http://different/v1'),tmp_path)._key('check','prompt',[])


def test_truncation_preserves_usage_and_is_not_a_valid_agent_failure(tmp_path):
    client=JudgeClient(JudgeProfile('j','openai-compatible','m','http://localhost/v1','KEY',retries=0),tmp_path)
    client._post=Mock(return_value={'id':'response','model':'actual-model','usage':{'prompt_tokens':10},
                                  'choices':[{'finish_reason':'length','message':{'content':'{}'}}]})
    record=client.judge('check','prompt',[])
    assert 'truncated' in record['error']
    assert record['response_metadata']['model']=='actual-model'
    assert record['response_metadata']['usage']['prompt_tokens']==10


def test_source_events_cannot_be_rewritten(tmp_path):
    rounds,_=packet_fixture(tmp_path)
    path=tmp_path/'rounds.json'
    path.write_text(json.dumps(rounds))
    load_rounds(path)
    rounds['events'][3]['text']='Invented diagnosis.'
    path.write_text(json.dumps(rounds))
    with pytest.raises(ValueError,match='original trajectory'):
        load_rounds(path)


def test_state_metric_inputs_keep_shared_evidence_and_exclude_other_targets():
    from multimodalcode.vsv_eval.states import state_metric_packet
    packet = {
        'checks': [{'check_id':'repair','reference_ids':['winston']},
                   {'check_id':'content','reference_ids':['winston']},
                   {'check_id':'other','reference_ids':['home']}],
        'evidence': [{'evidence_id':'shared','check_ids':['repair','content']},
                     {'evidence_id':'home','check_ids':['other']}],
        'image_order': [{'role':'reference','reference_id':'home','path':'home-ref','attachment_index':1},
                        {'role':'reference','reference_id':'winston','path':'winston-ref','attachment_index':2},
                        {'role':'evaluator_observation','evidence_id':'shared','path':'winston-shot','attachment_index':3},
                        {'role':'evaluator_observation','evidence_id':'home','path':'home-shot','attachment_index':4}],
        'rule_facts': [{'check_id':'other','state':'pass','evidence_ids':['home']}],
    }
    original = copy.deepcopy(packet)
    spec = {'transitions':[{'before':'V3','after':'V4','target_ids':['repair']}]}
    cat = {'checks':[packet['checks'][1], packet['checks'][2]]}
    rs = state_metric_packet(packet,spec,cat,'V3','RS')
    assert [c['check_id'] for c in rs['checks']] == ['repair']
    assert rs['evidence'] == [packet['evidence'][0]] and not rs['rule_facts']
    assert [v['path'] for v in rs['image_order']] == ['winston-ref','winston-shot']
    assert [v['attachment_index'] for v in rs['image_order']] == [1,2]
    cp = state_metric_packet(packet,spec,cat,'V3','CP')
    assert cp['evidence'] == packet['evidence'] and len(cp['image_order']) == 4
    assert [c['check_id'] for c in cp['checks']] == ['content','other']
    assert packet == original


def test_state_judgment_accepts_pre_action_context_but_requires_current_evidence():
    packet = {'checks':[{'check_id':'load_more'}], 'rule_facts':[], 'evidence':[
        {'evidence_id':'before','workflow_id':'customers','check_ids':['listing']},
        {'evidence_id':'after','workflow_id':'customers','check_ids':['load_more']},
        {'evidence_id':'later','workflow_id':'customers','check_ids':['filter']},
        {'evidence_id':'other','workflow_id':'careers','check_ids':['jobs']}]}
    result = {'checks':[{'check_id':'load_more','state':'pass','evidence_ids':['before','after']}]}
    assert validate_states(result,packet) == result['checks']
    for invalid in (['after','later'], ['after','other'], ['before']):
        result['checks'][0]['evidence_ids'] = invalid
        with pytest.raises(ValueError):
            validate_states(result,packet)


def model_plan_fixture(tmp_path, value):
    from types import SimpleNamespace
    task_root = tmp_path/'task'; task_root.mkdir()
    (task_root/'prompt.txt').write_text('The panel must open when the Open button is clicked.')
    calls = []
    cache = tmp_path/'cache'
    def judge(stage, prompt, images, **kwargs):
        calls.append((stage, prompt, images))
        wire=copy.deepcopy(value)
        for workflow in wire['acceptance_workflows']:
            for node in workflow['nodes']:
                node['checks']=[{'check_id':cid,'assertions':rules} for cid,rules in node['checks'].items()]
        record = {'stage':stage, 'model':'test-model', 'request_sha256':'request', 'parsed':wire}
        write_json(cache/'request.json', record)
        return record
    client = SimpleNamespace(profile=SimpleNamespace(model='test-model'), cache_root=cache, judge=judge)
    return task_root, client, calls


def plan_value():
    return {'checks':[{**c, 'requirement_types': ['interactive']} for c in catalogue('a','b')['checks']],
            'repair_checks':[], 'unresolved_check_ids':['b'],
            'acceptance_workflows':[{'workflow_id':'panel', 'setup':{'route':'/','viewport':{'width':800,'height':600}},
            'nodes':[{'source_ref':'task:0','actions':[],
                      'checks':{'a':[{'type':'text_visible','value':'Panel opened'}]}}]}]}


def test_model_plan_replaces_approval_preserves_sources_and_freezes_unresolved(tmp_path):
    value = plan_value()
    root, judge, calls = model_plan_fixture(tmp_path, value)
    cat, spec = prepare_evaluation_plan(root,judge,tmp_path/'prepared')
    assert len(calls) == 1 and calls[0][0] == 'plan_review'
    from multimodalcode.vsv_eval.catalogue import plan_schema
    assert json.dumps(plan_schema()) in calls[0][1]
    assert 'timeline' not in read_json(tmp_path/'prepared/plan_input.json')
    assert cat['review']['status'] == spec['review']['status'] == 'model_reviewed'
    assert load_catalogue(tmp_path/'prepared/catalogue.json') == cat
    metric = summarize(cat,[episode(target('a'))])
    assert metric['VC']['score'] == 50 and metric['VC']['goals']['b'] == 'none'
    assert cat['criteria_sha256'] != content_hash(cat['checks'])
    with pytest.raises(FileExistsError):
        prepare_evaluation_plan(root,judge,tmp_path/'prepared')
    cat['checks'][0]['criterion'] = 'Silently changed criterion.'
    cat['criteria_sha256'] = criteria_hash(cat['checks'],cat['unresolved_check_ids'])
    write_json(tmp_path/'prepared/catalogue.json',cat)
    with pytest.raises(ValueError,match='recorded model review'):
        load_catalogue(tmp_path/'prepared/catalogue.json')


def test_new_plan_limits_supplementary_goals_to_selected_repairs(tmp_path):
    value=plan_value()
    repair={**value['checks'][0],'check_id':'active_repair'}
    inactive={**repair,'check_id':'inactive_repair'}
    value['repair_checks']=[repair]
    value['acceptance_workflows'][0]['nodes'][0]['checks']['active_repair']=[]
    root,judge,calls=model_plan_fixture(tmp_path,value)
    seed={'repair_checks':[repair,inactive], 'transitions':[{'target_ids':['active_repair']}],
          'acceptance_workflows':copy.deepcopy(value['acceptance_workflows'])}
    seed['acceptance_workflows'][0]['nodes'][0]['checks']['inactive_repair']=[]
    write_json(tmp_path/'seed.json',seed)
    # The remaining transition fields are source relations, not model inputs.
    seed['transitions'][0].update(before='V0',after='V1',repair_event_ids=[3],episode_ids=['e'])
    write_json(tmp_path/'seed.json',seed)
    _,spec=prepare_evaluation_plan(root,judge,tmp_path/'prepared',state_spec_path=tmp_path/'seed.json')
    packet=read_json(tmp_path/'prepared/plan_input.json')
    assert packet['repair_checks']==[repair] and spec['repair_checks']==[repair]
    assert 'inactive_repair' not in packet['acceptance_workflows'][0]['nodes'][0]['checks']
    assert spec['transitions']==seed['transitions'] and read_json(tmp_path/'seed.json')==seed
    assert len(calls)==1


def test_existing_image_views_skip_pixel_conversion_and_keep_source_hashes(tmp_path,monkeypatch):
    from PIL import Image
    from multimodalcode.vsv_eval.catalogue import image_views
    source=tmp_path/'long.png';Image.new('RGB',(200,5000),'white').save(source)
    views=image_views(source,tmp_path/'views')
    def unexpected_conversion(*args,**kwargs):
        raise AssertionError('Existing views must not decode and convert source pixels again')
    monkeypatch.setattr(Image.Image,'convert',unexpected_conversion)
    assert image_views(source,tmp_path/'views')==views


def test_model_call_report_counts_http_retries_cache_hits_and_failures(tmp_path,monkeypatch):
    from multimodalcode.vsv_eval.judge import write_model_call_report
    from datetime import datetime,timezone
    profile=JudgeProfile('test','openai-compatible','model','http://unused/v1','UNUSED',retries=1)
    client=JudgeClient(profile,tmp_path/'judge_cache/test')
    class Response:
        headers={}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps({'choices':[{'message':{'content':'{"ok":true}'}}]}).encode()
    responses=iter([OSError('first attempt failed'),Response()])
    def urlopen(*args,**kwargs):
        response=next(responses)
        if isinstance(response,Exception):raise response
        return response
    monkeypatch.setattr('urllib.request.urlopen',urlopen)
    monkeypatch.setattr('multimodalcode.vsv_eval.judge.time.sleep',lambda seconds:None)
    context={'episode_id':'e','cutoff':2,'input_path':'input.json'}
    first=client.judge('text_vc','request',[],context=context)
    assert client.judge('text_vc','request',[],context=context)==first
    client.profile=replace(profile,retries=0)
    def fail(*args,**kwargs):raise OSError('request failed')
    monkeypatch.setattr('urllib.request.urlopen',fail)
    with pytest.raises(RuntimeError,match='request failed'):
        client.judge('text_cv','different',[],context=context)
    report=write_model_call_report(tmp_path)
    assert report['logical_calls']==3 and report['api_requests']==3
    assert report['cache_hits']==1 and report['error_calls']==1
    assert [r['api_requests'] for r in report['calls']]==[2,0,1]
    assert all(r['context']==context and Path(r['response_path']).is_file() for r in report['calls'])
    assert report['by_stage']['text_vc']['api_requests']==2
    empty=write_model_call_report(tmp_path,since=datetime.now(timezone.utc).isoformat())
    assert empty['api_requests']==empty['logical_calls']==0


def test_plan_schema_constrains_executor_discriminators_and_uses_existing_client(tmp_path):
    from multimodalcode.vsv_eval.catalogue import plan_schema
    from multimodalcode.vsv_eval.acceptance import UI_ACTIONS, ASSERTIONS
    from multimodalcode.vsv_eval.judge import build_client
    schema=plan_schema()
    node=schema['properties']['acceptance_workflows']['items']['properties']['nodes']['items']
    actions=node['properties']['actions']['items']['anyOf']
    checks=node['properties']['checks']
    assert checks['type']=='array' and set(checks['items']['properties'])=={'check_id','assertions'}
    assertions=checks['items']['properties']['assertions']['items']['anyOf']
    assert {v for item in actions for v in item['properties']['type']['enum']}==UI_ACTIONS
    assert {v for item in assertions for v in item['properties']['type']['enum']}==ASSERTIONS
    assert all('action' not in item['properties'] and 'type' in item['required'] for item in actions)
    assert all('assertion' not in item['properties'] and 'type' in item['required'] for item in assertions)
    config={'profiles':{'test':{'provider':'openai-compatible','model':'test','base_url':'http://unused',
                              'api_key_env':'UNUSED','structured_output':True}}}
    client=build_client(config,'test',tmp_path/'cache',response_schema=schema)
    assert client.profile.generation_settings()['response_format']['json_schema']['schema']==schema


def test_model_workflow_rows_compile_without_rewriting_or_losing_ids():
    from multimodalcode.vsv_eval.acceptance import compile_workflows
    wire=[{'workflow_id':'page','setup':{'route':'/','viewport':{'width':800,'height':600}},
           'nodes':[{'source_ref':'task:0','actions':[],'fullpage':True,
                     'checks':[{'check_id':'visual_goal','assertions':[]},
                               {'check_id':'text_goal','assertions':[{'type':'text_visible','value':'Visible'}]}]}]}]
    original=copy.deepcopy(wire)
    compiled=compile_workflows(wire)
    assert compiled[0]['nodes'][0]['checks']=={'visual_goal':[], 'text_goal':[{'type':'text_visible','value':'Visible'}]}
    assert wire==original and compiled[0]['nodes'][0]['actions']==wire[0]['nodes'][0]['actions']
    wire[0]['nodes'][0]['checks'].append(copy.deepcopy(wire[0]['nodes'][0]['checks'][0]))
    with pytest.raises(ValueError,match='Duplicate'):
        compile_workflows(wire)


@pytest.mark.parametrize('fault',['action_discriminator','assertion_discriminator','omitted_repair'])
def test_plan_rejects_nonexecutable_actions_and_missing_repair_goals(tmp_path,fault):
    value=plan_value()
    value['repair_checks']=[{**value['checks'][0],'check_id':'repair'}]
    node=value['acceptance_workflows'][0]['nodes'][0]
    node['checks']['repair']=[]
    node['actions']=[{'type':'click','target':{'role':'button','name':'Open'},'description':'Open the panel.'}]
    if fault=='action_discriminator':node['actions'][0]['action']=node['actions'][0].pop('type')
    elif fault=='assertion_discriminator':node['checks']['a'][0]['assertion']=node['checks']['a'][0].pop('type')
    else:node['checks'].pop('repair')
    root,judge,calls=model_plan_fixture(tmp_path,value)
    write_json(tmp_path/'spec.json',{'repair_checks':value['repair_checks']})
    original=copy.deepcopy(value)
    with pytest.raises(ValueError):
        prepare_evaluation_plan(root,judge,tmp_path/'prepared',state_spec_path=tmp_path/'spec.json')
    assert len(calls)==1 and value==original and not (tmp_path/'prepared/state_spec.json').exists()


@pytest.mark.parametrize('fault',['missing_goal','invented_source','assigned_unresolved','model_failure','removed_id'])
def test_invalid_model_plans_are_rejected_without_approval_or_fallback(tmp_path,fault):
    value = plan_value()
    if fault == 'missing_goal':
        value['unresolved_check_ids'] = []
    elif fault == 'invented_source':
        value['checks'][0]['source_ref'] = 'invented'
    elif fault == 'assigned_unresolved':
        value['unresolved_check_ids'] = ['a','b']
    elif fault == 'removed_id':
        value['checks'] = value['checks'][:1]
        value['unresolved_check_ids'] = []
    root, judge, _ = model_plan_fixture(tmp_path,value)
    previous = catalogue('a','b')
    previous['task_sha256'] = hashlib.sha256((root/'prompt.txt').read_bytes()).hexdigest()
    write_json(tmp_path/'previous.json',previous)
    if fault == 'model_failure':
        judge.judge = lambda *args, **kwargs: {'error':'invalid model response'}
    with pytest.raises(ValueError):
        prepare_evaluation_plan(root,judge,tmp_path/'prepared',catalogue_path=tmp_path/'previous.json')
    assert not (tmp_path/'prepared/catalogue.json').exists()


def test_unresolved_criterion_cannot_become_known_from_exact_or_model_labels(tmp_path):
    cat = catalogue('a'); cat['unresolved_check_ids'] = ['a']
    cat['criteria_sha256'] = criteria_hash(cat['checks'],['a'])
    validate_catalogue(cat,allow_draft=True)
    rounds,ep = packet_fixture(tmp_path)
    packet,_ = build_packet(rounds,ep,3,'task',cat,[],tmp_path)
    row=target('a'); row.pop('target_id')
    with pytest.raises(ValueError,match='unresolved criterion'):
        validate_targets({'targets':[row]},packet,cat)
    replay={'routes':{},'checks':[]}
    state,_ = state_packet(cat,{'version':'v'},replay,{},tmp_path)
    assert state['rule_facts'] == [{'check_id':'a','state':'unknown','evidence_ids':[]}]


def test_one_check_can_reference_multiple_repaired_conditions():
    repair = {'episode_ids': ['e'], 'target_ids': ['a', 'b'],
              'target_aliases': {'original': ['a', 'b']},
              'states': [{'check_id': 'a', 'before': 'fail', 'after': 'pass'},
                         {'check_id': 'b', 'before': 'fail', 'after': 'fail'}]}
    result = summarize(catalogue('a', 'b'), [episode(target('original', actual='fail'), repair_event_ids=[4])], [repair])
    assert result['RS']['score'] == 50
    assert result['VCS']['failure_count'] == 1


def test_state_judgment_groups_follow_existing_workflows():
    from multimodalcode.vsv_eval.states import state_check_groups, state_metric_packet
    cat = catalogue('a', 'b', 'c')
    packet = {'version':'v', 'checks':cat['checks'], 'rule_facts':[], 'image_order':[],
              'evidence':[{'evidence_id':'w:initial','workflow_id':'w','check_ids':[]},
                          {'evidence_id':'w:0','workflow_id':'w','check_ids':['a','c']},
                          {'evidence_id':'z:0','workflow_id':'z','check_ids':['b']}]}
    assert state_check_groups(packet, cat['checks']) == [['a','c'], ['b']]
    supplied = state_metric_packet(packet, {}, cat, 'v', 'CP', check_ids=['a','c'])
    assert {r['evidence_id'] for r in supplied['evidence']} == {'w:initial','w:0'}
    assert {c['check_id'] for c in supplied['checks']} == {'a','c'}
    packet['evidence'].append({'evidence_id':'z:1','workflow_id':'z','check_ids':['a']})
    with pytest.raises(ValueError, match='multiple workflows'):
        state_check_groups(packet, cat['checks'])


def test_parallel_image_views_publish_matching_hashes(tmp_path, monkeypatch):
    from PIL import Image
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    import time
    from multimodalcode.vsv_eval.catalogue import image_views
    source=tmp_path/'source.png';Image.new('RGB',(100,4200),'white').save(source)
    original=Image.Image.save
    guard=Lock();active=0;peak=0
    def save(image,*args,**kwargs):
        nonlocal active,peak
        with guard:
            active+=1;peak=max(peak,active)
        try:
            time.sleep(.01)
            return original(image,*args,**kwargs)
        finally:
            with guard:active-=1
    monkeypatch.setattr(Image.Image,'save',save)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:image_views(source,tmp_path/'views'),range(4)))
    assert peak==1
    assert all(result==results[0] for result in results)
    for row in results[0]:
        assert hashlib.sha256(Path(row['path']).read_bytes()).hexdigest()==row['sha256']


def test_incomplete_rates_are_not_reported_as_full_scores():
    from multimodalcode.vsv_eval.metrics import proportion
    row = proportion([True, False, None])
    assert row['score'] is None and row['known_score'] == 50
    assert row['sample_count'] == 3 and row['denominator'] == 2
    assert row['lower_bound'] == pytest.approx(100/3)
    assert row['upper_bound'] == pytest.approx(200/3)
    complete = summarize(catalogue('a'), [episode(target('a'))])
    partial = summarize(catalogue('a'), [episode(target('a', evidence_ok=None))])
    assert macro_average([complete, partial])['CV']['score'] is None


def test_recheck_must_cover_the_repaired_condition_and_use_new_evidence():
    from multimodalcode.vsv_eval.metrics import recheck_status
    original = episode(target('a', actual='fail', modality='visual'), repair_event_ids=[4])
    follow = episode(target('a', modality='visual', evidence_ids=[7]), eid='later')
    repairs = [{'episode_ids':['e'], 'repair_event_ids':[4], 'target_ids':['a']}]
    rounds = {'episodes': [
        {'episode_id':'e', 'core_event_ids':[1,2,3], 'relation_annotation_complete':True,
         'repair_links':[{'repair_event_ids':[4], 'recheck_episode_ids':['later']}]},
        {'episode_id':'later', 'core_event_ids':[6,7,8],
         'evidence_states':[{'event_id':7, 'producer_event_id':6}]}]}
    args = (original, original['targets'][0], [original, follow], rounds, repairs)
    assert recheck_status(*args) is True
    # A shared-component fix followed by inspection of another page is not enough.
    follow['targets'][0] = target('b', modality='visual', evidence_ids=[7])
    assert recheck_status(*args) is None  # Different IDs alone cannot settle correspondence.
    assert recheck_status(*args, matches=[]) is False
    follow['targets'][0] = target('a', modality='visual', evidence_ids=[7])
    rounds['episodes'][1]['evidence_states'][0]['producer_event_id'] = 2
    assert recheck_status(*args) is False
    rounds['episodes'][1]['evidence_states'][0]['producer_event_id'] = 6
    follow['targets'][0]['agent_state'] = 'absent'
    assert recheck_status(*args) is False


def test_acceptance_success_does_not_replace_the_agents_recheck():
    e = episode(target('a', actual='fail'), repair_event_ids=[4])
    repair = {'episode_ids':['e'], 'repair_event_ids':[4], 'target_ids':['a'],
              'states':[{'check_id':'a', 'before':'fail', 'after':'pass'}]}
    rounds = {'episodes':[{'episode_id':'e', 'core_event_ids':[1,2,3],
                          'relation_annotation_complete':True,
                          'repair_links':[{'repair_event_ids':[4], 'recheck_episode_ids':[]}]}]}
    scores = summarize(catalogue('a'), [e], [repair], rounds=rounds)
    assert scores['RS']['score'] == 100
    assert scores['VCS']['score'] == 0
    assert scores['VCS']['episodes'][0]['rechecks'] == [{'target_id':'a', 'verified':False}]


def test_findings_distinguish_missing_diagnosis_from_wrong_diagnosis():
    from multimodalcode.vsv_eval.metrics import findings
    rows = [episode(target('a', agent='absent'), target('b', agent='fail'),
                    target('c', actual='fail', agent='pass'),
                    target('d', actual='fail', issue_match=False))]
    result = findings(rows)
    for key, tid in [('missing_diagnosis','a'), ('false_alarm','b'),
                     ('missed_defect','c'), ('wrong_defect','d')]:
        assert result[key] == [{'episode_id':'e', 'target_id':tid}]
