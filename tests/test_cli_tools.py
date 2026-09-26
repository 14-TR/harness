"""Exercise the complete CLI tool registry through model/tool round trips."""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

from harness.cli import chat, main
from harness.types import Call, Delta


class CLIToolsTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which("git"), "requires git")
    async def test_discover_edit_test_diff_fetch_and_remember(self):
        names = {"read_file", "list_files", "search_workspace", "apply_patch", "run_tests",
                 "git_status", "git_diff", "fetch_url", "save_note", "read_notes"}
        batches = [
            [Call("list_files", {"pattern": "*.py"}), Call("search_workspace", {"query": "value = 1"})],
            [Call("read_file", {"path": "example.py", "start_line": 1, "end_line": 1})],
            [Call("apply_patch", {"path": "example.py", "old_text": "value = 1", "new_text": "value = 2"})],
            [Call("run_tests", {})],
            [Call("git_status", {}), Call("git_diff", {"path": "example.py"})],
            [Call("fetch_url", {"url": "https://example.test/docs"}),
             Call("save_note", {"name": "decision", "content": "Keep value at 2."})],
            [Call("read_notes", {}), Call("read_notes", {"name": "decision"})],
        ]
        observed = []
        case = self

        class Provider:
            async def stream(self, messages, tools):
                case.assertEqual({tool["function"]["name"] for tool in tools}, names)
                observed[:] = messages
                if batches:
                    yield Delta(calls=batches.pop(0))
                else:
                    yield Delta(text="Updated and verified.")

        real_client = httpx.AsyncClient

        def client(**kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(
                200, text="<html><body><p>Documentation.</p></body></html>",
                headers={"Content-Type": "text/html"}))
            return real_client(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "example.py").write_text("value = 1\n")
            (root / "check.py").write_text("from example import value\nassert value == 2\nprint('verified')\n")
            (root / ".gitignore").write_text(".harness/\n.traces/\n__pycache__/\n")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            args = argparse.Namespace(root=root, prompt="Fix the value, check it, and remember it.",
                model="fake", host="http://localhost:11434", max_tokens=256, max_turns=8,
                no_think=False, stats=True, trace_dir=None, no_trace=False,
                test_command=[sys.executable, "check.py"], test_timeout=5)
            output = io.StringIO()
            with patch("harness.cli.Ollama", return_value=Provider()), patch("harness.cli.httpx.AsyncClient", side_effect=client), \
                    redirect_stdout(output), redirect_stderr(io.StringIO()):
                await chat(args)
            self.assertEqual(output.getvalue(), "Updated and verified.\n")
            self.assertEqual((root / "example.py").read_text(), "value = 2\n")
            self.assertEqual((root / ".harness/notes/decision.md").read_text(), "Keep value at 2.")
            results = {m.tool_name: m.content for m in observed if m.role == "tool"}
            self.assertFalse(any(value.startswith("Tool error:") for value in results.values()), results)
            self.assertIn("Exit code: 0\nverified", results["run_tests"])
            self.assertIn("+value = 2", results["git_diff"])
            self.assertIn("Documentation.", results["fetch_url"])
            self.assertEqual(results["read_notes"], "Keep value at 2.")
            trace = [json.loads(line) for line in next((root / ".traces").glob("*.jsonl")).read_text().splitlines()]
            self.assertEqual({r["data"]["name"] for r in trace if r["kind"] == "tool_start"}, names)
            self.assertEqual(trace[-1]["data"], {"status": "completed"})
            self.assertEqual({tool["function"]["name"] for tool in trace[0]["data"]["tools"]}, names)

    def test_cli_configuration_validation(self):
        for flags in (("--test-timeout", "nan"), ("--test-timeout", "0"), ("--test-command", "")):
            with self.subTest(flags=flags), patch.object(sys, "argv", ["harness", "hello", *flags]), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    main()
                self.assertEqual(raised.exception.code, 2)
