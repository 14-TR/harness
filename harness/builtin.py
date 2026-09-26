"""One example tool. Add tools by composing more Tool objects in the CLI."""
import asyncio
from pathlib import Path

from .tools import Tool


def file_tools(root: Path) -> dict[str, Tool]:
    """Build a tool registry bound to one workspace directory.

    The nested function remembers root via a closure. That lets us configure
    the tool without globals or a separate class just to hold a directory.
    """
    root = root.resolve()

    async def read_file(path: str) -> str:
        # Type hints and the JSON schema do not enforce model-supplied values.
        if not isinstance(path, str):
            raise TypeError("path must be a string")
        # Resolve .. segments and existing symlinks before checking containment.
        # Path ancestry avoids string-prefix mistakes such as treating
        # /work/project-other as a child of /work/project.
        target = (root / path).resolve()
        if target != root and root not in target.parents:
            raise ValueError("path must stay inside the workspace")

        # This is a convenience boundary, not an OS sandbox. A hostile local
        # process could change filesystem links between resolution and opening.
        def read() -> str:
            # with closes the handle even on decoding errors or failed reads.
            with target.open("r", encoding="utf-8") as file:
                # Read one extra character to detect truncation without loading
                # the entire file. The limit is characters, not bytes or tokens.
                text = file.read(16001)
            return text[:16000] + ("\n[truncated]" if len(text) > 16000 else "")

        # Ordinary file reads block. Move the small read to a worker thread so
        # the event loop stays responsive. Cancelling this await does NOT kill
        # an already-running thread; keep thread-backed operations bounded.
        return await asyncio.to_thread(read)

    # Register more tools by adding entries. The runner needs no special cases
    # for file readers, calculators, search functions, or any future capability.
    return {"read_file": Tool(
        description="Read a UTF-8 file relative to the workspace; limited to 16000 characters.",
        parameters={"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"], "additionalProperties": False},
        # Pass the function itself, without parentheses. Calling read_file()
        # here would execute it during registration rather than on model demand.
        execute=read_file,
    )}
