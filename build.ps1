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
    python -c "from PyInstaller.utils.hooks.tcl_tk import tcltk_info; assert tcltk_info.available and tcltk_info.data_files and not tcltk_info.tcl_data_missing and not tcltk_info.tk_data_missing, 'Tcl/Tk runtime files are missing or inaccessible'"
    if ($LASTEXITCODE -ne 0) { throw 'Build requires readable Tcl/Tk runtime files for region capture, API Zoo and hotkey settings' }
    python -m PyInstaller --noconfirm --clean --onefile --windowed --hidden-import tkinter --hidden-import _tkinter --name $Name --distpath $OutputDirectory --workpath build/work --specpath build src/deepseek_flash_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Build EXE failed' }
    Copy-Item -LiteralPath README.md -Destination (Join-Path $OutputDirectory 'README.md') -Force
    Copy-Item -LiteralPath docs/CHUC_NANG.md -Destination (Join-Path $OutputDirectory 'CHUC_NANG.md') -Force
    Copy-Item -LiteralPath docs/API_PROVIDERS.md -Destination (Join-Path $OutputDirectory 'API_PROVIDERS.md') -Force
    Copy-Item -LiteralPath .env.example -Destination (Join-Path $OutputDirectory '.env.example') -Force
    $taskExecutable = Join-Path $OutputDirectory ($Name + '.exe')
    $taskHash = Get-FileHash -Algorithm SHA256 -LiteralPath $taskExecutable
    ($taskHash.Hash + '  ' + $Name + '.exe') | Set-Content (Join-Path $OutputDirectory 'SHA256.txt')
} finally {
    Pop-Location
}
