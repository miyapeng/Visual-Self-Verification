"""Small interface tests; these do not stand in for a distributed GPU training run."""
import asyncio
import copy
import json
import sys
import types
from pathlib import Path

from multimodalcode.io import write_json
from multimodalcode.training.handoff import make_checkpoint


def test_verl_wrapper_preserves_parent_tokens_logprobs_and_pixels(tmp_path, monkeypatch):
    def module(name, **attributes):
        obj = types.ModuleType(name)
        vars(obj).update(attributes)
        monkeypatch.setitem(sys.modules, name, obj)
        return obj
    module('torch')
    module('transfer_queue')
    module('tensordict', TensorDict=dict)
    pixels = object()
    native = types.SimpleNamespace(prompt_ids=[5, 8], response_ids=[90, 91, 7, 92],
        response_mask=[1, 1, 0, 1], response_logprobs=[-.2, -.1, 0, -.3],
        multi_modal_data={'images': [pixels]}, extra_fields={}, reward_score=None)
    expected_tokens = copy.deepcopy({k: getattr(native, k) for k in ('prompt_ids', 'response_ids', 'response_mask', 'response_logprobs')})
    class ParentLoop:
        def __init__(self, *args, tools=None, **kwargs):
            self.tools, self.tool_schemas, self.prompt_length = {'browser': object()}, ['browser'], 100
        async def run(self, sampling_params, priority=0, **kwargs):
            assert not self.tools  # Fixed-evidence diagnosis has no execution tools.
            self.suffix.append({'role': 'assistant', 'content': 'The screenshot meets the requirement.'})
            return native
    class Tool:
        def __init__(self, *args): pass
    class Schema:
        def __init__(self, **kwargs): pass
    module('verl.experimental.agent_loop.agent_loop', ToolListWrap=lambda tools: tools)
    module('verl.experimental.agent_loop.tool_agent_loop', ToolAgentLoop=ParentLoop)
    module('verl.tools.base_tool', BaseTool=Tool)
    module('verl.tools.schemas', OpenAIFunctionToolSchema=Schema, ToolResponse=Schema)
    module('verl.trainer.ppo.v1.trainer_base', register_trainer=lambda name: lambda cls: cls)
    module('verl.trainer.ppo.v1.trainer_sync', PPOTrainerSync=object)
    module('verl.utils.dataset.rl_dataset', RLHFDataset=object)
    namespace = {'__name__': 'contract_verl_adapter', '__package__': 'multimodalcode.training'}
    path = Path(__file__).parents[1] / 'src/multimodalcode/training/verl_integration.py'
    exec(compile(path.read_text(), str(path), 'exec'), namespace)
    class Reward:
        def __init__(self, *a): pass
        async def begin(self, *a): pass
        async def score(self, *a): return {'score': .75}
    namespace['VerificationReward'] = Reward
    checkpoint = make_checkpoint(task_id='unit', benchmark='fixture', kind='diagnosis', policy_revision='student-1',
        split='train', messages=[{'role': 'user', 'content': 'Inspect the evidence.'}])
    cp = tmp_path / 'checkpoint.json'
    write_json(cp, checkpoint)
    monkeypatch.setenv('VSV_ROLLOUT_DIR', str(tmp_path / 'rollouts'))
    loop = namespace['VerificationAgentLoop']()
    result = asyncio.run(loop.run({}, raw_prompt=checkpoint['messages'],
                                 extra_info={'checkpoint_path': str(cp), 'checkpoint_id': checkpoint['id']}))
    assert result is native
    assert result.multi_modal_data['images'][0] is pixels
    assert {k: getattr(result, k) for k in expected_tokens} == expected_tokens
    assert result.reward_score == .75
    assert result.extra_fields['reward_extra_info']['checkpoint_id'] == checkpoint['id']
    assert loop.tools  # The same loop can subsequently handle a different decision kind.
    dataset = namespace['VerificationDataset']()
    assert dataset._build_messages({'prompt': json.dumps(checkpoint['messages'])}) == checkpoint['messages']
