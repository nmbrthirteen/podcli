#Requires -Version 5.1
<#
  podcli - Install & Launch (Windows)

  Usage (from a PowerShell prompt in the repo root):
    .\setup.ps1               # Install everything + launch UI
    .\setup.ps1 -Install      # Install only (no launch)
    .\setup.ps1 -Ui           # Launch UI only (skip install)
    .\setup.ps1 -Mcp          # Show MCP config (for Claude Desktop/Code)

  If scripts are blocked, run once:
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Ui,
    [switch]$Mcp
)

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir "venv\Scripts\python.exe"

function Write-Banner {
    Write-Host ""
    Write-Host "  +======================================+"
    Write-Host "  |        podcli  (Windows)             |"
    Write-Host "  +======================================+"
    Write-Host ""
}

function Resolve-HostPython {
    foreach ($candidate in @("python", "py")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $candidate }
    }
    return $null
}

function Invoke-Install {
    Write-Host "--- [1/6] Checking system dependencies ---"
    $missing = $false

    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
        Write-Host "  ok  FFmpeg"
    } else {
        Write-Host "  X   FFmpeg not found  ->  winco: choco install ffmpeg  (or scoop install ffmpeg)"
        $missing = $true
    }

    $hostPy = Resolve-HostPython
    if ($hostPy) {
        $pyVer = (& $hostPy --version) 2>&1
        Write-Host "  ok  $pyVer"
    } else {
        Write-Host "  X   Python 3 not found  ->  https://www.python.org/downloads/ (check 'Add to PATH')"
        $missing = $true
    }

    if (Get-Command node -ErrorAction SilentlyContinue) {
        Write-Host "  ok  Node $(node --version)"
    } else {
        Write-Host "  X   Node.js not found  ->  https://nodejs.org/"
        $missing = $true
    }

    if ($missing) {
        Write-Host ""
        Write-Host "  Please install the missing dependencies above and re-run."
        exit 1
    }

    Write-Host ""
    Write-Host "--- [2/6] Creating directories ---"
    $clipperHome = if ($env:PODCLI_HOME) { $env:PODCLI_HOME } else { Join-Path $ScriptDir ".podcli" }
    $dataDir = if ($env:PODCLI_DATA) { $env:PODCLI_DATA } else { Join-Path $ScriptDir "data" }
    foreach ($d in @("assets", "history", "knowledge")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $clipperHome $d) | Out-Null
    }
    foreach ($d in @("cache\transcripts", "working", "working\uploads", "output", "logs")) {
        New-Item -ItemType Directory -Force -Path (Join-Path $dataDir $d) | Out-Null
    }
    Write-Host "  ok  $clipperHome (internal)"
    Write-Host "  ok  $dataDir (output & cache)"

    $modelDir = Join-Path $ScriptDir "backend\models"
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    $caffeModel = Join-Path $modelDir "res10_300x300_ssd_iter_140000.caffemodel"
    if (-not (Test-Path $caffeModel)) {
        Write-Host "  ..  Downloading face detection model..."
        $oldProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv/master/samples/dnn/face_detector/deploy.prototxt" `
            -OutFile (Join-Path $modelDir "deploy.prototxt")
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/opencv/opencv_3rdparty/dnn_samples_face_detector_20170830/res10_300x300_ssd_iter_140000.caffemodel" `
            -OutFile $caffeModel
        $ProgressPreference = $oldProgress
        Write-Host "  ok  Face detection model ready"
    } else {
        Write-Host "  ok  Face detection model exists"
    }

    Write-Host ""
    Write-Host "--- [3/6] Python virtual environment ---"
    if (-not (Test-Path $VenvPython)) {
        & $hostPy -m venv venv
        Write-Host "  ok  Created venv"
    } else {
        Write-Host "  ok  venv exists"
    }

    Write-Host ""
    Write-Host "--- [4/6] Installing Python packages ---"
    & $VenvPython -m pip install --quiet --upgrade pip
    & $VenvPython -m pip install --quiet -r backend\requirements.txt
    Write-Host "  ok  Python packages ready"

    Write-Host ""
    Write-Host "--- [5/6] Installing Node packages ---"
    npm install --silent
    Write-Host "  ok  Node packages ready"

    Write-Host ""
    Write-Host "--- [6/6] Building TypeScript ---"
    npx tsc
    Write-Host "  ok  Build complete"

    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
    }
    $envLines = Get-Content ".env"
    if ($envLines -match "^PYTHON_PATH=") {
        $envLines = $envLines -replace "^PYTHON_PATH=.*", "PYTHON_PATH=$VenvPython"
    } else {
        $envLines += "PYTHON_PATH=$VenvPython"
    }
    Set-Content ".env" $envLines
    Write-Host "  ok  .env configured (python: $VenvPython)"

    New-Item -ItemType Directory -Force -Path "dist\ui\public" | Out-Null
    Copy-Item -Recurse -Force "src\ui\public\*" "dist\ui\public\"

    Write-Host ""
    Write-Host "  Installation complete!"
    Write-Host ""
}

function Invoke-LaunchUi {
    if (Test-Path $VenvPython) {
        $env:PYTHON_PATH = $VenvPython
        Write-Host "  Using Python: $VenvPython"
    }

    if (Test-Path ".env") {
        foreach ($line in Get-Content ".env") {
            if ($line -match "^\s*#" -or $line -notmatch "=") { continue }
            $parts = $line -split "=", 2
            $key = $parts[0].Trim()
            $value = $parts[1].Trim()
            if (-not $key -or $key -eq "PYTHON_PATH") { continue }
            [Environment]::SetEnvironmentVariable($key, $value)
        }
    }

    if (-not (Test-Path "dist\ui\public\index.html")) {
        Write-Host "  Building the studio UI..."
        npm run build
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Build failed - run .\setup.ps1 -Install first."
            exit 1
        }
    }

    $port = if ($env:PORT) { $env:PORT } else { "3847" }
    Write-Host "  Studio: http://localhost:$port"
    Write-Host ""
    node dist\ui\web-server.js
}

function Show-Mcp {
    $distIndex = Join-Path $ScriptDir "dist\index.js"
    Write-Host "  -- Claude Desktop config --"
    Write-Host "  Add to %APPDATA%\Claude\claude_desktop_config.json:"
    Write-Host ""
    Write-Host "  {"
    Write-Host "    `"mcpServers`": {"
    Write-Host "      `"podcli`": {"
    Write-Host "        `"command`": `"node`","
    Write-Host "        `"args`": [`"$($distIndex -replace '\\','\\')`"],"
    Write-Host "        `"env`": {"
    Write-Host "          `"PYTHON_PATH`": `"$($VenvPython -replace '\\','\\')`""
    Write-Host "        }"
    Write-Host "      }"
    Write-Host "    }"
    Write-Host "  }"
    Write-Host ""
    Write-Host "  -- Claude Code --"
    Write-Host "  claude mcp add podcli -- node `"$distIndex`""
    Write-Host ""
}

Write-Banner

if ($Mcp) {
    Show-Mcp
} elseif ($Ui) {
    Invoke-LaunchUi
} elseif ($Install) {
    Invoke-Install
} else {
    Invoke-Install
    Write-Host "----------------------------------------"
    Write-Host ""
    Invoke-LaunchUi
}
