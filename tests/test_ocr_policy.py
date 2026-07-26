import unittest
from pathlib import Path
import tempfile

from application.owner_handoff.adapters.ocr import (
    MockOCRProvider,
    NoOpOCRProvider,
    OCRResult,
)
from application.owner_handoff.classification.ocr_policy import resolve_with_ocr
from application.owner_handoff.domain.activity import ActivityClassification


class _CountingOCRProvider:
    def __init__(self, result: OCRResult) -> None:
        self.result = result
        self.call_count = 0

    def capture_and_read(self) -> OCRResult:
        self.call_count += 1
        return self.result


class OCRPolicyTier1ResolvedTest(unittest.TestCase):
    def test_working_tier1_result_never_calls_ocr(self) -> None:
        provider = _CountingOCRProvider(OCRResult(text="ignored", had_text=True))
        outcome = resolve_with_ocr(
            tier1_result=ActivityClassification.WORKING,
            tier2_result=ActivityClassification.AMBIGUOUS,
            ocr_provider=provider,
        )
        self.assertEqual(provider.call_count, 0)
        self.assertFalse(outcome.ocr_invoked)
        self.assertEqual(outcome.classification, ActivityClassification.WORKING)

    def test_not_working_tier1_result_never_calls_ocr(self) -> None:
        provider = _CountingOCRProvider(OCRResult(text="ignored", had_text=True))
        outcome = resolve_with_ocr(
            tier1_result=ActivityClassification.NOT_WORKING,
            tier2_result=ActivityClassification.AMBIGUOUS,
            ocr_provider=provider,
        )
        self.assertEqual(provider.call_count, 0)
        self.assertFalse(outcome.ocr_invoked)


class OCRPolicyTier2ResolvedTest(unittest.TestCase):
    def test_tier2_resolution_never_calls_ocr(self) -> None:
        provider = _CountingOCRProvider(OCRResult(text="ignored", had_text=True))
        outcome = resolve_with_ocr(
            tier1_result=ActivityClassification.AMBIGUOUS,
            tier2_result=ActivityClassification.WORKING,
            ocr_provider=provider,
        )
        self.assertEqual(provider.call_count, 0)
        self.assertFalse(outcome.ocr_invoked)
        self.assertEqual(outcome.classification, ActivityClassification.WORKING)


class OCRPolicyDoublyAmbiguousTest(unittest.TestCase):
    def test_ocr_is_called_exactly_once_when_doubly_ambiguous(self) -> None:
        provider = _CountingOCRProvider(OCRResult(text="some text", had_text=True))
        outcome = resolve_with_ocr(
            tier1_result=ActivityClassification.AMBIGUOUS,
            tier2_result=ActivityClassification.AMBIGUOUS,
            ocr_provider=provider,
        )
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(outcome.ocr_invoked)
        self.assertEqual(outcome.classification, ActivityClassification.AMBIGUOUS)


class NoOpOCRProviderTest(unittest.TestCase):
    def test_noop_remains_ambiguous_with_no_evidence(self) -> None:
        outcome = resolve_with_ocr(
            tier1_result=ActivityClassification.AMBIGUOUS,
            tier2_result=ActivityClassification.AMBIGUOUS,
            ocr_provider=NoOpOCRProvider(),
        )
        self.assertTrue(outcome.ocr_invoked)
        self.assertEqual(outcome.classification, ActivityClassification.AMBIGUOUS)
        self.assertEqual(outcome.ocr_text, "")


class MockOCRProviderTest(unittest.TestCase):
    def test_mock_output_is_sanitized(self) -> None:
        provider = MockOCRProvider(
            text="visible text with token=SUPERSECRET123 and https://example.com/x"
        )
        outcome = resolve_with_ocr(
            tier1_result=ActivityClassification.AMBIGUOUS,
            tier2_result=ActivityClassification.AMBIGUOUS,
            ocr_provider=provider,
        )
        self.assertNotIn("SUPERSECRET123", outcome.ocr_text)
        self.assertNotIn("https://", outcome.ocr_text)


class OCRNoArtifactsTest(unittest.TestCase):
    def test_no_screenshot_or_temp_artifact_exists_after_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            before = set(Path(tmp_dir).iterdir())
            for provider in (NoOpOCRProvider(), MockOCRProvider(text="anything")):
                resolve_with_ocr(
                    tier1_result=ActivityClassification.AMBIGUOUS,
                    tier2_result=ActivityClassification.AMBIGUOUS,
                    ocr_provider=provider,
                )
            after = set(Path(tmp_dir).iterdir())
            self.assertEqual(before, after)

    def test_ocr_result_has_no_screenshot_or_path_field(self) -> None:
        import dataclasses

        field_names = {f.name for f in dataclasses.fields(OCRResult)}
        self.assertEqual(field_names, {"text", "had_text"})


if __name__ == "__main__":
    unittest.main()
