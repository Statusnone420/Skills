import tempfile
import unittest
from pathlib import Path

from tools import run_tests


ROOT = Path(__file__).parents[1]


class TestObservableTestRunner(unittest.TestCase):
    def test_repository_partition_is_complete_and_unique(self):
        groups = run_tests.grouped_test_files()

        run_tests.verify_partition(groups)

        assigned = [path for name in run_tests.GROUP_ORDER for path in groups[name]]
        self.assertEqual(set(assigned), set(run_tests.discover_test_files()))
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_new_unclassified_module_defaults_to_core(self):
        with tempfile.TemporaryDirectory() as directory:
            tests = Path(directory)
            (tests / "test_new_behavior.py").write_text("", encoding="utf-8")

            groups = run_tests.grouped_test_files(tests)
            run_tests.verify_partition(groups, tests)

        self.assertEqual(
            [path.name for path in groups["core"]],
            ["test_new_behavior.py"],
        )

    def test_command_uses_current_python_verbose_unittest_and_no_bytecode(self):
        command = run_tests.test_command((Path("tests/test_docs_skill.py"),), failfast=True)

        self.assertEqual(command[0], run_tests.sys.executable)
        self.assertEqual(command[1:6], ["-B", "-u", "-m", "unittest", "-v"])
        self.assertIn("--failfast", command)
        self.assertEqual(command[-1], "tests.test_docs_skill")

    def test_ci_exposes_one_stable_gate_for_the_complete_matrix(self):
        workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("\n  tests_gate:\n", workflow)
        gate = workflow.split("\n  tests_gate:\n", 1)[1]
        self.assertIn("    name: Tests\n", gate)
        self.assertIn("    needs: tests\n", gate)
        self.assertIn("    if: always()\n", gate)
        self.assertIn("needs.tests.result", gate)
        self.assertIn('!= "success"', gate)


if __name__ == "__main__":
    unittest.main()
