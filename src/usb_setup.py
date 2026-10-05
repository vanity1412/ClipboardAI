"""Install the bundled Ollama runtime and model into the current user profile."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
from urllib.request import urlopen


def child_path(root, name):
    # MSIX file-system virtualization can resolve a child into LocalCache even
    # when its parent resolves to AppData\Local. Validate the lexical path;
    # let Windows consistently map reads/writes to the current user's storage.
    root = Path(os.path.abspath(root))
    relative = Path(name)
    if relative.is_absolute() or relative.drive or '..' in relative.parts:
        raise ValueError("Invalid package path")
    path = Path(os.path.abspath(root / relative))
    try:
        path.relative_to(root)
    except ValueError:
        raise ValueError("Invalid package path") from None
    if path == root:
        raise ValueError("Invalid package path")
    return path


def install_payload(package_root, target, progress=lambda _: None):
    payload = package_root / "payload"
    manifest_bytes = (payload / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    package_id = hashlib.sha256(manifest_bytes).hexdigest()
    target = target / package_id[:16]
    marker = target / "installed.json"
    files = manifest["files"]
    if marker.exists() and all(child_path(target, item["path"]).exists() and
                               child_path(target, item["path"]).stat().st_size == item["size"] for item in files):
        return target
    target.mkdir(parents=True, exist_ok=True)
    needed = sum(item["size"] for item in files) + 256 * 1024 * 1024
    if shutil.disk_usage(target).free < needed:
        raise RuntimeError("Not enough disk space for bundled Ollama runtime and model")
    total = sum(item["size"] for item in files)
    copied = 0
    for item in files:
        destination = child_path(target, item["path"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        digest = hashlib.sha256()
        count = 0
        with temporary.open("wb") as output:
            for part in item["parts"]:
                with child_path(payload, part).open("rb") as source:
                    while chunk := source.read(8 * 1024 * 1024):
                        output.write(chunk)
                        digest.update(chunk)
                        count += len(chunk)
                        copied += len(chunk)
                        progress(int(copied * 100 / max(total, 1)))
        if count != item["size"] or digest.hexdigest() != item["sha256"]:
            raise RuntimeError("USB package checksum failed; recreate the package")
        temporary.replace(destination)
    marker.write_text(json.dumps({"package_id": package_id}), encoding="utf-8")
    return target


def prepare_server(package_root):
    # The CLI-only runtime is installed privately; no registry, service, or PATH changes.
    local_root = Path(os.environ["LOCALAPPDATA"]) / "ClipboardAI_USB"
    progress_file = package_root / "setup-status.txt"
    last_progress = [-1]

    def progress(percent):
        if percent != last_progress[0]:
            last_progress[0] = percent
            progress_file.write_text(f"Preparing Ollama and model: {percent}%", encoding="utf-8")

    target = install_payload(package_root, local_root, progress)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    model_name = json.loads((package_root / "payload" / "manifest.json").read_text(encoding="utf-8"))["model"]
    env.update(OLLAMA_HOST=f"127.0.0.1:{port}", OLLAMA_MODELS=str(target / "models"),
               OLLAMA_NO_CLOUD="1", OLLAMA_CONTEXT_LENGTH="8192")
    exe = target / "runtime" / "ollama.exe"
    process = subprocess.Popen([str(exe), "serve"], env=env, cwd=exe.parent,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError("Bundled Ollama could not start on this PC")
            try:
                with urlopen(base + "/api/tags", timeout=1) as response:
                    models = json.loads(response.read()).get("models", [])
                if any(m["name"] == model_name for m in models):
                    progress_file.write_text("Ready: Ollama only; F8 sends clipboard, F9 resends session. Copy alone does not send.", encoding="utf-8")
                    return {"OLLAMA_BASE_URL": base, "OLLAMA_MODEL": model_name}, process
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Timed out starting bundled Ollama")
    except Exception:
        process.terminate()
        raise
