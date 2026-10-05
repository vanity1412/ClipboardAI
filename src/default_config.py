"""Public defaults. Credentials never belong in source or the executable."""
import os
from pathlib import Path

def load_defaults(root):
    config = {"DEEPSEEK_API_KEY": "", "MAX_CLIPBOARD_CHARS": "100000"}
    path = Path(root) / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in config:
                config[key.strip()] = value.strip().strip("\"'")
    for key in config:
        if key in os.environ:
            config[key] = os.environ[key]
    return config
