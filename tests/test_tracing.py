"""Check saved traces through the CLI consumer, without running a model."""
import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.cli import chat
from harness.types import Call, Delta


class TraceTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, root, provider, **overrides):
        options = dict(root=root, prompt="Read sample.txt", model="fake", host="http://localhost:11434",
                       max_tokens=64, max_turns=3, no_think=False, stats=False,
                       trace_dir=None, no_trace=False, test_command=None, test_timeout=120)
        options.update(overrides)
        with patch("harness.session.Ollama", return_value=provider), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            await chat(argparse.Namespace(**options))

    def rows(self, root):
        paths = list((root / ".traces").glob("*.jsonl"))
        self.assertEqual(len(paths), 1)
        return [json.loads(line) for line in paths[0].read_text().splitlines()]

    async def test_cli_saves_tool_round_trip(self):
        class Provider:
            async def stream(self, messages, tools):
                if messages[-1].role == "user":
                    yield Delta(calls=[Call("read_file", {"path": "sample.txt"})])
                else:
                    yield Delta(text="Read it.")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("hello\nworld")
            await self.exercise(root, Provider())
            rows = self.rows(root)
            self.assertEqual([r["kind"] for r in rows],
                             ["run_start", "turn", "tool_start", "tool_result", "turn", "text", "done", "run_end"])
            self.assertEqual(rows[0]["data"]["messages"][-1]["content"], "Read sample.txt")
            self.assertEqual(rows[2]["data"]["arguments"], {"path": "sample.txt"})
            self.assertEqual(rows[3]["data"]["result"], "hello\nworld")
            self.assertEqual(rows[-1]["data"]["status"], "completed")
            self.assertEqual([r["sequence"] for r in rows], list(range(len(rows))))
            self.assertEqual(len({r["run_id"] for r in rows}), 1)
            self.assertTrue(all(r["timestamp"].endswith("+00:00") for r in rows))
            times = [r["elapsed_seconds"] for r in rows]
            self.assertEqual(times, sorted(times))

    async def test_partial_output_survives_failure_and_cancellation(self):
        for failure, status in [(RuntimeError("connection lost"), "failed"), (asyncio.CancelledError(), "cancelled")]:
            class Provider:
                async def stream(self, messages, tools):
                    yield Delta(text="partial")
                    raise failure

            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with self.assertRaises(type(failure)):
                    await self.exercise(root, Provider())
                rows = self.rows(root)
                self.assertEqual(rows[-3]["data"], "partial")
                self.assertEqual(rows[-2]["data"]["type"], type(failure).__name__)
                self.assertEqual(rows[-1]["data"]["status"], status)
                self.assertNotIn("done", [r["kind"] for r in rows])

    async def test_disabled_and_custom_directory(self):
        class Provider:
            async def stream(self, messages, tools):
                yield Delta(text="hello")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            await self.exercise(root, Provider(), no_trace=True)
            self.assertEqual(list(root.iterdir()), [])
            await self.exercise(root, Provider(), trace_dir=root / "custom")
            await self.exercise(root, Provider(), trace_dir=root / "custom")
            self.assertEqual(len(list((root / "custom").glob("*.jsonl"))), 2)
            self.assertFalse((root / ".traces").exists())
