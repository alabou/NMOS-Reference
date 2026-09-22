@echo off
REM Start one member of a raft-backed distributed NMOS registry on native
REM Windows.
REM
REM   start-registry-raft.bat 0              member 0 of 3 (default), plain HTTP
REM   start-registry-raft.bat 1 3            member 1 of 3
REM   start-registry-raft.bat 0 3 --secure   TLS everywhere, RAP=1
REM   start-registry-raft.bat 0 3 2 --secure ... RAP=2, mutual TLS Registration
REM
REM Usage:
REM   start-registry-raft.bat <index> [members] [rap] [--secure]
REM
REM   <index>   Which member this is, 0..members-1 (default 0).
REM   [members] Cluster size: 1, 3 or 5 (default 3).
REM   [rap]     Registry Access Policy for the Registration API, --secure only
REM             (default 1). Same vocabulary as start-registry-raft.sh:
REM               1  Unrestricted Registration, server-authenticated TLS
REM               2  Restricted Registration, mutual TLS
REM             RAP=0 (plain HTTP) is this script without --secure.
REM
REM This is the COMPLETE distributed rig on Windows, and the only one. There is
REM nothing to bring up first and nothing in WSL. start-registry-dist.bat has to
REM be a CLIENT of a cluster managed elsewhere because this project never runs
REM an etcd member on native Windows -- etcd rates the platform Tier 3, which is
REM explicitly "considered unstable" and outside the suites that verify its
REM durability guarantees. Raft has no such restriction: it is this checkout's
REM own asyncio code, it runs the same way on every platform Python does, and
REM the members ARE the registries.
REM
REM Start them all. A 3-member cluster has no quorum until two are up, so the
REM first window refuses writes with 503 until the second one starts. That is
REM the cluster working, not failing.
REM
REM --rust is NOT accepted here and is refused rather than ignored. The Rust
REM registry is not supported on Windows, and silently starting the Python one
REM instead would make a mixed-cluster run look like it proved something it
REM never exercised.
REM
REM The option vocabulary, the refusals, the exit codes and the port blocks are
REM start-registry-raft.sh's, deliberately: one rig has one vocabulary, and
REM nmos/registry/tests/test_launcher_contract.py asserts the same refusals
REM against this file on Windows and against the .sh on Linux.
REM
REM PORTS. Registration 8544 + index * 10, clear of the etcd rig's 8444 block,
REM and the raft transport on 2482 + index * 10, clear of etcd's 2382 -- so a
REM raft rig and an etcd rig can both be up on one machine.

setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "ME=%~nx0"

REM --- arguments -------------------------------------------------------------
REM
REM Positionals stop at the first option, so `... 0 3 --secure` cannot silently
REM land --secure in the RAP slot. Same guard, same reason, as the .sh.

set "POS_COUNT=0"
set "INDEX=0"
set "MEMBERS=3"
set "RAP=1"
set "RAP_GIVEN=0"
set "SECURE=0"

:positionals
if "%~1"=="" goto options
set "ARG=%~1"
if "!ARG:~0,2!"=="--" goto options
if !POS_COUNT! GEQ 3 goto options
if !POS_COUNT! EQU 0 set "INDEX=!ARG!"
if !POS_COUNT! EQU 1 set "MEMBERS=!ARG!"
if !POS_COUNT! EQU 2 (
  set "RAP=!ARG!"
  set "RAP_GIVEN=1"
)
set /a POS_COUNT+=1
shift
goto positionals

:options
if "%~1"=="" goto parsed
set "ARG=%~1"
if /i "!ARG!"=="--secure" (
  set "SECURE=1"
) else if /i "!ARG!"=="--rust" (
  echo %ME%: --rust is not available on native Windows 1>&2
  echo   The Rust registry is not supported on this platform. Run the mixed 1>&2
  echo   cluster on Linux with start-registry-raft.sh --rust. 1>&2
  exit /b 64
) else (
  echo %ME%: unknown arg !ARG! 1>&2
  exit /b 64
)
shift
goto options

:parsed

