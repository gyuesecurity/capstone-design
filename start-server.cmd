@echo off
setlocal
cd /d "%~dp0"

rem Prefer the project's dependencies when they are available.
if exist "%~dp0.deps\fastapi\__init__.py" set "PYTHONPATH=%~dp0.deps;%PYTHONPATH%"

rem Encryption key (11/23): the server does not start without a key.
rem If no key source is set yet, use the demo keyring in secrets\ (outside data\, not in Git).
rem The first run creates it with scripts\generate_demo_key.py; later runs reuse the same file.
rem Do not delete secrets\evidence-master-keys.json: evidence saved with it cannot be opened without it.
if defined EVIDENCE_MASTER_KEYS_FILE goto start
if defined EVIDENCE_MASTER_KEYS goto start
if defined EVIDENCE_MASTER_KEY goto start
set "EVIDENCE_MASTER_KEYS_FILE=%~dp0secrets\evidence-master-keys.json"
if exist "%EVIDENCE_MASTER_KEYS_FILE%" goto start
echo Creating the demo encryption keyring for this PC: secrets\evidence-master-keys.json
python scripts\generate_demo_key.py --output "%EVIDENCE_MASTER_KEYS_FILE%"
if errorlevel 1 goto keyfail
echo Back up the secrets folder separately. Without it, saved evidence cannot be opened.

:start
echo EVIDENCE: http://127.0.0.1:8000/  (Page 4: http://127.0.0.1:8000/page4/)
echo Keep this window open while using the site. Press Ctrl+C to stop.
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
exit /b

:keyfail
echo Could not create the encryption keyring. See the message above.
pause
exit /b 1
