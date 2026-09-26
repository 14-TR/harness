"""Executable examples of the harness's important behavior.

No live model is needed: fake providers let us control the conversation, and
httpx.MockTransport lets us exercise the adapter without network requests.
Run with: .venv/bin/python -m unittest discover -s tests -v
"""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx

from harness.builtin import file_tools
from harness.ollama import Ollama
from harness.runner import run
from harness.tools import Tool, invoke
from harness.types import Call, Delta, Message


class ScriptedProvider:
    """Return a predefined list of chunks for each successive model turn."""

    def __init__(self, replies):
        # Each next() advances one turn. Running out is a test failure rather
        # than silently inventing another model response.
        self.replies = iter(replies)
        self.histories = []

    async def stream(self, messages, tools):
        # Copy the list so future appends don't change our record of what the
        # provider received. This is shallow: the message objects are shared.
        self.histories.append(list(messages))
        for delta in next(self.replies):
            yield delta


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    # unittest supplies a fresh event loop for each async test, keeping tasks
    # from one case from leaking into another. No extra test library required.
    async def test_provider_can_return_plain_async_iterator(self):
        class Chunks:
            def __init__(self):
                self.chunks = iter([Delta(text="hello"), Delta(text=" world")])

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self.chunks)
                except StopIteration:
                    raise StopAsyncIteration

        class Provider:
            def stream(self, messages, tools):
                return Chunks()

        history = [Message("user", "test")]
        events = [event async for event in run(Provider(), history, {})]
        self.assertEqual(events[-1].kind, "done")
        self.assertEqual(history[-1], Message("assistant", "hello world"))

    async def test_tool_round_trip_and_streaming(self):
        async def add(a, b):
            return str(a + b)

        # First the model requests addition; then it answers in two chunks.
        provider = ScriptedProvider([
            [Delta(calls=[Call("add", {"a": 2, "b": 3})])],
            [Delta(text="It is "), Delta(text="5.")],
        ])
        history = [Message("user", "add")]
        events = [e async for e in run(provider, history, {"add": Tool("Add", {}, add)})]
        # Prove that the next model turn saw the actual tool result and that
        # the UI still received incremental text instead of one buffered answer.
        self.assertEqual(provider.histories[1][-1].content, "5")
        self.assertEqual(provider.histories[1][-1].tool_name, "add")
        self.assertEqual([e.data for e in events if e.kind == "text"], ["It is ", "5."])
        self.assertEqual(events[-1].kind, "done")

    async def test_tool_errors_are_recoverable(self):
        # Missing tools, timeouts, and bad arguments should become result text
        # the model can react to, rather than crash the conversation.
        self.assertIn("unknown tool", await invoke(Call("missing", {}), {}))

        async def slow():
            await asyncio.sleep(10)

        # The ten-second sleep gets cancelled after about 10ms; we don't wait
        # ten seconds just to test the timeout path.
        tools = {"slow": Tool("", {}, slow, timeout=0.01)}
        self.assertIn("exceeded", await invoke(Call("slow", {}), tools))
        self.assertIn("TypeError", await invoke(Call("slow", {"bad": 1}), tools))

    async def test_turn_limit(self):
        # A tool request on the final permitted turn must not be mistaken for
        # a final answer. Drain the generator to reach the limit exception.
        provider = ScriptedProvider([[Delta(calls=[Call("missing", {})])]])
        with self.assertRaisesRegex(RuntimeError, "1 model turns"):
            async for _ in run(provider, [], {}, max_turns=1):
                pass

    async def test_parallel_tools_actually_overlap_and_keep_order(self):
        # A rendezvous checks real overlap without relying on a fragile speed
        # benchmark. Neither call can complete until both have started.
        started = set()
        both = asyncio.Event()

        async def meet(number):
            started.add(number)
            if len(started) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 1)
            return str(number)

        provider = ScriptedProvider([
            [Delta(calls=[Call("meet", {"number": 1}), Call("meet", {"number": 2})])],
            [Delta(text="done")],
        ])
        history = []
        async for _ in run(provider, history, {"meet": Tool("", {}, meet)}, parallel_tools=True):
            pass
        self.assertEqual([m.content for m in history if m.role == "tool"], ["1", "2"])

    async def test_cancellation_preserves_consistent_history(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def block():
            started.set()
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        provider = ScriptedProvider([[Delta(calls=[Call("block", {})])]])
        history = [Message("user", "test")]

        async def consume():
            async for _ in run(provider, history, {"block": Tool("", {}, block)}):
                pass

        # Run the consumer in the background, wait until the tool is active,
        # then cancel. This avoids guessing timing with arbitrary sleeps.
        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # The tool's finally block ran, and no incomplete assistant/tool batch
        # was committed. Only the original user message remains in history.
        self.assertTrue(cancelled.is_set())
        self.assertEqual(len(history), 1)

    async def test_file_tool_boundary_and_size(self):
        # TemporaryDirectory cleans up the fixture even when an assertion fails.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test.txt").write_text("x" * 17000)
            tools = file_tools(root)
            result = await invoke(Call("read_file", {"path": "test.txt"}), tools)
            self.assertEqual(result, "x" * 16000 + "\n[truncated]")
            result = await invoke(Call("read_file", {"path": "../outside"}), tools)
            self.assertIn("inside the workspace", result)

    async def test_self_cancelling_tool_cleans_up_parallel_siblings(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def sibling():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def cancel_self():
            await started.wait()
            raise asyncio.CancelledError()

        provider = ScriptedProvider([[Delta(calls=[Call("sibling", {}), Call("cancel", {})])]])
        history = [Message("user", "test")]
        tools = {"sibling": Tool("", {}, sibling), "cancel": Tool("", {}, cancel_self)}
        with self.assertRaises(asyncio.CancelledError):
            async for _ in run(provider, history, tools, parallel_tools=True):
                pass
        self.assertTrue(stopped.is_set())
        self.assertEqual(len(history), 1)

    async def test_ollama_wire_format_and_incomplete_stream(self):
        requests = []

        # Mock the HTTP boundary, not the adapter itself. This exercises its
        # JSON encoding, line parsing, and conversion into our Delta objects.
        def respond(request):
            requests.append(json.loads(request.content))
            chunks = [
                {"message": {"thinking": "hmm", "content": "hello"}, "done": False},
                {"message": {"tool_calls": [{"function": {
                    "name": "read_file", "arguments": {"path": "README.md"}}}]}, "done": True},
            ]
            return httpx.Response(200, text="\n".join(json.dumps(c) for c in chunks))

        async with httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(respond)) as client:
            provider = Ollama(client)
            history = [Message("assistant", thinking="previous", calls=[Call("read_file", {"path": "a"})]),
                       Message("tool", "data", tool_name="read_file")]
            deltas = [d async for d in provider.stream(history, [])]
        self.assertEqual(deltas[1].calls[0].name, "read_file")
        self.assertEqual(requests[0]["messages"][0]["thinking"], "previous")
        self.assertEqual(requests[0]["messages"][1]["tool_name"], "read_file")
        self.assertTrue(requests[0]["stream"])

        # An HTTP 200 with partial text but no done flag must fail. A consumer
        # may already have displayed that text, but must not claim completion.
        async with httpx.AsyncClient(base_url="http://ollama", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text='{"message":{"content":"partial"}}\n')
        )) as client:
            with self.assertRaisesRegex(RuntimeError, "before completion"):
                async for _ in Ollama(client).stream([], []):
                    pass


if __name__ == "__main__":
    unittest.main()
