# Maker entry for the extracted Studio build kit (Windows PowerShell 5.1+).
param([switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$source = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not [Environment]::Is64BitProcess -or $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {
    throw 'Use 64-bit Windows x64 PowerShell.'
}
function Find-Tool($name, $fallbacks) {
    $found = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    foreach ($candidate in $fallbacks) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
}
function Get-BuildTool($name, $package, $fallbacks) {
    $tool = Find-Tool $name $fallbacks
    if ($tool) { return $tool }
    if ($NonInteractive) { throw "Missing build tool: $name" }
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "Install $package on the build PC, then reopen this file."
    }
    Write-Host "Build tool needed: $package. Install it using Windows Package Manager?"
    if ((Read-Host 'Enter Y to install') -ne 'Y') { throw 'Build cancelled.' }
    & winget.exe install --exact --id $package --source winget
    if ($LASTEXITCODE -ne 0) { throw "Installation failed: $package ($LASTEXITCODE)" }
    $tool = Find-Tool $name $fallbacks
    if (-not $tool) { throw "Reopen this file after installing $package." }
    return $tool
}
if (-not (Test-Path "$source\studio\dist\index.html")) { throw 'Studio UI missing. Extract the entire build kit first.' }
$uv = Get-BuildTool uv.exe astral-sh.uv @("$env:LOCALAPPDATA\Microsoft\WinGet\Links\uv.exe", "$env:USERPROFILE\.local\bin\uv.exe")
$nsis = Get-BuildTool makensis.exe NSIS.NSIS @("${env:ProgramFiles(x86)}\NSIS\makensis.exe", "$env:ProgramFiles\NSIS\makensis.exe")
$work = Join-Path ([IO.Path]::GetTempPath()) ('meritz-build-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
$env:UV_PROJECT_ENVIRONMENT = "$work\env"
$env:PYTHONUTF8 = '1'
& $uv sync --project $source --locked --extra dev --python 3.11
if ($LASTEXITCODE -ne 0) { throw "Dependency setup failed ($LASTEXITCODE)." }
$python = "$work\env\Scripts\python.exe"
& $python "$source\packaging\build_native.py" --source-root $source --output-root "$work\native"
if ($LASTEXITCODE -ne 0) { throw "Application build or Python backtest failed ($LASTEXITCODE)." }
$prepare = "$source\packaging\windows\prepare_installer.py"
$bundle = "$work\native\dist\meritz-strategy-platform"
& $python $prepare --manifest "$work\native\build-manifest.json" --bundle $bundle --output "$work\installer"
if ($LASTEXITCODE -ne 0) { throw 'Installer preparation failed.' }
& $python $prepare --verify-prepared "$work\installer"
if ($LASTEXITCODE -ne 0) { throw 'Installer file verification failed.' }
Push-Location "$work\installer"
try {
    & $nsis /V2 installer.nsi
    if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed ($LASTEXITCODE)." }
} finally { Pop-Location }
$setup = @(Get-ChildItem -LiteralPath "$work\installer" -Filter 'MeritzStrategyPlatformPreview-*-UNTESTED.exe')
if ($setup.Count -ne 1) { throw 'Expected exactly one installer.' }
# Publish only after both outputs exist; preserve earlier successful builds.
$zip = "$work\MeritzStudio-Windows-x64.zip"
Compress-Archive -LiteralPath $bundle -DestinationPath $zip
$dest = Join-Path $source ('Windows-Output-' + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Path $dest | Out-Null
Copy-Item -LiteralPath $setup[0].FullName -Destination "$dest\MeritzStudio-Setup-x64.exe"
Copy-Item -LiteralPath $zip -Destination $dest
Write-Host "DONE: $dest"
if (-not $NonInteractive) { Invoke-Item -LiteralPath $dest }
