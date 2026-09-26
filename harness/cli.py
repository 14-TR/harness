"""Choose a full-screen conversation or a pipe-friendly, one-shot command."""
import argparse
import asyncio
import math
import os
from pathlib import Path
import shlex
import sys
from time import perf_counter

import httpx

from .session import open_session


async def chat(args: argparse.Namespace) -> None:
    """Stream one prompt to stdout, with diagnostics on stderr."""
    start = perf_counter()
    first_text = None
    async with open_session(args) as session:
        async for event in session.stream(args.prompt):
            if event.kind == "trace":
                print(f"[trace: {event.data}]", file=sys.stderr)
            elif event.kind == "text":
                if first_text is None:
                    first_text = perf_counter() - start
                print(event.data, end="", flush=True)
            elif event.kind == "tool_start":
                print(f"\n[tool: {event.data.name}]", file=sys.stderr)
    print()
    if args.stats:
        first = f"{first_text:.2f}s" if first_text is not None else "none"
        print(f"first text: {first} | total: {perf_counter() - start:.2f}s", file=sys.stderr)


async def interactive(args: argparse.Namespace) -> None:
    # Import curses only for interactive mode; one-shot use works without it.
    try:
        import curses
        from .tui import run_tui
    except ImportError as error:
        raise RuntimeError("The TUI requires Python with curses (macOS/Linux). Use a quoted prompt for one-shot mode.") from error
    async with open_session(args) as session:
        try:
            await run_tui(args, session)
        except curses.error as error:
            raise RuntimeError(f"Cannot initialize terminal UI: {error}. Check TERM or use one-shot mode.") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="A small streaming Qwen / Ollama harness with a terminal chat UI")
    parser.add_argument("prompt", nargs="?", help="One-shot prompt; omit to open the TUI")
    parser.add_argument("--tui", action="store_true", help="Open the TUI, optionally with an initial prompt")
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    parser.add_argument("--host", default=os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--no-think", action="store_true", help="For models supporting thinking")
    parser.add_argument("--stats", action="store_true", help="Print timing statistics in one-shot mode")
    parser.add_argument("--trace-dir", type=Path, help="Trace directory (default: ROOT/.traces)")
    parser.add_argument("--no-trace", action="store_true", help="Do not save run traces")
    parser.add_argument("--test-command", type=shlex.split,
                        help="Test command, split into argv without a shell (default: current Python -m unittest discover -s tests -v)")
    parser.add_argument("--test-timeout", type=float, default=120,
                        help="Test command timeout in seconds (default: 120)")
    args = parser.parse_args()
    if args.max_tokens < 1 or args.max_turns < 1:
        parser.error("turn and token limits must be positive")
    if not math.isfinite(args.test_timeout) or args.test_timeout <= 0:
        parser.error("test timeout must be a finite positive number")
    if args.test_command == []:
        parser.error("test command must not be empty")
    use_tui = args.tui or args.prompt is None
    if use_tui and not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error("TUI mode needs an interactive terminal; pass a quoted prompt for one-shot mode")
    try:
        asyncio.run(interactive(args) if use_tui else chat(args))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
