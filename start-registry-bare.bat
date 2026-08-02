@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows equivalent of start-registry-bare.sh.
rem
rem NMOS Registry with no TLS -- the "just try it" configuration. Pairs with
rem start-node1-bare.bat / start-node2-bare.bat:
rem
rem   start-registry-bare.bat     (window 1)
rem   start-node1-bare.bat        (window 2)
rem   start-node2-bare.bat        (window 3)
rem
rem then open http://127.0.0.1:5050/controller/
rem
rem Usage:
rem   start-registry-bare.bat [registration-port]
rem
rem   %1 = Registration API port (default 8444; query = %1-1, ws = %1+4).
rem        The node launchers default to 8444 and derive the query port the
rem        same way, so the defaults line up with no arguments on either side.
rem
rem Set NMOS_PYTHON_EXE to override Python discovery. NMOS_PYTHON_SELECTOR may
rem contain launcher arguments such as -3.12 when NMOS_PYTHON_EXE is py.exe.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-registry-bare.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "REG_PORT=8444"
if defined NMOS_RDS_REG_PORT set "REG_PORT=%NMOS_RDS_REG_PORT%"
if not "%~1"=="" set "REG_PORT=%~1"

set /a "QUERY_PORT=REG_PORT - 1" >nul 2>&1
if errorlevel 1 (
  >&2 echo start-registry-bare.bat: invalid registration port "%REG_PORT%"
  set "EXIT_CODE=64"
  goto done
)
set /a "WS_PORT=REG_PORT + 4" >nul 2>&1

call :find_python
if errorlevel 1 (
  >&2 echo start-registry-bare.bat: Python 3.12 or newer was not found.
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo NMOS Registry: registration %REG_PORT%, query %QUERY_PORT%, websocket %WS_PORT%
"%PYTHON_EXE%" %PYTHON_SELECTOR% "%SCRIPT_DIR%nmos_registry.py" ^
  --registryAddr 127.0.0.1 ^
  --registryDisableTLS ^
  --registrationPort "%REG_PORT%" ^
  --queryPort "%QUERY_PORT%" ^
  --queryWebSocketPort "%WS_PORT%" ^
  --logFile nmos-registry.log
set "EXIT_CODE=%ERRORLEVEL%"
goto done

:find_python
set "PYTHON_EXE="
set "PYTHON_SELECTOR="
if defined NMOS_PYTHON_EXE (
  set "PYTHON_EXE=%NMOS_PYTHON_EXE%"
  if defined NMOS_PYTHON_SELECTOR set "PYTHON_SELECTOR=%NMOS_PYTHON_SELECTOR%"
  exit /b 0
)
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
  exit /b 0
)
where py.exe >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_EXE=py.exe"
  set "PYTHON_SELECTOR=-3"
  exit /b 0
)
where python.exe >nul 2>&1
if not errorlevel 1 (
  set "PYTHON_EXE=python.exe"
  exit /b 0
)
exit /b 1

:done
popd
endlocal & exit /b %EXIT_CODE%
