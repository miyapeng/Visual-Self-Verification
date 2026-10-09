"""Run a case through the repository-vendored mini-swe-agent snapshot."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import shlex
import struct
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cases import AgentCase
from .prompts import SYSTEM_TEMPLATE, instance_template, observation_template


MULTIMODAL_OPEN = "<MSWEA_" "MULTIMODAL_CONTENT>"
MULTIMODAL_CLOSE = "</MSWEA_" "MULTIMODAL_CONTENT>"
MULTIMODAL_REGEX = (
    rf"(?s){MULTIMODAL_OPEN}<CONTENT_TYPE>(.+?)</CONTENT_TYPE>(.+?){MULTIMODAL_CLOSE}"
)


def image_tag(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return image_bytes_tag(path.name, mime, encoded)


def image_bytes_tag(name: str, mime: str, encoded: str) -> str:
    return (
        f"{MULTIMODAL_OPEN}<CONTENT_TYPE>image_url</CONTENT_TYPE>"
        f"data:{mime};base64,{encoded}{MULTIMODAL_CLOSE}"
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_png_transport(data: bytes) -> tuple[bytes, list[str]]:
    """Remove only corrupt ancillary PNG chunks, leaving encoded pixels untouched.

    Some frozen upstream assets contain a valid PNG image with a bad CRC in an
    optional color-profile chunk. Pillow (and therefore vLLM) rejects the file
    before the model sees the task. Critical chunks and pixel-bearing IDAT data
    are never repaired or discarded; corruption in either remains a hard error.
    """

    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ValueError("invalid PNG signature")
    output = bytearray(signature)
    removed: list[str] = []
    offset = len(signature)
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk header")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG chunk payload")
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            # PNG chunk names use an uppercase first byte for critical chunks.
            if not chunk_type or not chr(chunk_type[0]).islower():
                name = chunk_type.decode("ascii", errors="replace")
                raise ValueError(f"corrupt critical PNG chunk: {name}")
            removed.append(chunk_type.decode("ascii", errors="replace"))
        else:
            output.extend(data[offset:end])
        offset = end
        if chunk_type == b"IEND":
            saw_iend = True
            if offset != len(data):
                raise ValueError("unexpected bytes after PNG IEND")
            break
    if not saw_iend:
        raise ValueError("PNG has no IEND chunk")
    return bytes(output), removed


def _prepare_image_for_transport(path: Path) -> tuple[str | None, dict[str, Any]]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    data = path.read_bytes()
    record: dict[str, Any] = {
        "source_path": str(path),
        "source_sha256": _sha256(data),
        "source_bytes": len(data),
        "detected_mime": mime,
    }
    if not mime.startswith("image/"):
        record.update({
            "action": "skipped_non_image",
            "reason": "Only actual image assets may be sent through the image_url field.",
        })
        return None, record

    transported = data
    removed_chunks: list[str] = []
    if mime == "image/png":
        transported, removed_chunks = _normalize_png_transport(data)
    action = "normalized_invalid_ancillary_metadata" if removed_chunks else "attached_unchanged"
    record.update({
        "action": action,
        "transport_sha256": _sha256(transported),
        "transport_bytes": len(transported),
        "removed_png_chunks": removed_chunks,
    })
    encoded = base64.b64encode(transported).decode("ascii")
    return image_bytes_tag(path.name, mime, encoded), record


def _task_with_images(case: AgentCase, audit_path: Path | None = None) -> str:
    missing = [str(path) for path in case.image_paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing frozen task image(s): {missing}")
    parts = [case.prompt]
    records: list[dict[str, Any]] = []
    attached_index = 0
    for source_index, path in enumerate(case.image_paths, start=1):
        tag, record = _prepare_image_for_transport(path)
        record["source_index"] = source_index
        records.append(record)
        if tag is None:
            continue
        attached_index += 1
        record["attached_index"] = attached_index
        parts.extend((f"\n\nTask image {attached_index} ({path.name}):\n", tag))
    if audit_path is not None:
        audit = {
            "schema": 1,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "benchmark": case.benchmark,
            "case_id": case.case_id,
            "frozen_sources_modified": False,
            "source_count": len(case.image_paths),
            "attached_count": attached_index,
            "assets": records,
        }
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "".join(parts)


def _load_mini(mini_root: Path, tool_mode: str):
    source = mini_root.resolve() / "src"
    if not source.is_dir():
        raise RuntimeError(f"vendored mini-swe-agent source not found: {source}")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    try:
        import minisweagent
        from minisweagent.agents.default import DefaultAgent
        from minisweagent.config import get_config_from_spec
        from minisweagent.environments.local import LocalEnvironment
        if tool_mode == "native":
            from minisweagent.models.litellm_model import LitellmModel as Model
        else:
            from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel as Model
    except ImportError as exc:
        raise RuntimeError(
            "mini-swe-agent dependencies are missing. In the mmcode environment run: "
            "python -m pip install -r /data/miyapeng/mmcode/MultimodalCode/requirements/agents.txt"
        ) from exc
    loaded_from = Path(minisweagent.__file__).resolve()
    if not loaded_from.is_relative_to(source):
        raise RuntimeError(
            f"mini-swe-agent import drift: expected a module below {source}, got {loaded_from}"
        )
    return DefaultAgent, LocalEnvironment, Model, get_config_from_spec


def run_mini(
    case: AgentCase,
    workspace: Path,
    raw_trajectory: Path,
    *,
    mini_root: Path,
    model: str,
    base_url: str,
    api_key: str,
    tool_mode: str,
    profile: str,
    step_limit: int,
    wall_time: int,
    command_timeout: int,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    # Keep mini's small dotenv/config state with the run rather than writing to
    # $HOME, which is read-only in some ClusterX task containers.
    os.environ.setdefault("MSWEA_GLOBAL_CONFIG_DIR", str(raw_trajectory.parent / ".mini-config"))
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
    if profile == "swebench-official" and case.benchmark != "swe-mm":
        raise ValueError("The swebench-official mini profile is only valid for SWE-MM")
    if profile == "swebench-official" and tool_mode != "native":
        raise ValueError("The upstream SWE-bench profile requires native bash tool calls")

    DefaultAgent, LocalEnvironment, Model, get_config_from_spec = _load_mini(mini_root, tool_mode)
    model_name = model if model.startswith("hosted_vllm/") else f"hosted_vllm/{model}"
    if profile == "swebench-official":
        # Load the frozen upstream file instead of maintaining a second copy.
        # Only the model identity/endpoint and ClusterX transport are replaced.
        upstream = get_config_from_spec(
            mini_root / "src" / "minisweagent" / "config" / "benchmarks" / "swebench.yaml"
        )
        model_config = dict(upstream["model"])
        model_config["model_name"] = model_name
        model_config["model_kwargs"] = {
            **model_config.get("model_kwargs", {}),
            "api_base": base_url.rstrip("/"),
            "api_key": api_key,
        }
        model_config["cost_tracking"] = "ignore_errors"
        model_config["multimodal_regex"] = MULTIMODAL_REGEX
        llm = Model(**model_config)
        agent_config = dict(upstream["agent"])
        environment_config = dict(upstream["environment"])
    else:
        llm = Model(
            model_name=model_name,
            model_kwargs={
                "api_base": base_url.rstrip("/"),
                "api_key": api_key,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "drop_params": True,
            },
            cost_tracking="ignore_errors",
            multimodal_regex=MULTIMODAL_REGEX,
            observation_template=observation_template(),
        )
        agent_config = {
            "system_template": SYSTEM_TEMPLATE,
            "instance_template": instance_template(case),
            "step_limit": step_limit,
            "cost_limit": 0,
            "wall_time_limit_seconds": wall_time,
        }
        environment_config = {
            "timeout": command_timeout,
            "env": {
                "PAGER": "cat",
                "MANPAGER": "cat",
                "PIP_PROGRESS_BAR": "off",
                "TQDM_DISABLE": "1",
                "BASH_ENV": "/root/.bashrc",
            },
        }

    class CaseEnvironment(LocalEnvironment):
        """Use the SWE image's Bash initialization without nesting a container."""

        def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
            if case.benchmark != "swe-mm":
                return super().execute(action, cwd, timeout=timeout)
            wrapped = dict(action)
            wrapped["command"] = f"bash -c {shlex.quote(action.get('command', ''))}"
            return super().execute(wrapped, cwd, timeout=timeout)

    environment_env = {
        **environment_config.get("env", {}),
        # ClusterX launches the official instance image as the outer container.
        # Keep the runner executable reachable until BASH_ENV activates the
        # image's testbed environment, matching upstream Docker initialization.
        "PATH": os.pathsep.join(
            (str(Path(sys.executable).resolve().parent), os.environ.get("PATH", ""))
        ),
    }
    environment = CaseEnvironment(
        cwd=str(workspace),
        timeout=int(environment_config["timeout"]),
        env=environment_env,
    )
    agent = DefaultAgent(
        llm,
        environment,
        **agent_config,
        output_path=raw_trajectory,
    )
    return agent.run(_task_with_images(case, raw_trajectory.parent / "input_transport.json"))
