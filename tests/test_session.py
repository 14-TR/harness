"""Conversation continuity and resource cleanup without a live model."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from harness.builtin import file_tools
from harness.notes import note_tools
from harness.session import Session, open_session, system_message
from harness.tools import invoke
from harness.types import Call, Delta


def options(root, **overrides):
    values = dict(root=root, model="fake", host="http://localhost:11434", max_tokens=64,
                  max_turns=3, no_think=False, trace_dir=None, no_trace=False,
                  test_command=None, test_timeout=120)
    values.update(overrides)
    return argparse.Namespace(**values)


class ReplyProvider:
    def __init__(self):
        self.histories = []

    async def stream(self, messages, tools):
        self.histories.append(list(messages))
        yield Delta(text="Reply to " + messages[-1].content)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def traces(self, root):
        return [[json.loads(line) for line in path.read_text().splitlines()]
                for path in (root / ".traces").glob("*.jsonl")]

    async def test_two_prompts_share_history_with_separate_traces(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = ReplyProvider()
            session = Session(options(root), provider, file_tools(root))
            first = [event async for event in session.stream("first")]
            second = [event async for event in session.stream("second")]
            self.assertEqual([m.role for m in provider.histories[1]],
                             ["system", "user", "assistant", "user"])
            self.assertEqual(provider.histories[1][2].content, "Reply to first")
            self.assertEqual(session.messages[-1].content, "Reply to second")
            self.assertEqual(sum(m.role == "system" for m in session.messages), 1)
            self.assertFalse(session.busy)
            self.assertEqual(first[0].kind, "trace")
            self.assertEqual(second[0].kind, "trace")
            self.assertNotEqual(first[0].data, second[0].data)
            traces = {rows[0]["data"]["messages"][-1]["content"]: rows
                      for rows in self.traces(root)}
            self.assertEqual(set(traces), {"first", "second"})
            self.assertEqual(len(traces["first"][0]["data"]["messages"]), 2)
            self.assertEqual(len(traces["second"][0]["data"]["messages"]), 4)
            for rows in traces.values():
                self.assertEqual([r["kind"] for r in rows],
                                 ["run_start", "turn", "text", "done", "run_end"])
                self.assertEqual(rows[-1]["data"]["status"], "completed")
                self.assertEqual(rows[0]["data"]["tools"],
                                 [tool.schema(name) for name, tool in session.tools.items()])

    async def test_reset_clears_history_without_changing_files_or_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("unchanged")
            session = Session(options(root, no_trace=True), ReplyProvider(), note_tools(root))
            await invoke(Call("save_note", {"name": "project", "content": "A verified fact."}), session.tools)
            async for _ in session.stream("first"):
                pass
            history = session.messages
            session.reset()
            self.assertIs(session.messages, history)
            self.assertEqual(session.messages, [system_message(root)])
            self.assertEqual((root / "sample.txt").read_text(), "unchanged")
            self.assertEqual(await invoke(Call("read_notes", {"name": "project"}), session.tools),
                             "A verified fact.")
            self.assertFalse((root / ".traces").exists())

    async def test_busy_rejects_second_prompt_and_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(options(Path(directory), no_trace=True), ReplyProvider(), {})
            events = session.stream("first")
            self.assertEqual((await events.__anext__()).kind, "turn")
            self.assertTrue(session.busy)
            with self.assertRaisesRegex(RuntimeError, "already running"):
                async for _ in session.stream("second"):
                    pass
            with self.assertRaisesRegex(RuntimeError, "Cannot reset"):
                session.reset()
            self.assertEqual([m.content for m in session.messages if m.role == "user"], ["first"])
            await events.aclose()
            self.assertFalse(session.busy)
            session.reset()
            self.assertEqual(len(session.messages), 1)

    async def test_failure_keeps_completed_tool_history_and_can_retry(self):
        class Provider:
            async def stream(self, messages, tools):
                if messages[-1].content == "read":
                    yield Delta(calls=[Call("read_file", {"path": "sample.txt"})])
                elif messages[-1].role == "tool":
                    yield Delta(text="partial")
                    raise RuntimeError("connection lost")
                else:
                    yield Delta(text="recovered")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("contents")
            session = Session(options(root), Provider(), file_tools(root))
            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                async for _ in session.stream("read"):
                    pass
            self.assertEqual([m.role for m in session.messages], ["system", "user", "assistant", "tool"])
            self.assertEqual(session.messages[-1].content, "contents")
            self.assertFalse(session.busy)
            rows = self.traces(root)[0]
            self.assertEqual(rows[-3]["data"], "partial")
            self.assertEqual(rows[-1]["data"]["status"], "failed")
            async for _ in session.stream("retry"):
                pass
            self.assertEqual(session.messages[-1].content, "recovered")

    async def test_cancelled_prompt_closes_provider_and_keeps_completed_tools(self):
        waiting = asyncio.Event()
        stopped = asyncio.Event()

        class Provider:
            async def stream(self, messages, tools):
                if messages[-1].role == "user":
                    yield Delta(calls=[Call("read_file", {"path": "sample.txt"})])
                else:
                    waiting.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        stopped.set()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.txt").write_text("contents")
            session = Session(options(root), Provider(), file_tools(root))

            async def consume():
                async for _ in session.stream("read"):
                    pass

            task = asyncio.create_task(consume())
            await asyncio.wait_for(waiting.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(stopped.is_set())
            self.assertFalse(session.busy)
            self.assertEqual([m.role for m in session.messages], ["system", "user", "assistant", "tool"])
            self.assertEqual(self.traces(root)[0][-1]["data"]["status"], "cancelled")

    async def test_explicit_early_close_releases_provider_and_cancels_trace(self):
        stopped = asyncio.Event()

        class Provider:
            def stream(self, messages, tools):
                # Retain the iterator so garbage collection cannot masquerade
                # as explicit cleanup by the session and runner.
                self.active = self.respond()
                return self.active

            async def respond(self):
                try:
                    yield Delta(text="partial")
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = Session(options(root), Provider(), {})
            events = session.stream("first")
            self.assertEqual((await events.__anext__()).kind, "trace")
            self.assertEqual((await events.__anext__()).kind, "turn")
            self.assertEqual((await events.__anext__()).data, "partial")
            await events.aclose()
            self.assertTrue(stopped.is_set())
            self.assertFalse(session.busy)
            self.assertEqual([m.role for m in session.messages], ["system", "user"])
            self.assertEqual(self.traces(root)[0][-1]["data"]["status"], "cancelled")

    async def test_open_session_reuses_and_closes_both_clients(self):
        clients = []
        real_client = httpx.AsyncClient

        def create_client(*args, **kwargs):
            client = real_client(*args, **kwargs)
            clients.append(client)
            return client

        provider = ReplyProvider()
        with tempfile.TemporaryDirectory() as directory, \
                patch("harness.session.httpx.AsyncClient", side_effect=create_client), \
                patch("harness.session.Ollama", return_value=provider) as factory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "consumer failed"):
                async with open_session(options(root, no_trace=True, no_think=True)) as session:
                    registry = session.tools
                    for prompt in ("first", "second"):
                        events = [event async for event in session.stream(prompt)]
                        self.assertNotIn("trace", [event.kind for event in events])
                        self.assertIs(session.tools, registry)
                        self.assertIs(session.provider, provider)
                    self.assertEqual(len(clients), 2)
                    self.assertTrue(all(not client.is_closed for client in clients))
                    factory.assert_called_once_with(clients[0], "fake", options={"num_predict": 64}, think=False)
                    self.assertEqual(len(registry), 10)
                    raise RuntimeError("consumer failed")
            self.assertTrue(all(client.is_closed for client in clients))
            self.assertEqual(list(root.iterdir()), [])
