"""Exercise real curses input/output without an Ollama server."""
import errno
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import curses
    import fcntl
    import pty
    import struct
    import termios
except ImportError:
    curses = None


CHILD = r'''
import argparse
import asyncio
import json
from pathlib import Path
import sys

from harness import tui
from harness.types import Call, Event, Message

log_path = Path(sys.argv[1])
screen_path = log_path.with_suffix(".screen")
draw = tui.draw

def observed_draw(screen, args, view):
    draw(screen, args, view)
    rows = [screen.instr(y, 0).decode("utf-8", errors="replace") for y in range(screen.getmaxyx()[0])]
    temporary = screen_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(rows))
    temporary.replace(screen_path)

tui.draw = observed_draw

def record(kind, **data):
    with log_path.open("a") as log:
        log.write(json.dumps({"kind": kind, **data}) + "\n")

class FakeSession:
    def __init__(self):
        self.messages = [Message("system", "Test conversation")]
        self.tools = {"fixture_tool": None}
        self.busy = False

    def reset(self):
        assert not self.busy
        self.messages[:] = [self.messages[0]]
        record("reset")

    async def stream(self, prompt):
        assert not self.busy
        self.busy = True
        record("start", prompt=prompt, previous=[m.content for m in self.messages])
        self.messages.append(Message("user", prompt))
        try:
            yield Event("trace", str(log_path.parent / "fixture-trace.jsonl"))
            if prompt == "slow":
                yield Event("text", "WAITING_FOR_CANCELLATION")
                record("waiting")
                await asyncio.Event().wait()
            yield Event("tool_start", Call("fixture_tool", {"path": "example.py"}))
            yield Event("tool_result", {"name": "fixture_tool", "result": "fixture result"})
            reply = "REPLY_" + prompt.replace("\n", "_")
            yield Event("text", reply)
            self.messages.append(Message("assistant", reply))
            yield Event("done", reply)
            record("completed", prompt=prompt)
        except asyncio.CancelledError:
            record("cancelled")
            raise
        finally:
            self.busy = False
            record("idle")

args = argparse.Namespace(model="fixture-model", root=log_path.parent, prompt=None)
asyncio.run(tui.run_tui(args, FakeSession()))
print("UI_RETURNED", flush=True)
'''


