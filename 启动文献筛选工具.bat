@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist "runtime\python\python.exe" (
    "runtime\python\python.exe" main.py
    goto :finished
)

py -3 -c "import sys" >nul 2>nul
if %errorlevel%==0 (
    py -3 main.py
) else (
    python -c "import sys" >nul 2>nul
    if %errorlevel%==0 (
        python main.py
    ) else (
        echo [错误] 未找到 Python 3。
        echo 请先安装 Python 3.10 或更高版本，并勾选“Add Python to PATH”。
        pause
        exit /b 1
    )
)

:finished
if errorlevel 1 (
    echo.
    echo 程序异常退出，请查看上方错误信息。
    pause
)
endlocal
