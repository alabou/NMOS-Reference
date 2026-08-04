@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows equivalent of start-node1-bare.sh.
rem
rem Usage:
rem   start-node1-bare.bat [as-host] [as-port] [rds-host] [rds-port] [options]
rem
rem Options retained for command-line compatibility with the shell launcher:
rem   --nap=N  --rap=R  --oaim=O  --tct=T  --split-controls
rem Set NMOS_PYTHON_EXE to override Python discovery. NMOS_PYTHON_SELECTOR may
rem contain launcher arguments such as -3.12 when NMOS_PYTHON_EXE is py.exe.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-node1-bare.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "AS_HOST=XYZ-SNX00000"
set "AS_PORT=9443"
set "RDS_HOST=127.0.0.1"
set "RDS_REG_PORT=8444"

if defined NMOS_RDS_HOST (
  set "RDS_HOST=%NMOS_RDS_HOST%"
) else (
  for /f "tokens=1" %%I in ('wsl.exe hostname -I 2^>nul') do if not defined WSL_RDS_HOST (
    set "WSL_RDS_HOST=%%I"
    set "RDS_HOST=%%I"
  )
)
if defined NMOS_RDS_REG_PORT set "RDS_REG_PORT=%NMOS_RDS_REG_PORT%"

if not "%~1"=="" set "AS_HOST=%~1"
if not "%~2"=="" set "AS_PORT=%~2"
if not "%~3"=="" set "RDS_HOST=%~3"
if not "%~4"=="" set "RDS_REG_PORT=%~4"

rem In cmd.exe, %%1 treats an equals sign as an argument separator. Keep %%* as
rem text and peel off tokens with FOR /F so options such as --rap=2 stay intact.
set "REMAINING_ARGS=%*"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"

set "NAP=0"
set "RAP=0"
set "OAIM=0"
set "TCT=0"
set "SPLIT_CONTROLS=0"

:parse_options
if not defined REMAINING_ARGS goto options_done
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do (
  set "ARG=%%~A"
  set "REMAINING_ARGS=%%B"
)
if /i "%ARG:~0,6%"=="--nap=" (
  set "NAP=%ARG:~6%"
  goto parse_options
)
if /i "%ARG:~0,6%"=="--rap=" (
  set "RAP=%ARG:~6%"
  goto parse_options
)
if /i "%ARG:~0,7%"=="--oaim=" (
  set "OAIM=%ARG:~7%"
  goto parse_options
)
if /i "%ARG:~0,6%"=="--tct=" (
  set "TCT=%ARG:~6%"
  goto parse_options
)
if /i "%ARG%"=="--split-controls" (
  set "SPLIT_CONTROLS=1"
  goto parse_options
)
>&2 echo start-node1-bare.bat: unknown argument %ARG%
set "EXIT_CODE=64"
goto done

:options_done
set /a "RDS_QUERY_PORT=RDS_REG_PORT - 1" >nul 2>&1
if errorlevel 1 (
  >&2 echo start-node1-bare.bat: invalid registry port "%RDS_REG_PORT%"
  set "EXIT_CODE=64"
  goto done
)

rem Prefer the certificate subset bundled inside this repository, so a
rem standalone clone of nmos-reference runs without the wider workspace PKI.
rem That subset ships only the serials the quick-start and tutorials use;
rem anything else falls back to the workspace-level Certificates\ tree.
rem An explicit IPMX_CERT_ROOT always wins over both.
if defined IPMX_CERT_ROOT (
  set "CERT_ROOT=%IPMX_CERT_ROOT%"
) else if exist "%SCRIPT_DIR%Certificates\build.0\ExampleRootCA.pem" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else (
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
)
set "CERTS=%CERT_ROOT%\build.0"

if "%RAP%"=="0" (
  set "RDS_FLAGS=--rdsDisableTLS"
) else if "%RAP%"=="1" (
  set "RDS_FLAGS="
) else if "%RAP%"=="2" (
  set RDS_FLAGS=--rdsClientCertificate "%CERTS%\pem\ExampleDeviceClient.ABC.SNX00001.chain.pem" --rdsClientKey "%CERTS%\key\ExampleDeviceClient.ABC.SNX00001.key"
) else (
  >&2 echo start-node1-bare.bat: unsupported --rap=%RAP%
  set "EXIT_CODE=64"
  goto done
)

call :find_python
if errorlevel 1 (
  >&2 echo start-node1-bare.bat: Python 3.12 or newer was not found.
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo NMOS Registry: %RDS_HOST%:%RDS_REG_PORT% ^(query port %RDS_QUERY_PORT%^)
"%PYTHON_EXE%" %PYTHON_SELECTOR% "%SCRIPT_DIR%nmos_node.py" ^
  --nodeSerialNumber SNX00001 ^
  --nodeAddr 127.0.0.1 ^
  --nodePort 7051 ^
  --nodeControlPort 5050 ^
  --nodeDisableTLS ^
  --controllerAdminPassword admin ^
  --rdsHost "%RDS_HOST%" ^
  --rdsRegistrationPort "%RDS_REG_PORT%" ^
  --rdsQueryPort "%RDS_QUERY_PORT%" ^
  %RDS_FLAGS% ^
  --logFile nmos-node1.log ^
  --debug-in-depth ^
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
