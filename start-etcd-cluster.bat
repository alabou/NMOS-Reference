@echo off
REM Bring up the etcd cluster for the distributed registry, inside WSL.
REM
REM   start-etcd-cluster.bat            3 members, unsecured
REM   start-etcd-cluster.bat 5          5 members, unsecured
REM   start-etcd-cluster.bat 3 --secure 3 members, mutual TLS
REM
REM etcd rates windows/amd64 Tier 3 -- "considered unstable", unmaintained, and
REM not covered by the functional and robustness suites that verify Raft/WAL/
REM fsync durability. So no etcd member runs on native Windows: the cluster
REM lives in WSL and the registry talks to it as a client.
REM
REM The wsl profile binds every member to 127.0.0.1 and separates them by port
REM block, because WSL2 forwards Windows localhost to the distribution's
REM loopback for 127.0.0.1 ONLY -- members on 127.0.0.11 would be unreachable
REM from here.
REM
REM --secure needs no profile: a secured cluster already puts every member on
REM 127.0.0.1 and separates them by port, because etcd verifies the certificate
REM a peer presents against the address its connection arrives from. So the
REM profile is passed only when it is not given, leaving --profile free to be
REM overridden here as it is in the shell script.
REM
REM Pair it with start-registry-dist-secure.bat, and map every member name to
REM 127.0.0.1 in %SystemRoot%\System32\drivers\etc\hosts as well as in WSL.

setlocal enabledelayedexpansion
set MEMBERS=%1
if "%MEMBERS%"=="" set MEMBERS=3
if not "%MEMBERS%"=="" shift

REM Everything after the member count is forwarded verbatim, so --secure,
REM --tct and --profile all work from here rather than only from WSL.
set EXTRA=
:collect
if "%1"=="" goto collected
set EXTRA=!EXTRA! %1
shift
goto collect
:collected

REM The wsl profile is the default only when the caller did not choose one and
REM did not ask for --secure, which selects its own topology.
set PROFILE=--profile wsl
echo !EXTRA! | findstr /C:"--profile" >nul && set PROFILE=
echo !EXTRA! | findstr /C:"--secure" >nul && set PROFILE=

echo Starting %MEMBERS%-member etcd cluster inside WSL...!EXTRA!
echo Leave this window open; Ctrl-C stops every member.
echo.

wsl.exe -- bash -lc "cd \"$(wslpath '%~dp0')\" && ./start-etcd-cluster.sh %MEMBERS% %PROFILE%!EXTRA!"
endlocal
