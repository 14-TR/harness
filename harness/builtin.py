"""Small workspace navigation and editing tools; no shell or search dependency."""
import asyncio
import difflib
import fnmatch
import os
from pathlib import Path
import stat
import tempfile
from typing import Optional

from .tools import Tool


OUTPUT_LIMIT = 16000
FILE_LIMIT = 1024 * 1024
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", "build", "dist",
             ".traces", ".harness", ".idea", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SKIP_FILES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jsonl", "*.log",
              "*.pyc", ".DS_Store", "credentials.json", "secrets.*")


def _clip(text: str) -> str:
    return text[:OUTPUT_LIMIT] + ("\n[truncated]" if len(text) > OUTPUT_LIMIT else "")


def _path(root: Path, path: str) -> Path:
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a nonempty string")
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise ValueError("path must stay inside the workspace")
    if any(part.casefold() == ".git" for part in (*target.relative_to(root).parts, *Path(path).parts)):
        raise ValueError("Git metadata is not a workspace file")
    return target


def _integer(value: int, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")


def _files(root: Path, directory: Path, pattern: str):
    if not directory.is_dir():
        raise ValueError("path must name a directory")
    if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
        raise ValueError("pattern must be a nonempty glob of at most 200 characters")
    visited = 0
    for parent, dirs, names in os.walk(directory):
        visited += 1 + len(names) + len(dirs)
        if visited > 10000 or len(Path(parent).relative_to(directory).parts) > 64:
            raise ValueError("Workspace scan limit reached; narrow path or pattern")
        dirs[:] = sorted(d for d in dirs if d.casefold() not in SKIP_DIRS and not d.endswith(".egg-info")
                         and not (Path(parent) / d).is_symlink())
        for name in sorted(names):
            target = Path(parent) / name
            if target.is_symlink() or not target.is_file():
                continue
            if name.casefold() == ".git" or any(fnmatch.fnmatchcase(name, p) for p in SKIP_FILES):
                continue
            relative = target.relative_to(directory).as_posix()
            if fnmatch.fnmatchcase(relative, pattern) or (
                pattern.startswith("**/") and fnmatch.fnmatchcase(relative, pattern[3:])
            ):
                yield target, target.relative_to(root).as_posix()


def file_tools(root: Path) -> dict[str, Tool]:
    """Bind tools to one workspace. Path checks are not an OS sandbox."""
    root = root.resolve()

    async def read_file(path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
        target = _path(root, path)
        _integer(start_line, "start_line", 1, 10000000)
        if end_line is not None:
            _integer(end_line, "end_line", start_line, 10000000)
        if not target.is_file():
            raise ValueError("path must name a regular file")

        def read():
            pieces, size, scanned, line = [], 0, 0, 1
            with target.open("r", encoding="utf-8", newline="") as file:
                while end_line is None or line <= end_line:
                    # Limit each read even for files with extremely long lines.
                    chunk = file.readline(OUTPUT_LIMIT + 1 - size if line >= start_line else OUTPUT_LIMIT)
                    if not chunk:
                        break
                    # A size boundary can split CRLF; consume it as one newline.
                    if chunk.endswith("\r"):
                        position = file.tell()
                        following = file.read(1)
                        if following == "\n":
                            chunk += following
                        else:
                            file.seek(position)
                    scanned += len(chunk)
                    if scanned > 16 * FILE_LIMIT:
                        raise ValueError("read scan limit reached (16 MiB characters)")
                    if line >= start_line:
                        pieces.append(chunk)
                        size += len(chunk)
                        if size > OUTPUT_LIMIT:
                            break
                    if chunk.endswith(("\n", "\r")):
                        line += 1
            return _clip("".join(pieces))

        return await asyncio.to_thread(read)

    async def list_files(path: str = ".", pattern: str = "*", limit: int = 200) -> str:
        directory = _path(root, path)
        _integer(limit, "limit", 1, 1000)

        def listing():
            names = []
            for _, name in _files(root, directory, pattern):
                if len(names) == limit:
                    names.append("[more files; narrow path/pattern or increase limit]")
                    break
                names.append(name)
            return _clip("\n".join(names)) or "No matching files."

        return await asyncio.to_thread(listing)

    async def search_workspace(query: str, path: str = ".", pattern: str = "*",
                               context: int = 2, limit: int = 50,
                               case_sensitive: bool = True) -> str:
        directory = _path(root, path)
        if not isinstance(query, str) or not query or len(query) > 1000 or "\n" in query:
            raise ValueError("query must be a single nonempty line of at most 1000 characters")
        _integer(context, "context", 0, 5)
        _integer(limit, "limit", 1, 200)
        if type(case_sensitive) is not bool:
            raise TypeError("case_sensitive must be a boolean")

        def search():
            blocks, matches, scanned, skipped, size = [], 0, 0, 0, 0
            needle = query if case_sensitive else query.casefold()
            for target, name in _files(root, directory, pattern):
                if target.stat().st_size > FILE_LIMIT:
                    skipped += 1
                    continue
                with target.open("rb") as file:
                    data = file.read(FILE_LIMIT + 1)
                scanned += len(data)
                if scanned > 16 * FILE_LIMIT:
                    blocks.append("[scan limit reached; narrow path or pattern]")
                    break
                if len(data) > FILE_LIMIT or b"\0" in data:
                    skipped += 1
                    continue
                try:
                    lines = data.decode("utf-8").splitlines()
                except UnicodeDecodeError:
                    skipped += 1
                    continue
                for index, line in enumerate(lines):
                    if needle not in (line if case_sensitive else line.casefold()):
                        continue
                    if matches == limit:
                        return _clip("\n\n".join(blocks) + "\n[more matches; narrow query or increase limit]")
                    block = "\n".join(f"{name}:{n + 1}: {lines[n]}"
                                      for n in range(max(0, index - context), min(len(lines), index + context + 1)))
                    blocks.append(block)
                    matches += 1
                    size += len(block) + 2
                    if size > OUTPUT_LIMIT:
                        return _clip("\n\n".join(blocks))
            if not blocks:
                blocks.append("No matches.")
            if skipped:
                blocks.append(f"[skipped {skipped} binary, non-UTF-8, or >1 MiB files]")
            return _clip("\n\n".join(blocks))

        return await asyncio.to_thread(search)

    async def apply_patch(path: str, old_text: str, new_text: str) -> str:
        target = _path(root, path)
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            raise TypeError("old_text and new_text must be strings")
        if max(len(old_text), len(new_text)) > FILE_LIMIT:
            raise ValueError("patch text must be at most 1 MiB characters")
        exists = target.exists()
        if exists and (not target.is_file() or target.stat().st_size > FILE_LIMIT):
            raise ValueError("patch target must be a regular UTF-8 file of at most 1 MiB")
        original = ""
        if exists:
            with target.open("r", encoding="utf-8", newline="") as file:
                original = file.read(FILE_LIMIT + 1)
            if len(original) > FILE_LIMIT or "\0" in original:
                raise ValueError("patch target must be a text file of at most 1 MiB")
        if not old_text:
            if original:
                raise ValueError("empty old_text is only allowed for a new or empty file")
            updated = new_text
        else:
            first = original.find(old_text)
            if first < 0 or original.find(old_text, first + 1) >= 0:
                raise ValueError("old_text must match exactly once; read the file and include more context")
            updated = original.replace(old_text, new_text, 1)
        if len(updated.encode("utf-8")) > FILE_LIMIT:
            raise ValueError("updated file must be at most 1 MiB")
        if exists and updated == original:
            return "No changes."
        relative = target.relative_to(root).as_posix()
        diff = "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                       for line in difflib.unified_diff(original.splitlines(keepends=True),
                           updated.splitlines(keepends=True), fromfile=f"a/{relative}", tofile=f"b/{relative}"))
        # A bounded synchronous write avoids mutations continuing in a worker
        # thread after the async tool has been cancelled. Replace atomically.
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                             dir=target.parent, delete=False) as file:
                temporary = Path(file.name)
                file.write(updated)
            if exists:
                temporary.chmod(stat.S_IMODE(target.stat().st_mode))
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return _clip(f"{'Updated' if exists else 'Created'} {relative}\n" + diff)

    return {
        "read_file": Tool("Read a workspace UTF-8 file, optionally an inclusive 1-based line range; max 16000 characters.",
            {"type": "object", "properties": {"path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}},
             "required": ["path"], "additionalProperties": False}, read_file),
        "list_files": Tool("List workspace files recursively, skipping common generated/local files and symlinks. Glob is relative to path.",
            {"type": "object", "properties": {"path": {"type": "string", "default": "."},
                "pattern": {"type": "string", "default": "*"}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}},
             "additionalProperties": False}, list_files),
        "search_workspace": Tool("Find literal text in workspace UTF-8 files <=1 MiB; return paths, line numbers and nearby lines. Glob is relative to path.",
            {"type": "object", "properties": {"query": {"type": "string"}, "path": {"type": "string", "default": "."},
                "pattern": {"type": "string", "default": "*"}, "context": {"type": "integer", "minimum": 0, "maximum": 5},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200}, "case_sensitive": {"type": "boolean", "default": True}},
             "required": ["query"], "additionalProperties": False}, search_workspace),
        "apply_patch": Tool("Edit one workspace file by replacing old_text exactly once with new_text. Read it first; include unique context. Empty old_text creates a new/empty file. Returns a diff.",
            {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}},
             "required": ["path", "old_text", "new_text"], "additionalProperties": False}, apply_patch),
    }
