from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from .backends import ModelBackend, create_backend
from .config import ProjectConfig
from .datasets import load_benchmark
from .evaluation import evaluate_run
from .generation import generate_run
from .io import read_json
from .rendering import render_run


def _json_object(value: Optional[str]) -> Dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Expected a JSON object")
    return parsed


def _backend_from_args(args: argparse.Namespace, prefix: str = "") -> ModelBackend:
    def value(name: str, default: Any = None) -> Any:
        return getattr(args, f"{prefix}{name}", default)

    api_key = value("api_key")
    api_key_env = value("api_key_env")
    if not api_key and api_key_env:
        api_key = os.environ.get(api_key_env)
    return create_backend(
        backend=value("backend"),
        model=value("model"),
        base_url=value("base_url"),
        api_key=api_key,
        timeout=value("timeout", 600),
        extra_body=_json_object(value("extra_body")),
        command=value("command"),
        trust_remote_code=value("trust_remote_code", False),
    )


def _add_backend_arguments(
    parser: argparse.ArgumentParser,
    *,
    prefix: str = "",
    title: str = "",
    required: bool = True,
) -> None:
    flag = f"--{prefix.replace('_', '-')}" if prefix else "--"
    destination = prefix
    parser.add_argument(
        f"{flag}backend",
        dest=f"{destination}backend",
        required=required,
        choices=[
            "openai-compatible",
            "vllm",
            "sglang",
            "lmdeploy",
            "transformers",
            "command",
            "mock",
        ],
        help=f"{title} backend",
    )
    parser.add_argument(
        f"{flag}model", dest=f"{destination}model", required=required,
        help=f"{title} model ID or served model name",
    )
    parser.add_argument(
        f"{flag}base-url",
        dest=f"{destination}base_url",
        help="One endpoint or comma-separated replica endpoints",
    )
    parser.add_argument(f"{flag}api-key", dest=f"{destination}api_key")
    parser.add_argument(
        f"{flag}api-key-env", dest=f"{destination}api_key_env",
        default="OPENAI_API_KEY",
    )
    parser.add_argument(
        f"{flag}timeout", dest=f"{destination}timeout", type=float, default=600
    )
    parser.add_argument(
        f"{flag}extra-body", dest=f"{destination}extra_body",
        help='Additional request body as JSON, e.g. \'{"top_p":0.9}\'',
    )
    parser.add_argument(
        f"{flag}command", dest=f"{destination}command",
        help="Executable for the command backend",
    )
    parser.add_argument(
        f"{flag}trust-remote-code",
        dest=f"{destination}trust_remote_code",
        action="store_true",
    )


def command_list(args: argparse.Namespace) -> int:
    project = ProjectConfig(args.config)
    for name in project.names(include_disabled=True):
        config = project.benchmark(name)
        evaluator = config.evaluator.get("type", "ui2code_vlm")
        status = "enabled" if config.enabled else "disabled"
        print(
            f"{name:24} {status:8} adapter={config.adapter:22} "
            f"output={config.output_format:7} evaluator={evaluator}"
        )
        if not config.enabled and config.values.get("disabled_reason"):
            print(f"{'':26}{config.values['disabled_reason']}")
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    project = ProjectConfig(args.config)
    config = project.benchmark(args.benchmark)
    print(json.dumps(config.values, ensure_ascii=False, indent=2))
    if config.enabled:
        items = load_benchmark(config, limit=args.limit)
        missing = [
            image
            for item in items
            for image in item.image_paths
            if not Path(image).exists()
        ]
        print(f"\nitems={len(items)} missing_images={len(missing)}")
        if items:
            print(json.dumps(items[0].to_dict(), ensure_ascii=False, indent=2)[:5000])
    return 0


def command_doctor(args: argparse.Namespace) -> int:
    project = ProjectConfig(args.config)
    print(f"config: {project.path}")
    print(f"python: {sys.version.split()[0]}")
    for module in ("PIL", "playwright", "torch", "transformers"):
        status = "available" if importlib.util.find_spec(module) else "missing"
        print(f"python module {module:12} {status}")
    for executable in ("node", "docker", "chromium", "google-chrome", "ffmpeg"):
        found = shutil.which(executable)
        print(f"executable    {executable:12} {found or 'missing'}")
    print("\nbenchmarks:")
    for name in project.names(include_disabled=True):
        config = project.benchmark(name)
        if not config.enabled:
            print(f"  {name:24} disabled: {config.values.get('disabled_reason', '')}")
            continue
        try:
            items = load_benchmark(config)
            missing = sum(
                not Path(image).exists()
                for item in items
                for image in item.image_paths
            )
            print(f"  {name:24} items={len(items):4} missing_images={missing}")
        except Exception as exc:
            print(f"  {name:24} ERROR {type(exc).__name__}: {exc}")
    print("\nevaluation notes:")
    print(
        "  Flame official embedding endpoint: "
        + ("configured" if os.environ.get("FLAME_EMBEDDING_URL") else "not configured")
    )
    print("  All registered benchmark data and source paths are project-local.")
    return 0


