"""Atomic provider journals with platform-aware directory durability."""

from __future__ import annotations

import json
import os
from pathlib import Path


def save(
    path: Path, value: dict, *, private: bool = False, ensure_ascii: bool = False
) -> None:
    # Own only provider records, never the core record or authority state.
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as output:
        if private:
            os.chmod(temp, 0o600)
        json.dump(value, output, ensure_ascii=ensure_ascii)
        output.flush()
        os.fsync(output.fileno())
    temp.replace(path)
    # Windows CRT cannot open directories. Keep file fsync and atomic replace;
    # on POSIX, a directory-sync failure still propagates to the caller.
    if os.name == "posix":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
