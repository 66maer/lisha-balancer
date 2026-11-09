param(
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskDist = [IO.Path]::GetFullPath((Join-Path $taskRoot 'dist\LishaOverlay'))
$taskWorkspace = [IO.Path]::GetFullPath($taskRoot).TrimEnd('\') + '\'
if (-not $taskDist.StartsWith($taskWorkspace, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must remain inside this workspace.'
}
if ((Test-Path -LiteralPath $taskDist) -and ((Get-Item -LiteralPath $taskDist).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    throw 'Refusing to replace a linked build output directory.'
}
$taskPython = if ($Python) { (Get-Command $Python -ErrorAction Stop).Source } else {
    Join-Path $taskRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $taskPython)) {
    throw 'Please create .venv and install the project dependencies first.'
}
$taskExecutable = Join-Path $taskDist 'LishaOverlay.exe'
$taskRunning = @(Get-Process -Name LishaOverlay -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -eq $taskExecutable
})
if ($taskRunning.Count -gt 0) {
    # Release loaded Qt/OpenCV DLLs before PyInstaller replaces this output.
    & $taskPython (Join-Path $taskRoot 'main.py') --quit
    if ($LASTEXITCODE -ne 0) { throw 'Could not request normal application shutdown.' }
    foreach ($taskProcess in $taskRunning) {
        if (-not $taskProcess.WaitForExit(8000)) {
            throw 'The existing application is still using the build output.'
        }
    }
}
& $taskPython (Join-Path $PSScriptRoot 'make_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'Application icon generation failed.' }
$taskPythonBase = (& $taskPython -c 'import sys; print(sys.base_prefix)').Trim()
$taskOriginalPath = $env:PATH
try {
    # Other applications may expose incompatible ICU or C runtime DLLs on PATH.
    # Qt uses Windows ICU; do not accidentally bundle Poppler/libheif DLLs.
    $env:PATH = @(
        (Join-Path $taskRoot '.venv\Scripts'),
        $taskPythonBase,
        (Join-Path $taskPythonBase 'DLLs'),
        (Join-Path $env:SystemRoot 'System32'),
        $env:SystemRoot,
        (Join-Path $env:SystemRoot 'System32\Wbem')
    ) -join ';'
    $taskBuildArguments = @(
        '--noconfirm', '--windowed', '--onedir',
        '--name', 'LishaOverlay', '--workpath', 'build/release',
        '--icon', 'assets/app.ico', '--paths', 'src',
        '--add-data', 'src/lisha_overlay/assets;lisha_overlay/assets',
        '--hidden-import', 'PySide6.support.deprecated',
        '--hidden-import', 'onnxruntime', '--collect-binaries', 'onnxruntime'
    )
    foreach ($taskDependency in @('PySide6-Essentials', 'shiboken6', 'mss', 'numpy', 'opencv-python-headless', 'onnxruntime')) {
        $taskBuildArguments += @('--copy-metadata', $taskDependency)
    }
    foreach ($taskModule in @('QtQml', 'QtQuick', 'QtWebEngineCore', 'QtWebEngineWidgets', 'QtPdf', 'QtOpenGL', 'QtOpenGLWidgets')) {
        $taskBuildArguments += @('--exclude-module', "PySide6.$taskModule")
    }
    & $taskPython -m PyInstaller @taskBuildArguments main.py
    if ($LASTEXITCODE -ne 0) { throw 'Application packaging failed.' }
} finally {
    $env:PATH = $taskOriginalPath
}
Copy-Item -LiteralPath (Join-Path $taskRoot 'assets\release-readme.txt') -Destination (Join-Path $taskDist '使用说明.txt') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'assets\export-debug.cmd') -Destination (Join-Path $taskDist '导出调试包.cmd') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'LICENSE') -Destination (Join-Path $taskDist 'LICENSE') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $taskDist 'THIRD_PARTY_NOTICES.md') -Force
Copy-Item -LiteralPath (Join-Path $taskRoot 'LICENSES') -Destination (Join-Path $taskDist 'LICENSES') -Recurse -Force
$taskCheck = Start-Process -FilePath (Join-Path $taskDist 'LishaOverlay.exe') -ArgumentList '--check-runtime' -WindowStyle Hidden -PassThru
if (-not $taskCheck.WaitForExit(15000)) {
    Stop-Process -Id $taskCheck.Id -ErrorAction SilentlyContinue
    throw 'Packaged runtime did not pass its startup check.'
}
if ($taskCheck.ExitCode -ne 0) { throw 'Packaged runtime failed its startup check. See startup-error.log in LocalAppData/LishaBalancer.' }
Write-Output (Join-Path $taskRoot 'dist\LishaOverlay\LishaOverlay.exe')
