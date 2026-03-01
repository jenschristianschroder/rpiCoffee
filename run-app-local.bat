@echo off
REM rpiCoffee – run ONLY the main app on the host (not in Docker).
REM
REM Use this when you need direct USB sensor access (PicoQuake mode)
REM while backend services (classifier, llm, tts, remote-save) run
REM in Docker containers.
REM
REM Prerequisites:
REM   1. Start Docker services:
REM        docker compose --profile classifier --profile llm --profile tts --profile remote-save up -d
REM   2. Stop the Docker app container (if running):
REM        docker compose stop app
REM   3. Activate venv:
REM        .venv\Scripts\activate
REM   4. pip install -r app\requirements.txt
REM

setlocal

set ROOT=%~dp0
set VENV=%ROOT%.venv\Scripts\activate.bat

echo ========================================
echo   rpiCoffee – App on Host (USB sensor)
echo ========================================
echo.
echo   Backend services must be running in Docker.
echo   Sensor will connect via host USB.
echo.

REM Stop the Docker app container so port 8080 is free
echo [*] Stopping Docker app container (if running)...
docker compose stop app 2>nul

REM Point at Docker services (exposed ports on localhost)
set CLASSIFIER_PORT=8001
set LLM_PORT=8002
set TTS_PORT=5050
set REMOTE_SAVE_PORT=7000
set APP_PORT=8080
set CLASSIFIER_ENDPOINT=http://localhost:%CLASSIFIER_PORT%
set LLM_ENDPOINT=http://localhost:%LLM_PORT%
set TTS_ENDPOINT=http://localhost:%TTS_PORT%
set REMOTE_SAVE_ENDPOINT=http://localhost:%REMOTE_SAVE_PORT%
set SETTINGS_DIR=%ROOT%data
set DATA_DIR=%ROOT%data

echo [*] Starting main app on port %APP_PORT% (host, USB sensor accessible)...
cd /d "%ROOT%app"
call "%VENV%"
uvicorn main:app --host 0.0.0.0 --port %APP_PORT% --reload --timeout-graceful-shutdown 3
