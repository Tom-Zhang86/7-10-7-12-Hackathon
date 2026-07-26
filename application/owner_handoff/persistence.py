"""Sanitized atomic persistence of task artifacts under the session
directory (Master Spec section 17-18).

Every write here is atomic (write to a same-directory temp file, then
``os.replace``) so a crash mid-write can never leave a half-written file
that a later read would mistake for a complete, valid artifact. Every
value is passed through ``redact_sensitive`` one more time before
serializing -- defense in depth on top of whatever sanitization already
happened upstream (e.g. Codex JSONL event sanitization) -- so no secret,
raw environment value, or authorization token is ever written to disk.
Artifacts are always written under ``<session_root>/<task_id>.artifacts/``,
never inside the original source workspace, and are never automatically
deleted by this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping

from application.owner_handoff.domain.work_context import redact_sensitive
from application.owner_handoff.workspace.path_policy import validate_session_id


def _sanitize_json_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _sanitize_json_value(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive(value)
    return value


def _atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sanitized = _sanitize_json_value(dict(payload))
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(sanitized, indent=2, sort_keys=True))
    os.replace(tmp_path, path)  # atomic on POSIX and Windows alike


class TaskArtifactStore:
    """Writes/reads sanitized task artifacts under one directory per task,
    always inside ``session_root`` -- never inside the original source
    workspace."""

    def __init__(self, *, session_root: Path) -> None:
        self._session_root = Path(session_root)

    def task_dir(self, task_id: str) -> Path:
        validate_session_id(task_id)  # same safe-single-path-component check
        return self._session_root / f"{task_id}.artifacts"

    def save_manifest(self, task_id: str, manifest) -> None:
        _atomic_write_json(self.task_dir(task_id) / "manifest.json", manifest.as_dict())

    def save_selected_task(self, task_id: str, *, selected_task: str, skill: str) -> None:
        _atomic_write_json(
            self.task_dir(task_id) / "selected_task.json",
            {"selected_task": selected_task, "skill": skill},
        )

    def save_execution_result(self, task_id: str, *, label: str, result) -> None:
        _atomic_write_json(
            self.task_dir(task_id) / f"execution_result_{label}.json",
            {"status": result.status.value, "summary": result.summary, "detail": dict(result.detail)},
        )

    def save_packages_installed(self, task_id: str, package_specs: tuple[str, ...]) -> None:
        _atomic_write_json(
            self.task_dir(task_id) / "packages_installed.json",
            {"packages_installed": list(package_specs)},
        )

    def save_final_report(self, task_id: str, report_or_failure) -> None:
        _atomic_write_json(self.task_dir(task_id) / "final_report.json", report_or_failure.as_dict())

    def load_final_report(self, task_id: str) -> dict | None:
        path = self.task_dir(task_id) / "final_report.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def load_packages_installed(self, task_id: str) -> tuple[str, ...]:
        path = self.task_dir(task_id) / "packages_installed.json"
        if not path.exists():
            return ()
        return tuple(json.loads(path.read_text()).get("packages_installed", ()))
