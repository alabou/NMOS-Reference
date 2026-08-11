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

call :require_port "<registration-port>" "%REG_PORT%" 2 65531
if errorlevel 1 (
  set "EXIT_CODE=64"
  goto done
)
rem Checked above, so the arithmetic cannot fail here.
set /a "QUERY_PORT=REG_PORT - 1" >nul
set /a "WS_PORT=REG_PORT + 4" >nul 2>&1

call :find_python
if errorlevel 1 (
  >&2 echo start-registry-bare.bat: no Python 3.12+ interpreter found (checked NMOS_PYTHON_EXE, .venv, py -3, python.exe).
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

rem Validate a port that came from the command line. set /a is no defence: it
rem evaluates a variable's VALUE as an expression, so a non-numeric port
rem silently becomes 0 and a derived port -1, which Python's argparse accepts as
rem a valid int. The minimum leaves room for the ports derived from this one.
:require_port
set "PORT_LABEL=%~1"
set "PORT_VALUE=%~2"
set "PORT_MIN=%~3"
set "PORT_MAX=%~4"
echo %PORT_VALUE%|findstr /r /c:"^[0-9][0-9]*$" >nul
if errorlevel 1 goto require_port_bad
rem Six characters or more cannot be a port, and dropping those here keeps the
rem numeric comparisons below away from values they cannot represent.
if not "%PORT_VALUE:~5,1%"=="" goto require_port_bad
if %PORT_VALUE% LSS %PORT_MIN% goto require_port_bad
if %PORT_VALUE% GTR %PORT_MAX% goto require_port_bad
exit /b 0

:require_port_bad
>&2 echo start-registry-bare.bat: %PORT_LABEL% must be a whole number between %PORT_MIN% and %PORT_MAX%, got "%PORT_VALUE%"
exit /b 1

:find_python
rem Picking an interpreter is not the same as picking a usable one:
rem pyproject.toml requires >=3.12, while py.exe -3 selects the newest 3.x
rem installed and a bare python.exe is whatever came first on PATH. Both can be
rem older, and the failure then lands inside Python as a syntax or typing error
rem with no hint about the cause. Ask the interpreter before handing it the app.
call :pick_python
if errorlevel 1 exit /b 1
rem No < or > in the probe: cmd.exe can read them as redirection, and max()
rem expresses the same comparison without either character.
"%PYTHON_EXE%" %PYTHON_SELECTOR% -c "import sys; v = sys.version_info[:2]; sys.exit(0 if max(v, (3, 12)) == v else 1)" >nul 2>&1
if errorlevel 1 exit /b 2
exit /b 0

:pick_python
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
