from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from application.handoff.models import HandoffRecord


def _items(values: Any) -> list[Any]:
    return values if isinstance(values, list) else []


def render_markdown(record: HandoffRecord) -> str:
    artifact = record.artifact or {}
    lines = [
        "# Research Handoff",
        "",
        f"**Task:** {record.capsule.goal}",
        f"**Handoff ID:** `{record.capsule.handoff_id}`",
        f"**Status:** {artifact.get('status') or record.status.value}",
        "",
        "## Executive summary",
        "",
        str(artifact.get("executive_summary") or record.error or "No summary available."),
        "",
        "## Findings",
        "",
    ]
    findings = _items(artifact.get("findings"))
    if findings:
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            ids = ", ".join(str(item) for item in _items(finding.get("source_ids")))
            confidence = str(finding.get("confidence") or "unknown")
            suffix = f" Sources: {ids}." if ids else ""
            lines.append(
                f"- {finding.get('claim', '')} (confidence: {confidence}).{suffix}"
            )
    else:
        lines.append("- No evidence-backed findings were returned.")

    lines.extend(["", "## Sources", ""])
    sources = _items(artifact.get("sources"))
    if sources:
        for source in sources:
            if not isinstance(source, dict):
                continue
            title = str(source.get("title") or source.get("id") or "Source")
            url = str(source.get("url") or "")
            year = source.get("publication_year")
            publisher = str(source.get("publisher") or "")
            detail = ", ".join(str(item) for item in (publisher, year) if item)
            link = f"[{title}]({url})" if url else title
            lines.append(f"- {link}" + (f" — {detail}" if detail else ""))
    else:
        lines.append("- No sources were returned.")

    for heading, key in (
        ("Open questions", "open_questions"),
        ("Recommended next actions", "recommended_next_actions"),
        ("Limitations", "limitations"),
    ):
        lines.extend(["", f"## {heading}", ""])
        values = _items(artifact.get(key))
        lines.extend(f"- {item}" for item in values)
        if not values:
            lines.append("- None reported.")

    lines.extend(
        [
            "",
            "## Resume context",
            "",
            str(artifact.get("resume_context") or "Review the sources before continuing."),
            "",
        ]
    )
    return "\n".join(lines)


def save_report(
    record: HandoffRecord,
    output_root: str | Path = "data/handoffs",
) -> Path:
    directory = Path(output_root) / record.capsule.handoff_id
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "report.md"
    report_path.write_text(render_markdown(record), encoding="utf-8")
    (directory / "artifact.json").write_text(
        json.dumps(record.artifact or {}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report_path
