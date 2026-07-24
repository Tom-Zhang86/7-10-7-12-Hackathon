import ast
import inspect
from pathlib import Path
import unittest

from application.ui.dashboard import DashboardApp


class RunDemoWiringTest(unittest.TestCase):
    def test_dashboard_keyword_arguments_match_current_constructor(self) -> None:
        source = Path("run_demo.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "DashboardApp"
        )
        passed = {keyword.arg for keyword in call.keywords if keyword.arg}
        accepted = set(inspect.signature(DashboardApp.__init__).parameters)

        self.assertEqual(passed - accepted, set())
        self.assertIn("presence_adapter", passed)
        self.assertIn("configurable_llm_client", passed)


if __name__ == "__main__":
    unittest.main()