def command_generate(args: argparse.Namespace) -> int:
    project = ProjectConfig(args.config)
    config = project.benchmark(args.benchmark)
    backend = _backend_from_args(args)
    summary = generate_run(
        config,
        backend,
        args.run_dir,
        model_name=args.model,
        backend_name=args.backend,
        limit=args.limit,
        workers=args.workers,
        samples=args.samples,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        retries=args.retries,
        system_prompt=args.system_prompt,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_render(args: argparse.Namespace) -> int:
    summary = render_run(
        args.run_dir,
        workers=args.workers,
        width=args.width,
        height=args.height,
        full_page=args.full_page,
        wait_ms=args.wait_ms,
        timeout_ms=args.timeout_ms,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _evaluator_type(run_dir: str | Path) -> str:
    manifest = read_json(Path(run_dir) / "run.json")
    value = manifest["benchmark_config"].get("evaluator", {})
    if isinstance(value, str):
        return value
    return value.get("type", "ui2code_vlm")


def command_evaluate(args: argparse.Namespace) -> int:
    evaluator_type = _evaluator_type(args.run_dir)
    backend = None
    if evaluator_type in {"ui2code_vlm", "web2code_vlm"}:
        if not args.judge_backend or not args.judge_model:
            raise ValueError(
                f"{evaluator_type} requires --judge-backend and --judge-model"
            )
        backend = _backend_from_args(args, prefix="judge_")
    summary = evaluate_run(
        args.run_dir,
        judge_backend=backend,
        judge_model=args.judge_model or "",
        workers=args.workers,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        retries=args.retries,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    project = ProjectConfig(args.config)
    names = args.benchmarks
    if names == ["all"]:
        names = project.names(include_disabled=False)
    stages = {value.strip() for value in args.stages.split(",") if value.strip()}
    unknown_stages = stages - {"generate", "render", "evaluate"}
    if not stages or unknown_stages:
        raise ValueError(
            "--stages must contain generate, render, and/or evaluate"
        )

    generation_backend = None
    if args.backend or args.model:
        if not args.backend or not args.model:
            raise ValueError("--backend and --model must be provided together")
        generation_backend = _backend_from_args(args)
    if "generate" in stages and generation_backend is None:
        raise ValueError("The generate stage requires --backend and --model")

    judge_backend = None
    judge_model = ""
    if args.judge_backend or args.judge_model:
        if not args.judge_backend or not args.judge_model:
            raise ValueError(
                "--judge-backend and --judge-model must be provided together"
            )
        judge_backend = _backend_from_args(args, prefix="judge_")
        judge_model = args.judge_model
    elif generation_backend is not None:
        # The common local workflow serves one multimodal model through vLLM.
        # Reuse that endpoint as the visual judge unless explicitly overridden.
        judge_backend = generation_backend
        judge_model = args.model

    output = Path(args.output_root) / "run_summary.json"
    all_summaries: Dict[str, Any] = (
        read_json(output) if output.exists() else {}
    )
    for name in names:
        config = project.benchmark(name)
        run_dir = Path(args.output_root) / name
        benchmark_summary: Dict[str, Any] = dict(all_summaries.get(name, {}))
        print(f"\n=== {name} ===", flush=True)
        if "generate" in stages:
            assert generation_backend is not None
            benchmark_summary["generation"] = generate_run(
                config,
                generation_backend,
                run_dir,
                model_name=args.model,
                backend_name=args.backend,
                limit=args.limit,
                workers=args.workers,
                samples=args.samples,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
                retries=args.retries,
                overwrite=args.overwrite,
            )
        if "render" in stages:
            benchmark_summary["render"] = render_run(
                run_dir,
                workers=args.render_workers,
                overwrite=args.overwrite,
            )
        if "evaluate" in stages:
            evaluator_type = _evaluator_type(run_dir)
            if evaluator_type in {"ui2code_vlm", "web2code_vlm"} and judge_backend is None:
                raise ValueError(
                    f"{name} requires --judge-backend and --judge-model"
                )
            benchmark_summary["evaluation"] = evaluate_run(
                run_dir,
                judge_backend=judge_backend,
                judge_model=judge_model,
                workers=args.judge_workers,
                retries=args.retries,
                overwrite=args.overwrite,
            )
        all_summaries[name] = benchmark_summary
    from .io import write_json

    write_json(output, all_summaries)
    print(json.dumps(all_summaries, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python run.py",
        description="Unified multimodal web-code benchmark runner",
    )
    parser.add_argument("--config", help="Benchmark registry JSON")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    list_parser = subparsers.add_parser("list", help="List registered benchmarks")
    list_parser.set_defaults(function=command_list)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect data resolution")
    inspect_parser.add_argument("benchmark")
    inspect_parser.add_argument("--limit", type=int, default=1)
    inspect_parser.set_defaults(function=command_inspect)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check datasets, runtimes, and optional dependencies"
    )
    doctor_parser.set_defaults(function=command_doctor)

    generate_parser = subparsers.add_parser("generate", help="Run model inference")
    generate_parser.add_argument("benchmark")
    generate_parser.add_argument("--run-dir", required=True)
    _add_backend_arguments(generate_parser)
    generate_parser.add_argument("--limit", type=int)
    generate_parser.add_argument("--workers", type=int, default=4)
    generate_parser.add_argument("--samples", type=int, default=1)
    generate_parser.add_argument("--max-tokens", type=int, default=8192)
    generate_parser.add_argument("--temperature", type=float, default=0.0)
    generate_parser.add_argument("--retries", type=int, default=2)
    generate_parser.add_argument("--system-prompt")
    generate_parser.add_argument("--overwrite", action="store_true")
    generate_parser.set_defaults(function=command_generate)

    render_parser = subparsers.add_parser("render", help="Render generated HTML")
    render_parser.add_argument("--run-dir", required=True)
    render_parser.add_argument("--workers", type=int, default=4)
    render_parser.add_argument("--width", type=int)
    render_parser.add_argument("--height", type=int)
    render_parser.add_argument(
        "--full-page", action=argparse.BooleanOptionalAction, default=None
    )
    render_parser.add_argument("--wait-ms", type=int)
    render_parser.add_argument("--timeout-ms", type=int, default=60000)
    render_parser.add_argument("--overwrite", action="store_true")
    render_parser.set_defaults(function=command_render)

    evaluate_parser = subparsers.add_parser("evaluate", help="Score a run")
    evaluate_parser.add_argument("--run-dir", required=True)
    _add_backend_arguments(
        evaluate_parser, prefix="judge_", title="judge", required=False
    )
    evaluate_parser.add_argument("--workers", type=int, default=4)
    evaluate_parser.add_argument("--max-tokens", type=int, default=1024)
    evaluate_parser.add_argument("--temperature", type=float, default=0.0)
    evaluate_parser.add_argument("--retries", type=int, default=2)
    evaluate_parser.add_argument("--overwrite", action="store_true")
    evaluate_parser.set_defaults(function=command_evaluate)

    def add_run_arguments(run_parser: argparse.ArgumentParser) -> None:
        run_parser.add_argument("benchmarks", nargs="+")
        run_parser.add_argument("--output-root", required=True)
        run_parser.add_argument(
            "--stages", default="generate,render,evaluate",
            help="Comma-separated: generate,render,evaluate",
        )
        _add_backend_arguments(run_parser, required=False)
        _add_backend_arguments(
            run_parser, prefix="judge_", title="judge", required=False
        )
        run_parser.add_argument("--limit", type=int)
        run_parser.add_argument("--workers", type=int, default=4)
        run_parser.add_argument("--render-workers", type=int, default=4)
        run_parser.add_argument("--judge-workers", type=int, default=4)
        run_parser.add_argument("--samples", type=int, default=1)
        run_parser.add_argument("--max-tokens", type=int, default=8192)
        run_parser.add_argument("--temperature", type=float, default=0.0)
        run_parser.add_argument("--retries", type=int, default=2)
        run_parser.add_argument("--overwrite", action="store_true")
        run_parser.set_defaults(function=command_run)

    run_parser = subparsers.add_parser(
        "run",
        prog="python run.py",
        help="Run inference and benchmark-specific evaluation",
    )
    add_run_arguments(run_parser)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.function(args))
    except (ValueError, KeyError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
