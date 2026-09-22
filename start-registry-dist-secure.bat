@echo off
REM One member of a SECURED distributed NMOS registry on native Windows: TLS
REM (optionally mutual) on the Registration and Query interfaces, mutual TLS to
REM the etcd members holding the shared state.
REM
REM Usage:
REM   start-registry-dist-secure.bat <index> [members] [rap] [--oauth2]
REM                                  [--as-host=H] [--as-port=P] [--tct=T]
REM                                  [--nap=N]
REM
REM   <index>   Which member this is, 0..members-1.
REM   [members] Cluster size: 1, 3 or 5 (default 3).
REM   [rap]     Registry Access Policy for the Registration API (default 1)
REM               1  Unrestricted Registration, server-authenticated TLS
REM               2  Restricted Registration, mutual TLS
REM             RAP=0 (plain HTTP) is start-registry-dist.bat, the unsecured rig.
REM
REM The option vocabulary is start-registry.sh's, deliberately: --oauth2,
REM --as-host, --as-port, --tct and --nap mean exactly what they mean there, so
REM one rig has one vocabulary whether or not the registry is distributed, and
REM whether or not it is Windows.
REM
REM   --managed is NOT available here and is refused rather than ignored. It
REM   starts and supervises this member's own etcd process, and this project
REM   never runs an etcd member on native Windows: etcd rates windows/amd64
REM   Tier 3 -- "considered unstable", unmaintained, and outside the functional
REM   and robustness suites that verify its Raft/WAL/fsync durability. A Windows
REM   registry is always a CLIENT of a cluster managed elsewhere.
REM
REM Bring the cluster up FIRST, secured:
REM
REM   start-etcd-cluster.bat 3 --secure
REM   start-registry-dist-secure.bat 0 3 2 --oauth2      window 1
REM   start-registry-dist-secure.bat 1 3 2 --oauth2      window 2
REM   start-registry-dist-secure.bat 2 3 2 --oauth2      window 3
REM
REM Launching etcd under WSL is the one place this rig uses WSL at all;
REM everything else on Windows, including the whole test suite, runs natively.
REM
REM One certificate per member, from Certificates/build.0.etcd/, serves FIVE
REM roles: this registry's Registration listener, its Query listener, its etcd
REM member's client listener, that member's peer listener and outbound peer
REM connections, and this registry's own client channel to etcd. That is what
REM the dual serverAuth, clientAuth EKU is for.
REM
REM HOSTS FILE. %SystemRoot%\System32\drivers\etc\hosts must map every member
REM name to 127.0.0.1:
REM
REM   127.0.0.1   XYZ-SNX10000
REM   127.0.0.1   XYZ-SNX10001
REM   127.0.0.1   XYZ-SNX10002
REM
REM Members co-located on one machine must share its address and separate by
REM port, because etcd verifies the certificate a peer presents against the
REM address that peer's connection arrives from.
REM
REM The refusals, their wording and their exit codes are
REM start-registry-dist-secure.sh's; nmos/registry/tests/test_launcher_contract.py
REM asserts the same contract against this file on Windows and the .sh on Linux.

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "ME=%~nx0"

REM --- arguments -------------------------------------------------------------
REM
REM Positionals stop at the first option, so `... 0 3 --oauth2` cannot silently
REM land --oauth2 in the RAP slot. Same guard, same reason, as the .sh.

set "POS_COUNT=0"
set "INDEX="
set "MEMBERS=3"
set "RAP=1"
set "AS_HOST=XYZ-SNX00000"
set "AS_PORT=9443"
set "TCT=0"
set "NAP=2"
set "USE_OAUTH2=0"

:positionals
if "%~1"=="" goto options
set "ARG=%~1"
if "!ARG:~0,2!"=="--" goto options
if !POS_COUNT! GEQ 3 goto options
if !POS_COUNT! EQU 0 set "INDEX=!ARG!"
if !POS_COUNT! EQU 1 set "MEMBERS=!ARG!"
if !POS_COUNT! EQU 2 set "RAP=!ARG!"
set /a POS_COUNT+=1
shift
goto positionals

