"""Thin extensions for the pinned verl revision; import only in the RL environment."""
from __future__ import annotations

import copy
import json
import os
from collections import Counter
from pathlib import Path
from uuid import uuid4

import torch
import transfer_queue as tq
from PIL import Image
from tensordict import TensorDict
from verl.experimental.agent_loop.agent_loop import ToolListWrap
from verl.experimental.agent_loop.tool_agent_loop import ToolAgentLoop
from verl.tools.base_tool import BaseTool
from verl.tools.schemas import OpenAIFunctionToolSchema, ToolResponse
from verl.trainer.ppo.v1.trainer_base import register_trainer
from verl.trainer.ppo.v1.trainer_sync import PPOTrainerSync
from verl.utils.dataset.rl_dataset import RLHFDataset

from multimodalcode.io import write_json
from .collector import tool_message
from .environment import AsyncWebEnvironment, TOOL_SCHEMAS
from .handoff import load_checkpoint, validate_native_output
from .reward import VerificationReward, objective_weights, trajectory


class VerificationDataset(RLHFDataset):
    def _build_messages(self, example, key='prompt'):
        return json.loads(example[key])


class VerificationAgentLoop(ToolAgentLoop):
    def __init__(self, *args, **kwargs):
        kwargs['tools'] = ToolListWrap([BaseTool({}, OpenAIFunctionToolSchema(**schema)) for schema in TOOL_SCHEMAS])
        super().__init__(*args, **kwargs)

    async def run(self, sampling_params, priority=0, **kwargs):
        extra = kwargs['extra_info']
        checkpoint = load_checkpoint(extra['checkpoint_path'])
        if checkpoint['id'] != extra['checkpoint_id'] or checkpoint['messages'] != kwargs['raw_prompt']:
            raise ValueError('Rollout input differs from the recorded student handoff')
        if checkpoint['split'] == 'test':
            raise ValueError('Held-out test checkpoints must not enter the RL trainer')
        root = Path(os.environ['VSV_ROLLOUT_DIR']).resolve() / uuid4().hex
        root.mkdir(parents=True, exist_ok=False)
        self.environment = AsyncWebEnvironment() if checkpoint['environment'] else None
        self.suffix, self.records, self.truncated = [], [], False
        evaluator = VerificationReward(checkpoint, root / 'evaluation')
        original_tools, original_schemas = self.tools, self.tool_schemas
        if checkpoint['kind'] == 'diagnosis':
            # Do not advertise execution tools for a fixed-evidence decision.
            self.tools, self.tool_schemas = {}, []
        try:
            if self.environment:
                await self.environment.restore(checkpoint['environment'], root / 'environment')
            await evaluator.begin(self.environment)
            output = await super().run(sampling_params, priority, **kwargs)
            validate_native_output(output)
            if len(output.prompt_ids) > self.prompt_length:
                raise ValueError('The actual multimodal prefix exceeds the configured prompt budget')
            if not self.suffix:
                raise ValueError('Rollout returned no generated turn')
            write_json(root / 'suffix.json', self.suffix)
            write_json(root / 'tool_records.json', self.records)
            run, _ = trajectory(checkpoint, self.suffix, self.records)
            write_json(root / 'run.json', run)
            result = await evaluator.score(self.suffix, self.records, self.environment)
            # No re-tokenization: only attach reward and metadata to verl's output.
            output.reward_score = result['score']
            output.extra_fields['reward_extra_info'] = {'verification_kind': checkpoint['kind'],
                'checkpoint_id': checkpoint['id'], 'generation_truncated': self.truncated,
                'reward_path': str(root / 'evaluation/reward.json')}
            write_json(root / 'policy_tokens.json', {'prompt_ids': output.prompt_ids,
                'response_ids': output.response_ids, 'response_mask': output.response_mask,
                'response_logprobs': output.response_logprobs})
            return output
        finally:
            self.tools, self.tool_schemas = original_tools, original_schemas
            if self.environment:
                await self.environment.close()

    def _build_assistant_message(self, content, agent_data):
        if len(agent_data.tool_calls) > self.max_parallel_calls:
            raise ValueError('Increase max_parallel_calls; never silently discard generated tool calls')
        for call in agent_data.tool_calls:
            if call.tool_call_id is None:
                call.tool_call_id = uuid4().hex
        return super()._build_assistant_message(content, agent_data)

    async def _handle_generating_state(self, agent_data, sampling_params, ignore_termination=False):
        before = len(agent_data.messages)
        remaining = max(0, self.response_length - len(agent_data.response_mask))
        state = await super()._handle_generating_state(agent_data, sampling_params, ignore_termination)
        if len(agent_data.messages) == before:
            # Budget exhaustion is policy behavior, not an infrastructure failure.
            # Decode only for the evidence log; never rebuild policy tokens or execute
            # a call that the native loop did not execute.
            self.truncated = True
            self.suffix.append({'role': 'assistant', 'content': self.tokenizer.decode(
                agent_data.response_ids[:remaining], skip_special_tokens=False)})
        else:
            self.suffix.append(copy.deepcopy(agent_data.messages[-1]))
        return state

    async def _call_tool(self, tool_call, tools_kwargs, agent_data):
        if self.environment is None:
            raise ValueError('A fixed-evidence diagnosis attempted an execution tool')
        try:
            arguments = json.loads(tool_call.arguments)
        except (ValueError, TypeError):
            arguments = {'invalid_arguments': tool_call.arguments}
        record = await self.environment.call('execute', tool_call.name, arguments, tool_call.tool_call_id)
        self.records.append(record)
        self.suffix.append(tool_message(record))
        images = []
        for path in record['images']:
            with Image.open(path) as im:
                images.append(im.convert('RGB').copy())
        return ToolResponse(text=record['text'], image=images or None), 0.0, {}


