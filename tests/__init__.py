"""Test-suite process guardrails.

Keep implicit database access away from the local production files. Individual
tests may still override these paths with ``patch.dict`` for isolated fixtures.
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import tempfile


_TEST_ROOT = Path(tempfile.gettempdir()) / f"weatherbot-test-suite-{os.getpid()}"
_DATABASES = {
    "V3_DB_PATH": _TEST_ROOT / "weatherbot_v3.db",
    "WEATHERBOT_DB_PATH": _TEST_ROOT / "weatherbot.db",
}

def ensure_test_environment() -> None:
    """Force default database access into this test process's temp directory."""
    _TEST_ROOT.mkdir(parents=True, exist_ok=True)
    for env_name, path in _DATABASES.items():
        os.environ[env_name] = str(path)


ensure_test_environment()


def _cleanup_test_databases() -> None:
    for _path in _DATABASES.values():
        for _suffix in ("", "-wal", "-shm"):
            try:
                Path(f"{_path}{_suffix}").unlink(missing_ok=True)
            except OSError:
                pass
    try:
        _TEST_ROOT.rmdir()
    except OSError:
        pass


atexit.register(_cleanup_test_databases)
