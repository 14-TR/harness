"""Behavior tests for the terminal view, without requiring a real terminal."""
import asyncio
import curses
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from harness.tui import Editor, MAX_DISPLAY, MAX_ENTRIES, MAX_TEXT, View, cells, clean, clip, consume, interact, run_tui, wrap
from harness.types import Call, Event


class TextTests(unittest.TestCase):
    def test_terminal_controls_removed_and_unicode_kept(self):
        text = "\x1b[31mred\x1b[0m\x1b]0;bad title\x07\x00\x08\u202ee\u0301界\nnext\tline"
        result = clean(text)
        self.assertEqual(result, "rede\u0301界\nnext    line")
        self.assertNotIn("\x1b", result)
        self.assertEqual(cells("e\u0301界"), 3)
        self.assertEqual(clip("e\u0301界!", 3), "e\u0301界")

    def test_wrapping_respects_cells_and_newlines(self):
        self.assertEqual(wrap("one two three", 7), ["one two", "three"])
        self.assertEqual(wrap("界界界\n\nend", 4), ["界界", "界", "", "end"])
        self.assertEqual(wrap("abcdef", 1), list("abcdef"))

    def test_editor_editing_and_draft_history(self):
        editor = Editor()
        for key in "hello":
            editor.key(key)
        self.assertEqual(editor.remember(), "hello")
        for key in "draft":
            editor.key(key)
        editor.key(curses.KEY_UP)
        self.assertEqual(editor.text, "hello")
        editor.key(curses.KEY_DOWN)
        self.assertEqual(editor.text, "draft")
        editor.key(curses.KEY_LEFT)
        editor.key(curses.KEY_BACKSPACE)
        editor.key(curses.KEY_HOME)
        editor.key(curses.KEY_DC)
        self.assertEqual(editor.text, "rat")
        editor.key(curses.KEY_END)
        editor.key("\n")
        editor.key("x")
        editor.key(curses.KEY_HOME)
        self.assertEqual(editor.cursor, 4)
        self.assertEqual(editor.text, "rat\nx")

    def test_tools_group_and_failures_remain_visible(self):
        view = View()
        view.event(Event("turn", 1))
        view.event(Event("text", "First "))
        view.event(Event("text", "answer"))
        view.event(Event("tool_start", Call("read_file", {"path": "test.py"})))
        view.event(Event("tool_result", {"name": "read_file", "result": "contents"}))
        self.assertNotIn("contents", "\n".join(text for text, _ in view.lines(80)))
        view.details = True
        self.assertIn("contents", "\n".join(text for text, _ in view.lines(80)))
        view.details = False
        view.event(Event("tool_start", Call("run_tests", {})))
        view.event(Event("tool_result", {"name": "run_tests", "result": "Exit code: 1\nfailure"}))
        self.assertEqual(view.entries[-1].status, "error")
        self.assertIn("failure", "\n".join(text for text, _ in view.lines(80)))
        view.event(Event("done", "First answer"))
        self.assertEqual(view.entries[0].text, "First answer")
        self.assertEqual(len(view.entries), 3)

    def test_display_and_history_are_bounded(self):
        view = View()
        for _ in range(MAX_ENTRIES + 1):
            view.add("Assistant", "x" * MAX_TEXT)
        self.assertLessEqual(len(view.entries), MAX_ENTRIES)
        self.assertLessEqual(sum(len(e.text) for e in view.entries), MAX_DISPLAY)
        view.event(Event("text", "x" * (MAX_TEXT * 2)))
        self.assertIn("Earlier content omitted", view.entries[-1].text)
        editor = Editor()
        for number in range(110):
            editor.text = str(number)
            editor.remember()
        self.assertEqual(len(editor.history), 100)


class ViewAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_trace_and_followup(self):
        class Session:
            def __init__(self):
                self.prompts = []

            async def stream(self, prompt):
                self.prompts.append(prompt)
                yield Event("trace", Path("example.jsonl"))
                yield Event("turn", 1)
                yield Event("thinking", "private reasoning")
                yield Event("text", "reply ")
                yield Event("text", str(len(self.prompts)))
                yield Event("done", "reply")

        session, view = Session(), View()
        await consume(session, view, "first")
        await consume(session, view, "second")
        self.assertEqual(session.prompts, ["first", "second"])
        self.assertEqual([e.text for e in view.entries], ["first", "reply 1", "second", "reply 2"])
        self.assertEqual(view.trace, "example.jsonl")
        self.assertFalse(view.busy)
        self.assertTrue(view.status.startswith("Done"))

    async def test_cancellation_marks_queued_tools_and_finishes_stream(self):
        started, closed = asyncio.Event(), asyncio.Event()

        class Session:
            async def stream(self, prompt):
                try:
                    yield Event("tool_start", Call("run_tests", {}))
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        view = View()
        task = asyncio.create_task(consume(Session(), view, "work"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(closed.is_set())
        self.assertEqual(view.status, "Cancelled")
        self.assertEqual(view.entries[1].status, "interrupted")
        self.assertFalse(view.busy)

    async def test_errors_leave_input_available(self):
        class Session:
            async def stream(self, prompt):
                raise OSError("server unavailable")
                yield

        view = View()
        await consume(Session(), view, "hello")
        self.assertFalse(view.busy)
        self.assertIn("Error", view.status)
        self.assertEqual(view.entries[-1].text, "OSError: server unavailable")

    async def test_render_event_failure_closes_stream_before_retry(self):
        closed = asyncio.Event()

        class Session:
            async def stream(self, prompt):
                try:
                    yield Event("tool_result", {})
                finally:
                    closed.set()

        view = View()
        await consume(Session(), view, "hello")
        self.assertTrue(closed.is_set())
        self.assertFalse(view.busy)
        self.assertIn("Error", view.status)

    async def test_idle_view_does_not_redraw_without_changes(self):
        class Screen:
            polls = 0

            def getmaxyx(self):
                return (24, 80)

            def get_wch(self):
                self.polls += 1
                if self.polls < 6:
                    raise curses.error()
                return "\x04"

        with patch("harness.tui.draw") as draw:
            await interact(Screen(), SimpleNamespace(prompt=None), None)
        self.assertEqual(draw.call_count, 1)

    async def test_failed_terminal_setup_still_restores_terminal(self):
        screen = Mock()
        stdout = Mock()
        stdout.write.side_effect = OSError("closed terminal")
        with patch("harness.tui.sys.stdout", stdout), patch("harness.tui.curses.initscr", return_value=screen), \
                patch("harness.tui.curses.noecho"), patch("harness.tui.curses.raw"), \
                patch("harness.tui.curses.nonl"), patch("harness.tui.curses.set_escdelay"), \
                patch("harness.tui.curses.noraw", side_effect=curses.error), \
                patch("harness.tui.curses.echo") as echo, patch("harness.tui.curses.nl"), \
                patch("harness.tui.curses.endwin") as endwin:
            with self.assertRaisesRegex(OSError, "closed terminal"):
                await run_tui(None, None)
        echo.assert_called_once()
        endwin.assert_called_once()

    async def test_paste_is_text_and_cannot_submit_or_quit(self):
        keys = list("\x1b[200~hello\x04\r/quit\x1b[201~\r\x04")

        class Screen:
            def getmaxyx(self):
                return (24, 80)

            def get_wch(self):
                return keys.pop(0)

        class Session:
            async def stream(self, prompt):
                self.prompt = prompt
                yield Event("done", "")

        session = Session()
        args = SimpleNamespace(prompt=None)
        with patch("harness.tui.draw"):
            await interact(Screen(), args, session)
        self.assertEqual(session.prompt, "hello\n/quit")


if __name__ == "__main__":
    unittest.main()
