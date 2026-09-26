"""Real file operations against disposable workspaces."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from harness.builtin import file_tools
from harness.tools import invoke
from harness.types import Call


class FileToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.tools = file_tools(self.root)

    async def call(self, name, **arguments):
        return await invoke(Call(name, arguments), self.tools)

    async def test_list_and_search_with_globs_lines_and_context(self):
        (self.root / "tests").mkdir()
        (self.root / "tests" / "example.py").write_text("before\nNeedle here\nafter\n")
        (self.root / "other.txt").write_text("needle elsewhere\n")
        self.assertEqual(await self.call("list_files", pattern="tests/*.py"), "tests/example.py")
        self.assertEqual(await self.call("list_files", path="tests", pattern="**/*.py"), "tests/example.py")
        result = await self.call("search_workspace", query="Needle", pattern="*.py", context=1)
        self.assertEqual(result, "tests/example.py:1: before\ntests/example.py:2: Needle here\ntests/example.py:3: after")
        result = await self.call("search_workspace", query="NEEDLE", case_sensitive=False, context=0)
        self.assertIn("other.txt:1: needle elsewhere", result)
        self.assertIn("tests/example.py:2: Needle here", result)
        self.assertEqual(await self.call("search_workspace", query="absent"), "No matches.")

    async def test_navigation_skips_local_files_and_symlinks(self):
        (self.root / "good.txt").write_text("visible")
        for name in (".git", ".venv", "node_modules", ".harness", ".traces", "project.egg-info"):
            (self.root / name).mkdir()
            (self.root / name / "hidden.txt").write_text("hidden")
        for name in (".env", "secret.key", "run.jsonl"):
            (self.root / name).write_text("hidden")
        (self.root / "alias").symlink_to(self.root / "good.txt")
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)
        self.assertEqual(await self.call("list_files"), "good.txt")
        self.assertEqual(await self.call("search_workspace", query="hidden"), "No matches.")

    async def test_read_ranges_beyond_original_cap_and_long_lines(self):
        (self.root / "large.txt").write_text("x" * 20000 + "\nsecond\nthird\n")
        self.assertEqual(await self.call("read_file", path="large.txt", start_line=2, end_line=2), "second\n")
        self.assertTrue((await self.call("read_file", path="large.txt")).endswith("[truncated]"))
        self.assertEqual(await self.call("read_file", path="large.txt", start_line=10), "")
        for data in (b"first\rsecond\rthird\r", b"first\r\nsecond\r\nthird\r\n",
                     b"x" * 15999 + b"\r\nsecond\r\n"):
            (self.root / "lines.txt").write_bytes(data)
            result = await self.call("read_file", path="lines.txt", start_line=2, end_line=2)
            self.assertEqual(result.rstrip("\r\n"), "second")

    async def test_navigation_skips_worktree_git_metadata_file(self):
        (self.root / ".git").write_text("gitdir: private-worktree-metadata\n")
        self.assertEqual(await self.call("list_files"), "No matching files.")
        self.assertEqual(await self.call("search_workspace", query="gitdir"), "No matches.")

    async def test_output_limits_and_nontext_files(self):
        for number in range(3):
            (self.root / f"{number}.txt").write_text("match\n" * 4)
        (self.root / "binary").write_bytes(b"match\0")
        (self.root / "bad-encoding").write_bytes(b"match\xff")
        (self.root / "too-big").write_bytes(b"x" * (1024 * 1024 + 1))
        self.assertIn("more files", await self.call("list_files", limit=1))
        self.assertIn("more matches", await self.call("search_workspace", query="match", limit=1))
        self.assertIn("skipped 3", await self.call("search_workspace", query="absent"))
        (self.root / "0.txt").write_text("match " * 5000)
        self.assertLessEqual(len(await self.call("search_workspace", query="match")), 16012)

    async def test_patch_create_edit_and_preserve_mode(self):
        result = await self.call("apply_patch", path="src/example.py", old_text="", new_text="value = 1\n")
        self.assertIn("Created src/example.py", result)
        target = self.root / "src" / "example.py"
        target.chmod(0o755)
        result = await self.call("apply_patch", path="src/example.py", old_text="value = 1", new_text="value = 2")
        self.assertEqual(target.read_text(), "value = 2\n")
        self.assertIn("-value = 1\n+value = 2\n", result)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)
        self.assertEqual(await self.call("apply_patch", path="src/example.py", old_text="2", new_text="2"), "No changes.")

    async def test_patch_rejects_ambiguous_stale_and_empty_matches(self):
        target = self.root / "code.py"
        for original, old in (("abc abc", "abc"), ("aaa", "aa"), ("abc", "missing"), ("abc", "")):
            target.write_text(original)
            result = await self.call("apply_patch", path="code.py", old_text=old, new_text="oops")
            self.assertIn("Tool error:", result)
            self.assertEqual(target.read_text(), original)

    async def test_patch_diff_without_final_newlines(self):
        (self.root / "code.py").write_text("abc")
        result = await self.call("apply_patch", path="code.py", old_text="abc", new_text="xyz")
        self.assertIn("-abc\n\\ No newline at end of file\n+xyz\n", result)
        self.assertEqual((self.root / "code.py").read_bytes(), b"xyz")

    async def test_patch_failure_keeps_original_and_cleans_temp_file(self):
        target = self.root / "code.py"
        target.write_text("abc")
        with patch("harness.builtin.os.replace", side_effect=OSError("disk error")):
            self.assertIn("disk error", await self.call("apply_patch", path="code.py", old_text="abc", new_text="xyz"))
        self.assertEqual(target.read_text(), "abc")
        self.assertEqual(list(self.root.iterdir()), [target])

    async def test_workspace_and_metadata_boundaries(self):
        with tempfile.TemporaryDirectory() as outside:
            (self.root / "escape").symlink_to(outside, target_is_directory=True)
            for path in ("../outside", "escape/file", ".git/config", ".GIT/config"):
                for name, args in (("read_file", {}), ("list_files", {}), ("search_workspace", {"query": "x"}),
                                   ("apply_patch", {"old_text": "", "new_text": "x"})):
                    self.assertIn("Tool error:", await self.call(name, path=path, **args))
            self.assertEqual(list(Path(outside).iterdir()), [])
        (self.root / ".git").mkdir()
        (self.root / "alias").symlink_to(self.root / ".git", target_is_directory=True)
        self.assertIn("Git metadata", await self.call("apply_patch", path="alias/config", old_text="", new_text="x"))

    async def test_runtime_validation_and_patch_size(self):
        (self.root / "file").write_text("data")
        for name, args in (("read_file", {"path": "file", "start_line": True}),
                           ("read_file", {"path": "file", "start_line": 3, "end_line": 2}),
                           ("list_files", {"limit": 0}), ("list_files", {"pattern": 3}),
                           ("search_workspace", {"query": ""}),
                           ("search_workspace", {"query": "x", "case_sensitive": "false"}),
                           ("apply_patch", {"path": "file", "old_text": "data", "new_text": "x" * (1024 * 1024 + 1)})):
            self.assertIn("Tool error:", await self.call(name, **args))
        self.assertEqual((self.root / "file").read_text(), "data")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    async def test_special_files_are_rejected_without_blocking(self):
        os.mkfifo(self.root / "pipe")
        self.assertIn("regular file", await self.call("read_file", path="pipe"))
        self.assertIn("regular", await self.call("apply_patch", path="pipe", old_text="", new_text="text"))
        self.assertEqual(await self.call("list_files"), "No matching files.")
