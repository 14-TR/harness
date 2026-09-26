"""Small, persistent project notes, kept separate from workspace code."""
import os
from pathlib import Path
import re
import tempfile

from .tools import Tool


def note_tools(root: Path) -> dict[str, Tool]:
    """Build note tools scoped to .harness/notes in this workspace."""
    root = root.resolve()
    directory = root / ".harness" / "notes"

    def check_directory() -> None:
        # Refuse redirects even within the workspace: a .harness -> .git link
        # must not turn a saved note into a write to repository metadata.
        for path in (directory.parent, directory):
            if path.is_symlink() or path.resolve() != path:
                raise ValueError("note storage must not contain symlinks")
            if path.exists() and not path.is_dir():
                raise ValueError("note storage must be a directory")

    def note_path(name: str) -> Path:
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name) is None:
            raise ValueError("name must contain 1-80 letters, digits, underscores, or hyphens")
        check_directory()
        path = directory / (name + ".md")
        if path.is_symlink() or path.resolve() != path:
            raise ValueError("notes must not be symlinks")
        if path.exists() and not path.is_file():
            raise ValueError("note must be a regular file")
        return path

    async def save_note(name: str, content: str) -> str:
        path = note_path(name)
        if not isinstance(content, str):
            raise TypeError("content must be a string")
        if len(content) > 16000:
            raise ValueError("content must be at most 16000 characters")
        directory.mkdir(parents=True, exist_ok=True)
        # The bounded write is synchronous so cancellation cannot leave a
        # background thread writing after invoke() has reported a timeout.
        # Like the other filesystem tools, this is not an OS sandbox against
        # another local process changing links between validation and opening.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                             prefix=".note-", delete=False) as file:
                temporary = Path(file.name)
                file.write(content)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return f"Saved note '{name}'."

    async def read_notes(name: str = "") -> str:
        if not isinstance(name, str):
            raise TypeError("name must be a string")
        if name:
            path = note_path(name)
            with path.open(encoding="utf-8") as file:
                content = file.read(16001)
            return content[:16000] + ("\n[truncated]" if len(content) > 16000 else "")
        check_directory()
        if not directory.exists():
            return "No saved notes."
        names = []
        truncated = False
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                # Bound both directory scanning and model-visible output.
                if index >= 1000 or len(names) >= 100:
                    truncated = True
                    break
                path = Path(entry.name)
                if (path.suffix == ".md" and
                        re.fullmatch(r"[A-Za-z0-9_-]{1,80}", path.stem) and
                        entry.is_file(follow_symlinks=False)):
                    names.append(path.stem)
        result = "\n".join(sorted(names)) or "No saved notes."
        return result + ("\n[truncated]" if truncated else "")

    name_schema = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,80}$"}
    return {
        "save_note": Tool(
            "Save or replace a project note in .harness/notes; maximum 16000 characters.",
            {"type": "object", "properties": {
                "name": name_schema, "content": {"type": "string", "maxLength": 16000}},
             "required": ["name", "content"], "additionalProperties": False},
            save_note,
        ),
        "read_notes": Tool(
            "Read a saved project note, or omit name to list available note names.",
            {"type": "object", "properties": {"name": {"type": "string"}},
             "additionalProperties": False},
            read_notes,
        ),
    }
