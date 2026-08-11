@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows equivalent of start-registry.sh.
rem
rem NMOS Registry with TLS, optionally mutual TLS, optionally OAuth 2.0 on the
rem Query API.
rem
rem Usage:
rem   start-registry.bat [rap] [registration-port] [--oauth2] [--as-host=H] [--as-port=P] [--tct=T]
rem
rem   %1 = Registry Access Policy for the Registration API (default 1)
rem          1  Unrestricted Registration, server-authenticated TLS
rem          2  Restricted Registration, mutual TLS
rem        RAP=0 (plain HTTP) is start-registry-bare.bat.
rem   %2 = Registration API port (default 8444; query = %2-1, ws = %2+4)
rem
rem   --oauth2      Require OAuth 2.0 on the Query API in addition to TLS.
rem   --as-host=H   Authorization server host (default XYZ-SNX00000)
rem   --as-port=P   Authorization server port (default 9443)
rem   --tct=T       TLS Certificate Type: 0=RSA (default), 1=ECDSA
rem   --nap=N       Query API access policy (default 2)
rem                   1  Unrestricted Read Only -- reads open to any client
rem                      trusting the registry cert; subscription create and
rem                      delete still need a client certificate. Use this to
rem                      browse the Query API without a client cert in the
rem                      browser. Not allowed with --oauth2.
rem                   2  Restricted Read Write -- mutual TLS for everything.
rem
rem TR-10-SEC: the Registration API must never require OAuth 2.0 (:105), so
rem --oauth2 deliberately affects the Query API only.
rem
rem Set IPMX_CERT_ROOT to relocate the Certificates tree.
rem Set NMOS_PYTHON_EXE to override Python discovery.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-registry.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "RAP=1"
set "REG_PORT=8444"
rem Positionals are assigned in :parse_positionals below, which stops at the
rem first --option. Taking them as %~1..%~4 here meant `--rap=2` with no
rem positionals landed in AS_HOST and was then dropped from the option list:
rem accepted in appearance, ignored in effect.

rem In cmd.exe, %%1 treats an equals sign as an argument separator. Keep %%*
rem as text and peel off tokens with FOR /F so options such as --tct=1 survive.
set "REMAINING_ARGS=%*"
set "POS_INDEX=0"
:parse_positionals
if not defined REMAINING_ARGS goto positionals_done
if %POS_INDEX% GEQ 2 goto positionals_done
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do (
  set "POS_ARG=%%~A"
  set "POS_REST=%%B"
)
if "%POS_ARG:~0,2%"=="--" goto positionals_done
if %POS_INDEX%==0 set "RAP=%POS_ARG%"
if %POS_INDEX%==1 set "REG_PORT=%POS_ARG%"
set /a "POS_INDEX+=1" >nul
set "REMAINING_ARGS=%POS_REST%"
goto parse_positionals
:positionals_done

set "AS_HOST=XYZ-SNX00000"
set "AS_PORT=9443"
set "TCT=0"
set "NAP=2"
set "USE_OAUTH2=0"

:parse_options
if not defined REMAINING_ARGS goto options_done
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do (
  set "ARG=%%~A"
  set "REMAINING_ARGS=%%B"
)
if /i "%ARG%"=="--oauth2" (
  set "USE_OAUTH2=1"
  goto parse_options
)
if /i "%ARG:~0,10%"=="--as-host=" (
  set "AS_HOST=%ARG:~10%"
  goto parse_options
)
if /i "%ARG:~0,10%"=="--as-port=" (
  set "AS_PORT=%ARG:~10%"
  goto parse_options
)
if /i "%ARG:~0,6%"=="--tct=" (
  set "TCT=%ARG:~6%"
  goto parse_options
)
if /i "%ARG:~0,6%"=="--nap=" (
  set "NAP=%ARG:~6%"
  goto parse_options
)
>&2 echo start-registry.bat: unknown argument %ARG%
set "EXIT_CODE=64"
goto done

