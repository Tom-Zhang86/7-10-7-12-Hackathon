"""OCR invocation policy (Master Spec section 4, 8).

The invocation ORDER below — Tier 1 activity classification, then Tier 2
context analysis, then at most one OCR call only if both remain AMBIGUOUS —
is a mandatory safety invariant (see ``application.owner_handoff.config``
and the Master Spec's Phase 1 Review Addendum), not a configurable value.
There is no environment variable that can bypass or reorder it; the only
configurable choice is which ``OCRProvider`` implementation is loaded
(``AI_DESK_V2_OCR_MODE`` — "noop" or "mock").
"""
from __future__ import annotations

from dataclasses import dataclass

from application.owner_handoff.adapters.ocr import OCRProvider
from application.owner_handoff.domain.activity import ActivityClassification
from application.owner_handoff.domain.work_context import redact_sensitive


@dataclass(frozen=True)
class OCRPolicyResult:
    classification: ActivityClassification
    ocr_invoked: bool
    ocr_text: str = ""


def resolve_with_ocr(
    *,
    tier1_result: ActivityClassification,
    tier2_result: ActivityClassification,
    ocr_provider: OCRProvider,
) -> OCRPolicyResult:
    """Apply the fixed Tier 1 -> Tier 2 -> OCR control flow.

    OCR is invoked at most once, and only when both tiers are AMBIGUOUS.
    A resolved Tier 1 or Tier 2 result short-circuits before OCR is ever
    called.
    """

    if tier1_result is not ActivityClassification.AMBIGUOUS:
        return OCRPolicyResult(classification=tier1_result, ocr_invoked=False)
    if tier2_result is not ActivityClassification.AMBIGUOUS:
        return OCRPolicyResult(classification=tier2_result, ocr_invoked=False)

    result = ocr_provider.capture_and_read()
    if not result.had_text:
        return OCRPolicyResult(
            classification=ActivityClassification.AMBIGUOUS, ocr_invoked=True
        )

    # Sanitize OCR output before any evidence is retained (Master Spec
    # section 4) — even a deterministic mock's text is redacted here so a
    # test can never accidentally assert on an unsanitized value.
    sanitized = redact_sensitive(result.text)
    return OCRPolicyResult(
        classification=ActivityClassification.AMBIGUOUS,
        ocr_invoked=True,
        ocr_text=sanitized,
    )
