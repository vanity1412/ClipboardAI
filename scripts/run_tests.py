"""Windows unit tests; desktop integration is opt-in, never a live API call."""
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

source = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(source))
os.chdir(source)
import windows_native as native

def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item

suite = unittest.defaultTestLoader.discover("tests")
# GitHub runners have no user desktop. This test opens windows/captures pixels.
# Run manually with --desktop on a Windows desktop to include it.
if "--desktop" not in sys.argv:
    for case in cases(suite):
        if case.id().endswith("PanelTests.test_real_panel_image_clipboard_modes_and_conversation"):
            type(case).__unittest_skip__ = True
            type(case).__unittest_skip_why__ = "Desktop integration: run scripts/run_tests.py --desktop locally"
user, kernel, shell = native.setup_winapi()
with patch.object(native, "setup_winapi", return_value=(user, kernel, SimpleNamespace(Shell_NotifyIconW=Mock(return_value=True)))):
    result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