REM cmd.exe treats `=` as an argument separator, so `--nap=1` reaches this file
REM as the two tokens `--nap` and `1` -- the `=` is simply gone. The value is
REM therefore taken from the following token, which also makes `--nap 1` work.
REM That is a superset of the .sh's `--nap=1`, not a different vocabulary: every
REM documented spelling means the same thing on both platforms.
REM
REM `shift` cannot be used inside a parenthesised block to then read `%1`: the
REM whole block is expanded before any of it runs, so `%1` would still be the
REM pre-shift token. PENDING carries the destination to the next iteration
REM instead.

:options
if "%~1"=="" goto parsed
set "ARG=%~1"
if defined PENDING (
  set "!PENDING!=!ARG!"
  set "PENDING="
  shift
  goto options
)
if /i "!ARG!"=="--oauth2" (
  set "USE_OAUTH2=1"
) else if /i "!ARG!"=="--managed" (
  echo %ME%: --managed is not available on native Windows 1>&2
  echo   etcd rates windows/amd64 Tier 3 -- "considered unstable", unmaintained, 1>&2
  echo   and not covered by the suites that verify Raft/WAL/fsync durability, so 1>&2
  echo   this project never runs an etcd member there. Bring the cluster up with 1>&2
  echo   start-etcd-cluster.bat and let this registry be a client of it. 1>&2
  exit /b 64
) else if /i "!ARG!"=="--as-host" (
  set "PENDING=AS_HOST"
  set "PENDING_OPT=--as-host"
) else if /i "!ARG!"=="--as-port" (
  set "PENDING=AS_PORT"
  set "PENDING_OPT=--as-port"
) else if /i "!ARG!"=="--tct" (
  set "PENDING=TCT"
  set "PENDING_OPT=--tct"
) else if /i "!ARG!"=="--nap" (
  set "PENDING=NAP"
  set "PENDING_OPT=--nap"
) else (
  echo %ME%: unknown arg !ARG! 1>&2
  exit /b 64
)
shift
goto options

:parsed

REM An option whose value never arrived. Defaulting it would be the silent
REM failure this file exists to avoid.
if defined PENDING (
  echo %ME%: !PENDING_OPT! needs a value 1>&2
  exit /b 64
)

REM --- validation ------------------------------------------------------------

echo %INDEX%| findstr /r /c:"^[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo %ME%: first argument must be the member index 1>&2
  exit /b 64
)

if not "%MEMBERS%"=="1" if not "%MEMBERS%"=="3" if not "%MEMBERS%"=="5" (
  echo %ME%: members must be 1, 3 or 5 1>&2
  exit /b 64
)

set /a LAST_INDEX=%MEMBERS%-1
if %INDEX% GEQ %MEMBERS% (
  echo %ME%: member index must be 0..%LAST_INDEX% 1>&2
  exit /b 64
)

REM Every value the command line can get wrong is settled here, before the
REM certificate probe below touches the disk. The member index and count are
REM already checked above; TCT, RAP and NAP were not, because the blocks that
REM validate them also build a path or a flag out of !CERT_ROOT! and !CA!, so
REM they could not run until the probe had succeeded. On a machine whose PKI
REM did not resolve that made an unsupported --nap answer "missing
REM ...etcd.chain.pem" and exit 66 (EX_NOINPUT) rather than naming the
REM argument and exiting 64 (EX_USAGE).
REM
REM The blocks below are deliberately left exactly as they were: a value that
REM reaches them is one this block already accepted, so nothing about a
REM successful start changes, and their else-arms are now unreachable rather
REM than wrong.
if not "%TCT%"=="0" if not "%TCT%"=="1" (
  echo %ME%: unsupported --tct=%TCT% 1>&2
  exit /b 64
)
if "%RAP%"=="0" (
  echo %ME%: RAP=0 ^(plain HTTP^) is start-registry-dist.bat 1>&2
  exit /b 64
)
if not "%RAP%"=="1" if not "%RAP%"=="2" (
  echo %ME%: unsupported RAP=%RAP% 1>&2
  exit /b 64
)
if "%NAP%"=="0" (
  echo %ME%: NAP=0 ^(plain HTTP^) is start-registry-dist.bat 1>&2
  exit /b 64
)
if not "%NAP%"=="1" if not "%NAP%"=="2" (
  echo %ME%: unsupported --nap=%NAP% 1>&2
  exit /b 64
)
if "%NAP%"=="1" if "%USE_OAUTH2%"=="1" (
  echo %ME%: --nap=1 ^(Unrestricted Read Only^) is not allowed 1>&2
  echo   with --oauth2; the specification requires read access to be granted 1>&2
  echo   by the OAuth 2.0 authorizations. Use --nap=2, or drop --oauth2. 1>&2
  exit /b 64
)

