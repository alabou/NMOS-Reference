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
rem Note the node launchers have Windows counterparts only for the bare
rem (no-TLS) scenario, so this script is normally paired with a Linux node or
rem used to serve a TLS registry to nodes elsewhere on the network.
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
if not "%~1"=="" set "RAP=%~1"
if not "%~2"=="" set "REG_PORT=%~2"

rem In cmd.exe, %%1 treats an equals sign as an argument separator. Keep %%*
rem as text and peel off tokens with FOR /F so options such as --tct=1 survive.
set "REMAINING_ARGS=%*"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"

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
set /a "QUERY_PORT=REG_PORT - 1" >nul 2>&1
if errorlevel 1 (
  >&2 echo start-registry.bat: invalid registration port "%REG_PORT%"
  set "EXIT_CODE=64"
  goto done
)
set /a "WS_PORT=REG_PORT + 4" >nul 2>&1

rem Prefer the certificate subset bundled inside this repository, so a
rem standalone clone of nmos-reference runs without the wider workspace PKI.
rem That subset ships only the serials the quick-start and tutorials use;
rem anything else falls back to the workspace-level Certificates\ tree.
rem An explicit IPMX_CERT_ROOT always wins over both.
if defined IPMX_CERT_ROOT (
  set "CERT_ROOT=%IPMX_CERT_ROOT%"
) else if exist "%SCRIPT_DIR%Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00000.chain.pem" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else (
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
)
set "CERTS=%CERT_ROOT%\build.0"

rem One trust store carrying both the RSA and ECDSA roots, matching what the
rem shell launchers build.
set "CA=%TEMP%\ExampleRootCA-bundle.pem"
copy /b "%CERTS%\ExampleRootCA.pem" + "%CERTS%\ExampleRootCA.ec.pem" "%CA%" >nul || (
  >&2 echo start-registry.bat: cannot build CA bundle from "%CERTS%"
  set "EXIT_CODE=1"
  goto done
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
  >&2 echo start-registry.bat: Python 3.12 or newer was not found.
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
