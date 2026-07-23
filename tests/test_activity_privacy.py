from pathlib import Path
import tempfile
import unittest

from application.activity import ActivityPrivacyPolicy, ActivityPrivacyStore


class ActivityPrivacyTest(unittest.TestCase):
    def test_excludes_incognito_apps_and_hostnames(self) -> None:
        policy = ActivityPrivacyPolicy(
            excluded_apps={"1Password"},
            excluded_hosts={"bank.example"},
        )

        self.assertFalse(policy.allows({"incognito": True}))
        self.assertFalse(policy.allows({"app": "1password"}))
        self.assertFalse(
            policy.allows({"url": "https://login.bank.example/account"})
        )
        self.assertTrue(policy.allows({"url": "https://docs.example/lesson"}))

    def test_round_trips_local_privacy_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = ActivityPrivacyStore(Path(temp) / "privacy.json")
            policy = ActivityPrivacyPolicy(
                paused=True,
                allow_remote_classification=True,
                excluded_hosts={"private.example"},
            )
            store.save(policy)

            loaded = store.load()

        self.assertTrue(loaded.paused)
        self.assertTrue(loaded.allow_remote_classification)
        self.assertEqual(loaded.excluded_hosts, {"private.example"})

