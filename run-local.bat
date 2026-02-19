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
set LOCALLM_ENDPOINT=http://localhost:8000
set LOCALTTS_ENDPOINT=http://localhost:5000
set LOCALML_ENDPOINT=http://localhost:8001
set REMOTE_SAVE_ENDPOINT=http://localhost:7000
set SENSOR_MOCK_ENABLED=true
set SETTINGS_DIR=%ROOT%data
set DATA_DIR=%ROOT%data

echo [*] Starting remote_save_mock on port 7000...
start "remotesave-mock" cmd /k "cd /d %ROOT%remote_save_mock && call %VENV% && uvicorn main:app --host 0.0.0.0 --port 7000 --reload"

echo [*] Starting localml_mock on port 8001...
start "localml-mock" cmd /k "cd /d %ROOT%localml_mock && call %VENV% && uvicorn main:app --host 0.0.0.0 --port 8001 --reload"

echo [*] Starting locallm_mock on port 8000...
start "locallm-mock" cmd /k "cd /d %ROOT%locallm_mock && call %VENV% && uvicorn main:app --host 0.0.0.0 --port 8000 --reload"

echo [*] Starting main app on port 8080...
timeout /t 2 >nul
start "rpicoffee-app" cmd /k "cd /d %ROOT%app && call %VENV% && set LOCALLM_ENDPOINT=http://localhost:8000 && set LOCALTTS_ENDPOINT=http://localhost:5000 && set LOCALML_ENDPOINT=http://localhost:8001 && set REMOTE_SAVE_ENDPOINT=http://localhost:7000 && set SENSOR_MOCK_ENABLED=true && set SETTINGS_DIR=%ROOT%data && set DATA_DIR=%ROOT%data && uvicorn main:app --host 0.0.0.0 --port 8080 --reload"

echo.
echo ========================================
echo   All services starting!
echo ========================================
echo.
echo   Admin UI:     http://localhost:8080/admin/
echo   Password:     1234
echo   localml:      http://localhost:8001/docs
echo   locallm:      http://localhost:8000/docs
echo.
echo   Note: localtts is skipped locally (requires Piper/Linux).
echo         The pipeline will run steps 1-2 and skip TTS.
echo.
echo   Press any key to close this launcher window.
pause >nul
