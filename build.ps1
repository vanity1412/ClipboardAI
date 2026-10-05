$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python -m PyInstaller --noconfirm --clean --onefile --windowed --hidden-import tkinter --hidden-import _tkinter --name ClipboardAI --distpath dist --workpath build/work --specpath build src/deepseek_flash_entry.py
    if ($LASTEXITCODE -ne 0) { throw 'Build EXE failed' }
    Copy-Item -LiteralPath README.md -Destination dist/README.md -Force
    Copy-Item -LiteralPath .env.example -Destination dist/.env.example -Force
    Get-FileHash -Algorithm SHA256 dist/ClipboardAI.exe | Select-Object Hash | Format-Table -HideTableHeaders | Out-String | Set-Content dist/SHA256.txt
} finally {
    Pop-Location
}
