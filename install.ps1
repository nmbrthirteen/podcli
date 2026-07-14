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

function Get-UserPathEntry {
  $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
  if (-not $key) { return $null }
  $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
  try { $kind = $key.GetValueKind('Path') } catch {}
  [pscustomobject]@{
    Key = $key
    Kind = $kind
    Value = [string]$key.GetValue('Path', '', [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  }
}

function Test-PathEntryEquals {
  param([string]$Entry, [string]$Target)
  try {
    return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($Entry)).TrimEnd('\') -ieq [IO.Path]::GetFullPath($Target).TrimEnd('\')
  } catch {
    return $Entry.TrimEnd('\') -ieq $Target.TrimEnd('\')
  }
}

function Send-EnvironmentPathChange {
  if (-not ('Podcli.NativeMethods' -as [type])) {
    Add-Type -Namespace Podcli -Name NativeMethods -MemberDefinition @'
[DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);
'@
  }

  $result = [UIntPtr]::Zero
  $status = [Podcli.NativeMethods]::SendMessageTimeout([IntPtr]0xffff, 0x1a, [UIntPtr]::Zero, 'Environment', 0x0002, 5000, [ref]$result)
  if ($status -eq [IntPtr]::Zero) {
    $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    Write-Warning "could not broadcast PATH update (SendMessageTimeout error $errorCode); restart your terminal"
  }
}

if ($Uninstall) {
  Write-Host "Uninstalling podcli..."
  if ($Purge) {
    $targets = @($homeDir)
  } else {
    $targets = @($binDir, (Join-Path $homeDir 'runtime'), (Join-Path $homeDir 'models'))
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
  $pathEntry = Get-UserPathEntry
  if ($pathEntry) {
    $parts = @($pathEntry.Value -split ';' | Where-Object { $_ })
    $kept = @($parts | Where-Object { -not (Test-PathEntryEquals $_ $binDir) })
    if ($kept.Count -ne $parts.Count) {
      $pathEntry.Key.SetValue('Path', ($kept -join ';'), $pathEntry.Kind)
      Send-EnvironmentPathChange
      Write-Host "  removed from user PATH (restart your terminal)"
    }
  }
  if ($Purge) {
    Write-Host "  removed podcli and user data."
  } else {
    Write-Host "  removed podcli runtime files. User data preserved; pass -Purge to remove it."
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
  $sums = Invoke-RestMethod "$base/checksums.txt" -Headers @{ 'Accept' = 'text/plain'; 'User-Agent' = 'podcli-install' }
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

$pathEntry = Get-UserPathEntry
if ($pathEntry) {
  $parts = @($pathEntry.Value -split ';' | Where-Object { $_ })
  if (-not ($parts | Where-Object { Test-PathEntryEquals $_ $binDir })) {
    $pathEntry.Key.SetValue('Path', ($binDir + ';' + $pathEntry.Value), $pathEntry.Kind)
    Send-EnvironmentPathChange
    Write-Host "  added to PATH (restart your terminal)"
  }
}
Write-Host ""
Write-Host "Done - run:  podcli"
