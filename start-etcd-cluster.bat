@echo off
REM Bring up the etcd cluster for the distributed registry, inside WSL.
REM
REM   start-etcd-cluster.bat        3 members
REM   start-etcd-cluster.bat 5      5 members
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

setlocal
set MEMBERS=%1
if "%MEMBERS%"=="" set MEMBERS=3

echo Starting %MEMBERS%-member etcd cluster inside WSL...
echo Leave this window open; Ctrl-C stops every member.
echo.

wsl.exe -- bash -lc "cd \"$(wslpath '%~dp0')\" && ./start-etcd-cluster.sh %MEMBERS% --profile wsl"
endlocal
