"""Save one run as JSON Lines: one independently readable JSON object per line."""
from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from time import perf_counter
from uuid import uuid4


class TraceRecorder:
    """Observe events at the CLI boundary without changing the agent loop.

    Pass None to disable recording. The context manager always closes the file
    and records whether the run succeeded, failed, or was cancelled. It never
    suppresses the original exception. A forced process kill cannot run cleanup.
    """

    def __init__(self, directory: Path | None, metadata: dict):
        self.directory = directory
        self.metadata = metadata
        self.run_id = uuid4().hex
        self.path = None
        self.file = None
        self.sequence = 0
        self.started = perf_counter()

    def __enter__(self):
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.path = self.directory / f"{self.run_id}.jsonl"
            # Exclusive creation prevents overwrites. Line buffering keeps a
            # partial trace readable if execution fails midway through a run.
            self.file = self.path.open("x", encoding="utf-8", buffering=1)
            try:
                self.record("run_start", self.metadata)
            except BaseException:
                self.file.close()
                raise
        return self

    def record(self, kind: str, data=None):
        if self.file is None:
            return
        # Calls and messages are dataclasses; asdict also converts nested ones.
        def encode(value):
            if is_dataclass(value) and not isinstance(value, type):
                return asdict(value)
            raise TypeError(f"Cannot trace {type(value).__name__}")

        row = {
            "version": 1,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": perf_counter() - self.started,
            "kind": kind,
            "data": data,
        }
        self.file.write(json.dumps(row, default=encode, ensure_ascii=False) + "\n")
        self.sequence += 1

    def __exit__(self, error_type, error, traceback):
        status = "completed"
        try:
            if error is not None:
                status = "cancelled" if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)) else "failed"
                self.record("error", {"type": type(error).__name__, "message": str(error)})
            self.record("run_end", {"status": status})
        except Exception:
            # If recording itself fails during cleanup, retain the original
            # run failure. On an otherwise successful run, surface the I/O error.
            if error is None:
                raise
        finally:
            if self.file is not None:
                self.file.close()
        return False
