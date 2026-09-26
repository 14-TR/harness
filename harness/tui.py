"""A small curses view over Session: no terminal concerns in the agent loop."""
import asyncio
import curses
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from time import monotonic


MAX_TEXT = 24_000
MAX_ENTRIES = 160
MAX_DISPLAY = 120_000
HELP = (
    "Enter sends · Ctrl-J inserts a newline · Left/Right/Home/End edit\n"
    "Up/Down recall prompts · PageUp/PageDown scroll the conversation\n"
    "Ctrl-T or F2 shows tool details · Ctrl-C cancels a run or clears input\n"
    "Ctrl-D or /quit exits · /new clears chat · /trace shows the latest trace\n"
    "Write your next prompt while a reply streams; send it once the run finishes."
)
_ANSI = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-_]")


def clean(value):
    """Never interpret model, tool, or path text as terminal control sequences."""
    text = _ANSI.sub("", str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C")).expandtabs(4)


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def clip(text, width):
    result, used = [], 0
    for char in text:
        size = cells(char)
        if used + size > max(0, width):
            break
        result.append(char)
        used += size
    return "".join(result)


def wrap(text, width):
    """Wrap terminal cells, including wide characters, without ANSI formatting."""
    width = max(1, width)
    rows = []
    for paragraph in text.split("\n"):
        part, used = "", 0
        for char in paragraph:
            size = cells(char)
            if part and used + size > width:
                split = part.rfind(" ")
                if split > len(part) // 2:
                    rows.append(part[:split])
                    part = part[split + 1:]
                else:
                    rows.append(part)
                    part = ""
                used = cells(part)
                if not part and char == " ":
                    continue
            part += char
            used += size
        rows.append(part)
    return rows


def bounded(text):
    return text if len(text) <= MAX_TEXT else "[Earlier content omitted]\n" + text[-MAX_TEXT:]


@dataclass
class Entry:
    role: str
    text: str = ""
    name: str = ""
    status: str = ""
    arguments: str = ""


@dataclass
class Editor:
    text: str = ""
    cursor: int = 0
    history: list = field(default_factory=list)
    history_index: int = 0
    draft: str = ""

    def remember(self):
        prompt = self.text.strip()
        if prompt and (not self.history or self.history[-1] != prompt):
            self.history.append(prompt)
            self.history = self.history[-100:]
        self.text, self.cursor, self.draft = "", 0, ""
        self.history_index = len(self.history)
        return prompt

    def key(self, key):
        if key in (curses.KEY_UP, curses.KEY_DOWN):
            if self.history_index == len(self.history):
                self.draft = self.text
            self.history_index = max(0, min(len(self.history), self.history_index + (-1 if key == curses.KEY_UP else 1)))
            self.text = self.history[self.history_index] if self.history_index < len(self.history) else self.draft
            self.cursor = len(self.text)
        elif key == curses.KEY_LEFT:
            self.cursor = max(0, self.cursor - 1)
        elif key == curses.KEY_RIGHT:
            self.cursor = min(len(self.text), self.cursor + 1)
        elif key in (curses.KEY_HOME, "\x01"):
            self.cursor = self.text.rfind("\n", 0, self.cursor) + 1
        elif key in (curses.KEY_END, "\x05"):
            end = self.text.find("\n", self.cursor)
            self.cursor = len(self.text) if end < 0 else end
        elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
            if self.cursor:
                self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
                self.cursor -= 1
        elif key == curses.KEY_DC:
            self.text = self.text[:self.cursor] + self.text[self.cursor + 1:]
        elif isinstance(key, str) and (key == "\n" or key.isprintable()) and len(self.text) < MAX_TEXT:
            self.text = self.text[:self.cursor] + key + self.text[self.cursor:]
            self.cursor += len(key)


@dataclass
class View:
    entries: list = field(default_factory=list)
    editor: Editor = field(default_factory=Editor)
    details: bool = False
    scroll: int = 0
    status: str = "Ready"
    trace: str = ""
    busy: bool = False
    started: float = 0
    assistant: object = None
    revision: int = 0
    _cache: object = field(default=None, repr=False)

    def prune(self):
        self.revision += 1
        self._cache = None
        self.entries = self.entries[-MAX_ENTRIES:]
        size = sum(len(e.text) + len(e.arguments) for e in self.entries)
        while len(self.entries) > 1 and size > MAX_DISPLAY:
            entry = self.entries.pop(0)
            size -= len(entry.text) + len(entry.arguments)

    def add(self, role, text="", **kwargs):
        entry = Entry(role, bounded(clean(text)), **kwargs)
        self.entries.append(entry)
        self.prune()
        return entry

    def event(self, event):
        if event.kind == "text":
            if self.assistant is None:
                self.assistant = self.add("Assistant")
            self.assistant.text = bounded(self.assistant.text + clean(event.data))
            self.status = "Writing"
            self.prune()
        elif event.kind == "thinking":
            self.status = "Thinking"
        elif event.kind == "turn":
            self.assistant = None
            self.status = f"Thinking · turn {event.data}"
        elif event.kind == "tool_start":
            self.assistant = None
            self.add("Tool", name=clean(event.data.name), status="queued",
                     arguments=bounded(clean(json.dumps(event.data.arguments, ensure_ascii=False))))
            self.status = "Running tools"
        elif event.kind == "tool_result":
            name, result = clean(event.data["name"]), clean(event.data["result"])
            entry = next((e for e in self.entries if e.role == "Tool" and e.name == name and e.status == "queued"), None)
            if entry is None:
                entry = self.add("Tool", name=name)
            entry.text = bounded(result)
            failed = result.startswith("Tool error:") or bool(re.match(r"Exit code: (?!0(?:\n|$))-?\d+", result))
            entry.status = "error" if failed else "done"
            self.prune()
        elif event.kind == "trace":
            data = event.data
            self.trace = clean(data.get("path", "") if isinstance(data, dict) else data or "")
        elif event.kind == "done":
            self.status = "Done"

    def lines(self, width):
        if self._cache is not None and self._cache[:2] == (width, self.details):
            return self._cache[2]
        result = []
        for entry in self.entries:
            label = entry.role
            if entry.role == "Tool":
                label += f" · {entry.name} · {entry.status}"
            result.extend((row, True) for row in wrap(label, width))
            if entry.role != "Tool" or self.details or entry.status == "error":
                if entry.arguments and self.details:
                    result.extend((row, False) for row in wrap(entry.arguments, width))
                result.extend((row, False) for row in wrap(entry.text, width))
            result.append(("", False))
        self._cache = (width, self.details, result)
        return result


async def consume(session, view, prompt):
    """Own one run so cancellation and failures always leave a usable composer."""
    view.busy, view.started, view.scroll = True, monotonic(), 0
    view.status, view.assistant = "Connecting", None
    view.add("You", prompt)
    try:
        events = session.stream(prompt)
        try:
            async for event in events:
                view.event(event)
        finally:
            await events.aclose()
        view.status = f"Done · {monotonic() - view.started:.1f}s"
    except asyncio.CancelledError:
        view.status = "Cancelled"
        view.add("System", "Run cancelled. Completed tool changes may remain.")
        raise
    except Exception as error:
        view.status = "Error · edit or retry your prompt"
        view.add("Error", f"{type(error).__name__}: {error}")
    finally:
        for entry in view.entries:
            if entry.role == "Tool" and entry.status == "queued":
                entry.status = "interrupted"
        view.prune()
        view.busy, view.assistant = False, None


def put(screen, y, x, text, attr=0):
    height, width = screen.getmaxyx()
    if 0 <= y < height and 0 <= x < width:
        try:
            screen.addstr(y, x, clip(text, width - x - 1), attr)
        except curses.error:
            pass  # A resize or writing the lower-right cell can raise.


def draw(screen, args, view):
    screen.erase()
    height, width = screen.getmaxyx()
    if height < 8 or width < 20:
        put(screen, 0, 0, "Enlarge terminal")
        put(screen, 1, 0, "Ctrl-D exits")
        screen.refresh()
        return
    inside = max(1, width - 4)
    put(screen, 0, 1, f"HARNESS  /  {clean(args.model)}", curses.A_BOLD)
    put(screen, 1, 1, clean(args.root), curses.A_DIM)
    # Use hard wrapping in the editor so its cursor always maps to text cells.
    editor_rows, row, col, cursor_row, cursor_col = [""], 0, 0, 0, 0
    for index, char in enumerate(view.editor.text + "\0"):
        size = cells(char)
        if char != "\n" and col + size > inside:
            editor_rows.append("")
            row, col = row + 1, 0
        if index == view.editor.cursor:
            cursor_row, cursor_col = row, col
        if char == "\0":
            break
        if char == "\n":
            editor_rows.append("")
            row, col = row + 1, 0
        else:
            editor_rows[-1] += char
            col += size
    composer_height = min(4, max(1, height // 4), len(editor_rows))
    composer_top = height - composer_height - 2
    transcript_height = max(1, composer_top - 4)
    lines = view.lines(inside)
    view.scroll = min(view.scroll, max(0, len(lines) - transcript_height))
    end = max(0, len(lines) - view.scroll)
    for offset, (text, bold) in enumerate(lines[max(0, end - transcript_height):end]):
        put(screen, 3 + offset, 2, text, curses.A_BOLD if bold else 0)
    elapsed = f" · {monotonic() - view.started:.1f}s" if view.busy else ""
    status = view.status + elapsed
    if view.scroll:
        status += f" · {view.scroll} lines above latest"
    put(screen, composer_top - 1, 1, status, curses.A_DIM)
    first_row = max(0, cursor_row - composer_height + 1)
    for offset, text in enumerate(editor_rows[first_row:first_row + composer_height]):
        put(screen, composer_top + offset, 1, ">" if first_row + offset == 0 else "·", curses.A_BOLD)
        put(screen, composer_top + offset, 3, text)
    put(screen, height - 1, 1, "Enter send  ^J newline  ^C cancel  ^D quit  /help", curses.A_DIM)
    try:
        screen.move(composer_top + cursor_row - first_row, min(width - 2, 3 + cursor_col))
    except curses.error:
        pass
    screen.refresh()


async def interact(screen, args, session):
    view = View()
    view.add("System", "A local agent for your workspace. Ask a question or describe a change.\n/help for shortcuts · Ctrl-T for tool details")
    task = None
    initial = getattr(args, "prompt", None)
    if initial:
        view.editor.text = clean(initial)[:MAX_TEXT]
        view.editor.cursor = len(view.editor.text)
    paste, escape, escape_time, last_draw = False, "", 0, 0
    rendered = None
    try:
        while True:
            if task is not None and task.done():
                await asyncio.gather(task, return_exceptions=True)
                task = None
            now = monotonic()
            signature = (view.revision, view.status, view.editor.text, view.editor.cursor,
                         view.scroll, view.details, screen.getmaxyx(),
                         int((now - view.started) * 5) if view.busy else None)
            if signature != rendered and now - last_draw > 0.04:
                draw(screen, args, view)
                rendered, last_draw = signature, now
            try:
                key = "\r" if initial else screen.get_wch()
                initial = None
            except curses.error:
                await asyncio.sleep(0.04)
                continue
            # Bracketed paste is text, even when it contains newlines, slash
            # commands, or control keys. Never execute a pasted command.
            if escape and monotonic() - escape_time > 0.2:
                escape = ""
            if isinstance(key, str) and (key == "\x1b" or escape):
                escape += key
                escape_time = monotonic()
                if escape in ("\x1b[200~", "\x1b[201~"):
                    paste = escape == "\x1b[200~"
                    escape = ""
                elif not any(marker.startswith(escape) for marker in ("\x1b[200~", "\x1b[201~")):
                    escape = ""
                continue
            if paste:
                if isinstance(key, str):
                    view.editor.key(clean(key))
                continue
            if key in ("\x04",):
                break
            if key == "\x03":
                if task:
                    view.status = "Cancelling"
                    task.cancel()
                else:
                    view.editor.text, view.editor.cursor = "", 0
                    view.status = "Ready"
            elif key in ("\x14", curses.KEY_F2):
                view.details = not view.details
            elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE):
                view.scroll = max(0, view.scroll + (1 if key == curses.KEY_PPAGE else -1) * max(1, screen.getmaxyx()[0] - 8))
            elif key in ("\r", curses.KEY_ENTER):
                prompt = view.editor.text.strip()
                if prompt in ("/quit", "/exit"):
                    break
                if prompt == "/help":
                    view.editor.remember()
                    view.add("Help", HELP)
                    view.scroll = 0
                elif prompt == "/trace":
                    view.editor.remember()
                    view.add("Trace", view.trace or "No trace saved yet (tracing may be disabled).")
                    view.scroll = 0
                elif task:
                    view.status = "Run in progress · Ctrl-C to cancel"
                elif prompt == "/new":
                    session.reset()
                    view.editor.remember()
                    view.entries.clear()
                    view.prune()
                    view.status, view.scroll = "New conversation", 0
                elif prompt.startswith("/"):
                    view.status = "Unknown command · /help for shortcuts"
                elif prompt:
                    task = asyncio.create_task(consume(session, view, view.editor.remember()))
            else:
                view.editor.key(key)
            await asyncio.sleep(0)  # Keep streaming/cancellation responsive during typing.
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def run_tui(args, session):
    """Initialize curses once and restore terminal state even after exceptions."""
    screen = curses.initscr()
    try:
        curses.noecho()
        curses.raw()  # Ctrl-C is an input key; it cancels only the current run.
        curses.nonl()  # Preserve CR (Enter) vs LF (Ctrl-J for multiline input).
        screen.keypad(True)
        screen.nodelay(True)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(25)
        sys.stdout.write("\x1b[?2004h")
        sys.stdout.flush()
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        await interact(screen, args, session)
    finally:
        try:
            sys.stdout.write("\x1b[?2004l")
            sys.stdout.flush()
        except (OSError, ValueError):
            pass  # A closed output stream must not prevent terminal restoration.
        finally:
            try:
                for restore in (lambda: screen.keypad(False), curses.noraw, curses.echo, curses.nl):
                    try:
                        restore()
                    except curses.error:
                        pass
            finally:
                curses.endwin()
