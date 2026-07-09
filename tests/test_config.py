"""Offline tests for data-dir resolution — the platform state-dir fallback that
makes a pip/pipx install work outside a repo checkout.

Precedence under test (see ``config.resolve_data_dir``):
  1. ``$INBOX_DATA_DIR`` always wins.
  2. A repo checkout with an existing ``data/`` keeps using it (unchanged).
  3. Otherwise the per-user platform state dir.

    python3 -m unittest tests.test_config -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from inboxcatalog import config  # noqa: E402


def _clean_env(**overrides):
    """os.environ with INBOX_DATA_DIR / XDG_STATE_HOME removed, plus overrides."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("INBOX_DATA_DIR", "XDG_STATE_HOME")}
    env.update(overrides)
    return env


class TestResolveDataDir(unittest.TestCase):
    def test_env_override_always_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Even with a repo-local data/ present, the env override takes it.
            with mock.patch.object(config, "ROOT", Path(tmp)), \
                 mock.patch.dict(os.environ, _clean_env(INBOX_DATA_DIR="/some/where"),
                                 clear=True):
                (Path(tmp) / "data").mkdir()
                path, source = config.resolve_data_dir()
        self.assertEqual(path, Path("/some/where"))
        self.assertEqual(source, "$INBOX_DATA_DIR")

    def test_repo_checkout_with_existing_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_data = Path(tmp) / "data"
            repo_data.mkdir()
            with mock.patch.object(config, "ROOT", Path(tmp)), \
                 mock.patch.dict(os.environ, _clean_env(), clear=True):
                path, source = config.resolve_data_dir()
            self.assertEqual(path, repo_data)
            self.assertEqual(source, "repo checkout")

    def test_no_data_dir_falls_back_to_platform(self):
        # ROOT with no data/ subdir simulates a pip install in site-packages.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, "ROOT", Path(tmp)), \
                 mock.patch.dict(os.environ, _clean_env(), clear=True):
                path, source = config.resolve_data_dir()
                self.assertEqual(path, config.platform_state_dir())
                self.assertEqual(source, "platform state dir")


class TestPlatformStateDir(unittest.TestCase):
    def test_macos(self):
        with mock.patch.object(config.sys, "platform", "darwin"), \
             mock.patch.dict(os.environ, _clean_env(), clear=True):
            self.assertEqual(
                config.platform_state_dir(),
                Path.home() / "Library" / "Application Support" / "inbox-catalog",
            )

    def test_linux_respects_xdg_state_home(self):
        with mock.patch.object(config.sys, "platform", "linux"), \
             mock.patch.dict(os.environ,
                             _clean_env(XDG_STATE_HOME="/xdg/state"), clear=True):
            self.assertEqual(config.platform_state_dir(),
                             Path("/xdg/state") / "inbox-catalog")

    def test_linux_default_without_xdg(self):
        with mock.patch.object(config.sys, "platform", "linux"), \
             mock.patch.dict(os.environ, _clean_env(), clear=True):
            self.assertEqual(
                config.platform_state_dir(),
                Path.home() / ".local" / "state" / "inbox-catalog",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