@register_trainer('verification_sync')
class VerificationTrainer(PPOTrainerSync):
    def _compute_advantage(self, batch, metrics):
        config = self.config
        if config.algorithm.adv_estimator != 'grpo' or config.algorithm.use_kl_in_reward:
            raise ValueError('Use GRPO with a separate actor KL loss')
        if not config.actor_rollout_ref.actor.use_kl_loss:
            raise ValueError('Enable the frozen distilled reference KL loss')
        if config.actor_rollout_ref.actor.loss_agg_mode != 'seq-mean-token-mean':
            raise ValueError('Decision-type weighting requires sequence-mean loss aggregation')
        if config.actor_rollout_ref.rollout.n < 2:
            raise ValueError('GRPO requires at least two continuations per checkpoint')
        data = tq.kv_batch_get(keys=batch.keys, partition_id=batch.partition_id, select_fields=['extra_info', 'uid'])
        extra = data['extra_info'].tolist()
        uids = data['uid'].tolist()
        if any(n != config.actor_rollout_ref.rollout.n for n in Counter(uids).values()):
            raise ValueError('Every GRPO group must retain all configured continuations')
        groups = {}
        for uid, row in zip(uids, extra):
            group = (row['checkpoint_id'], row['kind'])
            if uid in groups and groups[uid] != group:
                raise ValueError('A GRPO group mixes checkpoint identities or decision kinds')
            groups[uid] = group
        weights = objective_weights([r['kind'] for r in extra],
            local_weight=config.verification.local_weight,
            local_kinds=list(config.verification.local_kinds), include_task=config.verification.include_task)
        batch = super()._compute_advantage(batch, metrics)
        data = tq.kv_batch_get(keys=batch.keys, partition_id=batch.partition_id, select_fields=['advantages'])
        advantages = data['advantages']
        weighted = torch.nested.as_nested_tensor(
            [row * weight for row, weight in zip(advantages.unbind(), weights)], layout=advantages.layout)
        metrics['verification/local_weight'] = config.verification.local_weight
        return tq.kv_batch_put(keys=batch.keys, partition_id=batch.partition_id,
                               fields=TensorDict({'advantages': weighted}, batch_size=len(batch)))
