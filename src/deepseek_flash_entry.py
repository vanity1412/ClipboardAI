"""Public executable; API credentials are configured locally by the user."""
import sys
import windows_native as native
from cloud_client import CloudClient, load_cloud_config
from default_config import load_defaults


if __name__ == "__main__":
    native.AIClient = CloudClient
    native.read_config = lambda: load_cloud_config(native.ROOT, load_defaults(native.ROOT))
    try:
        if '--verify-region' in sys.argv:
            from verify_region_capture import run
            run(native.ROOT)
        elif '--verify-quick' in sys.argv:
            import json
            from verify_quick_flow import run
            report = run(native)
            (native.ROOT / 'quick-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        elif '--verify-chat' in sys.argv:
            from verify_chat_flow import run
            run(native)
        elif '--verify-zoo' in sys.argv:
            from verify_api_zoo import run
            run(native.ROOT)
        elif '--verify-prompts' in sys.argv:
            from verify_prompt_ui import run
            run(native.ROOT)
        elif "--verify-ui" in sys.argv:
            from verify_flash_flow import run
            run(native, verify_hotkeys=False)
        elif "--verify-capture" in sys.argv:
            from verify_flash_flow import run_capture
            run_capture(native)
        elif "--verify-api" in sys.argv:
            from verify_flash_flow import run_live
            run_live(native)
        elif "--verify-flow" in sys.argv:
            from verify_flash_flow import run
            run(native)
        else:
            app = native.WindowsApp("--self-test" in sys.argv)
            if '--open-zoo' in sys.argv:
                app.command(220)
            app.run()
    except Exception as exc:
        native.log_event("startup_failed", error_type=type(exc).__name__)
        raise
