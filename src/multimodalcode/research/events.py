from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ..io import append_jsonl


class EventLog:
    """Append-only JSONL event stream with stable sequence numbers."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._next_sequence = self._discover_next_sequence()

    def _discover_next_sequence(self) -> int:
        last = -1
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    last = max(last, int(json.loads(line).get("sequence", -1)))
        return last + 1

    def append(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        case_id: str,
        code_version: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            row = {
                "event_id": str(uuid.uuid4()),
                "sequence": self._next_sequence,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": event_type,
                "case_id": case_id,
                "code_version": code_version,
                "payload": payload,
            }
            append_jsonl(self.path, row)
            self._next_sequence += 1
            return row

    def read(self, *, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return rows if case_id is None else [row for row in rows if row["case_id"] == case_id]


class EvidenceStore:
    """Immutable artifact store; context policies select references, never delete data."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.objects = self.root / "objects"
        self.manifest = self.root / "manifest.jsonl"
        self.objects.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def add_bytes(
        self,
        data: bytes,
        *,
        kind: str,
        case_id: str,
        code_version: str,
        checklist_id: Optional[str] = None,
        media_type: str = "application/octet-stream",
        source: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        digest = hashlib.sha256(data).hexdigest()
        suffix = _suffix_for_media_type(media_type)
        target = self.objects / f"{digest}{suffix}"
        if not target.exists():
            temporary = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        row = {
            "evidence_id": digest,
            "kind": kind,
            "case_id": case_id,
            "code_version": code_version,
            "checklist_id": checklist_id,
            "media_type": media_type,
            "path": str(target),
            "relative_path": target.relative_to(self.root.parent).as_posix(),
            "bytes": len(data),
            "source": source,
            "metadata": metadata or {},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        with self._lock:
            append_jsonl(self.manifest, row)
        return row

    def add_text(self, text: str, **kwargs: Any) -> Dict[str, Any]:
        return self.add_bytes(text.encode("utf-8"), media_type="text/plain", **kwargs)

    def add_file(self, path: str | Path, **kwargs: Any) -> Dict[str, Any]:
        source_path = Path(path).resolve()
        media_type = kwargs.pop("media_type", _media_type_for_suffix(source_path.suffix))
        return self.add_bytes(
            source_path.read_bytes(),
            media_type=media_type,
            source=str(source_path),
            **kwargs,
        )

    def records(self, *, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.manifest.exists():
            return []
        rows = [
            json.loads(line)
            for line in self.manifest.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return rows if case_id is None else [row for row in rows if row["case_id"] == case_id]


def _suffix_for_media_type(media_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "video/webm": ".webm",
        "application/json": ".json",
        "text/html": ".html",
        "text/plain": ".txt",
    }.get(media_type, ".bin")


def _media_type_for_suffix(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webm": "video/webm",
        ".json": "application/json",
        ".html": "text/html",
        ".txt": "text/plain",
    }.get(suffix.lower(), "application/octet-stream")
