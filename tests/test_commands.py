"""Real subprocess checks for bounded commands and Git workspace boundaries."""
import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from harness.commands import command_tools
from harness.tools import invoke
from harness.types import Call


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def tools(self, script, timeout=5):
        return command_tools(self.root, [sys.executable, "-c", script], timeout)

    async def test_tests_report_exit_code_and_run_in_workspace(self):
        result = await invoke(Call("run_tests", {}), self.tools(
            "import os, sys; print(os.getcwd()); print('failure', file=sys.stderr); sys.exit(3)"))
        self.assertTrue(result.startswith("Exit code: 3\n"))
        self.assertIn(str(self.root), result)
        self.assertIn("failure", result)

    async def test_tests_use_default_unittest_command(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_sample.py").write_text(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    def test_ok(self): self.assertTrue(True)\n")
        result = await invoke(Call("run_tests", {}), command_tools(self.root))
        self.assertIn("Exit code: 0", result)
        self.assertIn("Ran 1 test", result)

    async def test_tests_drain_but_bound_output_and_reject_model_commands(self):
        tools = self.tools("print('x' * 1000000)")
        result = await invoke(Call("run_tests", {}), tools)
        self.assertEqual(result, "Exit code: 0\n" + "x" * 16000 + "\n[truncated]")
        self.assertIn("TypeError", await invoke(Call("run_tests", {"command": "pwd"}), tools))

    def test_invalid_configuration(self):
        for command in [[], "python", ["python", 1], [""], 42, {"python": "args"}]:
            with self.assertRaises(ValueError):
                command_tools(self.root, command)
        for timeout in [0, -1, float("inf"), float("nan"), "120", None, True]:
            with self.assertRaises(ValueError):
                command_tools(self.root, test_timeout=timeout)

    async def test_nonexistent_command_is_recoverable(self):
        result = await invoke(Call("run_tests", {}), command_tools(self.root, ["/missing/test-command"]))
        self.assertIn("FileNotFoundError", result)

    async def test_timeout_reaps_process(self):
        tools = self.tools("import os, pathlib, time; pathlib.Path('pid').write_text(str(os.getpid())); time.sleep(60)",
                           timeout=0.3)
        result = await invoke(Call("run_tests", {}), tools)
        self.assertIn("exceeded 0.3s", result)
        pid = int((self.root / "pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    async def test_timeout_while_output_pipe_is_busy(self):
        tools = self.tools("import os\nwhile True: os.write(1, b'x' * 65536)", timeout=0.05)
        result = await asyncio.wait_for(invoke(Call("run_tests", {}), tools), 2)
        self.assertIn("exceeded 0.05s", result)

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    async def test_cancellation_kills_descendants(self):
        marker = self.root / "started"
        # The child records its PID before either process enters a long sleep.
        child = "import os, pathlib, time; pathlib.Path('started').write_text(str(os.getpid())); time.sleep(60)"
        tools = self.tools(
            f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)")
        task = asyncio.create_task(invoke(Call("run_tests", {}), tools))
        try:
            for _ in range(200):
                if marker.exists():
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(marker.exists(), "subprocess did not start")
            pid = int(marker.read_text())
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        for _ in range(100):
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                   text=True, capture_output=True).stdout.strip()
            if not state or state.startswith("Z"):
                break
            await asyncio.sleep(0.01)
        self.assertTrue(not state or state.startswith("Z"), f"child {pid} still running")


@unittest.skipUnless(shutil.which("git"), "Git is not installed")
class GitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True,
                              text=True, capture_output=True).stdout

    def init(self):
        self.git("init", "-q")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")

    async def test_status_and_staged_and_unstaged_diff(self):
        self.init()
        file = self.root / "sample.txt"
        file.write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        file.write_text("staged\n")
        self.git("add", "sample.txt")
        file.write_text("unstaged\n")
        tools = command_tools(self.root)
        status = await invoke(Call("git_status", {}), tools)
        self.assertIn("MM sample.txt", status)
        staged = await invoke(Call("git_diff", {"staged": True}), tools)
        self.assertIn("-original\n+staged", staged)
        unstaged = await invoke(Call("git_diff", {}), tools)
        self.assertIn("-staged\n+unstaged", unstaged)
        self.assertIn("Exit code: 0", unstaged)

    async def test_diff_literal_path_and_boundary(self):
        self.init()
        for name in ["one.txt", "two.txt"]:
            (self.root / name).write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        for name in ["one.txt", "two.txt"]:
            (self.root / name).write_text("changed\n")
        tools = command_tools(self.root)
        result = await invoke(Call("git_diff", {"path": "one.txt"}), tools)
        self.assertIn("one.txt", result)
        self.assertNotIn("two.txt", result)
        result = await invoke(Call("git_diff", {"path": "*.txt"}), tools)
        self.assertEqual(result, "Exit code: 0\n")
        result = await invoke(Call("git_diff", {"path": "../outside"}), tools)
        self.assertIn("inside the workspace", result)
        self.assertIn("boolean", await invoke(Call("git_diff", {"staged": "false"}), tools))

    async def test_no_repository_and_parent_repository_are_rejected(self):
        tools = command_tools(self.root)
        self.assertIn("root of a Git working tree", await invoke(Call("git_status", {}), tools))
        self.init()
        child = self.root / "nested"
        child.mkdir()
        tools = command_tools(child)
        self.assertIn("root of a Git working tree", await invoke(Call("git_status", {}), tools))

    @unittest.skipUnless(os.name == "posix", "fixture needs symlinks")
    async def test_diff_tracks_symlink_retargeting_without_reading_targets(self):
        self.init()
        (self.root / "first.txt").write_text("first file contents\n")
        (self.root / "second.txt").write_text("second file contents\n")
        link = self.root / "link"
        link.symlink_to("first.txt")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        link.unlink()
        link.symlink_to("second.txt")
        tools = command_tools(self.root)
        result = await invoke(Call("git_diff", {"path": "link"}), tools)
        self.assertIn("diff --git a/link b/link", result)
        self.assertIn("-first.txt", result)
        self.assertIn("+second.txt", result)
        self.assertNotIn("file contents", result)
        self.git("add", "link")
        result = await invoke(Call("git_diff", {"path": "link", "staged": True}), tools)
        self.assertIn("-first.txt", result)
        self.assertIn("+second.txt", result)

        # A final directory link is also just link metadata to Git; traversing
        # through that link to an outside file must still be rejected.
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "private.txt").write_text("outside secret fixture\n")
            link.unlink()
            link.symlink_to(outside, target_is_directory=True)
            result = await invoke(Call("git_diff", {"path": "link"}), tools)
            self.assertIn("-second.txt", result)
            self.assertIn("+" + outside, result)
            self.assertNotIn("outside secret fixture", result)
            result = await invoke(Call("git_diff", {"path": "link/private.txt"}), tools)
            self.assertIn("inside the workspace", result)


if __name__ == "__main__":
    unittest.main()
