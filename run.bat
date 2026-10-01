@echo off
setlocal

REM 切到脚本所在目录
cd /d "%~dp0"

REM 若存在虚拟环境则使用
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)

endlocal