@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows equivalent of start-node2-bare.sh.
rem Set NMOS_PYTHON_EXE to override Python discovery. NMOS_PYTHON_SELECTOR may
rem contain launcher arguments such as -3.12 when NMOS_PYTHON_EXE is py.exe.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-node2-bare.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "RDS_HOST=127.0.0.1"
set "RDS_REG_PORT=8444"
if defined NMOS_RDS_HOST set "RDS_HOST=%NMOS_RDS_HOST%"
if defined NMOS_RDS_REG_PORT set "RDS_REG_PORT=%NMOS_RDS_REG_PORT%"
set /a "RDS_QUERY_PORT=RDS_REG_PORT - 1" >nul 2>&1
if errorlevel 1 (
  >&2 echo start-node2-bare.bat: invalid registry port "%RDS_REG_PORT%"
  set "EXIT_CODE=64"
  goto done
)

call :find_python
if errorlevel 1 (
  >&2 echo start-node2-bare.bat: Python 3.12 or newer was not found.
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo NMOS Registry: %RDS_HOST%:%RDS_REG_PORT% ^(query port %RDS_QUERY_PORT%^)
"%PYTHON_EXE%" %PYTHON_SELECTOR% "%SCRIPT_DIR%nmos_node.py" ^
  --nodeSerialNumber SNX00002 ^
  --nodeAddr 127.0.0.1 ^
  --nodePort 7052 ^
  --nodeDisableTLS ^
  --rdsHost "%RDS_HOST%" ^
  --rdsRegistrationPort "%RDS_REG_PORT%" ^
  --rdsQueryPort "%RDS_QUERY_PORT%" ^
  --rdsDisableTLS ^
  --logFile nmos-node2.log ^
  --nodeConfig config10 ^
  --ipmx
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
