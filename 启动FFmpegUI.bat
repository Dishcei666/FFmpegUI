@echo off
title FFmpegUI
cd /d "%~dp0"

rem ---- 定位可用的 Python：真实试运行来筛选，避开 Microsoft Store 的占位 python.exe ----
set "PYEXE="
if exist "%USERPROFILE%\anaconda3\python.exe" set "PYEXE=%USERPROFILE%\anaconda3\python.exe"
if not defined PYEXE (
    python -c "print(1)" >nul 2>nul && set "PYEXE=python"
)
if not defined PYEXE (
    py -3 -c "print(1)" >nul 2>nul && set "PYEXE=py -3"
)

if not defined PYEXE (
    echo [错误] 未找到可用的 Python。若系统弹窗提示从 Microsoft Store 安装，
    echo        请关闭它，改为安装 Anaconda 或把已有 Python 加入系统 PATH。
    pause
    exit /b 1
)

echo [FFmpegUI] 使用 Python: %PYEXE%

rem ---- 首次运行自动安装依赖 ----
"%PYEXE%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo 首次运行，正在安装依赖 PySide6，约 1-2 分钟，仅需一次...
    "%PYEXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试。
        pause
        exit /b 1
    )
)

"%PYEXE%" -m app.main
if errorlevel 1 pause
