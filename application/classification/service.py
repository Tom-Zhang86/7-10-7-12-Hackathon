from dataclasses import replace
import logging
from typing import Any

from application.activity.models import ActivitySegment
from application.classification.models import ClassificationDecision
from application.classification.rules import RuleClassifier


logger = logging.getLogger(__name__)


class ActivityClassificationService:
    VERSION = "mvp-classifier-v1"

    def __init__(
        self,
        store: Any,
        rules: RuleClassifier | None = None,
        remote_client: Any | None = None,
        allow_remote: bool = False,
    ) -> None:
        self.store = store
        self.rules = rules or RuleClassifier()
        self.remote_client = remote_client
        self.allow_remote = allow_remote

    def classify(self, segment: ActivitySegment) -> ActivitySegment:
        cached = self.store.get_segment(segment.segment_hash)
        if cached and (
            cached.user_corrected
            or cached.classifier_version == self.VERSION
        ):
            return replace(
                segment,
                category=cached.category,
                activity_type=cached.activity_type,
                confidence=cached.confidence,
                evidence={**segment.evidence, **cached.evidence},
                classifier_version=cached.classifier_version,
                user_corrected=cached.user_corrected,
            )

        decision = self.rules.classify(segment)
        if decision is None and self.allow_remote and self.remote_client:
            try:
                decision = self.remote_client.classify(
                    self._remote_evidence(segment)
                )
            except Exception:
                logger.exception("Remote activity classification failed.")
        if decision is None:
            decision = ClassificationDecision(
                "unknown",
                segment.activity_type,
                0.0,
                "现有元数据不足，未进行强制推断。",
                "fallback",
            )

        evidence = dict(segment.evidence)
        evidence["classification"] = {
            "reason": decision.reason,
            "method": decision.method,
        }
        return replace(
            segment,
            category=decision.category,
            activity_type=decision.activity_type,
            confidence=decision.confidence,
            evidence=evidence,
            classifier_version=self.VERSION,
        )

    @staticmethod
    def _remote_evidence(segment: ActivitySegment) -> dict[str, Any]:
        allowed = {
            "app",
            "description",
            "headings",
            "hostname",
            "language",
            "media",
            "page_title",
            "project",
            "title",
            "url",
            "window_title",
        }
        result = {
            key: value
            for key, value in segment.evidence.items()
            if key in allowed
        }
        result.update(
            {
                "presence_state": segment.presence_state,
                "activity_type": segment.activity_type,
                "duration_seconds": int(segment.duration_seconds),
            }
        )
        return result
