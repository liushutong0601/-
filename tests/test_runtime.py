from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autoquiz.runtime import ensure_debug_edge


class RuntimeTests(unittest.TestCase):
    def test_existing_debug_endpoint_is_reused_without_launching_edge(self) -> None:
        with (
            patch("autoquiz.runtime.debug_endpoint_ready", return_value=True),
            patch("autoquiz.runtime.subprocess.Popen") as popen,
        ):
            self.assertEqual(ensure_debug_edge(), "connected")
        popen.assert_not_called()

    def test_launch_uses_separate_profile_and_edge_new_tab(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_dir = Path(temporary_directory)
            with (
                patch("autoquiz.runtime.debug_endpoint_ready", side_effect=[False, True]),
                patch("autoquiz.runtime.find_edge_executable", return_value=Path("C:/Edge/msedge.exe")),
                patch("autoquiz.runtime.app_data_dir", return_value=data_dir),
                patch("autoquiz.runtime.is_frozen", return_value=True),
                patch("autoquiz.runtime.subprocess.Popen") as popen,
            ):
                self.assertEqual(ensure_debug_edge(), "launched")

            arguments = popen.call_args.args[0]
            self.assertIn("--remote-debugging-port=9222", arguments)
            self.assertIn(f"--user-data-dir={data_dir / 'edge-profile'}", arguments)
            self.assertIn("--no-first-run", arguments)
            self.assertEqual(arguments[-1], "edge://newtab")
            self.assertFalse(any("baidu" in argument.lower() for argument in arguments))


if __name__ == "__main__":
    unittest.main()
