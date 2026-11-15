@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto error
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto error
echo 安装完成，可以运行 启动软件.bat。
pause
exit /b 0
:error
echo 安装失败，请检查 Python 和网络。
pause
exit /b 1
