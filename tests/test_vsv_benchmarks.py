import base64
import copy
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from multimodalcode.io import read_json, write_json
from multimodalcode.vsv_eval.benchmarks import import_run, prepare_task, audit_input
from multimodalcode.vsv_eval.episodes import extract_candidate_windows, extract_verification_rounds
from multimodalcode.vsv_eval.catalogue import content_hash, prepare_evaluation_plan, load_catalogue
from multimodalcode.vsv_eval.artifact_acceptance import replay_commands, validate_assignments
from multimodalcode.vsv_eval.judge import JudgeClient, JudgeProfile
from multimodalcode.vsv_eval import system


def test_sdk_original_pairing_thoughts_images_and_failed_checks(tmp_path):
    from PIL import Image
    image=io.BytesIO();Image.new('RGB',(5,5)).save(image,format='PNG')
    rows=[{'id':'user','kind':'MessageEvent','source':'user','llm_message':{'content':[{'type':'text','text':'Fix the game.'}]}},
          {'id':'action-a','kind':'ActionEvent','tool_name':'terminal','tool_call_id':'a','action':{'command':'test-game'},'thought':[]},
          {'id':'action-b','kind':'ActionEvent','tool_name':'browser_get_state','tool_call_id':'b','action':{},'thought':[]},
          {'id':'result-b','kind':'ObservationEvent','action_id':'action-b','tool_name':'browser_get_state','observation':{'screenshot_data':base64.b64encode(image.getvalue()).decode()}},
          {'id':'result-a','kind':'ObservationEvent','tool_call_id':'a','observation':{'is_error':True,'content':[{'type':'text','text':'Runtime failed'}]}},
          {'id':'action-c','kind':'ActionEvent','tool_name':'file_editor','tool_call_id':'c',
           'thought':[{'type':'text','text':'The test failed; fix the bad property.'}],
           'action':{'command':'str_replace','path':'game.gd','old_str':'bad','new_str':'good'}}]
    archive=tmp_path/'trace.tar.gz'
    with tarfile.open(archive,'w:gz') as t:
        # Deliberately stored out of order; original sequence is in member names.
        for i in reversed(range(len(rows))):
            data=json.dumps(rows[i]).encode();m=tarfile.TarInfo(f'conv/events/event-{i:05d}-x.json');m.size=len(data)
            t.addfile(m,io.BytesIO(data))
    run=import_run(archive,tmp_path/'out',benchmark='swe-mm',case_id='test',model='test',format='openhands-sdk')
    assert run['timeline'][3]['source_event_id']=='result-b'
    assert run['timeline'][5]['kind']=='model_text'
    windows=extract_candidate_windows(tmp_path/'out/run.json')
    assert [e['ordinal'] for e in windows[0]['events']]==[1,4,5]
    assert [e['ordinal'] for e in windows[1]['events']]==[2,3]
    audit=audit_input(tmp_path/'out/run.json')
    assert audit['images'][0]['available'] is True
    assert audit['missing_tool_results']==[6]
    assert audit['recorded_edit_events']==[6]


def test_reference_read_never_becomes_reconstructed_self_observation(tmp_path):
    source=tmp_path/'claude.jsonl'
    rows=[{'type':'user','uuid':'u','message':{'content':'Match reference /data/reference.png.'}},
          {'type':'assistant','uuid':'a','message':{'content':[{'type':'tool_use','id':'r','name':'Read','input':{'file_path':'/data/reference.png'}}]}},
          {'type':'user','uuid':'b','message':{'content':[{'type':'tool_result','tool_use_id':'r','content':[{'type':'image','source':{'data':'missing','media_type':'image/png'}}]}]}}]
    source.write_text('\n'.join(json.dumps(r) for r in rows))
    run=import_run(source,tmp_path/'out',benchmark='3dcodebench',case_id='x',model='x',format='claude')
    assert run['timeline'][0]['text']==rows[0]['message']['content']
    assert run['timeline'][2]['image_role']=='task_reference'
    assert len(extract_candidate_windows(tmp_path/'out/run.json'))==1
    run['_path']=str(tmp_path/'out/run.json')
    missing=prepare_task(tmp_path/'task',benchmark='3dcodebench',run=run)
    assert len(missing)==1
    assert not audit_input(tmp_path/'out/run.json')['images'][0]['available']


