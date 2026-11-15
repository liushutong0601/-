@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 尚未安装运行环境，请先执行 安装依赖.bat。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "app.py"
if errorlevel 1 pause