:options_done
call :require_port "registration-port" "%REG_PORT%" 2 65531
if errorlevel 1 (
  set "EXIT_CODE=64"
  goto done
)
rem Checked above, so the arithmetic cannot fail here.
set /a "QUERY_PORT=REG_PORT - 1" >nul
set /a "WS_PORT=REG_PORT + 4" >nul 2>&1

rem Prefer the certificate subset bundled inside this repository, so a
rem standalone clone of nmos-reference runs without the wider workspace PKI.
rem That subset ships only the serials the quick-start and tutorials use;
rem anything else falls back to the workspace-level Certificates\ tree.
rem An explicit IPMX_CERT_ROOT always wins over both.
set "CERT_PROBE=pem\ExampleDeviceServer.ABC.SNX00000.chain.pem"
if defined IPMX_CERT_ROOT (
  set "CERT_ROOT=%IPMX_CERT_ROOT%"
) else if exist "%SCRIPT_DIR%Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00000.chain.pem" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else if exist "%SCRIPT_DIR%..\Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00000.chain.pem" (
  rem The workspace PKI carries serials this checkout does not ship, which is
  rem how the IPMX security test suite supplies them, so the fallback stays --
  rem but it announces itself. The silent version hid a missing serial through
  rem an entire bring-up.
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
  >&2 echo start-registry.bat: %CERT_PROBE% is not in this checkout - using the workspace PKI.
) else (
  >&2 echo start-registry.bat: missing build.0\%CERT_PROBE%
  >&2 echo   Searched "%SCRIPT_DIR%Certificates" and "%SCRIPT_DIR%..\Certificates".
  >&2 echo   Set IPMX_CERT_ROOT to a Certificates tree that carries it.
  set "EXIT_CODE=66"
  goto done
)
set "CERTS=%CERT_ROOT%\build.0"

rem One trust store carrying both the RSA and ECDSA roots, matching what the
rem shell launchers use. Prefer the copy that ships in Certificates\ and derive
rem one only when the resolved PKI has none -- a workspace tree, or an
rem IPMX_CERT_ROOT pointing elsewhere. Deriving unconditionally wrote a single
rem shared %TEMP% path from every launcher, so two starting at once could have
rem one truncate the file while the other's Python was still reading it.
set "CA=%CERTS%\ExampleRootCA-bundle.pem"
if not exist "%CA%" (
  call :derive_ca
  if errorlevel 1 (
    set "EXIT_CODE=66"
    goto done
  )
)

rem SNX00000 is the reserved infrastructure serial in this PKI.
if "%TCT%"=="0" (
  set "REG_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00000.chain.pem"
  set "REG_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00000.key"
) else if "%TCT%"=="1" (
  set "REG_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00000.chain.ec.pem"
  set "REG_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00000.ec.key"
) else (
  >&2 echo start-registry.bat: unsupported --tct=%TCT%
  set "EXIT_CODE=64"
  goto done
)

rem The Registration trust anchor is what selects RAP 1 from RAP 2.
if "%RAP%"=="1" (
  set "REG_CA_FLAGS="
) else if "%RAP%"=="2" (
  set REG_CA_FLAGS=--registrationTrustedRootCA "%CA%"
) else if "%RAP%"=="0" (
  >&2 echo start-registry.bat: RAP=0 ^(plain HTTP^) is start-registry-bare.bat
  set "EXIT_CODE=64"
  goto done
) else (
  >&2 echo start-registry.bat: unsupported RAP=%RAP%
  set "EXIT_CODE=64"
  goto done
)