def test_gamedev_task_does_not_leak_validation_or_turn_results_into_trajectory(tmp_path):
    task=tmp_path/'game';task.mkdir()
    write_json(task/'task_config.json',{'instruction':'Draw a red player.','metadata':{'answer':'SECRET'}})
    (task/'task_validation.md').write_text('SECRET VALIDATOR')
    prepare_task(tmp_path/'task',benchmark='gamedevbench',task_source=task)
    assert (tmp_path/'task/prompt.txt').read_text()=='Draw a red player.\n'
    write_json(tmp_path/'result.json',{'success':True,'tasks':[]})
    with pytest.raises(ValueError,match='timeline'):
        import_run(tmp_path/'result.json',tmp_path/'out',benchmark='gamedevbench',case_id='x',model='x',format='canonical')


def test_command_acceptance_runs_a_copy_and_keeps_multiple_images(tmp_path):
    from PIL import Image
    app=tmp_path/'version/app';app.mkdir(parents=True);(app/'value').write_text('before')
    # Inline Python here is a test fixture; production probes use an explicit isolated launcher.
    code="import pathlib,sys; p=pathlib.Path(sys.argv[1]); print((p/'app/value').read_text()); (p/'app/value').write_text('changed')"
    runtime={'kind':'command','command':[sys.executable], 'probes':[{'probe_id':'test','description':'Inspect recorded value.','args':['-c',code,'{workspace}'],'images':[]}]}
    spec={'runtime':runtime,'acceptance_workflows':[{'probe_id':'test','check_ids':['a']}]}
    from multimodalcode.vsv_eval.repair_pilot import digest_tree
    version={'version':'V0','workspace':str(app.parent),'code_manifest':digest_tree(app)}
    result=replay_commands(version,spec,tmp_path/'out')
    assert result['workflows'][0]['nodes'][0]['observation']['stdout']=='before\n'
    assert (app/'value').read_text()=='before'
    assert replay_commands(version,spec,tmp_path/'out')==result
    with pytest.raises(ValueError,match='criterion'):
        validate_assignments([{'probe_id':'test','check_ids':['bogus']}],runtime,{'checks':[{'check_id':'a'}]})
    from multimodalcode.vsv_eval.states import state_packet
    paths=[]
    for i in range(2):
        p=tmp_path/f'{i}.png';Image.new('RGB',(10,10)).save(p);paths.append(str(p))
    node=result['workflows'][0]['nodes'][0];node['observation']['screenshots']=paths
    packet,images=state_packet({'checks':[{'check_id':'a','reference_ids':[]}],'references':{},'criteria_sha256':'x'},version,result,{},tmp_path/'packet')
    assert len({v['source_path'] for v in packet['image_order']})==2


def test_catalogue_only_review_has_no_browser_plan_and_is_reproducible(tmp_path):
    task=tmp_path/'task';task.mkdir();(task/'prompt.txt').write_text('Create a chair.')
    check={'check_id':'chair','source_ref':'task:0','setup':'Inspect the object.', 'criterion':'A recognizable chair.','required_evidence':'Rendered views.','reference_ids':[], 'requirement_types':['visual']}
    judge=JudgeClient(JudgeProfile('test','openai-compatible','test','unused','UNUSED'),tmp_path/'cache')
    judge._request=Mock(return_value=json.dumps({'checks':[check],'repair_checks':[], 'acceptance_workflows':[],'unresolved_check_ids':[]}))
    cat,plan=prepare_evaluation_plan(task,judge,tmp_path/'plan',catalogue_only=True)
    assert load_catalogue(tmp_path/'plan/catalogue.json')==cat
    assert plan['acceptance_workflows']==[]
    assert 'probe_id' in judge._request.call_args.args[0]


