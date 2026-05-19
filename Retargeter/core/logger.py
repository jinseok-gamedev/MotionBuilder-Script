"""Tee logger that writes to MotionBuilder's console and an optional file.

The pipeline emits one line per phase per take so the operator can grep the
log file post-mortem. The logger is intentionally simple: no dependencies,
no third-party logging frameworks, no module-global state beyond an open
file handle so a long batch run cannot leak observers.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Callable, List, Optional


class Logger:
    """Append-only logger with multiple sinks.

    Sinks (callables ``fn(str) -> None``) can be attached at any time -- the
    UI registers one that pushes lines into its QPlainTextEdit, while the
    pipeline attaches a file sink for the duration of the run.
    """

    def __init__(self, also_print: bool = True) -> None:
        self._sinks: List[Callable[[str], None]] = []
        self._file = None
        self._also_print = also_print

    def add_sink(self, sink: Callable[[str], None]) -> None:
        self._sinks.append(sink)

    def remove_sink(self, sink: Callable[[str], None]) -> None:
        try:
            self._sinks.remove(sink)
        except ValueError:
            pass

    def open_file(self, path: str) -> None:
        self.close_file()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._file = open(path, "w", encoding="utf-8")
        self._file.write(f"# Retargeter run started {datetime.now().isoformat()}\n")
        self._file.flush()

    def close_file(self) -> None:
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            self._file = None

    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", msg)

    def error(self, msg: str) -> None:
        self._emit("ERROR", msg)

    def _emit(self, level: str, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{stamp}] [{level}] {msg}"
        if self._also_print:
            try:
                print(line)
                sys.stdout.flush()
            except Exception:
                pass
        if self._file is not None:
            try:
                self._file.write(line + "\n")
                self._file.flush()
            except Exception:
                pass
        for sink in list(self._sinks):
            try:
                sink(line)
            except Exception:
                # Sinks must never break the pipeline; swallow and continue.
                pass


def make_run_log_path(out_dir: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"_retarget_log_{stamp}.txt")