set "SERIAL=SNX1000%INDEX%"

REM --- certificates ----------------------------------------------------------
REM
REM Same resolution order as the .sh: IPMX_CERT_ROOT, this checkout, then the
REM workspace tree one level up, announcing the fallback rather than taking it
REM silently.
set "CERT_PROBE=build.0.etcd\pem\ExampleDeviceServer.ABC.SNX10000.etcd.chain.pem"
if not "%IPMX_CERT_ROOT%"=="" (
  set "CERT_ROOT=%IPMX_CERT_ROOT%"
) else if exist "%SCRIPT_DIR%Certificates\%CERT_PROBE%" (
  set "CERT_ROOT=%SCRIPT_DIR%Certificates"
) else if exist "%SCRIPT_DIR%..\Certificates\%CERT_PROBE%" (
  set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
  echo %ME%: %CERT_PROBE% is not in this checkout -- using the workspace PKI 1>&2
) else (
  echo %ME%: missing %CERT_PROBE% 1>&2
  echo   Searched %SCRIPT_DIR%Certificates and %SCRIPT_DIR%..\Certificates. 1>&2
  echo   Set IPMX_CERT_ROOT to a Certificates/ tree that carries it. 1>&2
  exit /b 66
)

if "%TCT%"=="0" (
  set "CERT=!CERT_ROOT!\build.0.etcd\pem\ExampleDeviceServer.ABC.%SERIAL%.etcd.chain.pem"
  set "KEY=!CERT_ROOT!\build.0.etcd\key\ExampleDeviceServer.ABC.%SERIAL%.etcd.key"
) else if "%TCT%"=="1" (
  set "CERT=!CERT_ROOT!\build.0.etcd\pem\ExampleDeviceServer.ABC.%SERIAL%.etcd.ec.chain.pem"
  set "KEY=!CERT_ROOT!\build.0.etcd\key\ExampleDeviceServer.ABC.%SERIAL%.etcd.ec.key"
) else (
  echo %ME%: unsupported --tct=%TCT% 1>&2
  exit /b 64
)
if not exist "!CERT!" (
  echo %ME%: missing !CERT! 1>&2
  exit /b 66
)
if not exist "!KEY!" (
  echo %ME%: missing !KEY! 1>&2
  exit /b 66
)

REM One file holding both generations of the root CA, so either certificate
REM flavour validates against a single anchor.
set "CA=!CERT_ROOT!\build.0\ExampleRootCA-bundle.pem"
if not exist "!CA!" (
  if not exist "!CERT_ROOT!\build.0\ExampleRootCA.pem" (
    echo %ME%: missing !CERT_ROOT!\build.0\ExampleRootCA.pem 1>&2
    exit /b 66
  )
  if not exist "!CERT_ROOT!\build.0\ExampleRootCA.ec.pem" (
    echo %ME%: missing !CERT_ROOT!\build.0\ExampleRootCA.ec.pem 1>&2
    exit /b 66
  )
  set "CA=%TEMP%\ExampleRootCA-bundle-%RANDOM%.pem"
  copy /b "!CERT_ROOT!\build.0\ExampleRootCA.pem"+"!CERT_ROOT!\build.0\ExampleRootCA.ec.pem" "!CA!" >nul
)

REM --- access policies -------------------------------------------------------
REM
REM Identical to the .sh: the Registration trust anchor is what selects RAP 1
REM from RAP 2, and the Query anchor plus --queryOptionalClientAuth select
REM NAP 1 from NAP 2.
set "REG_CA_FLAGS="
if "%RAP%"=="1" (
  set "REG_CA_FLAGS="
) else if "%RAP%"=="2" (
  set "REG_CA_FLAGS=--registrationTrustedRootCA "!CA!""
) else if "%RAP%"=="0" (
  echo %ME%: RAP=0 ^(plain HTTP^) is start-registry-dist.bat 1>&2
  exit /b 64
) else (
  echo %ME%: unsupported RAP=%RAP% 1>&2
  exit /b 64
)

