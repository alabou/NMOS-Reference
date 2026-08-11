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

if defined NMOS_RDS_HOST set "RDS_HOST=%NMOS_RDS_HOST%"
if defined NMOS_RDS_REG_PORT set "RDS_REG_PORT=%NMOS_RDS_REG_PORT%"

rem Positionals are assigned in :parse_positionals below, which stops at the
rem first --option. Taking them as %~1..%~4 here meant `--rap=2` with no
rem positionals landed in AS_HOST and was then dropped from the option list:
rem accepted in appearance, ignored in effect.

rem In cmd.exe, %%1 treats an equals sign as an argument separator. Keep %%* as
rem text and peel off tokens with FOR /F so options such as --rap=2 stay intact.
set "REMAINING_ARGS=%*"
set "POS_INDEX=0"
:parse_positionals
if not defined REMAINING_ARGS goto positionals_done
if %POS_INDEX% GEQ 4 goto positionals_done
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do (
  set "POS_ARG=%%~A"
  set "POS_REST=%%B"
)
if "%POS_ARG:~0,2%"=="--" goto positionals_done
if %POS_INDEX%==0 set "AS_HOST=%POS_ARG%"
if %POS_INDEX%==1 set "AS_PORT=%POS_ARG%"
if %POS_INDEX%==2 set "RDS_HOST=%POS_ARG%"
if %POS_INDEX%==3 set "RDS_REG_PORT=%POS_ARG%"
set /a "POS_INDEX+=1" >nul
set "REMAINING_ARGS=%POS_REST%"
goto parse_positionals
:positionals_done

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
call :require_port "as-port" "%AS_PORT%" 1 65535
if errorlevel 1 (
  set "EXIT_CODE=64"
  goto done
)
call :require_port "registry-port" "%RDS_REG_PORT%" 2 65535
if errorlevel 1 (
  set "EXIT_CODE=64"
  goto done
)
rem Checked above, so the arithmetic cannot fail here.
set /a "RDS_QUERY_PORT=RDS_REG_PORT - 1" >nul

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
  >&2 echo start-node1-bare.bat: no Python 3.12+ interpreter found ^(checked NMOS_PYTHON_EXE, .venv, py -3, python.exe^).
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
>&2 echo start-node1-bare.bat: %PORT_LABEL% must be a whole number between %PORT_MIN% and %PORT_MAX%, got "%PORT_VALUE%"
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
