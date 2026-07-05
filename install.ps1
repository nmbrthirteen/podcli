# podcli installer for Windows - downloads the prebuilt native binary (no Go,
# Node, Python, or ffmpeg needed; the binary provisions those on first run).
# Usage: irm https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.ps1 | iex
# Uninstall: & ([scriptblock]::Create((irm https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.ps1))) -Uninstall
# Purge:     & ([scriptblock]::Create((irm https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.ps1))) -Uninstall -Purge
param([switch]$Uninstall, [switch]$Purge)
$ErrorActionPreference = 'Stop'
$repo = 'nmbrthirteen/podcli'
$target = 'windows-amd64'

$homeDir = Join-Path $env:LOCALAPPDATA 'podcli'
$binDir = Join-Path $homeDir 'bin'

if ($Uninstall) {
  Write-Host "Uninstalling podcli..."
  if ($Purge) {
    $targets = @($homeDir)
  } else {
    $targets = @($binDir, (Join-Path $homeDir 'runtime'), (Join-Path $homeDir 'models'), (Join-Path $homeDir 'tools'))
  }
  foreach ($p in $targets) {
    if (Test-Path $p) {
      try {
        Remove-Item $p -Recurse -Force -ErrorAction Stop
        Write-Host "  removed: $p"
      } catch {
        Write-Warning ("could not remove {0}: {1}" -f $p, $_.Exception.Message)
      }
    }
  }
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if ($userPath -like "*$binDir*") {
    $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $binDir) }
    [Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'User')
    Write-Host "  removed from user PATH (restart your terminal)"
  }
  if ($Purge) {
    Write-Host "  removed managed data."
  } else {
    Write-Host "  kept user data (config, knowledge, presets, assets, history, cache)."
    Write-Host "  To remove everything: rerun with -Uninstall -Purge"
  }
  exit 0
}

New-Item -ItemType Directory -Force -Path $binDir | Out-Null

$version = $env:PODCLI_VERSION
if (-not $version) {
  $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'podcli-install' }
  $version = $rel.tag_name -replace '^v', ''
}

$asset = "podcli-$target.exe"
$base = "https://github.com/$repo/releases/download/v$version"
Write-Host "Installing podcli v$version ($target)..."

$dest = Join-Path $binDir 'podcli.exe'
Invoke-WebRequest "$base/$asset" -OutFile $dest -UseBasicParsing

try {
  $sums = (Invoke-WebRequest "$base/checksums.txt" -UseBasicParsing).Content
  $want = $sums -split "`n" |
    Where-Object { $_ -match ([regex]::Escape($asset) + '\s*$') } |
    ForEach-Object { ($_ -split '\s+')[0] } | Select-Object -First 1
  if ($want) {
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $want.ToLower()) { Remove-Item $dest -Force; throw "checksum mismatch (got $got want $want)" }
    Write-Host "  checksum verified"
  } else {
    Write-Host "  no checksum entry for $asset - skipped verification"
  }
} catch {
  Write-Host "  checksum verification skipped: $($_.Exception.Message)"
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$binDir*") {
  [Environment]::SetEnvironmentVariable('Path', ($binDir + ';' + $userPath), 'User')
  Write-Host "  added to PATH (restart your terminal)"
}
Write-Host ""
Write-Host "Done - run:  podcli"
