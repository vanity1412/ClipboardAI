"""Per-user official Codex CLI bootstrap. No elevation, PATH edits or login."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.request import Request, urlopen
import zipfile

VERSION = '0.162.1'
ASSET = 'codex-x86_64-pc-windows-msvc.exe.zip'
URL = f'https://github.com/openai/codex/releases/download/rust-v{VERSION}/{ASSET}'
SHA256 = '4b17206ed3045de64064b2016627d51fba3bde386d03838312054882a64d781d'
MAX_DOWNLOAD = 200 * 1024 * 1024
MAX_EXTRACTED = 700 * 1024 * 1024
_lock = threading.Lock()


def install_root():
    base = os.environ.get('LOCALAPPDATA')
    if not base:
        raise RuntimeError('Không tìm thấy LOCALAPPDATA để cài Codex CLI.')
    return Path(base) / 'ClipboardAI' / 'codex-cli'


def find_existing(root=None):
    if root is not None:
        executable = Path(root) / VERSION / 'codex.exe'
        return str(executable) if executable.is_file() and (executable.parent / '.complete').is_file() else None
    executable = shutil.which('codex.exe' if os.name == 'nt' else 'codex')
    if executable:
        return executable
    if os.name == 'nt':
        base = os.environ.get('LOCALAPPDATA')
        if base:
            candidates = list((Path(base) / 'OpenAI' / 'Codex' / 'bin').glob('*/codex.exe'))
            if candidates:
                return str(max(candidates, key=lambda p: p.stat().st_mtime))
            root = install_root()
            executable = root / VERSION / 'codex.exe'
            if executable.is_file() and (executable.parent / '.complete').is_file():
                return str(executable)
    return None


def _check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise InterruptedError('Đã hủy cài Codex CLI.')
    if time.monotonic() >= deadline:
        raise TimeoutError('Hết thời gian cài Codex CLI; kiểm tra mạng rồi thử lại.')


def _download(destination, cancel, deadline):
    digest, total = hashlib.sha256(), 0
    request = Request(URL, headers={'User-Agent': 'ClipboardAI-Codex-Setup'})
    with urlopen(request, timeout=min(15, max(.1, deadline - time.monotonic()))) as response:
        with destination.open('wb') as output:
            while True:
                _check(cancel, deadline)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD:
                    raise RuntimeError('Bộ cài Codex CLI vượt giới hạn dung lượng.')
                digest.update(chunk)
                output.write(chunk)
    if digest.hexdigest() != SHA256:
        raise RuntimeError('Checksum Codex CLI không đúng; đã bỏ bộ tải xuống.')


def _extract(archive_path, destination, cancel, deadline):
    with zipfile.ZipFile(archive_path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_EXTRACTED:
            raise RuntimeError('Bộ cài Codex CLI vượt giới hạn giải nén.')
        for item in archive.infolist():
            _check(cancel, deadline)
            relative = PurePosixPath(item.filename)
            if relative.is_absolute() or '..' in relative.parts or ':' in item.filename or '\\' in item.filename:
                raise RuntimeError('Đường dẫn trong bộ cài Codex CLI không hợp lệ.')
            target = destination.joinpath(*relative.parts)
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open('wb') as output:
                while True:
                    _check(cancel, deadline)
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
    (destination / ASSET.removesuffix('.zip')).rename(destination / 'codex.exe')


def _probe(executable, deadline):
    result = subprocess.run([str(executable), '--version'], capture_output=True,
        timeout=max(.1, min(15, deadline - time.monotonic())),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode or result.stdout.decode('utf-8', errors='replace').strip() != f'codex-cli {VERSION}':
        raise RuntimeError('Codex CLI tải xuống không chạy được trên máy này.')


def ensure_codex(cancel=None, deadline=None, root=None):
    """Reuse an installed CLI; otherwise atomically publish a verified runtime."""
    deadline = min(deadline, time.monotonic() + 180) if deadline is not None else time.monotonic() + 180
    _check(cancel, deadline)
    existing = find_existing(root)
    if existing:
        return existing
    if os.name != 'nt':
        raise RuntimeError('Cần cài Codex CLI chính thức trên hệ điều hành này.')
    while not _lock.acquire(timeout=.1):
        _check(cancel, deadline)
    try:
        _check(cancel, deadline)
        existing = find_existing(root)
        if existing:
            return existing
        root = Path(root) if root is not None else install_root()
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='setup-', dir=root) as scratch:
            scratch = Path(scratch)
            archive = scratch / 'download.zip'
            _download(archive, cancel, deadline)
            runtime = scratch / 'runtime'
            runtime.mkdir()
            _extract(archive, runtime, cancel, deadline)
            _check(cancel, deadline)
            _probe(runtime / 'codex.exe', deadline)
            _check(cancel, deadline)
            (runtime / '.complete').write_text(f'{VERSION}\n{SHA256}\n', encoding='ascii')
            destination = root / VERSION
            if destination.exists():
                # A completed install from another process always wins.
                if (destination / 'codex.exe').is_file() and (destination / '.complete').is_file():
                    return str(destination / 'codex.exe')
                raise RuntimeError('Thư mục Codex CLI chưa hoàn chỉnh; đổi tên thư mục rồi thử lại.')
            try:
                runtime.rename(destination)
            except FileExistsError:
                if not (destination / '.complete').is_file():
                    raise
            return str(destination / 'codex.exe')
    except (InterruptedError, TimeoutError, RuntimeError):
        raise
    except Exception as exc:
        raise RuntimeError('Không tự cài được Codex CLI; kiểm tra Internet/dung lượng rồi thử lại trong API Zoo.') from exc
    finally:
        _lock.release()


def start_background(log):
    def run():
        try:
            log('codex_setup_started')
            ensure_codex()
            log('codex_setup_ready', version=VERSION)
        except Exception as exc:
            log('codex_setup_failed', error_type=type(exc).__name__)
    thread = threading.Thread(target=run, name='CodexSetup', daemon=True)
    thread.start()
    return thread
