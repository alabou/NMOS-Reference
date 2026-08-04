@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows equivalent of start-node2.sh.
rem
rem Configuration C (mTLS + OAuth 2.0) -- TR-10-SEC 12.3 RAAM=2.
rem
rem No Controller UI here: node 1 serves one on 5050 and shows every node
rem it discovers through the registry, this one included.
rem
rem Usage:
rem   start-node2.bat [as-host] [as-port] [rds-host] [rds-port]
rem                  [--nap=N] [--rap=R] [--oaim=O] [--tct=T]
rem
rem   %1 = OAuth 2.0 authorization server host (default XYZ-SNX00000)
rem   %2 = OAuth 2.0 authorization server port (default 9443)
rem   %3 = Registry host (default 127.0.0.1)
rem   %4 = Registry registration port (default 8444; query = %4-1)
rem
rem   --nap=N   Node Access Policy. Config C pins NAP=2 per 9.2.
rem   --rap=R   Registry Access Policy: 0=HTTP, 1=server-TLS, 2=mTLS.
rem   --oaim=O  OAuth2 Audience ID Mode: 0=serial, 1=cert, 2=either.
rem   --tct=T   TLS Cert Type: 0=RSA (default), 1=ECDSA.
rem
rem Requires hosts-file entries. This node addresses its peers by DNS name
rem because the certificates carry DNS SANs (XYZ-SNX000nn) and an IP literal
rem matches none of them. Add to C:\Windows\System32\drivers\etc\hosts
rem (as Administrator):
rem
rem     127.0.0.1   XYZ-SNX00000    registry + Authorization Server
rem     127.0.0.1   XYZ-SNX00001    node 1 + Controller UI
rem     127.0.0.1   XYZ-SNX00002    node 2
rem
rem Passing 127.0.0.1 as the registry host fails TLS verification under
rem --rap=1 or --rap=2 for the same reason -- pass XYZ-SNX00000.
rem
rem Set IPMX_CERT_ROOT to relocate the Certificates tree.
rem Set NMOS_PYTHON_EXE to override Python discovery.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-node2.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "AS_HOST=XYZ-SNX00000"
set "AS_PORT=9443"
set "RDS_HOST=127.0.0.1"
set "RDS_REG_PORT=8444"
if not "%~1"=="" set "AS_HOST=%~1"
if not "%~2"=="" set "AS_PORT=%~2"
if not "%~3"=="" set "RDS_HOST=%~3"
if not "%~4"=="" set "RDS_REG_PORT=%~4"

rem In cmd.exe, %%1 treats an equals sign as an argument separator. Keep %%*
rem as text and peel the four positionals off with FOR /F so options such as
rem --tct=1 survive intact.
set "REMAINING_ARGS=%*"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"
for /f "tokens=1,*" %%A in ("%REMAINING_ARGS%") do set "REMAINING_ARGS=%%B"

set "NAP=2"
set "RAP=0"
set "OAIM=0"
set "TCT=0"

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
>&2 echo start-node2.bat: unknown argument %ARG%
set "EXIT_CODE=64"
goto done

:options_done
if not "%NAP%"=="2" (
  >&2 echo start-node2.bat: Config C ^(RAAM=2^) pins NAP=2; got --nap=%NAP%
  set "EXIT_CODE=64"
  goto done
)

set /a "RDS_QUERY_PORT=RDS_REG_PORT - 1" >nul 2>&1
if errorlevel 1 (
  >&2 echo start-node2.bat: invalid registration port "%RDS_REG_PORT%"
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
) else if exist "%SCRIPT_DIR%Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00002.chain.pem" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else (
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
)
set "CERTS=%CERT_ROOT%\build.0"

rem One trust store carrying both the RSA and ECDSA roots, matching what the
rem shell launchers build.
set "CA=%TEMP%\ExampleRootCA-bundle.pem"
copy /b "%CERTS%\ExampleRootCA.pem" + "%CERTS%\ExampleRootCA.ec.pem" "%CA%" >nul || (
  >&2 echo start-node2.bat: cannot build CA bundle from "%CERTS%"
  set "EXIT_CODE=1"
  goto done
)

if "%TCT%"=="0" (
  set "NODE_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00002.chain.pem"
  set "NODE_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00002.key"
) else if "%TCT%"=="2" (
  set "NODE_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00002.chain.pem"
  set "NODE_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00002.key"
) else if "%TCT%"=="1" (
  set "NODE_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00002.chain.ec.pem"
  set "NODE_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00002.ec.key"
) else (
  >&2 echo start-node2.bat: unsupported --tct=%TCT%
  set "EXIT_CODE=64"
  goto done
)

if "%OAIM%"=="0" (
  set "OAIM_FLAG=serial"
) else if "%OAIM%"=="1" (
  set "OAIM_FLAG=cert"
) else if "%OAIM%"=="2" (
  set "OAIM_FLAG=either"
) else (
  >&2 echo start-node2.bat: unsupported --oaim=%OAIM%
  set "EXIT_CODE=64"
  goto done
)

if "%RAP%"=="0" (
  set "RDS_FLAGS=--rdsDisableTLS"
) else if "%RAP%"=="1" (
  set "RDS_FLAGS="
) else if "%RAP%"=="2" (
  set RDS_FLAGS=--rdsClientCertificate "%CERTS%\pem\ExampleDeviceClient.ABC.SNX00002.chain.pem" --rdsClientKey "%CERTS%\key\ExampleDeviceClient.ABC.SNX00002.key"
) else (
  >&2 echo start-node2.bat: unsupported --rap=%RAP%
  set "EXIT_CODE=64"
  goto done
)

call :find_python
if errorlevel 1 (
  >&2 echo start-node2.bat: Python 3.12 or newer was not found.
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo Node SNX00002: Config C ^(mTLS + OAuth 2.0^), NAP=%NAP% RAP=%RAP% OAIM=%OAIM% TCT=%TCT%
"%PYTHON_EXE%" %PYTHON_SELECTOR% "%SCRIPT_DIR%nmos_node.py" ^
  --nodeSerialNumber SNX00002 ^
  --nodeAddr XYZ-SNX00002 ^
  --nodePort 7052 ^
  --nodeCertificate "%NODE_CERT%" ^
  --nodeKey "%NODE_KEY%" ^
  --nodeTrustedRootCA "%CA%" ^
  --nodeClientCertificate "%CERTS%\pem\ExampleDeviceClient.ABC.SNX00002.chain.pem" ^
  --nodeClientKey "%CERTS%\key\ExampleDeviceClient.ABC.SNX00002.key" ^
  --oauth2 ^
  --oauth2Host "%AS_HOST%" ^
  --oauth2Port "%AS_PORT%" ^
  --oauth2TrustedRootCA "%CA%" ^
  --oauth2ClientSecret secret ^
  --oauth2ApiSelector realms/TR-10-SEC ^
  --oauth2AudienceMode "%OAIM_FLAG%" ^
  --oauth2ClientId Example.Company.Device.Client.ABC.SNX00002.example.com ^
  --rdsHost "%RDS_HOST%" ^
  --rdsRegistrationPort "%RDS_REG_PORT%" ^
  --rdsQueryPort "%RDS_QUERY_PORT%" ^
  %RDS_FLAGS% ^
  --trustedRootCA "%CA%" ^
  --debug-in-depth ^
  --nodeConfig config_av_usb_tb_A
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
