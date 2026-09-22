@echo off
REM Start one member of a distributed NMOS registry on native Windows.
REM
REM   start-registry-dist.bat 0       member 0 of 3
REM   start-registry-dist.bat 1 3     member 1 of 3
REM
REM Anything after the member count is forwarded to nmos_registry.py verbatim,
REM exactly as start-registry-dist.sh forwards "$@".
REM
REM Bring the cluster up FIRST with start-etcd-cluster.bat.
REM
REM --etcdExternal is not optional here, it is the rule: this project never runs
REM an etcd member on native Windows (etcd rates the platform Tier 3), so a
REM Windows registry is a CLIENT of a cluster managed elsewhere. Launching etcd
REM under WSL is the one place this rig uses WSL at all; everything else on
REM Windows, including the whole test suite, runs natively. If the cluster is on
REM a Linux host rather than in WSL, pass --etcdEndpoints through to override.
REM
REM No TLS on either the NMOS listeners or etcd: this is the development rig.
REM The secured equivalent is start-registry-dist-secure.bat.
REM
REM Nothing here accepts --oauth2, deliberately. The listeners are plain HTTP,
REM and TR-10-SEC classifies that as NAP=0, a configuration a device "MUST not
REM claim compliance" with; adding OAuth 2.0 on top would put bearer tokens on
REM the wire in the clear while reporting a policy the deployment does not have.
REM Use start-registry-dist-secure.bat --oauth2 instead.
REM
REM Registration ports follow the repository convention (8444 + index * 10), so
REM several members coexist on one machine.

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"

set "INDEX=%~1"
if "%INDEX%"=="" set "INDEX=0"
if not "%~1"=="" shift
set "MEMBERS=%~1"
if "%MEMBERS%"=="" set "MEMBERS=3"
if not "%~1"=="" shift

REM Everything left over is forwarded, matching the .sh's trailing "$@".
set "EXTRA="
:collect
if "%~1"=="" goto collected
set "EXTRA=!EXTRA! %1"
shift
goto collect
:collected

set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM Same refusal, same wording and same exit code as start-registry-dist.sh.
REM Without it a mistyped index starts a member on a port block nobody is
REM talking to, and the cluster simply never reaches quorum.
echo %INDEX%| findstr /r /c:"^[0-9][0-9]*$" >nul
if errorlevel 1 goto badindex
set /a LAST_INDEX=%MEMBERS%-1
if %INDEX% GEQ %MEMBERS% goto badindex
goto indexok

:badindex
set /a LAST_INDEX=%MEMBERS%-1
echo member index must be 0..%LAST_INDEX% 1>&2
exit /b 1

:indexok

REM One port block of 10 per member, so every member's three listeners move
REM together and adding a member can never collide with an existing one.
set /a REG_PORT=8444+%INDEX%*10
set /a QUERY_PORT=8443+%INDEX%*10
set /a WS_PORT=8448+%INDEX%*10

REM Ask the cluster tool for the endpoints rather than hard-coding them, so this
REM script cannot drift from the topology the cluster actually formed -- the
REM same reason the .sh does it. The wsl profile is the one that binds every
REM member to 127.0.0.1 and separates them by port, which is what WSL2 forwards.
for /f "usebackq delims=" %%e in (`"%PY%" etcd_cluster.py --members %MEMBERS% --profile wsl endpoints`) do set "ENDPOINTS=%%e"
if "%ENDPOINTS%"=="" (
  echo start-registry-dist.bat: etcd_cluster.py could not derive the endpoints 1>&2
  exit /b 1
)

echo Registry member %INDEX% of %MEMBERS%
echo   Registration : http://127.0.0.1:%REG_PORT%/x-nmos/registration/v1.3/
echo   Query        : http://127.0.0.1:%QUERY_PORT%/x-nmos/query/v1.3/
echo   etcd         : %ENDPOINTS%
echo.

"%PY%" nmos_registry.py ^
    --registryDisableTLS ^
    --registryAddr 127.0.0.1 ^
    --registrationPort %REG_PORT% ^
    --queryPort %QUERY_PORT% ^
    --queryWebSocketPort %WS_PORT% ^
    --distributed ^
    --distributedBackend etcd ^
    --etcdExternal ^
    --etcdDisableTLS ^
    --registryAdvertisedHost 127.0.0.1 ^
    --etcdEndpoints %ENDPOINTS%!EXTRA!
endlocal
