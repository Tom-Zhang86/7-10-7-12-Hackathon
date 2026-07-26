import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.config import load_application_environment


class ApplicationConfigTest(unittest.TestCase):
    def test_loads_dotenv_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                'OPENAI_API_KEY="file-key"\n'
                'OPENAI_MODEL="file-model"\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_application_environment(env_path)

                self.assertTrue(loaded)
                self.assertEqual(os.environ["OPENAI_API_KEY"], "file-key")
                self.assertEqual(os.environ["OPENAI_MODEL"], "file-model")

    def test_process_environment_has_priority_over_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text(
                "OPENAI_API_KEY=file-key\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"OPENAI_API_KEY": "process-key"},
                clear=True,
            ):
                load_application_environment(env_path)

                self.assertEqual(
                    os.environ["OPENAI_API_KEY"],
                    "process-key",
                )


if __name__ == "__main__":
    unittest.main()
