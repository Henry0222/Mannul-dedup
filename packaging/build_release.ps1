param([switch]$FastStart)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'build\packaging-venv\Scripts\python.exe'
$runtimeRoot = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node'
$nodeExe = Join-Path $runtimeRoot 'bin\node.exe'
$artifactTool = Join-Path $runtimeRoot 'node_modules\@oai\artifact-tool'
$builder = Join-Path $projectRoot 'tools\build_workbook.mjs'
$versionFile = Join-Path $projectRoot 'packaging\version_info.txt'
$runtimeHook = Join-Path $projectRoot 'packaging\runtime_tcl_bootstrap.py'
$pyinstallerHooks = Join-Path $projectRoot 'packaging\hooks'
$mainScript = Join-Path $projectRoot 'main.py'
$iconFile = Join-Path $projectRoot 'assets\app_icon.ico'
$iconPng = Join-Path $projectRoot 'assets\app_icon.png'
$vosFrontend = Join-Path $projectRoot 'vosviewer_frontend\dist'
$vosLicense = Join-Path $projectRoot 'vosviewer_frontend\node_modules\vosviewer-online\LICENSE'
$systemPythonRoot = Split-Path -Parent (Get-Command python).Source
$systemTclRoot = Join-Path $systemPythonRoot 'tcl'
$localTclRoot = Join-Path $projectRoot 'build\tcl_runtime'

foreach ($required in @($python, $nodeExe, $artifactTool, $builder, $versionFile, $runtimeHook, $pyinstallerHooks, $mainScript, $iconFile, $iconPng, (Join-Path $vosFrontend 'index.html'), $vosLicense)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "缺少打包依赖：$required"
    }
}
Copy-Item -LiteralPath $vosLicense -Destination (Join-Path $vosFrontend 'VOSviewer-Online-LICENSE.txt') -Force
& $python (Join-Path $projectRoot 'vosviewer_frontend\collect_licenses.py')
if ($LASTEXITCODE -ne 0) { throw 'VOSviewer 依赖许可证汇总失败。' }

& $python -c 'import tkinterdnd2'
if ($LASTEXITCODE -ne 0) {
    throw '打包环境缺少 tkinterdnd2；请先安装 requirements.txt。'
}

New-Item -ItemType Directory -Path $localTclRoot -Force | Out-Null
foreach ($tclFolder in @('tcl8.6', 'tk8.6', 'tcl8')) {
    $source = Join-Path $systemTclRoot $tclFolder
    $destination = Join-Path $localTclRoot $tclFolder
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $source -Destination $destination -Recurse
    }
}
$env:TCL_LIBRARY = Join-Path $localTclRoot 'tcl8.6'
$env:TK_LIBRARY = Join-Path $localTclRoot 'tk8.6'

Push-Location $projectRoot
try {
    $bundleMode = if ($FastStart) { '--onedir' } else { '--onefile' }
    $workPath = if ($FastStart) { 'build\pyinstaller-fast' } else { 'build\pyinstaller' }
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        $bundleMode `
        --windowed `
        --noupx `
        --name 'WOS_Literature_Filter' `
        --icon "$iconFile" `
        --version-file "$versionFile" `
        --runtime-hook "$runtimeHook" `
        --additional-hooks-dir "$pyinstallerHooks" `
        --distpath 'release' `
        --workpath $workPath `
        --specpath $workPath `
        --add-data "$builder;tools" `
        --add-data "$iconPng;assets" `
        --add-data "$vosFrontend;vosviewer_frontend\dist" `
        --add-binary "$nodeExe;runtime\node" `
        --add-data "$artifactTool;runtime\node\node_modules\@oai\artifact-tool" `
        "$mainScript"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出码：$LASTEXITCODE"
    }
    $warningFile = Join-Path $projectRoot "$workPath\WOS_Literature_Filter\warn-WOS_Literature_Filter.txt"
    if (Test-Path -LiteralPath $warningFile) {
        $tkWarning = Select-String -LiteralPath $warningFile -Pattern 'tkinter installation is broken' -Quiet
        if ($tkWarning) {
            throw 'PyInstaller 未能打包 Tkinter，拒绝生成不可用的发布版。'
        }
    }
    if ($FastStart) {
        $builtFolder = Join-Path $projectRoot 'release\WOS_Literature_Filter'
        $finalFolder = Join-Path $projectRoot 'release\WOS文献筛选工具-1.22.0-快速启动版'
        if (Test-Path -LiteralPath $finalFolder) {
            throw "快速启动版目录已存在，请先保留或移走旧目录：$finalFolder"
        }
        Move-Item -LiteralPath $builtFolder -Destination $finalFolder
        $builtExe = Join-Path $finalFolder 'WOS_Literature_Filter.exe'
        $finalExe = Join-Path $finalFolder 'WOS文献筛选工具.exe'
    } else {
        $builtExe = Join-Path $projectRoot 'release\WOS_Literature_Filter.exe'
        $finalExe = Join-Path $projectRoot 'release\WOS文献筛选工具.exe'
    }
    Move-Item -LiteralPath $builtExe -Destination $finalExe -Force
}
finally {
    Pop-Location
}