@unittest.skipUnless(os.name == "posix" and curses is not None, "requires POSIX curses")
class TUIPTYTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log_path = Path(self.directory.name) / "events.jsonl"
        self.screen_path = self.log_path.with_suffix(".screen")
        self.master, self.slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        self.addCleanup(os.close, self.slave)
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
        self.original_terminal = termios.tcgetattr(self.slave)
        self.output = bytearray()
        environment = dict(os.environ, TERM="xterm-256color")
        self.child = subprocess.Popen(
            [sys.executable, "-u", "-c", CHILD, str(self.log_path)],
            cwd=Path(__file__).resolve().parents[1], env=environment,
            stdin=self.slave, stdout=self.slave, stderr=self.slave, start_new_session=True,
        )
        self.addCleanup(self.stop_child)
        self.wait_for(lambda: b"fixture-model" in self.output, "initial screen")

    def stop_child(self):
        if self.child.poll() is None:
            os.killpg(self.child.pid, signal.SIGKILL)
        self.child.wait(timeout=5)

    def pump_output(self, timeout=0.05):
        if select.select([self.master], [], [], timeout)[0]:
            try:
                self.output.extend(os.read(self.master, 65536))
            except OSError as error:
                if error.errno != errno.EIO:
                    raise

    def wait_for(self, condition, description):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.pump_output()
            if condition():
                return
            if self.child.poll() is not None:
                break
        self.fail("Timed out waiting for {} (exit {}): {!r}".format(
            description, self.child.poll(), bytes(self.output[-6000:])))

    def events(self):
        if not self.log_path.exists():
            return []
        # Ignore an unfinished last line if observed between writes.
        return [json.loads(line) for line in self.log_path.read_text().splitlines(keepends=True)
                if line.endswith("\n")]

    def screen(self):
        if not self.screen_path.exists():
            return []
        return json.loads(self.screen_path.read_text())

    def screen_text(self):
        return "\n".join(self.screen())

    def wait_event(self, kind, **values):
        self.wait_for(lambda: any(event["kind"] == kind and all(event.get(k) == v for k, v in values.items())
                                  for event in self.events()), kind)

    def send(self, data):
        os.write(self.master, data)

    def resize(self, rows, columns):
        fcntl.ioctl(self.slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))
        # This isolated child has no controlling terminal to deliver SIGWINCH.
        os.kill(self.child.pid, signal.SIGWINCH)

    def quit_and_check_terminal(self):
        self.send(b"\x04")
        self.wait_for(lambda: self.child.poll() is not None, "clean exit")
        self.assertEqual(self.child.returncode, 0, bytes(self.output).decode(errors="replace"))
        self.assertIn(b"UI_RETURNED", self.output)
        restored = termios.tcgetattr(self.slave)
        # macOS can add a pending-retype flag when curses restores canonical
        # input. It is transient; echo, signals, raw mode, and speeds must match.
        restored[3] &= ~getattr(termios, "PENDIN", 0)
        expected = self.original_terminal[:]
        expected[3] &= ~getattr(termios, "PENDIN", 0)
        self.assertEqual(restored, expected)

    def test_conversation_newline_cancel_reset_and_quit(self):
        self.send(b"alpha\r")
        self.wait_event("completed", prompt="alpha")
        self.wait_for(lambda: "REPLY_alpha" in self.screen_text(), "streamed response")
        self.assertIn("fixture_tool", self.screen_text())

        self.send(b"second\x0aline\r")
        self.wait_event("completed", prompt="second\nline")
        followup = [event for event in self.events() if event["kind"] == "start"][-1]
        self.assertIn("REPLY_alpha", followup["previous"])

        self.send(b"slow\r")
        self.wait_event("waiting")
        self.send(b"\x03")
        self.wait_event("cancelled")

        self.send(b"/new\r")
        self.wait_event("reset")
        self.send(b"fresh\r")
        self.wait_event("completed", prompt="fresh")
        fresh = [event for event in self.events() if event["kind"] == "start"][-1]
        self.assertEqual(fresh["previous"], ["Test conversation"])
        self.quit_and_check_terminal()

    def test_quit_cancels_active_response_and_restores_terminal(self):
        self.send(b"slow\r")
        self.wait_event("waiting")
        self.quit_and_check_terminal()
        self.assertTrue(any(event["kind"] == "cancelled" for event in self.events()))

    def test_help_and_multiline_paste_wait_for_explicit_submission(self):
        self.send(b"/help\r")
        self.wait_for(lambda: "Up/Down recall prompts" in self.screen_text(), "help command")
        self.assertEqual(self.events(), [])
        self.send(b"\x1b[200~pasted\ntext\x1b[201~")
        self.wait_for(lambda: "> pasted" in self.screen_text() and "\u00b7 text" in self.screen_text(), "pasted draft")
        self.assertEqual(self.events(), [])
        self.send(b"\r")
        self.wait_event("completed", prompt="pasted\ntext")
        self.quit_and_check_terminal()

    def test_resize_recovers_from_small_terminal(self):
        self.resize(6, 24)
        self.wait_for(lambda: len(self.screen()) == 6 and "Enlarge terminal" in self.screen_text(),
                      "small-terminal redraw")
        self.resize(18, 45)
        self.wait_for(lambda: len(self.screen()) == 18 and "fixture-model" in self.screen_text(),
                      "resized conversation redraw")
        self.send(b"resized\r")
        self.wait_event("completed", prompt="resized")
        self.wait_for(lambda: "REPLY_resized" in self.screen_text(), "response after resize")
        self.quit_and_check_terminal()

    def test_tool_details_and_up_down_prompt_history(self):
        self.send(b"alpha\r")
        self.wait_event("completed", prompt="alpha")
        self.wait_for(lambda: "REPLY_alpha" in self.screen_text(), "initial response")
        self.assertNotIn("fixture result", self.screen_text())
        self.send(b"\x14")
        self.wait_for(lambda: "fixture result" in self.screen_text(), "expanded tool result")
        self.assertIn("example.py", self.screen_text())
        self.send(b"\x14")
        self.wait_for(lambda: "fixture result" not in self.screen_text(), "collapsed tool result")

        self.send(b"draft")
        self.wait_for(lambda: "> draft" in self.screen_text(), "typed draft")
        # xterm application-cursor sequences, enabled by curses keypad mode.
        self.send(b"\x1bOA")
        self.wait_for(lambda: "> alpha" in self.screen_text(), "recalled prompt")
        self.send(b"\x1bOB\r")
        self.wait_event("completed", prompt="draft")
        self.quit_and_check_terminal()
