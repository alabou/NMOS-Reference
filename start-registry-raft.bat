@echo off
REM Start one member of a raft-backed distributed NMOS registry on native
REM Windows.
REM
REM   start-registry-raft.bat 0       member 0 of 3
REM   start-registry-raft.bat 1 3     member 1 of 3
REM
REM Unlike start-registry-dist.bat there is nothing to bring up first and
REM nothing in WSL. That script has to be a CLIENT of a cluster managed
REM elsewhere because this project never runs an etcd member on native Windows
REM -- etcd rates the platform Tier 3, which is explicitly "considered
REM unstable" and outside the suites that verify its durability guarantees.
REM
REM Raft has no such restriction: it is this checkout's own asyncio code, it
REM runs the same way on every platform Python does, and the members ARE the
REM registries. So on Windows this is the complete distributed rig -- start N
REM of these in N windows and they elect a leader among themselves.
REM
REM Start them all. A 3-member cluster has no quorum until two are up, so the
REM first window refuses writes with 503 until the second one starts. That is
REM the cluster working, not failing.
REM
REM Plain HTTP and a plaintext raft transport, on the loopback, as the
REM development rig. The configuration layer refuses an unencrypted transport
REM anywhere else, because it carries every registration and every write. For
REM mutual TLS use start-registry-raft.sh --secure, which resolves the shared
REM certificate set.

setlocal
set INDEX=%1
if "%INDEX%"=="" set INDEX=0
set MEMBERS=%2
if "%MEMBERS%"=="" set MEMBERS=3

REM One port block of 10 per member, clear of start-registry-dist.bat's 8444
REM block so a raft rig and an etcd rig can both be up on one machine.
set /a REG_PORT=8544+%INDEX%*10
set /a QUERY_PORT=8543+%INDEX%*10
set /a WS_PORT=8548+%INDEX%*10
set /a RAFT_CLIENT_PORT=2481+%INDEX%*10
set /a RAFT_PEER_PORT=2482+%INDEX%*10

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

REM Not a database: about 24 bytes of term and vote, written when the election
REM term changes. The log is in memory. Repo-local and git-ignored, unlike the
REM production default under ProgramData.
set STATE_DIR=%~dp0.raft\m%INDEX%
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"

REM --registryAdvertisedHost carries host:client_port and the peer port is
REM client_port + 1, so the pair moves together.
set NEIGHBOURS=
if %MEMBERS% GEQ 3 call :neighbour 0
if %MEMBERS% GEQ 3 call :neighbour 1
if %MEMBERS% GEQ 3 call :neighbour 2
if %MEMBERS% GEQ 5 call :neighbour 3
if %MEMBERS% GEQ 5 call :neighbour 4

echo Raft registry member %INDEX% of %MEMBERS%
echo   Registration : http://127.0.0.1:%REG_PORT%/x-nmos/registration/v1.3/
echo   Query        : http://127.0.0.1:%QUERY_PORT%/x-nmos/query/v1.3/
echo   raft         : in-process on port %RAFT_PEER_PORT%, PLAINTEXT (loopback only)
echo   state-dir    : %STATE_DIR%
echo.

"%PY%" nmos_registry.py ^
    --registryDisableTLS ^
    --registryAddr 127.0.0.1 ^
    --registrationPort %REG_PORT% ^
    --queryPort %QUERY_PORT% ^
    --queryWebSocketPort %WS_PORT% ^
    --distributed ^
    --distributedBackend raft ^
    --raftDisableTLS ^
    --raftStateDir "%STATE_DIR%" ^
    --registryAdvertisedHost 127.0.0.1:%RAFT_CLIENT_PORT% ^
    %NEIGHBOURS%
endlocal
goto :eof

REM Every member except this one becomes a --registryNeighbour. Skipping the
REM local index matters: a member listed as its own neighbour is a duplicate
REM entry in the member list, and the configuration layer refuses it.
:neighbour
if "%1"=="%INDEX%" goto :eof
set /a PEER_CLIENT_PORT=2481+%1*10
set NEIGHBOURS=%NEIGHBOURS% --registryNeighbour 127.0.0.1:%PEER_CLIENT_PORT%
goto :eof
