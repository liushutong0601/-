@echo off
setlocal
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" (
  echo 未找到 Microsoft Edge，请手动确认安装路径。
  pause
  exit /b 1
)
start "" "%EDGE%" --remote-debugging-port=9222 --user-data-dir="%~dp0.edge-debug-profile" --no-first-run --no-default-browser-check --disable-search-engine-choice-screen "edge://newtab"
echo 调试 Edge 已启动，使用 Edge 原生新标签页，不修改常用 Edge 配置。
echo 首次使用请登录学习通并进入课程小节。
endlocal
