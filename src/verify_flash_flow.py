"""Packaged EXE verification; generated image/mock API, no desktop upload."""
import ctypes as C
from ctypes import wintypes as W
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
import time
from unittest.mock import Mock, patch
from PIL import Image


def run_live(native):
    """Two real API calls using only a generated public sample, never desktop."""
    from PIL import ImageDraw, ImageFont
    from deepseek_client import DeepSeekClient
    report = {"ok": False, "desktop_capture": False, "synthetic_image": True}
    try:
        image = Image.new("RGB", (1000, 480), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
        draw.multiline_text((20, 20), "SUM\nGiven two integers a and b, print a+b.\nInput: two integers a b.\n(continued on next screen)", font=font, fill="black", spacing=10)
        png = BytesIO()
        image.save(png, format="PNG")
        client = DeepSeekClient(dict(native.read_config(), DEEPSEEK_MAX_TOKENS="8192", DEEPSEEK_TIMEOUT_S="90"))
        client.cancel_event = threading.Event()
        first = client.read_problem(png.getvalue())
        assert first["readable"] and first["text"], "First partial screenshot was discarded"
        assert not first["complete"], "Missing constraints were invented"
        image2 = Image.new("RGB", (1000, 480), "white")
        ImageDraw.Draw(image2).multiline_text((20, 20), "SUM (continued)\nConstraints: 1 <= a,b <= 1000000.\nOutput: one integer, the sum a+b.\nSample Input: 2 3\nSample Output: 5\nTime limit: 1 second. Memory limit: 256 MB.", font=font, fill="black", spacing=10)
        png2 = BytesIO()
        image2.save(png2, format="PNG")
        second = client.read_problem(png2.getvalue(), first["text"])
        assert second["complete"], "Second screenshot was not merged"
        text = second["text"]
        assert "1000000" in text and "2 3" in text, "OCR lost limits/sample"
        answer, provider = client.ask(text)
        assert "main" in answer and "#include" in answer, "Missing C++ program"
        report.update(ok=True, ocr=True, partial_preserved=True, two_images_merged=True, code_response=True, response_characters=len(answer), provider=provider)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
    (native.ROOT / "live-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise RuntimeError("Live API verification failed")


def run(native, verify_hotkeys=True):
    from verify_chat_flow import run as verify_chat
    verify_chat(native)
    report = json.loads((native.ROOT / 'chat-verification.json').read_text(encoding='utf-8'))
    (native.ROOT / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def run_capture(native):
    """Capture only our own foreground synthetic test window; never upload it."""
    original_root = native.ROOT
    app, window = None, None
    report = {"ok": False, "network_calls": 0, "synthetic_window": True}
    try:
        with tempfile.TemporaryDirectory(prefix="ClipboardAI_CaptureTest_") as directory:
            native.ROOT = Path(directory)
            app = native.WindowsApp(self_test=True)
            text = "SYNTHETIC CAPTURE TEST\r\nSUM\r\nGiven a and b, print a+b.\r\nInput: a b, 1 <= a,b <= 1000000.\r\nOutput: the sum.\r\nSample: 2 3 -> 5."
            window = app.user.CreateWindowExW(8, "STATIC", text, 0x80800000, 80, 80, 680, 340, None, None, app.instance, None)
            assert window, "Test window could not be created"
            app.user.ShowWindow(window, 5)
            assert app.user.SetForegroundWindow(window), "Test window could not take focus"
            app.user.UpdateWindow.argtypes = [W.HWND]
            app.user.UpdateWindow.restype = W.BOOL
            app.user.UpdateWindow(window)
            from screen_capture import capture_foreground_png
            png = capture_foreground_png(window)
            image = Image.open(BytesIO(png)).convert("RGB")
            extrema = image.convert("L").getextrema()
            assert image.width > 100 and image.height > 100 and extrema[0] < extrema[1], "Captured image is blank"
            report.update(ok=True, png_bytes=len(png), width=image.width, height=image.height, nonblank=True)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, error=str(exc))
    finally:
        if app:
            if window:
                app.user.DestroyWindow(window)
            app.user.DestroyWindow(app.hwnd)
            app.kernel.CloseHandle(app.mutex)
        native.ROOT = original_root
    (original_root / "capture-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not report["ok"]:
        raise RuntimeError("Real capture verification failed")
