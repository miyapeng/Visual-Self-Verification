#!/usr/bin/env python3
"""Build the local benchmark gallery from frozen, agent-visible data.

The generated site deliberately keeps evaluator-only artifacts out of sample
prompts.  It describes their schemas, but never reads Vision2Web workflow.json
or SWE-MM oracle patches into the browser payload.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT = Path(__file__).resolve().parents[2]
MMCODE = PROJECT.parent
OUT = PROJECT / "reports" / "benchmark_gallery"
ASSETS = OUT / "assets"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in read_text(path).splitlines() if line.strip()]


def python_string_constant(path: Path, name: str) -> str:
    """Read a top-level literal prompt without importing benchmark code."""
    tree = ast.parse(read_text(path), filename=str(path))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
    raise KeyError(f"String constant {name!r} not found in {path}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_image(src: Path, dest_stem: str, provenance: list[dict[str, Any]]) -> str:
    """Copy a browser-friendly thumbnail, retaining a source checksum."""
    ASSETS.mkdir(parents=True, exist_ok=True)
    dest = ASSETS / f"{dest_stem}.webp"
    try:
        from PIL import Image

        with Image.open(src) as image:
            image.thumbnail((1800, 1500))
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.save(dest, "WEBP", quality=84, method=6)
    except Exception:
        dest = ASSETS / f"{dest_stem}{src.suffix.lower()}"
        shutil.copy2(src, dest)
    provenance.append(
        {
            "gallery_asset": str(dest.relative_to(OUT)),
            "source": str(src),
            "source_sha256": sha256(src),
        }
    )
    return str(dest.relative_to(OUT))


def media_for_dir(
    directory: Path,
    prefix: str,
    provenance: list[dict[str, Any]],
    role: str = "Agent input",
    limit: int | None = None,
) -> list[dict[str, str]]:
    paths = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    if limit is not None:
        paths = paths[:limit]
    return [
        {
            "src": copy_image(path, f"{prefix}-{index:02d}-{path.stem}", provenance),
            "label": path.stem.replace("_", " ").replace("-", " ").title(),
            "role": role,
        }
        for index, path in enumerate(paths, start=1)
    ]


def file_inventory(root: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in root.rglob("*"):
        if path.is_file():
            suffix = path.suffix.lower() or "[no extension]"
            counts[suffix] += 1
    return dict(counts.most_common())


def build_vision2web(provenance: list[dict[str, Any]]) -> dict[str, Any]:
    root = PROJECT / "data" / "vision2web"
    prompts = root / "agent_visible" / "prompts"
    samples: list[dict[str, Any]] = []

    specifications = [
        (
            "webpage",
            "classic-clashes",
            "Level 1 · Webpage",
            "Static responsive reconstruction",
            "This instance has no case-specific text brief. The generic Level 1 prompt and the three viewport prototypes are the specification.",
            "webpage.md",
        ),
        (
            "frontend",
            "forum_vectorworks",
            "Level 2 · Frontend",
            "Interactive multi-page frontend",
            read_text(root / "extracted" / "frontend" / "forum_vectorworks" / "prompt.txt"),
            "frontend.md",
        ),
        (
            "website",
            "permanent",
            "Level 3 · Website",
            "Full-stack product from a PRD",
            read_text(root / "extracted" / "website" / "permanent" / "prd.md"),
            "website.md",
        ),
    ]

    for split, case_id, level, title, brief, prompt_name in specifications:
        case = root / "extracted" / split / case_id
        prototypes = case / "prototypes"
        resources = case / "resources"
        resource_files = [path for path in resources.rglob("*") if path.is_file()]
        samples.append(
            {
                "id": case_id,
                "type": level,
                "title": title,
                "summary": {
                    "webpage": "Recreate one page at desktop, tablet, and mobile resolutions.",
                    "frontend": "Implement forum navigation, threads, activity, search, and a resource gallery using mock data.",
                    "website": "Implement a content platform with live APIs, database-backed seed data, navigation, search, categories, articles, and video.",
                }[split],
                "inputLabel": "Case-specific input" if split != "webpage" else "How the case is specified",
                "inputText": brief,
                "frameworkPrompt": read_text(prompts / prompt_name),
                "media": media_for_dir(prototypes, f"v2w-{split}-{case_id}", provenance),
                "artifacts": [
                    {"name": "generic task prompt", "value": f"agent_visible/prompts/{prompt_name}"},
                    {"name": "case brief", "value": "none" if split == "webpage" else ("prompt.txt" if split == "frontend" else "prd.md")},
                    {"name": "prototype images", "value": str(len(list(prototypes.glob("*"))))},
                    {"name": "resource files", "value": str(len(resource_files))},
                    {"name": "required endpoint", "value": "http://localhost:3000"},
                ],
                "modelSees": [
                    "Official level-specific instruction",
                    *( [] if split == "webpage" else ["prompt.txt" if split == "frontend" else "prd.md"] ),
                    "Prototype screenshots",
                    "Bundled resources (images, fonts, media)",
                    "Writable /workspace repository",
                ],
                "modelReturns": [
                    "Complete source tree under /workspace",
                    "/workspace/start.sh",
                    "README and design documentation",
                    "A runnable site on port 3000",
                ],
                "evaluatorSees": [
                    "workflow.json after generation (never model input)",
                    "Browser-executed functionality",
                    "Rendered screenshots and task-specific visual checks",
                ],
                "schema": f"{split}/{case_id}/\n├── {('prompt.txt' if split == 'frontend' else 'prd.md') + chr(10) + '├── ' if split != 'webpage' else ''}prototypes/*.jpg\n├── resources/**/*\n└── workflow.json   # evaluator-only",
                "sourcePath": str(case),
            }
        )

    return {
        "id": "vision2web",
        "name": "Vision2Web",
        "eyebrow": "Prototype → deployed application",
        "accent": "#7357ff",
        "description": "Three difficulty levels move from static visual reconstruction to interactive frontends and full-stack websites. Every task is an isolated application project.",
        "stats": [
            {"value": "193", "label": "tasks"},
            {"value": "3", "label": "levels"},
            {"value": "100 / 66 / 27", "label": "L1 / L2 / L3"},
            {"value": "image + text + repo", "label": "input mix"},
        ],
        "categories": [
            {"name": "Level 1 · Webpage", "count": 100, "input": "3 viewport screenshots + resources", "task": "Pixel-faithful responsive static page"},
            {"name": "Level 2 · Frontend", "count": 66, "input": "prompt.txt + multiple prototypes + resources", "task": "Interactive, multi-route frontend; mock data allowed"},
            {"name": "Level 3 · Website", "count": 27, "input": "prd.md + multiple prototypes + resources", "task": "Full stack, APIs, database, migrations, seed data"},
        ],
        "flow": ["task folder", "implement in /workspace", "start.sh deploys :3000", "workflow runs after submission", "function + visual scores"],
        "samples": samples,
        "dataBoundary": "workflow.json is evaluator-private. The gallery lists its role but does not read or reproduce its steps.",
        "provenance": "Frozen local revision 8f03299d92b9bd852e93852d0c21e8a4848ab661.",
    }


def build_frontalk(provenance: list[dict[str, Any]]) -> dict[str, Any]:
    data_path = PROJECT / "data" / "frontalk" / "data.jsonl"
    rows = jsonl(data_path)
    selected_ids = [
        "e4aa9d67d4a5e40cdffac7a3429994a8.html",
        "30a80a0d27ba6662d0faf41c8af3e8bd.html",
        "7eb3a73b076c81ff43b69eaafe0459c0.html",
    ]
    by_id = {row["id"]: row for row in rows}
    framework_prompt = python_string_constant(MMCODE / "frontalk" / "infer_multiturn_textual.py", "PROMPT")
    teaser = copy_image(MMCODE / "frontalk" / "static" / "teaser.jpg", "frontalk-paper-overview", provenance)
    samples = []
    for index, instance_id in enumerate(selected_ids):
        row = by_id[instance_id]
        turns = []
        for turn_index, case in enumerate(row["cases"]):
            turns.append(
                {
                    "index": turn_index + 1,
                    "type": case["type"],
                    "instruction": case["instructions"],
                    "testConditions": case["test_conditions"],
                }
            )
        samples.append(
            {
                "id": instance_id,
                "type": "10-turn dialogue",
                "title": row["summary"]["purpose"].split(".")[0][:110],
                "summary": row["summary"]["purpose"],
                "inputLabel": "Turn sequence",
                "inputText": row["cases"][0]["instructions"],
                "frameworkPrompt": framework_prompt,
                "turns": turns,
                "media": ([{"src": teaser, "label": "Official benchmark overview", "role": "Paper figure — not per-case model input"}] if index == 0 else []),
                "artifacts": [
                    {"name": "dialogue id", "value": instance_id},
                    {"name": "turns", "value": "10"},
                    {"name": "function turns", "value": str(sum(c["type"] == "function" for c in row["cases"]))},
                    {"name": "design turns", "value": str(sum(c["type"] == "design" for c in row["cases"]))},
                    {"name": "test conditions", "value": str(sum(len(c["test_conditions"]) for c in row["cases"]))},
                ],
                "modelSees": [
                    "A common coding system prompt",
                    "One user instruction at a time",
                    "Previous conversation turns and accumulated site code",
                    "Textual feedback, or a dynamically generated visual instruction in the visual protocol",
                ],
                "modelReturns": [
                    "One or more complete HTML/CSS/JS file blocks",
                    "A new cumulative website version t.0 … t.9",
                    "New changes while preserving earlier requirements",
                ],
                "evaluatorSees": [
                    "Public per-turn test_conditions (not given to coding model)",
                    "Final and intermediate website versions",
                    "Browser-agent pass/fail and usability evidence",
                    "Forgetting across turns",
                ],
                "schema": f"data.jsonl row\n├── id: {instance_id}\n├── summary.purpose\n└── cases[10]\n    ├── instructions\n    ├── type: function | design\n    └── test_conditions[]\n        ├── condition\n        ├── pass\n        └── fail",
                "sourcePath": str(data_path),
            }
        )

    return {
        "id": "frontalk",
        "name": "FronTalk",
        "eyebrow": "Conversation → cumulative frontend",
        "accent": "#e44b78",
        "description": "A multi-turn frontend benchmark in which requirements arrive over ten turns. The central failure mode is implementing the new request while forgetting earlier functionality or design constraints.",
        "stats": [
            {"value": "100", "label": "dialogues"},
            {"value": "1,000", "label": "turns"},
            {"value": "3,676", "label": "test conditions"},
            {"value": "500 / 500", "label": "function / design turns"},
        ],
        "categories": [
            {"name": "Function turn", "count": 500, "input": "Feature addition/refinement", "task": "Add behavior without breaking earlier turns"},
            {"name": "Design turn", "count": 500, "input": "Visual refinement request", "task": "Restyle an existing component while preserving behavior"},
            {"name": "Textual protocol", "count": 1000, "input": "Dynamically refined textual instruction", "task": "Conversational code generation"},
            {"name": "Visual protocol", "count": 1000, "input": "Dynamically drawn instruction image", "task": "Multimodal feedback; image is generated at runtime"},
        ],
        "flow": ["goal + turn intent", "dynamic user feedback", "generate file blocks", "carry site to next turn", "agent tests + forgetting"],
        "samples": samples,
        "dataBoundary": "test_conditions are public benchmark data but are evaluator input, not coding-model input. Visual turn images are generated at inference time and are not stored in data.jsonl.",
        "provenance": "Local official data.jsonl: 100 rows, exactly 10 turns per row.",
    }


def build_webcompass(provenance: list[dict[str, Any]]) -> dict[str, Any]:
    research = PROJECT / "data" / "research"
    prompt_source = MMCODE / "Benchmarks" / "WebCompass" / "generation" / "prompts.py"
    image_prompt = python_string_constant(prompt_source, "IMAGE_TO_WEB_PROMPT")
    video_prompt = python_string_constant(prompt_source, "VIDEO_TO_WEB_PROMPT")
    release_samples = [
        (
            "image-1",
            "Image generation release example",
            "A fortune-cookie interface with an initial state and a revealed result.",
            research / "webcompass-image-1" / "reference",
            "Released result example; the files here are not a frozen raw HF split record.",
        ),
        (
            "image-107",
            "Multi-page image release example",
            "A home/index/calendar website represented by three rendered pages.",
            research / "webcompass-image-107" / "reference",
            "Released result example; useful for understanding multi-page visual scope.",
        ),
        (
            "video-22",
            "Video-to-web temporal example",
            "A shopping filter demo: initial state, price filter, high-price state, and reset.",
            research / "webcompass-video-22" / "reference",
            "Representative frames copied from an official released video-generation output.",
        ),
    ]
    samples = []
    for case_id, title, summary, reference_dir, caveat in release_samples:
        modality = case_id.split("-")[0]
        samples.append(
            {
                "id": case_id,
                "type": f"{modality.title()} generation",
                "title": title,
                "summary": summary,
                "inputLabel": "Local provenance note",
                "inputText": read_text(reference_dir.parent / "README.upstream.md"),
                "frameworkPrompt": video_prompt if modality == "video" else image_prompt,
                "media": media_for_dir(reference_dir, f"webcompass-{case_id}", provenance, role="Official release artifact"),
                "artifacts": [
                    {"name": "modality", "value": modality},
                    {"name": "states/pages shown", "value": str(len(list(reference_dir.iterdir())))},
                    {"name": "local status", "value": "release example, not raw split row"},
                    {"name": "source code", "value": "bundled beside reference/"},
                ],
                "modelSees": [
                    "Text: design document",
                    "Image: sorted reference screenshots + generated goal document",
                    "Video: sampled demonstration frames",
                    "Editing/repair: existing source; screenshots depend on text/image mode",
                ],
                "modelReturns": [
                    "Generation: complete repository as file blocks",
                    "Editing/repair: XML search/replace blocks",
                    "Runnable HTML/CSS/JS project",
                ],
                "evaluatorSees": [
                    "Generation checklist with Runnability / Spec / Design dimensions",
                    "Browser agent execution and visual judge",
                    "Editing/repair rubric judge after code generation",
                ],
                "schema": "generation row\n├── instance_id\n├── instruction / media path\n├── problem_statement[]  # evaluation checklist\n│   ├── task + category\n│   ├── operation_sequence\n│   ├── expected_result + criteria\n│   └── max_score\n└── meta.class + difficulty",
                "sourcePath": str(reference_dir.parent),
                "caveat": caveat,
            }
        )

    return {
        "id": "webcompass",
        "name": "WebCompass",
        "eyebrow": "Text / image / video → generate, edit, repair",
        "accent": "#008c83",
        "description": "A broad web-coding suite with three generation modalities plus single- and multi-page editing and repair. Unlike Vision2Web, generation is single-response file emission rather than a long-horizon coding-agent workspace.",
        "stats": [
            {"value": "933", "label": "total records"},
            {"value": "333", "label": "generation"},
            {"value": "300 / 300", "label": "editing / repair"},
            {"value": "5", "label": "task families"},
        ],
        "categories": [
            {"name": "Text generation", "count": 123, "input": "Text design document", "task": "Emit a runnable site"},
            {"name": "Image generation", "count": 116, "input": "Reference screenshot set", "task": "Reproduce visual states and implied interactions"},
            {"name": "Video generation", "count": 94, "input": "Frames sampled from a demo video", "task": "Reproduce appearance and temporal behavior"},
            {"name": "Editing · SP / MP", "count": 300, "input": "Existing code + requested features (+ current screenshots in image mode)", "task": "150 single-page + 150 multi-page"},
            {"name": "Repair · SP / MP", "count": 300, "input": "Broken code + defect types (+ current/target screenshots in image mode)", "task": "150 single-page + 150 multi-page"},
        ],
        "flow": ["modality-specific input", "single response", "parse files or patch", "render site", "agent/judge scoring"],
        "samples": samples,
        "dataBoundary": "For generation, problem_statement is an evaluation checklist and is intentionally not placed in the model generation prompt. The local gallery has official release examples, not a locally frozen copy of every HF row.",
        "provenance": "Official local repository commit d5fe352e065f6dcf87aca605babf01499b2b12a3; split counts from its README.",
    }


def build_swemm(provenance: list[dict[str, Any]]) -> dict[str, Any]:
    root = PROJECT / "data" / "swe_mm" / "dev"
    rows = jsonl(root / "agent_visible" / "instances.jsonl")
    by_id = {row["instance_id"]: row for row in rows}
    selected_ids = [
        "Automattic__wp-calypso-22026",
        "chartjs__Chart.js-10157",
        "diegomura__react-pdf-1178",
        "markedjs__marked-1535",
        "processing__p5.js-3680",
    ]
    samples = []
    for row_id in selected_ids:
        row = by_id[row_id]
        media = []
        for index, image in enumerate(row["images"], start=1):
            source = root / image["local_path"]
            if source.exists():
                media.append(
                    {
                        "src": copy_image(source, f"swemm-{row_id}-{index:02d}", provenance),
                        "label": f"Issue image {index}",
                        "role": "Agent-visible issue attachment",
                    }
                )
        title, _, remainder = row["problem_statement"].partition("\n")
        samples.append(
            {
                "id": row_id,
                "type": row["repo"],
                "title": title,
                "summary": remainder.strip()[:360] or title,
                "inputLabel": "GitHub issue / problem_statement",
                "inputText": row["problem_statement"],
                "media": media,
                "artifacts": [
                    {"name": "repository", "value": row["repo"]},
                    {"name": "base commit", "value": row["base_commit"][:12]},
                    {"name": "issue images", "value": str(len(row["images"]))},
                    {"name": "environment", "value": "repository-specific container"},
                ],
                "modelSees": [
                    "Repository checked out at base_commit",
                    "GitHub issue text as problem_statement",
                    "Issue images/attachments",
                    "Shell, search, editor, and test feedback through the coding scaffold",
                ],
                "modelReturns": [
                    "A repository patch",
                    "Agent trajectory (commands, reads, edits, tests)",
                ],
                "evaluatorSees": [
                    "Gold patch and test patch (never model input)",
                    "FAIL_TO_PASS and PASS_TO_PASS tests",
                    "Repository-specific Docker image and official log parser",
                ],
                "schema": f"agent_visible row\n├── instance_id: {row_id}\n├── repo: {row['repo']}\n├── base_commit\n├── problem_statement\n├── images[]\n│   ├── local_path\n│   ├── source_url\n│   └── sha256\n└── version",
                "sourcePath": str(root / "agent_visible" / "instances.jsonl"),
            }
        )

    repo_counts = Counter(row["repo"] for row in rows)
    return {
        "id": "swemm",
        "name": "SWE-bench Multimodal",
        "shortName": "SWE-MM",
        "eyebrow": "Issue + images + repository → patch",
        "accent": "#e77722",
        "description": "Repository-level software repair grounded in real GitHub issues and visual evidence. The visual modality is usually an issue attachment; subsequent observations are mainly code, shell, and test output unless the agent launches a visual application itself.",
        "stats": [
            {"value": str(len(rows)), "label": "frozen dev tasks"},
            {"value": str(len(repo_counts)), "label": "repositories"},
            {"value": str(sum(len(row["images"]) for row in rows)), "label": "agent-linked images"},
            {"value": "patch", "label": "submission unit"},
        ],
        "categories": [
            {"name": repo, "count": count, "input": "GitHub issue + repository + optional images", "task": {
                "Automattic/wp-calypso": "Large web application UI and flows",
                "chartjs/Chart.js": "Chart rendering and interactions",
                "processing/p5.js": "Canvas/WebGL visual behavior",
                "markedjs/marked": "Markdown parsing with rendered-output evidence",
                "diegomura/react-pdf": "PDF layout and rendering",
            }[repo]}
            for repo, count in repo_counts.most_common()
        ],
        "flow": ["checkout base commit", "agent reads issue + images", "inspect / edit / test loop", "export patch", "clean container official tests"],
        "samples": samples,
        "dataBoundary": "The gallery reads only agent_visible/instances.jsonl. Gold patches, test patches, and oracle test lists remain in evaluator_private and are not embedded.",
        "provenance": "Frozen dev split: resolved Hugging Face revision 3548373bb5f604b55600884af34ac33e5a90ef66.",
    }


def build() -> dict[str, Any]:
    provenance: list[dict[str, Any]] = []
    benchmarks = [
        build_vision2web(provenance),
        build_frontalk(provenance),
        build_webcompass(provenance),
        build_swemm(provenance),
    ]
    payload = {
        "title": "Multimodal Coding Benchmark Atlas",
        "subtitle": "真实样例、模型输入边界与评测组织方式",
        "benchmarks": benchmarks,
        "comparison": [
            {
                "benchmark": "Vision2Web",
                "unit": "One application project",
                "input": "Prototypes; L2 prompt; L3 PRD; resources",
                "interaction": "Workspace coding agent; deployable site",
                "output": "Repository + start.sh",
                "evaluation": "Browser workflow + visual/function scoring",
            },
            {
                "benchmark": "FronTalk",
                "unit": "10-turn dialogue",
                "input": "Sequential text or generated visual feedback",
                "interaction": "Cumulative conversational generation",
                "output": "Website version after every turn",
                "evaluation": "Agent test cases + forgetting + usability",
            },
            {
                "benchmark": "WebCompass",
                "unit": "Generation/edit/repair record",
                "input": "Text, images, video frames, or existing code",
                "interaction": "Mostly single model response",
                "output": "Files or search/replace patch",
                "evaluation": "Runnability/spec/design or rubric judge",
            },
            {
                "benchmark": "SWE-MM",
                "unit": "Repository issue",
                "input": "Issue text + images + base repository",
                "interaction": "Coding-agent tool loop",
                "output": "Git patch",
                "evaluation": "Official repo tests in clean container",
            },
        ],
        "boundaryLegend": [
            {"kind": "agent", "label": "Agent-visible", "description": "Can condition generation or repair."},
            {"kind": "environment", "label": "Environment", "description": "Repository, resources, browser, or runtime made available during work."},
            {"kind": "evaluator", "label": "Evaluator-only", "description": "Used only after submission; never exposed to the coding model."},
        ],
    }

    OUT.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    (OUT / "gallery-data.json").write_text(encoded + "\n", encoding="utf-8")
    (OUT / "gallery-data.js").write_text("window.GALLERY_DATA = " + encoded + ";\n", encoding="utf-8")
    (OUT / "provenance.json").write_text(
        json.dumps({"assets": provenance}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


if __name__ == "__main__":
    data = build()
    print(f"Built {OUT / 'index.html'} data for {len(data['benchmarks'])} benchmarks")
