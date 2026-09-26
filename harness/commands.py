"""A configured test command and two read-only Git views; never a shell tool."""
import asyncio
import os
from pathlib import Path
import signal
import sys
from typing import Optional

from .tools import Tool


async def _execute(root: Path, argv: list[str], env=None) -> tuple[int, str]:
    """Drain output continuously, retain only a small prefix, and reap children."""
    # Shield creation so cancellation cannot lose the handle to a spawned child.
    spawning = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, cwd=root, env=env, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=os.name == "posix",
    ))
    process = None
    try:
        process = await asyncio.shield(spawning)
        output = bytearray()
        truncated = False
        while True:
            chunk = await process.stdout.read(8192)
            if not chunk:
                break
            remaining = 16000 - len(output)
            output.extend(chunk[:remaining])
            truncated |= len(chunk) > remaining
        code = await process.wait()
        text = output.decode("utf-8", errors="replace")
        return code, text + ("\n[truncated]" if truncated else "")
    finally:
        if process is None:
            process = await spawning
        # The leader may have exited while descendants still hold its pipes.
        # Clean up the group even after successful completion of the leader.
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            elif process.returncode is None:
                process.kill()
        except ProcessLookupError:
            pass
        # A full pipe can keep asyncio's process waiter blocked after SIGKILL.
        while await process.stdout.read(8192):
            pass
        await process.wait()


def _result(code: int, output: str) -> str:
    return f"Exit code: {code}\n{output}"


def command_tools(root: Path, test_command: Optional[list[str]] = None,
                  test_timeout: float = 120) -> dict[str, Tool]:
    """Bind execution to this directory and to an argv chosen by the caller."""
    root = Path(root).resolve()
    if test_command is not None and not isinstance(test_command, (list, tuple)):
        raise ValueError("test_command must be a nonempty list of command arguments")
    command = ([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
               if test_command is None else list(test_command))
    if (not command or not command[0]
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)):
        raise ValueError("test_command must be a nonempty list of command arguments")
    if (type(test_timeout) not in (int, float)
            or not 0 < test_timeout < float("inf")):
        raise ValueError("test_timeout must be a finite positive number")

    async def run_tests() -> str:
        return _result(*await _execute(root, command))

    async def git(*args: str) -> str:
        # Inherited Git overrides must not redirect these workspace-only views.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env["GIT_OPTIONAL_LOCKS"] = "0"
        prefix = ["git", "--no-pager", "--literal-pathspecs", "-c", "core.fsmonitor=false"]
        code, top = await _execute(root, prefix + ["rev-parse", "--show-toplevel"], env)
        if code != 0 or Path(top.strip()).resolve() != root:
            raise ValueError("workspace must be the root of a Git working tree")
        return _result(*await _execute(root, prefix + list(args), env))

    async def git_status() -> str:
        return await git("status", "--short", "--untracked-files=normal")

    async def git_diff(staged: bool = False, path: Optional[str] = None) -> str:
        if type(staged) is not bool:
            raise ValueError("staged must be a boolean")
        args = ["diff", "--no-ext-diff", "--no-textconv", "--no-color"]
        if staged:
            args.append("--cached")
        args.append("--")
        if path is not None:
            if not isinstance(path, str) or not path or "\0" in path:
                raise ValueError("path must be a nonempty workspace path")
            try:
                candidate = Path(os.path.abspath(root / path))
                relative = candidate.relative_to(root)
                if candidate != root:
                    candidate.parent.resolve().relative_to(root)
            except ValueError:
                raise ValueError("path must stay inside the workspace") from None
            # Git compares the link itself. Resolving its final component would
            # silently select the target file and miss symlink retargeting.
            args.append(str(relative))
        return await git(*args)

    no_args = {"type": "object", "properties": {}, "additionalProperties": False}
    return {
        "run_tests": Tool(
            "Run the configured workspace test command; return its exit code and bounded output.",
            no_args, run_tests, timeout=test_timeout,
        ),
        "git_status": Tool(
            "Show short Git status for the workspace repository.", no_args, git_status,
        ),
        "git_diff": Tool(
            "Show unstaged changes, or staged changes when staged=true, optionally for one path.",
            {"type": "object", "properties": {
                "staged": {"type": "boolean", "default": False},
                "path": {"type": "string", "description": "Optional workspace-relative file or directory."},
            }, "additionalProperties": False}, git_diff,
        ),
    }
