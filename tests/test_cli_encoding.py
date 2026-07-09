"""The CLI must not crash on terminals that can't encode emoji.

Windows redirects stdout through cp1252 by default (locale encoding, not
utf-8), and the returns/triage reports contain emoji (⏳, 🔨, …). Printing
must degrade (``errors="replace"``) rather than raise UnicodeEncodeError.
Reproduced by forcing PYTHONIOENCODING=cp1252 in a subprocess — the same
codec a redirected Windows console picks.

    python3 -m unittest tests.test_cli_encoding -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(args: list[str], data_dir: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["INBOX_DATA_DIR"] = data_dir
    env["PYTHONIOENCODING"] = "cp1252"
    return subprocess.run(
        [sys.executable, "-m", "inboxcatalog", *args],
        cwd=REPO, env=env, capture_output=True, timeout=60,
        encoding="cp1252", errors="replace",
    )


class TestCp1252Console(unittest.TestCase):
    def test_reports_survive_cp1252_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ingest = _run(["--profile", "amazon", "--ingest", "--fixtures",
                           "--apply"], tmp)
            self.assertEqual(ingest.returncode, 0, ingest.stderr)
            for report in (["--returns"], ["--triage"], ["--stats"]):
                out = _run(["--profile", "amazon", *report], tmp)
                self.assertEqual(out.returncode, 0,
                                 f"{report} crashed:\n{out.stderr}")
                self.assertNotIn("UnicodeEncodeError", out.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
