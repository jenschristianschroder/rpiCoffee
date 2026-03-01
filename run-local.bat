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
set CLASSIFIER_PORT=8001
set LLM_PORT=8002
set TTS_PORT=5050
set REMOTE_SAVE_PORT=7000
set APP_PORT=8080
set CLASSIFIER_ENDPOINT=http://localhost:%CLASSIFIER_PORT%
set LLM_ENDPOINT=http://localhost:%LLM_PORT%
set TTS_ENDPOINT=http://localhost:%TTS_PORT%
set REMOTE_SAVE_ENDPOINT=http://localhost:%REMOTE_SAVE_PORT%
set SENSOR_MODE=mock
set SETTINGS_DIR=%ROOT%data
set DATA_DIR=%ROOT%data

echo [*] Starting classifier (mock) on port %CLASSIFIER_PORT%...
start "classifier" cmd /k "cd /d %ROOT%services\classifier && call %VENV% && uvicorn main:app --host 0.0.0.0 --port %CLASSIFIER_PORT% --reload"

echo [*] Starting LLM on port %LLM_PORT%...
start "llm" cmd /k "cd /d %ROOT%services\llm && call %VENV% && python server.py --port %LLM_PORT%"

echo [*] Starting main app on port %APP_PORT%...
timeout /t 2 >nul
start "rpicoffee-app" cmd /k "cd /d %ROOT%app && call %VENV% && set CLASSIFIER_ENDPOINT=http://localhost:%CLASSIFIER_PORT% && set LLM_ENDPOINT=http://localhost:%LLM_PORT% && set TTS_ENDPOINT=http://localhost:%TTS_PORT% && set REMOTE_SAVE_ENDPOINT=http://localhost:%REMOTE_SAVE_PORT% && set SENSOR_MODE=mock && set SETTINGS_DIR=%ROOT%data && set DATA_DIR=%ROOT%data && uvicorn main:app --host 0.0.0.0 --port %APP_PORT% --reload"

echo.
echo ========================================
echo   All services starting!
echo ========================================
echo.
echo   Admin UI:     http://localhost:%APP_PORT%/admin/
echo   Classifier:   http://localhost:%CLASSIFIER_PORT%/docs
echo   LLM:          http://localhost:%LLM_PORT%/health
echo.
echo   Note: TTS is skipped locally (requires Piper/Linux).
echo         The pipeline will run steps 1-2 and skip TTS.
echo.
echo   Press any key to close this launcher window.
pause >nul
