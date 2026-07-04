"""Offline tests for the friendly onboarding surfaces: the no-arg welcome and
the `doctor` setup check. Both must run in any environment (deps present or not,
mailbox or not) and never touch a real catalog or a secret.

    python3 -m unittest tests.test_cli -v
"""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog import cli, config  # noqa: E402


class TestOnboardingCommands(unittest.TestCase):
    def test_welcome_on_no_args(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main([])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Inbox Catalog", out)
        self.assertIn("doctor", out)          # points at the setup check
        self.assertIn("--fixtures", out)      # points at the zero-setup demo

    def test_doctor_runs_clean_and_isolated(self):
        tmp = Path(tempfile.mkdtemp())
        buf = io.StringIO()
        # Isolate the DB so the check never reads/migrates a real catalog.
        with mock.patch.object(config, "DATA_DIR", tmp), \
             mock.patch.object(config, "DB_PATH", tmp / "catalog.db"), \
             mock.patch.object(config, "IMAGES_DIR", tmp / "images"), \
             redirect_stdout(buf):
            rc = cli.main(["doctor"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("setup check", out)
        self.assertIn("Read real mail", out)
        self.assertIn("Next", out)
        # Captured (non-TTY) output must be plain — no raw ANSI escapes.
        self.assertNotIn("\033[", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