rem The Query API's own access policy, classified as a Node's API is -- see
rem nmos_registry.py::classify_query_nap. Both modes accept client
rem certificates; they differ in what an unauthenticated client may do.
if "%NAP%"=="1" (
  set QUERY_CA_FLAGS=--queryTrustedRootCA "%CA%" --queryOptionalClientAuth
) else if "%NAP%"=="2" (
  set QUERY_CA_FLAGS=--queryTrustedRootCA "%CA%"
) else if "%NAP%"=="0" (
  >&2 echo start-registry.bat: NAP=0 ^(plain HTTP^) is start-registry-bare.bat
  set "EXIT_CODE=64"
  goto done
) else (
  >&2 echo start-registry.bat: unsupported --nap=%NAP%
  set "EXIT_CODE=64"
  goto done
)

rem Unrestricted Read Only is not available under OAuth 2.0: the
rem specification requires even read access to come from the OAuth 2.0
rem authorizations, so accepting --nap=1 here would mislead the operator.
if "%NAP%"=="1" if "%USE_OAUTH2%"=="1" (
  >&2 echo start-registry.bat: --nap=1 is not allowed with --oauth2; use --nap=2
  set "EXIT_CODE=64"
  goto done
)

if "%USE_OAUTH2%"=="1" (
  set OAUTH2_FLAGS=--oauth2 --oauth2Host "%AS_HOST%" --oauth2Port "%AS_PORT%" --oauth2TrustedRootCA "%CA%" --oauth2ApiSelector realms/TR-10-SEC
) else (
  set "OAUTH2_FLAGS="
)

call :find_python
if errorlevel 1 (
  >&2 echo start-registry.bat: no Python 3.12+ interpreter found ^(checked NMOS_PYTHON_EXE, .venv, py -3, python.exe^).
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo NMOS Registry: RAP=%RAP% registration %REG_PORT%, query %QUERY_PORT%, websocket %WS_PORT%
"%PYTHON_EXE%" %PYTHON_SELECTOR% "%SCRIPT_DIR%nmos_registry.py" ^
  --registryAddr 127.0.0.1 ^
  --registrySerialNumber SNX00000 ^
  --registryCertificate "%REG_CERT%" ^
  --registryKey "%REG_KEY%" ^
  --registrationPort "%REG_PORT%" ^
  --queryPort "%QUERY_PORT%" ^
  --queryWebSocketPort "%WS_PORT%" ^
  %REG_CA_FLAGS% ^
  %QUERY_CA_FLAGS% ^
  %OAUTH2_FLAGS% ^
  --trustedRootCA "%CA%" ^
  --logFile nmos-registry.log
set "EXIT_CODE=%ERRORLEVEL%"
goto done

rem Build the combined trust store from the two roots of the resolved PKI.
rem A subroutine rather than an inline block: each line here is parsed as it
rem runs, so the CA path set below is visible to the copy that follows. Inside
rem a parenthesised block under DisableDelayedExpansion it would not be.
:derive_ca
if not exist "%CERTS%\ExampleRootCA.pem" (
  >&2 echo start-registry.bat: missing "%CERTS%\ExampleRootCA.pem"
  >&2 echo   Set IPMX_CERT_ROOT to a Certificates tree that carries the roots.
  exit /b 1
)
if not exist "%CERTS%\ExampleRootCA.ec.pem" (
  >&2 echo start-registry.bat: missing "%CERTS%\ExampleRootCA.ec.pem"
  exit /b 1
)
rem A name of its own per run, so concurrent launchers cannot overwrite each
rem other's bundle while it is being read.
set "CA=%TEMP%\ExampleRootCA-bundle.%RANDOM%%RANDOM%.pem"
copy /b "%CERTS%\ExampleRootCA.pem" + "%CERTS%\ExampleRootCA.ec.pem" "%CA%" >nul
if errorlevel 1 (
  >&2 echo start-registry.bat: cannot build a CA bundle from "%CERTS%"
  exit /b 1
)
exit /b 0

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
>&2 echo start-registry.bat: %PORT_LABEL% must be a whole number between %PORT_MIN% and %PORT_MAX%, got "%PORT_VALUE%"
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