REM --- validation ------------------------------------------------------------
REM
REM Every refusal below exists in start-registry-raft.sh with the same wording
REM and the same exit code. An entry-level platform is exactly where a mistyped
REM member index is most likely, so it is the last place that should answer with
REM a certificate-not-found error instead of saying what is wrong.

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

if "%RAP%"=="0" (
  echo %ME%: RAP=0 ^(plain HTTP^) is this script without --secure 1>&2
  exit /b 64
)
if not "%RAP%"=="1" if not "%RAP%"=="2" (
  echo %ME%: unsupported RAP=%RAP% 1>&2
  exit /b 64
)

REM Silently ignoring it would let an operator believe they had asked for
REM Restricted Registration and got it, on a listener that is plain HTTP.
if "%SECURE%"=="0" if "%RAP_GIVEN%"=="1" (
  echo %ME%: a RAP only means something with --secure 1>&2
  exit /b 64
)

set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "SERIAL=SNX1000%INDEX%"

REM --- state -----------------------------------------------------------------
REM
REM Not a database: about 24 bytes of term and vote, written when the election
REM term changes. The log is in memory. Repo-local and git-ignored, unlike the
REM production default under ProgramData. Named by serial, matching the .sh, so
REM a member started by either script uses the same directory.
REM
REM Deleting it between runs is safe and is what the rig wants: a member that
REM has never voted is a member starting from scratch. Deleting it under a LIVE
REM cluster is the one thing that is not safe, which is why this happens before
REM the registry starts and never while it is running.
set "STATE_DIR=%SCRIPT_DIR%.raft\%SERIAL%"
if not exist "%STATE_DIR%" mkdir "%STATE_DIR%"

REM --- topology --------------------------------------------------------------
REM
REM NO HOSTS FILE, in either posture -- a real difference from the etcd rig, not
REM an omission. etcd verifies the certificate a peer presents against the
REM address the connection arrives from, which on one machine is always
REM 127.0.0.1, so its members must be named and those names must resolve. Raft
REM verifies against the shared SAN instead, passed as the TLS server name, so
REM the address is free to be a bare 127.0.0.1 for every member.
REM
REM --registryAdvertisedHost carries host:client_port and the peer port is
REM client_port + 1, so the pair moves together.
set /a RAFT_CLIENT_PORT=2481+%INDEX%*10
set /a RAFT_PEER_PORT=2482+%INDEX%*10

set "NEIGHBOURS="
set /a LAST=%MEMBERS%-1
for /l %%p in (0,1,%LAST%) do (
  if not %%p==%INDEX% (
    set /a PEER_CLIENT_PORT=2481+%%p*10
    set "NEIGHBOURS=!NEIGHBOURS! --registryNeighbour 127.0.0.1:!PEER_CLIENT_PORT!"
  )
)

set /a REG_PORT=8544+%INDEX%*10
set /a QUERY_PORT=8543+%INDEX%*10
set /a WS_PORT=8548+%INDEX%*10

REM --- security posture ------------------------------------------------------

