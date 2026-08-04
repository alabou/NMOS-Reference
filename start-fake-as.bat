@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows equivalent of start-fake-as.sh.
rem
rem Test OAuth 2.0 Authorization Server -- the Keycloak-free path. No Docker.
rem
rem Usage:
rem   start-fake-as.bat [--port=P] [--tct=T] [--serial=S] [--control-port=P]
rem                     [--client-id=ID] [--client-secret=S]
rem                     [--operator=NAME] [--password=PW]
rem
rem   --port=P          Listen port (default 9443, same as start-keycloak.sh)
rem   --tct=T           TLS Certificate Type: 0=RSA (default), 1=ECDSA
rem   --serial=S        Node serial the issued tokens are scoped to
rem                     (default SNX00001). Sets the token aud entry and the
rem                     registered redirect URIs.
rem   --control-port=P  Controller UI port to register redirect URIs for
rem                     (default 5050, matching start-node1.bat)
rem   --client-id=ID    OAuth 2.0 client_id (default matches start-node1)
rem   --client-secret=S Client secret (default secret)
rem   --operator=NAME   Pre-canned sign-in account (default ipmx-operator)
rem   --password=PW     Its password (default admin)
rem
rem Requires hosts-file entries. This server is reached by DNS name because
rem its certificate carries DNS SANs and an IP literal matches none of them.
rem Add to C:\Windows\System32\drivers\etc\hosts (as Administrator):
rem
rem     127.0.0.1   XYZ-SNX00000
rem     127.0.0.1   XYZ-SNX00001
rem     127.0.0.1   XYZ-SNX00002
rem
rem Set IPMX_CERT_ROOT to relocate the Certificates tree.
rem Set IPMX_SECURITY_ROOT if the security/ project lives elsewhere.
rem Set NMOS_PYTHON_EXE to override Python discovery.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-fake-as.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "AS_PORT=9443"
set "TCT=0"
set "NODE_SERIAL=SNX00001"
set "CONTROL_PORT=5050"
set "CLIENT_ID=Example.Company.Device.Client.ABC.SNX00001.example.com"
set "CLIENT_SECRET=secret"
set "OPERATOR=ipmx-operator"
set "PASSWORD=admin"

rem In cmd.exe, %%1 treats an equals sign as an argument separator. Keep %%*
rem as text and peel off tokens with FOR /F so options such as --tct=1 survive.
set "REMAINING_ARGS=%*"

:parse_options
if not defined REMAINING_ARGS goto options_done
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do (
  set "ARG=%%~A"
  set "REMAINING_ARGS=%%B"
)
if /i "%ARG:~0,7%"=="--port=" (
  set "AS_PORT=%ARG:~7%"
  goto parse_options
)
if /i "%ARG:~0,6%"=="--tct=" (
  set "TCT=%ARG:~6%"
  goto parse_options
)
if /i "%ARG:~0,9%"=="--serial=" (
  set "NODE_SERIAL=%ARG:~9%"
  goto parse_options
)
if /i "%ARG:~0,15%"=="--control-port=" (
  set "CONTROL_PORT=%ARG:~15%"
  goto parse_options
)
if /i "%ARG:~0,12%"=="--client-id=" (
  set "CLIENT_ID=%ARG:~12%"
  goto parse_options
)
if /i "%ARG:~0,16%"=="--client-secret=" (
  set "CLIENT_SECRET=%ARG:~16%"
  goto parse_options
)
if /i "%ARG:~0,11%"=="--operator=" (
  set "OPERATOR=%ARG:~11%"
  goto parse_options
)
if /i "%ARG:~0,11%"=="--password=" (
  set "PASSWORD=%ARG:~11%"
  goto parse_options
)
>&2 echo start-fake-as.bat: unknown argument %ARG%
set "EXIT_CODE=64"
goto done

:options_done

rem Prefer the certificate subset bundled inside this repository, so a
rem standalone clone of nmos-reference runs without the wider workspace PKI.
rem SNX00000 is the reserved infrastructure serial: the registry and this
rem Authorization Server both present it. An explicit IPMX_CERT_ROOT wins.
if defined IPMX_CERT_ROOT (
  set "CERT_ROOT=%IPMX_CERT_ROOT%"
) else if exist "%SCRIPT_DIR%Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00000.chain.pem" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else (
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
)
set "CERTS=%CERT_ROOT%\build.0"

