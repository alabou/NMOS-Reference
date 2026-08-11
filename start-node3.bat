@echo off
setlocal EnableExtensions DisableDelayedExpansion
if not defined PYTHONUTF8 set PYTHONUTF8=1

rem Windows counterpart of start-node3.sh, built to the Config C contract.
rem
rem Configuration C (mTLS + OAuth 2.0) -- TR-10-SEC 12.3 RAAM=2.
rem
rem Unlike start-node3.sh -- which runs this node against a plaintext
rem registry (--rdsDisableTLS) and carries no client certificate -- this
rem launcher mirrors start-node2.bat, so node 3 joins the same secured rig
rem as nodes 1 and 2.
rem
rem No Controller UI here: node 1 serves one on 5050 and shows every node
rem it discovers through the registry, this one included.
rem
rem Usage:
rem   start-node3.bat [as-host] [as-port] [rds-host] [rds-port]
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
rem     127.0.0.1   XYZ-SNX00003    node 3
rem
rem Passing 127.0.0.1 as the registry host fails TLS verification under
rem --rap=1 or --rap=2 for the same reason -- pass XYZ-SNX00000.
rem
rem Set IPMX_CERT_ROOT to relocate the Certificates tree.
rem Set NMOS_PYTHON_EXE to override Python discovery.

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%" >nul || (
  >&2 echo start-node3.bat: cannot enter "%SCRIPT_DIR%"
  exit /b 1
)

set "AS_HOST=XYZ-SNX00000"
set "AS_PORT=9443"
set "RDS_HOST=127.0.0.1"
set "RDS_REG_PORT=8444"
rem Positionals are assigned in :parse_positionals below, which stops at the
rem first --option. Taking them as %~1..%~4 here meant `--rap=2` with no
rem positionals landed in AS_HOST and was then dropped from the option list:
rem accepted in appearance, ignored in effect.

rem In cmd.exe, %%1 treats an equals sign as an argument separator, so options
rem such as --tct=1 would arrive split. Keep %%* as text and walk it with FOR /F
rem instead, taking leading tokens as positionals only until the first --option.
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
>&2 echo start-node3.bat: unknown argument %ARG%
set "EXIT_CODE=64"
goto done

:options_done
if not "%NAP%"=="2" (
  >&2 echo start-node3.bat: Config C ^(RAAM=2^) pins NAP=2; got --nap=%NAP%
  set "EXIT_CODE=64"
  goto done
)

call :require_port "as-port" "%AS_PORT%" 1 65535
if errorlevel 1 (
  set "EXIT_CODE=64"
  goto done
)
call :require_port "registration-port" "%RDS_REG_PORT%" 2 65535
if errorlevel 1 (
  set "EXIT_CODE=64"
  goto done
)
rem Checked above, so the arithmetic cannot fail here.
set /a "RDS_QUERY_PORT=RDS_REG_PORT - 1" >nul

