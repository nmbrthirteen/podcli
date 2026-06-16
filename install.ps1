# podcli installer for Windows — downloads the prebuilt native binary (no Go,
# Node, Python, or ffmpeg needed; the binary provisions those on first run).
# Usage: irm https://raw.githubusercontent.com/nmbrthirteen/podcli/main/install.ps1 | iex
$ErrorActionPreference = 'Stop'
$repo = 'nmbrthirteen/podcli'
$target = 'windows-amd64'

$homeDir = Join-Path $env:LOCALAPPDATA 'podcli'
$binDir = Join-Path $homeDir 'bin'
New-Item -ItemType Directory -Force -Path $binDir | Out-Null

$version = $env:PODCLI_VERSION
if (-not $version) {
  $rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ 'User-Agent' = 'podcli-install' }
  $version = $rel.tag_name -replace '^v', ''
}

$asset = "podcli-$target.exe"
$base = "https://github.com/$repo/releases/download/v$version"
Write-Host "Installing podcli v$version ($target)…"

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
    Write-Host "  no checksum entry for $asset — skipped verification"
  }
} catch {
  Write-Host "  checksum verification skipped: $($_.Exception.Message)"
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$binDir*") {
  [Environment]::SetEnvironmentVariable('Path', "$binDir;$userPath", 'User')
  Write-Host "  added to PATH (restart your terminal)"
}
Write-Host ""
Write-Host "Done — run:  podcli"
