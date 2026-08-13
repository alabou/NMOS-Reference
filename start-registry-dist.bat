@echo off
REM Start one member of a distributed NMOS registry on native Windows.
REM
REM   start-registry-dist.bat 0       member 0 of 3
REM   start-registry-dist.bat 1 3     member 1 of 3
REM
REM Bring the cluster up FIRST with start-etcd-cluster.bat.
REM
REM --etcdExternal is not optional here, it is the rule: this project never runs
REM an etcd member on native Windows (etcd rates the platform Tier 3), so a
REM Windows registry is a CLIENT of a cluster managed in WSL or on Linux.
REM Endpoints are localhost because that is how WSL2 forwarding reaches them.

setlocal
set INDEX=%1
if "%INDEX%"=="" set INDEX=0
set MEMBERS=%2
if "%MEMBERS%"=="" set MEMBERS=3

REM One port block of 10 per member, so every member's three listeners move
REM together and adding a member can never collide with an existing one.
set /a REG_PORT=8444+%INDEX%*10
set /a QUERY_PORT=8443+%INDEX%*10
set /a WS_PORT=8448+%INDEX%*10

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

REM Port block per member: 2381, 2391, 2401 -- matching etcd_cluster.py's wsl profile.
set ENDPOINTS=localhost:2381
if %MEMBERS% GEQ 3 set ENDPOINTS=localhost:2381,localhost:2391,localhost:2401
if %MEMBERS% GEQ 5 set ENDPOINTS=localhost:2381,localhost:2391,localhost:2401,localhost:2411,localhost:2421

echo Registry member %INDEX% of %MEMBERS%
echo   Registration : http://127.0.0.1:%REG_PORT%/x-nmos/registration/v1.3/
echo   Query        : http://127.0.0.1:%QUERY_PORT%/x-nmos/query/v1.3/
echo   etcd         : %ENDPOINTS%  (in WSL)
echo.

"%PY%" nmos_registry.py ^
    --registryDisableTLS ^
    --registryAddr 127.0.0.1 ^
    --registrationPort %REG_PORT% ^
    --queryPort %QUERY_PORT% ^
    --queryWebSocketPort %WS_PORT% ^
    --distributed ^
    --etcdExternal ^
    --etcdDisableTLS ^
    --registryAdvertisedHost 127.0.0.1 ^
    --etcdEndpoints %ENDPOINTS%
endlocal