if "%NAP%"=="1" (
  set "QUERY_CA_FLAGS=--queryTrustedRootCA "!CA!" --queryOptionalClientAuth"
) else if "%NAP%"=="2" (
  set "QUERY_CA_FLAGS=--queryTrustedRootCA "!CA!""
) else if "%NAP%"=="0" (
  echo %ME%: NAP=0 ^(plain HTTP^) is start-registry-dist.bat 1>&2
  exit /b 64
) else (
  echo %ME%: unsupported --nap=%NAP% 1>&2
  exit /b 64
)

REM TR-10-SEC "Unrestricted Read Only": read access MUST be granted by the
REM OAuth 2.0 authorizations, so NAP=1 cannot be claimed alongside --oauth2.
if "%NAP%"=="1" if "%USE_OAUTH2%"=="1" (
  echo %ME%: --nap=1 ^(Unrestricted Read Only^) is not allowed 1>&2
  echo   with --oauth2; the specification requires read access to be granted 1>&2
  echo   by the OAuth 2.0 authorizations. Use --nap=2, or drop --oauth2. 1>&2
  exit /b 64
)

set "OAUTH2_FLAGS="
if "%USE_OAUTH2%"=="1" set "OAUTH2_FLAGS=--oauth2 --oauth2Host %AS_HOST% --oauth2Port %AS_PORT% --oauth2TrustedRootCA "!CA!" --oauth2ApiSelector realms/TR-10-SEC"

set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM --- topology --------------------------------------------------------------
REM
REM Derived from the cluster tool rather than hard-coded, so this script cannot
REM drift from the topology etcd_cluster.py actually forms -- the same reason
REM the .sh does it. A secured cluster puts every member on its own name and
REM separates them by port block.
set /a ETCD_CLIENT_PORT=2381+%INDEX%*10
for /f "usebackq delims=" %%e in (`"%PY%" etcd_cluster.py --members %MEMBERS% --secure endpoints`) do set "ENDPOINTS=%%e"
if "%ENDPOINTS%"=="" (
  echo %ME%: etcd_cluster.py could not derive the endpoints 1>&2
  exit /b 1
)

set "MEMBER_FLAGS=--registryAdvertisedHost XYZ-%SERIAL%:%ETCD_CLIENT_PORT%"
set /a LAST=%MEMBERS%-1
for /l %%p in (0,1,%LAST%) do (
  if not %%p==%INDEX% (
    set /a PEER_PORT=2381+%%p*10
    set "MEMBER_FLAGS=!MEMBER_FLAGS! --registryNeighbour XYZ-SNX1000%%p:!PEER_PORT!"
  )
)

set /a REG_PORT=8444+%INDEX%*10
set /a QUERY_PORT=8443+%INDEX%*10
set /a WS_PORT=8448+%INDEX%*10

echo Secured registry member %INDEX% of %MEMBERS%  ^(RAP=%RAP% NAP=%NAP% OAuth2=%USE_OAUTH2%^)
echo   Registration : https://XYZ-%SERIAL%:%REG_PORT%/x-nmos/registration/v1.3/
echo   Query        : https://XYZ-%SERIAL%:%QUERY_PORT%/x-nmos/query/v1.3/
echo   Identity     : %SERIAL%
echo   etcd         : mutual TLS, external: %ENDPOINTS%
echo.

"%PY%" nmos_registry.py ^
    --registryAddr 127.0.0.1 ^
    --registrySerialNumber %SERIAL% ^
    --registryCertificate "!CERT!" ^
    --registryKey "!KEY!" ^
    --registrationPort %REG_PORT% ^
    --queryPort %QUERY_PORT% ^
    --queryWebSocketPort %WS_PORT% ^
    !REG_CA_FLAGS! ^
    !QUERY_CA_FLAGS! ^
    !OAUTH2_FLAGS! ^
    --trustedRootCA "!CA!" ^
    --distributed ^
    --distributedBackend etcd ^
    --etcdExternal ^
    --etcdEndpoints %ENDPOINTS% ^
    !MEMBER_FLAGS! ^
    --etcdCertificate "!CERT!" ^
    --etcdKey "!KEY!" ^
    --etcdTrustedRootCA "!CA!" ^
    --logFile nmos-registry-%INDEX%.log
endlocal
