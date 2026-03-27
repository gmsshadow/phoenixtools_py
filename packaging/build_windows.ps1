param(
  [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot\..

python -m pip install -e ".[packaging]"

pyinstaller `
  --name phoenixtools `
  --noconfirm `
  --clean `
  --windowed `
  --distpath $OutDir `
  .\packaging\entrypoint.py

Write-Host "Built to $OutDir\phoenixtools"

