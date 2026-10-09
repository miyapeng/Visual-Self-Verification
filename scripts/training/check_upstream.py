#!/usr/bin/env python3
"""Check the pinned source interfaces and Swift's message-level loss behavior on CPU."""
import argparse
import ast
import json
import os
import types
import typing
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', required=True, help='Directory with verl/ and ms-swift/ source trees')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sources = Path(args.sources).resolve()
    pins = json.loads((root / 'configs/training/upstream_sources.json').read_text())
    for name, pin in pins.items():
        metadata = sources / name / 'source.json'
        if metadata.exists() and json.loads(metadata.read_text())['revision'] != pin['commit']:
            raise ValueError(f'Unexpected {name} source revision')
    expected = {
        'verl/experimental/agent_loop/tool_agent_loop.py': {'ToolAgentLoop': ['run', '_handle_generating_state', '_call_tool', '_build_assistant_message']},
        'verl/experimental/agent_loop/agent_loop.py': {'AgentLoopOutput': [], 'ToolListWrap': []},
        'verl/trainer/ppo/v1/trainer_base.py': {'PPOTrainer': ['_compute_advantage']},
        'verl/utils/dataset/rl_dataset.py': {'RLHFDataset': ['_build_messages']},
    }
    for file, classes in expected.items():
        tree = ast.parse((sources / 'verl' / file).read_text())
        definitions = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
        for name, methods in classes.items():
            available = {n.name for n in definitions[name].body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if not set(methods) <= available:
                raise ValueError(f'Upstream interface changed: {file}:{name}')
    # Execute the actual upstream loss implementation, supplying only its template enums.
    # This verifies message masks, not the model-specific image processor or GPU kernels.
    source = ast.parse((sources / 'ms-swift/swift/loss_scale/base.py').read_text())
    loss_class = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'LossScale')
    context = types.SimpleNamespace(RESPONSE='response', SUFFIX='suffix', OTHER='other')
    scope = {**vars(typing), 'ContextType': context, 'Messages': list,
             'ALL_BASE_STRATEGY': ['default', 'last_round', 'all'], 'get_last_user_round': lambda messages: 2}
    exec(compile(ast.Module(body=[loss_class], type_ignores=[]), '<pinned Swift LossScale>', 'exec'), scope)
    messages = [{'role': 'user', 'content': 'task'}, {'role': 'assistant', 'content': 'student', 'loss': False},
                {'role': 'tool', 'content': 'pixels/logs'}, {'role': 'assistant', 'content': 'teacher', 'loss': True}]
    texts = ['prompt', 'student', 'pixels/logs', 'teacher', 'end']
    _, masks = scope['LossScale']()(texts, ['other', 'response', 'other', 'response', 'suffix'], messages)
    if masks != [0, 0, 0, 1, 1]:
        raise ValueError(f'Swift prefix/suffix loss behavior changed: {masks}')
    from hydra import compose, initialize_config_dir
    from omegaconf import OmegaConf
    for name in ('VSV_TRAIN_DATA', 'VSV_VALIDATION_DATA', 'VSV_DISTILLED_MODEL', 'VSV_AGENT_LOOP_CONFIG', 'VSV_ROLLOUT_DIR'):
        os.environ.setdefault(name, '/interface-check/' + name.lower())
    with initialize_config_dir(version_base=None, config_dir=str(root / 'configs/training')):
        config = compose(config_name='verification_grpo', overrides=[
            'hydra.searchpath=[file://' + str(sources / 'verl/verl/trainer/config') + ']'])
        OmegaConf.resolve(config)
    if config.trainer.v1.trainer_mode != 'verification_sync' or config.algorithm.adv_estimator != 'grpo':
        raise ValueError('Joint GRPO configuration is not active')
    print(json.dumps({'source_interfaces': 'passed', 'swift_message_masks': masks,
                      'hydra_composition': 'passed', 'gpu_training': 'not_run'}, indent=2))


if __name__ == '__main__':
    main()
