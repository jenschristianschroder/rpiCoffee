@echo off
REM rpiCoffee – local development launcher for Windows
REM Starts all services in separate terminal windows
REM
REM Prerequisites:
REM   cd c:\src\rpiCoffee
REM   python -m venv .venv
REM   .venv\Scripts\activate
REM   pip install -r app\requirements.txt
REM

setlocal

set ROOT=%~dp0
set VENV=%ROOT%.venv\Scripts\activate.bat

echo ========================================
echo   rpiCoffee – Local Dev Launcher
echo ========================================
echo.

REM Override endpoints for local development
set CLASSIFIER_ENDPOINT=http://localhost:8001
set LLM_ENDPOINT=http://localhost:8000
set TTS_ENDPOINT=http://localhost:5050
set REMOTE_SAVE_ENDPOINT=http://localhost:7000
set SENSOR_MODE=mock
set SETTINGS_DIR=%ROOT%data
set DATA_DIR=%ROOT%data

echo [*] Starting classifier (mock) on port 8001...
start "classifier" cmd /k "cd /d %ROOT%services\classifier && call %VENV% && uvicorn main:app --host 0.0.0.0 --port 8001 --reload"

echo [*] Starting LLM on port 8000...
start "llm" cmd /k "cd /d %ROOT%services\llm && call %VENV% && python server.py --port 8000"

echo [*] Starting main app on port 8080...
timeout /t 2 >nul
start "rpicoffee-app" cmd /k "cd /d %ROOT%app && call %VENV% && set CLASSIFIER_ENDPOINT=http://localhost:8001 && set LLM_ENDPOINT=http://localhost:8000 && set TTS_ENDPOINT=http://localhost:5050 && set REMOTE_SAVE_ENDPOINT=http://localhost:7000 && set SENSOR_MODE=mock && set SETTINGS_DIR=%ROOT%data && set DATA_DIR=%ROOT%data && uvicorn main:app --host 0.0.0.0 --port 8080 --reload"

echo.
echo ========================================
echo   All services starting!
echo ========================================
echo.
echo   Admin UI:     http://localhost:8080/admin/
echo   Classifier:   http://localhost:8001/docs
echo   LLM:          http://localhost:8000/health
echo.
echo   Note: TTS is skipped locally (requires Piper/Linux).
echo         The pipeline will run steps 1-2 and skip TTS.
echo.
echo   Press any key to close this launcher window.
pause >nul
