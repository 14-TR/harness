"""Persistence and filesystem boundaries for saved project notes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from harness.notes import note_tools
from harness.tools import invoke
from harness.types import Call


class NoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.tools = note_tools(self.root)

    async def call(self, tool_name, **arguments):
        return await invoke(Call(tool_name, arguments), self.tools)

    async def test_persistence_overwrite_and_listing(self):
        self.assertEqual(await self.call("read_notes"), "No saved notes.")
        self.assertFalse((self.root / ".harness").exists())
        await self.call("save_note", name="decisions", content="Use unittest.")
        # Rebuilding the registry must preserve notes from previous runs.
        self.tools = note_tools(self.root)
        self.assertEqual(await self.call("read_notes", name="decisions"), "Use unittest.")
        await self.call("save_note", name="decisions", content="Use unittest and temp dirs.")
        await self.call("save_note", name="architecture", content="Small modules.")
        self.assertEqual(await self.call("read_notes"), "architecture\ndecisions")
        self.assertEqual(await self.call("read_notes", name="decisions"),
                         "Use unittest and temp dirs.")
        self.assertEqual(sorted(p.name for p in (self.root / ".harness/notes").iterdir()),
                         ["architecture.md", "decisions.md"])

    async def test_bounds_and_types(self):
        await self.call("save_note", name="large", content="é" * 16000)
        self.assertEqual(await self.call("read_notes", name="large"), "é" * 16000)
        result = await self.call("save_note", name="large", content="x" * 16001)
        self.assertIn("at most 16000", result)
        self.assertEqual(await self.call("read_notes", name="large"), "é" * 16000)
        self.assertIn("TypeError", await self.call("save_note", name="wrong", content=42))
        self.assertIn("TypeError", await self.call("read_notes", name=42))
        # An externally edited note remains bounded when read.
        (self.root / ".harness/notes/large.md").write_text("x" * 17000)
        self.assertEqual(await self.call("read_notes", name="large"),
                         "x" * 16000 + "\n[truncated]")

    async def test_names_cannot_be_paths(self):
        for name in ("", "..", "../outside", "/tmp/outside", "a/b", "a\\b",
                     ".git", "name.md", "with space", "é", "a" * 81, None):
            with self.subTest(name=name):
                self.assertIn("Tool error:", await self.call("save_note", name=name, content="no"))
                if name != "":
                    self.assertIn("Tool error:", await self.call("read_notes", name=name))
        self.assertFalse((self.root / ".harness").exists())
        self.assertIn("Saved note", await self.call("save_note", name="a" * 80, content=""))

    async def test_rejects_symlink_storage_and_note_files(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside).resolve()
            (self.root / ".harness").symlink_to(target, target_is_directory=True)
            self.assertIn("symlink", await self.call("save_note", name="escape", content="bad"))
            self.assertIn("symlink", await self.call("read_notes"))
            self.assertEqual(list(target.iterdir()), [])
            (self.root / ".harness").unlink()
            (self.root / ".harness").mkdir()
            notes = self.root / ".harness/notes"
            notes.symlink_to(target, target_is_directory=True)
            self.assertIn("symlink", await self.call("save_note", name="escape", content="bad"))
            notes.unlink()
            notes.mkdir()
            outside_note = target / "outside.md"
            outside_note.write_text("original")
            (notes / "escape.md").symlink_to(outside_note)
            self.assertIn("symlink", await self.call("save_note", name="escape", content="bad"))
            self.assertIn("symlink", await self.call("read_notes", name="escape"))
            self.assertEqual(outside_note.read_text(), "original")
            self.assertEqual(await self.call("read_notes"), "No saved notes.")

    async def test_rejects_redirect_into_repository_metadata(self):
        metadata = self.root / ".git"
        metadata.mkdir()
        (self.root / ".harness").symlink_to(metadata, target_is_directory=True)
        self.assertIn("symlink", await self.call("save_note", name="config", content="bad"))
        self.assertEqual(list(metadata.iterdir()), [])

    async def test_failed_replace_keeps_original_and_cleans_temporary(self):
        await self.call("save_note", name="decision", content="original")
        with patch("harness.notes.os.replace", side_effect=OSError("disk failure")):
            self.assertIn("disk failure", await self.call("save_note", name="decision", content="new"))
        self.assertEqual(await self.call("read_notes", name="decision"), "original")
        self.assertEqual([p.name for p in (self.root / ".harness/notes").iterdir()], ["decision.md"])


if __name__ == "__main__":
    unittest.main()
