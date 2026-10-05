import unittest
from unittest.mock import Mock, patch
from io import BytesIO
from PIL import Image
from screen_capture import capture_foreground_png


class CaptureTests(unittest.TestCase):
    def user(self):
        user = Mock()
        user.GetForegroundWindow.return_value = 123
        user.IsIconic.return_value = False
        user.SetThreadDpiAwarenessContext.return_value = 7
        user.GetSystemMetrics.side_effect = lambda n: {76: 0, 77: 0, 78: 1000, 79: 800}[n]
        def rect(hwnd, ref):
            ref._obj.left, ref._obj.top = -5, 20
            ref._obj.right, ref._obj.bottom = 500, 900
            return True
        user.GetWindowRect.side_effect = rect
        return user

    def test_capture_clips_foreground_bounds_and_returns_png(self):
        user = self.user()
        image = Image.new("RGB", (500, 780), "white")
        with patch("screen_capture.C.WinDLL", return_value=user), patch("PIL.ImageGrab.grab", return_value=image) as grab:
            png = capture_foreground_png()
        grab.assert_called_once_with(bbox=(0, 20, 500, 800), all_screens=True)
        self.assertEqual(Image.open(BytesIO(png)).size, (500, 780))
        self.assertEqual(user.SetThreadDpiAwarenessContext.call_args.args, (7,))

    def test_minimized_window_never_captures_desktop(self):
        user = self.user()
        user.IsIconic.return_value = True
        with patch("screen_capture.C.WinDLL", return_value=user), patch("PIL.ImageGrab.grab") as grab:
            with self.assertRaises(RuntimeError):
                capture_foreground_png()
        grab.assert_not_called()
        self.assertEqual(user.SetThreadDpiAwarenessContext.call_args.args, (7,))

    def test_changed_window_is_not_captured_by_accident(self):
        with patch("screen_capture.C.WinDLL", return_value=self.user()), patch("PIL.ImageGrab.grab") as grab:
            with self.assertRaises(RuntimeError):
                capture_foreground_png(999)
        grab.assert_not_called()

    def test_win7_without_thread_dpi_api_can_capture(self):
        user = self.user()
        del user.SetThreadDpiAwarenessContext
        image = Image.new("RGB", (500, 780), "white")
        with patch("screen_capture.C.WinDLL", return_value=user), patch("PIL.ImageGrab.grab", return_value=image):
            png = capture_foreground_png()
        self.assertEqual(Image.open(BytesIO(png)).size, (500, 780))

    def test_black_capture_is_rejected_but_dark_code_with_text_is_kept(self):
        image = Image.new('RGB', (500, 780), (2, 2, 2))
        with patch('screen_capture.C.WinDLL', return_value=self.user()), patch('PIL.ImageGrab.grab', return_value=image):
            with self.assertRaisesRegex(RuntimeError, 'toàn đen'):
                capture_foreground_png()
            image.putpixel((100, 100), (200, 200, 200))
            self.assertTrue(capture_foreground_png().startswith(b'\x89PNG'))

    def test_secondary_monitor_negative_coordinates_keep_physical_bounds(self):
        user = self.user()
        user.GetSystemMetrics.side_effect = lambda n: {76: -1920, 77: -200, 78: 4480, 79: 1640}[n]
        def rect(hwnd, ref):
            ref._obj.left, ref._obj.top = -1800, -100
            ref._obj.right, ref._obj.bottom = -300, 900
            return True
        user.GetWindowRect.side_effect = rect
        with patch('screen_capture.C.WinDLL', return_value=user), patch('PIL.ImageGrab.grab', return_value=Image.new('RGB', (1500, 1000), 'white')) as grab:
            capture_foreground_png()
        grab.assert_called_once_with(bbox=(-1800, -100, -300, 900), all_screens=True)
        self.assertEqual(user.SetThreadDpiAwarenessContext.call_args.args, (7,))
