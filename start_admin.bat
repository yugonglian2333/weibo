@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

:: Try to find Python
set PYTHON=
for %%p in (py python3 python) do (
    where %%p >nul 2>&1
    if not errorlevel 1 (
        set PYTHON=%%p
        goto :found
    )
)

:: Fallback: try common install paths
for %%d in (
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Python312\python.exe"
) do (
    if exist %%d (
        set PYTHON=%%d
        goto :found
    )
)

echo [ERROR] Python not found. Please install Python from https://python.org
pause
exit /b 1

:found
echo.
echo =============================================
echo   Weibo Assistant Admin System Starting...
echo =============================================
echo.
%PYTHON% admin_server.py
pause
