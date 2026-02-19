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
set CLASSIFIER_ENDPOINT=http://localhost:8001
set LLM_ENDPOINT=http://localhost:8000
set TTS_ENDPOINT=http://localhost:5050
set REMOTE_SAVE_ENDPOINT=http://localhost:7000
set SETTINGS_DIR=%ROOT%data
set DATA_DIR=%ROOT%data

echo [*] Starting main app on port 8080 (host, USB sensor accessible)...
cd /d "%ROOT%app"
call "%VENV%"
uvicorn main:app --host 0.0.0.0 --port 8080 --reload --timeout-graceful-shutdown 3