@pytest.mark.parametrize('benchmark',['swe-mm','3dcodebench','gamedevbench'])
def test_six_metric_runner_with_real_command_acceptance(tmp_path,monkeypatch,benchmark):
    from test_vsv_preparation import fixture
    rounds,cat=fixture(tmp_path)
    binding=read_json(tmp_path/'binding.json')
    binding['runtime']={'kind':'command','command':[sys.executable], 'code_subdir':'app',
        'probes':[{'probe_id':'inspect','description':'Read item visibility and service availability.',
                   'args':['-c',"import pathlib; print(pathlib.Path('app/item.txt').read_text()); print('service available')"], 'images':[]}]}
    write_json(tmp_path/'binding.json',binding)
    experiment=read_json(tmp_path/'experiment.json');experiment['cases'][0]['benchmark']=benchmark
    write_json(tmp_path/'experiment.json',experiment)
    calls=[]
    def client(config,profile,cache,response_schema=None):
        cache=Path(cache)
        def judge(stage,prompt,images,**kwargs):
            calls.append(stage)
            if stage=='plan_review':
                value={'checks':cat['checks'],'repair_checks':[],'unresolved_check_ids':[],
                       'acceptance_workflows':[{'probe_id':'inspect','check_ids':['a','b']}],
                       'repair_bindings':[{'id':'repair-3','target_ids':['a'],
                         'target_aliases':[{'target_id':'a','check_ids':['a']},{'target_id':'b','check_ids':[]}]}]}
            elif stage=='recheck_alignment':
                value={'matches':[{'episode_id':'before','target_id':'a','recheck_episode_id':'after','recheck_target_id':'a'}]}
            else:
                packet=json.loads(prompt.split('EVIDENCE:\n',1)[1])
                if stage.endswith(('_rs','_cp')):
                    # Mock only semantic judgments; the observations came from real subprocesses.
                    observed=packet['evidence'][0]
                    assert observed['observation']['returncode']==0
                    before='missing' in observed['observation']['stdout']
                    value={'checks':[{'check_id':c['check_id'],'state':'fail' if before and c['check_id']=='a' else 'pass',
                                      'evidence_ids':[observed['evidence_id']]} for c in packet['checks']]}
                else:
                    before=packet['episode_id']=='before';obs,diag=(1,2) if before else (6,7)
                    if stage.endswith('_vc'):
                        value={'targets':[{'target':c['criterion'],'check_id':c['check_id'],'coverage':'full','evidence_ids':[obs],'modality':'text'} for c in cat['checks']]}
                    elif stage.endswith('_cv'):
                        value={'targets':[{'target_id':c,'method_ok':True,'evidence_ok':True,'evidence_ids':[obs]} for c in ['a','b']]}
                    elif stage.endswith('_observation'):
                        value={'targets':[{'target_id':c,'actual_state':'fail' if before and c=='a' else 'pass',
                            'actual_issue':{'object':'item','symptom':'missing'} if before and c=='a' else None,'evidence_ids':[obs]} for c in ['a','b']],
                            'additional_defects':[]}
                    elif stage.endswith('_bda'):
                        value={'targets':[{'target_id':c,'agent_state':'fail' if before and c=='a' else 'pass',
                            'issue_match':True if before and c=='a' else None,'diagnosis_ids':[diag]} for c in ['a','b']]}
                    else:pytest.fail(stage)
            key=content_hash([stage,prompt]);record={'stage':stage,'model':'test','prompt':prompt,'request_sha256':key,'parsed':value}
            write_json(cache/f'{key}.json',record);return record
        return SimpleNamespace(cache_root=cache,judge=judge,_key=lambda stage,prompt,images:content_hash([stage,prompt]),
                               profile=SimpleNamespace(model='test',base_url='offline',generation_settings=lambda:{}))
    for module in ['evaluation','preparation','states']:
        monkeypatch.setattr(f'multimodalcode.vsv_eval.{module}.build_client',client)
    assert system.run_experiment(tmp_path/'experiment.json',tmp_path/'out')==0
    row=read_json(tmp_path/'out/summary.json')['cases'][0]
    assert row['benchmark']==benchmark
    assert row['status']=='completed'
    assert all(row['metrics'][m]['score']==100 for m in system.METRICS)
    assert 'text_rs' in calls and 'text_cp' in calls
    assert not any(c=='gui_step' for c in calls)
    # The same request/response evidence also validates on a resumed complete run.
    assert system.run_experiment(tmp_path/'experiment.json',tmp_path/'out')==0


def test_render_failure_keeps_real_output_without_inventing_pixels(tmp_path):
    app=tmp_path/'version/app';app.mkdir(parents=True)
    from multimodalcode.vsv_eval.repair_pilot import digest_tree
    runtime={'kind':'command','command':[sys.executable],'probes':[
        {'probe_id':'render','description':'Render object.','args':['-c',"print('ERR_NO_MESH: no mesh was produced')"],
         'images':['view.png'],'reports':['render_log.json']}]}
    result=replay_commands({'version':'V0','workspace':str(app.parent),'code_manifest':digest_tree(app)},
                           {'runtime':runtime,'acceptance_workflows':[{'probe_id':'render','check_ids':['object']}]},tmp_path/'out')
    observation=result['workflows'][0]['nodes'][0]['observation']
    assert observation['missing_images']==['view.png']
    assert observation['screenshots']==[]
    assert 'ERR_NO_MESH' in observation['stdout']