if "%TCT%"=="0" (
  set "AS_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00000.chain.pem"
  set "AS_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00000.key"
) else if "%TCT%"=="1" (
  set "AS_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00000.chain.ec.pem"
  set "AS_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00000.ec.key"
) else (
  >&2 echo start-fake-as.bat: unsupported --tct=%TCT%
  set "EXIT_CODE=64"
  goto done
)

if not exist "%AS_CERT%" (
  >&2 echo start-fake-as.bat: missing "%AS_CERT%"
  >&2 echo Set IPMX_CERT_ROOT to a Certificates tree carrying SNX00000.
  set "EXIT_CODE=66"
  goto done
)

rem The Authorization Server ships vendored in this repository, so a checkout
rem of nmos-reference alone can run the tutorial. When the security/ project
rem is alongside it is preferred, because that copy is the source of truth.
set "FAKE_AS="
if defined IPMX_SECURITY_ROOT (
  if exist "%IPMX_SECURITY_ROOT%\ipmx_fake_as.py" set "FAKE_AS=%IPMX_SECURITY_ROOT%\ipmx_fake_as.py"
)
if not defined FAKE_AS (
  if exist "%SCRIPT_DIR%..\security\ipmx_fake_as.py" set "FAKE_AS=%SCRIPT_DIR%..\security\ipmx_fake_as.py"
)
if not defined FAKE_AS (
  if exist "%SCRIPT_DIR%fake-as\ipmx_fake_as.py" set "FAKE_AS=%SCRIPT_DIR%fake-as\ipmx_fake_as.py"
)
if not defined FAKE_AS (
  >&2 echo start-fake-as.bat: no Authorization Server found.
  >&2 echo Looked for fake-as\ipmx_fake_as.py here and for a security\ project alongside.
  set "EXIT_CODE=66"
  goto done
)

rem Lowercase host: the HTTP layer normalises the Host header before the
rem Controller reads it, and redirect-URI matching is case-sensitive even
rem though TLS hostname matching is not (RFC 6125). Registering the uppercase
rem spelling would reject every real callback.
rem
rem cmd.exe has no case-folding operator, but its substring replacement
rem matches case-insensitively while substituting the replacement verbatim --
rem so replacing each lowercase letter with itself folds the string.
setlocal EnableDelayedExpansion
set "NODE_HOST=xyz-%NODE_SERIAL%"
for %%L in (a b c d e f g h i j k l m n o p q r s t u v w x y z) do set "NODE_HOST=!NODE_HOST:%%L=%%L!"
endlocal & set "NODE_HOST=%NODE_HOST%"

rem Exact-match redirect URIs -- no wildcards. IS-10 "Behaviour - Clients.md":
rem redirect URIs must be complete and must not use pattern-matching.
set REDIRECT_ARGS=--redirect-uri "https://%NODE_HOST%:%CONTROL_PORT%/controller/oauth2/callback" --redirect-uri "https://127.0.0.1:%CONTROL_PORT%/controller/oauth2/callback" --redirect-uri "https://localhost:%CONTROL_PORT%/controller/oauth2/callback"

call :find_python
if errorlevel 1 (
  >&2 echo start-fake-as.bat: Python 3.12 or newer was not found.
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo Authorization Server (test)   https://XYZ-SNX00000:%AS_PORT%/realms/TR-10-SEC
echo   metadata   /.well-known/oauth-authorization-server/realms/TR-10-SEC
echo   sign in as %OPERATOR% / %PASSWORD%
echo   client     %CLIENT_ID%
echo   tokens aud XYZ-%NODE_SERIAL%
echo.

"%PYTHON_EXE%" %PYTHON_SELECTOR% "%FAKE_AS%" ^
  --host XYZ-SNX00000 ^
  --port "%AS_PORT%" ^
  --cert "%AS_CERT%" ^
  --key "%AS_KEY%" ^
  --api-selector realms/TR-10-SEC ^
  --default-aud "XYZ-%NODE_SERIAL%" ^
  --client-id "%CLIENT_ID%" ^
  --client-secret "%CLIENT_SECRET%" ^
  %REDIRECT_ARGS% ^
  --operator-username "%OPERATOR%" ^
  --operator-password "%PASSWORD%"
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
