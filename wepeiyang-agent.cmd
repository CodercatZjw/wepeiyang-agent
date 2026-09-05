@echo off
setlocal
chcp 65001 >nul
title WePeiYang Agent - Read Only
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Python。请先安装 Python 3.10 或更高版本。
  pause
  exit /b 1
)

python -m wepeiyang_agent chat
set "WPY_EXIT=%ERRORLEVEL%"
if not "%WPY_EXIT%"=="0" (
  echo.
  echo 程序异常结束，错误码：%WPY_EXIT%
  pause
)
exit /b %WPY_EXIT%
