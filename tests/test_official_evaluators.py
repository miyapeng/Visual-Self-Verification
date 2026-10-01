from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _verification_module():
    path = PROJECT_ROOT / "evaluate" / "verify_official_sources.py"
    spec = importlib.util.spec_from_file_location("verify_official_sources", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_official_evaluator_snapshots_are_unchanged():
    module = _verification_module()
    for benchmark in (
        "chartmimic",
        "vision2web",
        "swe_mm",
        "frontalk",
        "interactweb_bench",
    ):
        result = module.verify(benchmark)
        assert result["ok"], result


def test_active_evaluator_adapters_do_not_reference_sibling_checkouts():
    paths = [
        PROJECT_ROOT / "scripts" / "swe_mm" / "direct_case_eval.py",
        PROJECT_ROOT / "scripts" / "chartmimic" / "prepare_official_evaluator.py",
        PROJECT_ROOT / "evaluate" / "chartmimic" / "run_official_case.py",
        PROJECT_ROOT / "evaluate" / "vision2web" / "run_official.py",
        PROJECT_ROOT / "evaluate" / "frontalk" / "run_official.py",
        PROJECT_ROOT / "evaluate" / "interactweb_bench" / "run_official.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "/Benchmarks/" not in source
        assert "../Benchmarks" not in source


def _load_adapter(name: str):
    path = PROJECT_ROOT / "evaluate" / name / "run_official.py"
    spec = importlib.util.spec_from_file_location(f"official_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frontalk_native_adapter_preserves_release_shape(tmp_path):
    module = _load_adapter("frontalk")
    report = module.preflight()
    assert report["ok"], report
    assert report["dialogues"] == 100
    assert report["turns"] == 1000
    assert report["test_conditions"] == 3676

    command = module.build_command("infer-text", ["relative-run", "--num_workers", "2"], tmp_path)
    assert command[1].endswith("upstream/infer_multiturn_textual.py")
    assert command[2] == str((tmp_path / "relative-run").resolve())
    assert command[3:] == ["--num_workers", "2"]


def test_interactweb_native_adapter_defaults_to_full_public_suite():
    module = _load_adapter("interactweb_bench")
    report = module.preflight()
    assert report["ok"], report
    assert report["tasks"] == 404
    assert report["seed_tasks"] == 101
    assert report["personas"] == {persona: 101 for persona in ("P-CON", "P-INT", "P-MIN", "P-RAM")}

    full = module.build_command("run", ["--builder_model", "model"])
    mini = module.build_command("run-mini", ["--builder_model", "model"])
    assert str(module.FULL_DATA) in full
    assert str(module.MINI_DATA) not in full
    assert str(module.MINI_DATA) in mini
    assert str(module.DEFAULT_OUTPUT) in full


def test_native_adapters_only_wrap_upstream_entrypoints():
    frontalk = _load_adapter("frontalk")
    interactweb = _load_adapter("interactweb_bench")
    assert set(frontalk.COMMANDS.values()) == {
        "infer_multiturn_textual.py",
        "infer_multiturn_visual.py",
        "infer_acecoder_textual.py",
        "infer_acecoder_visual.py",
        "evaluate_all.py",
        "usability.py",
    }
    assert all(path.is_relative_to(interactweb.UPSTREAM) for path in interactweb.COMMANDS.values())


def test_swe_mm_clean_eval_matches_official_patch_fallbacks():
    source = (PROJECT_ROOT / "scripts" / "swe_mm" / "direct_case_eval.py").read_text(
        encoding="utf-8"
    )
    for fragment in (
        '("git", "apply", "--verbose")',
        '("git", "apply", "--verbose", "--3way")',
        '("git", "apply", "--verbose", "--reject")',
        '("patch", "--batch", "--forward", "--fuzz=5", "-p1", "-i")',
        '"git", "apply", "--check", "--reverse"',
    ):
        assert fragment in source


def test_swe_mm_clean_eval_is_a_separate_clusterx_transport():
    controller = (PROJECT_ROOT / "scripts" / "swe_mm" / "submit_clean_eval.py").read_text(
        encoding="utf-8"
    )
    wrapper = (PROJECT_ROOT / "scripts" / "swe_mm" / "run_clean_eval_case.sh").read_text(
        encoding="utf-8"
    )
    assert '"--image", record["image"]' in controller
    assert "fresh_clusterx_instance_image=true" in wrapper
    assert "run_clean_eval_case.sh" in controller
