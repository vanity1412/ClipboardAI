param(
    [ValidateSet('ClipboardAI', 'ClipboardAI_Region_Test')]
    [string]$Name = 'ClipboardAI',
    [string]$OutputDirectory = 'dist'
)

$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    # Hidden imports alone do not stop PyInstaller from excluding a broken or
    # inaccessible Tcl/Tk runtime. Fail before producing an EXE without its UI.
    python scripts/check_tk_runtime.py
    if ($LASTEXITCODE -ne 0) { throw 'Build requires readable Tcl/Tk runtime files for region capture, API Zoo and hotkey settings' }
    python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --hidden-import tkinter --hidden-import _tkinter --name $Name --distpath $OutputDirectory --workpath build/work --specpath build src/deepseek_flash_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Build EXE failed' }
    $taskExecutable = Join-Path $OutputDirectory ($Name + '.exe')
    python -c "import sys, xml.etree.ElementTree as ET; from PyInstaller.utils.win32.winmanifest import read_manifest_from_executable; root = ET.fromstring(read_manifest_from_executable(sys.argv[1])); level = root.find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel'); assert level is not None and level.get('level') == 'requireAdministrator', 'EXE must require administrator'; print('EXE administrator manifest: OK')" $taskExecutable
    if ($LASTEXITCODE -ne 0) { throw 'EXE administrator manifest verification failed' }
    Copy-Item -LiteralPath README.md -Destination (Join-Path $OutputDirectory 'README.md') -Force
    Copy-Item -LiteralPath docs/CHUC_NANG.md -Destination (Join-Path $OutputDirectory 'CHUC_NANG.md') -Force
    Copy-Item -LiteralPath docs/API_PROVIDERS.md -Destination (Join-Path $OutputDirectory 'API_PROVIDERS.md') -Force
    Copy-Item -LiteralPath .env.example -Destination (Join-Path $OutputDirectory '.env.example') -Force
    $taskHash = Get-FileHash -Algorithm SHA256 -LiteralPath $taskExecutable
    ($taskHash.Hash + '  ' + $Name + '.exe') | Set-Content (Join-Path $OutputDirectory 'SHA256.txt')
} finally {
    Pop-Location
}
