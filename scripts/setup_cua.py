"""Install pinned official Cua Driver binaries alongside the portable EXE."""
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile

VERSION = '0.34.1'
WHEEL_SHA256 = '0b21aa96e3fedaeb01e8fe9c907717e726877f774f9f08f4089247b729bfd140'
ROOT = Path(__file__).resolve().parent.parent


def setup():
    cache = ROOT / 'build' / 'cua-driver'
    cache.mkdir(parents=True, exist_ok=True)
    wheel = cache / f'cua_driver-{VERSION}-py3-none-win_amd64.whl'
    if not wheel.is_file():
        subprocess.run([sys.executable, '-m', 'pip', 'download', '--no-deps',
                        '--only-binary=:all:', '--dest', str(cache), f'cua-driver=={VERSION}'], check=True)
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise RuntimeError('Cua Driver wheel SHA256 does not match the pinned PyPI release')
    runtime = ROOT / 'build' / 'cua-runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    licenses = runtime / 'cua-licenses'
    licenses.mkdir(exist_ok=True)
    (licenses / 'LICENSE.md').write_bytes((ROOT / 'third_party' / 'cua' / 'LICENSE.md').read_bytes())
    with zipfile.ZipFile(wheel) as archive:
        for name in ('cua-driver.exe', 'cua-driver-uia.exe'):
            member = 'cua_driver/bin/' + name
            (runtime / name).write_bytes(archive.read(member))
        for member in archive.namelist():
            if '.dist-info/licenses/' in member and not member.endswith('/'):
                relative = member.split('.dist-info/licenses/', 1)[1]
                output = runtime / 'cua-licenses' / relative
                if '..' in Path(relative).parts or Path(relative).is_absolute():
                    raise RuntimeError('Unexpected wheel license path')
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(member))
    (runtime / 'CUA_VERSION.txt').write_text(f'cua-driver {VERSION}\nhttps://github.com/trycua/cua\n'
        f'Wheel SHA256: {hashlib.sha256(wheel.read_bytes()).hexdigest()}\n', encoding='utf-8')
    print('Cua Driver MCP runtime:', runtime)


if __name__ == '__main__':
    setup()