rem Prefer the certificate subset bundled inside this repository, so a
rem standalone clone of nmos-reference runs without the wider workspace PKI.
rem An explicit IPMX_CERT_ROOT always wins over both.
set "CERT_PROBE=pem\ExampleDeviceServer.ABC.SNX00003.chain.pem"
if defined IPMX_CERT_ROOT (
  set "CERT_ROOT=%IPMX_CERT_ROOT%"
) else if exist "%SCRIPT_DIR%Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00003.chain.pem" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else if exist "%SCRIPT_DIR%..\Certificates\build.0\pem\ExampleDeviceServer.ABC.SNX00003.chain.pem" (
  rem The workspace PKI carries serials this checkout does not ship, which is
  rem how the IPMX security test suite supplies them, so the fallback stays --
  rem but it announces itself. The silent version hid a missing serial through
  rem an entire bring-up.
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
  >&2 echo start-node3.bat: %CERT_PROBE% is not in this checkout - using the workspace PKI.
) else (
  >&2 echo start-node3.bat: missing build.0\%CERT_PROBE%
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

if "%TCT%"=="0" (
  set "NODE_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00003.chain.pem"
  set "NODE_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00003.key"
) else if "%TCT%"=="2" (
  set "NODE_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00003.chain.pem"
  set "NODE_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00003.key"
) else if "%TCT%"=="1" (
  set "NODE_CERT=%CERTS%\pem\ExampleDeviceServer.ABC.SNX00003.chain.ec.pem"
  set "NODE_KEY=%CERTS%\key\ExampleDeviceServer.ABC.SNX00003.ec.key"
) else (
  >&2 echo start-node3.bat: unsupported --tct=%TCT%
  set "EXIT_CODE=64"
  goto done
)

if not exist "%NODE_CERT%" (
  >&2 echo start-node3.bat: missing "%NODE_CERT%"
  >&2 echo Set IPMX_CERT_ROOT to a Certificates tree carrying SNX00003.
  set "EXIT_CODE=66"
  goto done
)

if "%OAIM%"=="0" (
  set "OAIM_FLAG=serial"
) else if "%OAIM%"=="1" (
  set "OAIM_FLAG=cert"
) else if "%OAIM%"=="2" (
  set "OAIM_FLAG=either"
) else (
  >&2 echo start-node3.bat: unsupported --oaim=%OAIM%
  set "EXIT_CODE=64"
  goto done
)

if "%RAP%"=="0" (
  set "RDS_FLAGS=--rdsDisableTLS"
) else if "%RAP%"=="1" (
  set "RDS_FLAGS="
) else if "%RAP%"=="2" (
  set RDS_FLAGS=--rdsClientCertificate "%CERTS%\pem\ExampleDeviceClient.ABC.SNX00003.chain.pem" --rdsClientKey "%CERTS%\key\ExampleDeviceClient.ABC.SNX00003.key"
) else (
  >&2 echo start-node3.bat: unsupported --rap=%RAP%
  set "EXIT_CODE=64"
  goto done
)

call :find_python
if errorlevel 1 (
  >&2 echo start-node3.bat: no Python 3.12+ interpreter found ^(checked NMOS_PYTHON_EXE, .venv, py -3, python.exe^).
  >&2 echo Create .venv first, or install Python and make python.exe or py.exe available.
  set "EXIT_CODE=9009"
  goto done
)

echo Node SNX00003: Config C ^(mTLS + OAuth 2.0^), NAP=%NAP% RAP=%RAP% OAIM=%OAIM% TCT=%TCT%
"%PYTHON_EXE%" %PYTHON_SELECTOR% "%SCRIPT_DIR%nmos_node.py" ^
  --nodeSerialNumber SNX00003 ^
  --nodeAddr XYZ-SNX00003 ^
  --nodePort 7053 ^
  --nodeCertificate "%NODE_CERT%" ^
  --nodeKey "%NODE_KEY%" ^
  --nodeTrustedRootCA "%CA%" ^
  --nodeClientCertificate "%CERTS%\pem\ExampleDeviceClient.ABC.SNX00003.chain.pem" ^
  --nodeClientKey "%CERTS%\key\ExampleDeviceClient.ABC.SNX00003.key" ^
  --oauth2 ^
  --oauth2Host "%AS_HOST%" ^
  --oauth2Port "%AS_PORT%" ^
  --oauth2TrustedRootCA "%CA%" ^
  --oauth2ClientSecret secret ^
  --oauth2ApiSelector realms/TR-10-SEC ^
  --oauth2AudienceMode "%OAIM_FLAG%" ^
  --oauth2ClientId Example.Company.Device.Client.ABC.SNX00003.example.com ^
  --rdsHost "%RDS_HOST%" ^
  --rdsRegistrationPort "%RDS_REG_PORT%" ^
  --rdsQueryPort "%RDS_QUERY_PORT%" ^
  %RDS_FLAGS% ^
  --trustedRootCA "%CA%" ^
  --debug-in-depth ^
  --nodeConfig config_av_usb_tb_B
set "EXIT_CODE=%ERRORLEVEL%"
goto done

rem Build the combined trust store from the two roots of the resolved PKI.
rem A subroutine rather than an inline block: each line here is parsed as it
rem runs, so the CA path set below is visible to the copy that follows. Inside
rem a parenthesised block under DisableDelayedExpansion it would not be.
:derive_ca
if not exist "%CERTS%\ExampleRootCA.pem" (
  >&2 echo start-node3.bat: missing "%CERTS%\ExampleRootCA.pem"
  >&2 echo   Set IPMX_CERT_ROOT to a Certificates tree that carries the roots.
  exit /b 1
)
if not exist "%CERTS%\ExampleRootCA.ec.pem" (
  >&2 echo start-node3.bat: missing "%CERTS%\ExampleRootCA.ec.pem"
  exit /b 1
)
rem A name of its own per run, so concurrent launchers cannot overwrite each
rem other's bundle while it is being read.
set "CA=%TEMP%\ExampleRootCA-bundle.%RANDOM%%RANDOM%.pem"
copy /b "%CERTS%\ExampleRootCA.pem" + "%CERTS%\ExampleRootCA.ec.pem" "%CA%" >nul
if errorlevel 1 (
  >&2 echo start-node3.bat: cannot build a CA bundle from "%CERTS%"
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
>&2 echo start-node3.bat: %PORT_LABEL% must be a whole number between %PORT_MIN% and %PORT_MAX%, got "%PORT_VALUE%"
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