if "%SECURE%"=="1" (
  REM Same resolution order as start-registry-raft.sh: IPMX_CERT_ROOT, this
  REM checkout, then the workspace tree one level up, announcing the fallback
  REM rather than taking it silently.
  set "CERT_PROBE=build.0.etcd\pem\ExampleDeviceServer.ABC.SNX10000.etcd.chain.pem"
  if not "%IPMX_CERT_ROOT%"=="" (
    set "CERT_ROOT=%IPMX_CERT_ROOT%"
  ) else if exist "%SCRIPT_DIR%Certificates\!CERT_PROBE!" (
    set "CERT_ROOT=%SCRIPT_DIR%Certificates"
  ) else if exist "%SCRIPT_DIR%..\Certificates\!CERT_PROBE!" (
    set "CERT_ROOT=%SCRIPT_DIR%..\Certificates"
    echo %ME%: !CERT_PROBE! is not in this checkout -- using the workspace PKI at !CERT_ROOT! 1>&2
  ) else (
    echo %ME%: missing !CERT_PROBE! 1>&2
    echo   Searched %SCRIPT_DIR%Certificates and %SCRIPT_DIR%..\Certificates. 1>&2
    echo   Set IPMX_CERT_ROOT to a Certificates/ tree that carries it. 1>&2
    exit /b 66
  )

  REM The etcd certificate set serves raft unchanged. The roles are identical --
  REM one certificate that both listens and dials, which is what its dual
  REM serverAuth+clientAuth EKU is for -- and it carries the shared SAN that
  REM --raftCertificateName defaults to.
  set "CERT=!CERT_ROOT!\build.0.etcd\pem\ExampleDeviceServer.ABC.%SERIAL%.etcd.chain.pem"
  set "KEY=!CERT_ROOT!\build.0.etcd\key\ExampleDeviceServer.ABC.%SERIAL%.etcd.key"
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

  REM RAP 2 is Restricted Registration: the Registration trust anchor is what
  REM selects it from RAP 1, exactly as in the .sh.
  set "REG_CA_FLAGS="
  if "%RAP%"=="2" set "REG_CA_FLAGS=--registrationTrustedRootCA "!CA!""

  set "LISTENER_FLAGS=--registrySerialNumber %SERIAL% --registryCertificate "!CERT!" --registryKey "!KEY!" !REG_CA_FLAGS! --queryTrustedRootCA "!CA!" --trustedRootCA "!CA!""
  set "RAFT_FLAGS=--raftCertificate "!CERT!" --raftKey "!KEY!" --raftTrustedRootCA "!CA!""
  set "SCHEME=https"
  REM The listeners are reached by the certificate's own name, which is what a
  REM client verifies. The raft members are not: they reach each other at
  REM 127.0.0.1 and verify the shared SAN. Two names, two checks, one cert.
  set "LISTENER_HOST=XYZ-%SERIAL%"
  set "TRANSPORT_DESCRIPTION=mutual TLS, peers verified against the shared etcd SAN"
) else (
  set "LISTENER_FLAGS=--registryDisableTLS"
  set "RAFT_FLAGS=--raftDisableTLS"
  set "SCHEME=http"
  set "LISTENER_HOST=127.0.0.1"
  REM No caret-escaping inside a quoted `set`: the parentheses are data here,
  REM and the echo that prints this is at top level, not inside a block.
  set "TRANSPORT_DESCRIPTION=PLAINTEXT (loopback only) -- development rig"
)

if "%SECURE%"=="1" (
  echo Raft registry member %INDEX% of %MEMBERS%  ^(RAP=%RAP%^)
) else (
  echo Raft registry member %INDEX% of %MEMBERS%
)
echo   Registration : !SCHEME!://!LISTENER_HOST!:%REG_PORT%/x-nmos/registration/v1.3/
echo   Query        : !SCHEME!://!LISTENER_HOST!:%QUERY_PORT%/x-nmos/query/v1.3/
echo   raft         : in-process on port %RAFT_PEER_PORT%, !TRANSPORT_DESCRIPTION!
echo   state-dir    : %STATE_DIR%  ^(term/vote only -- the log is in memory^)
if %MEMBERS% GTR 1 (
  set /a QUORUM=%MEMBERS%/2+1
  echo.
  echo   Start all %MEMBERS% members. Until !QUORUM! are up there
  echo   is no quorum and writes are refused with 503; that is the cluster
  echo   working, not failing.
)
echo.

"%PY%" nmos_registry.py ^
    --registryAddr 127.0.0.1 ^
    --registrationPort %REG_PORT% ^
    --queryPort %QUERY_PORT% ^
    --queryWebSocketPort %WS_PORT% ^
    !LISTENER_FLAGS! ^
    --distributed ^
    --distributedBackend raft ^
    !RAFT_FLAGS! ^
    --raftStateDir "%STATE_DIR%" ^
    --registryAdvertisedHost 127.0.0.1:%RAFT_CLIENT_PORT% ^
    !NEIGHBOURS! ^
    --logFile nmos-registry-raft-%INDEX%.log
endlocal
