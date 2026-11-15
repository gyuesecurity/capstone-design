@echo off
setlocal
cd /d "%~dp0"

rem Prefer the project's dependencies when they are available.
if exist "%~dp0.deps\fastapi\__init__.py" set "PYTHONPATH=%~dp0.deps;%PYTHONPATH%"

echo EVIDENCE: http://127.0.0.1:8000/index.html
echo Keep this window open while using the site. Press Ctrl+C to stop.
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
