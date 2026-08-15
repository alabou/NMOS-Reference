@echo off
REM One member of a SECURED distributed NMOS registry on native Windows.
REM
REM   start-registry-dist-secure.bat 0        member 0 of 3, RAP=1
REM   start-registry-dist-secure.bat 1 3 2    member 1 of 3, RAP=2 (mutual TLS)
REM
REM Bring the cluster up FIRST, secured, inside WSL:
REM
REM   wsl ./start-etcd-cluster.sh 3 --secure
REM
REM --etcdExternal is not optional here, it is the rule: this project never runs
REM an etcd member on native Windows (etcd rates the platform Tier 3), so a
REM Windows registry is a CLIENT of a cluster managed in WSL or on Linux.
REM
REM HOSTS FILE. %SystemRoot%\System32\drivers\etc\hosts must map every member
REM name to 127.0.0.1, matching the WSL side:
REM
REM   127.0.0.1   XYZ-SNX10000
REM   127.0.0.1   XYZ-SNX10001
REM   127.0.0.1   XYZ-SNX10002
REM
REM WSL2 forwards Windows localhost to the distribution, and it forwards
REM 127.0.0.1 only -- which is also the address the secured cluster's members
REM share, so one mapping satisfies both requirements.
REM
REM One certificate per member serves the Registration listener, the Query
REM listener and the registry's client channel to etcd; it is the same
REM certificate its etcd member presents, which is what the dual serverAuth,
REM clientAuth EKU is for.

setlocal
set INDEX=%1
if "%INDEX%"=="" set INDEX=0
set MEMBERS=%2
if "%MEMBERS%"=="" set MEMBERS=3
set RAP=%3
if "%RAP%"=="" set RAP=1

set SERIAL=SNX1000%INDEX%
set CERTS=%~dp0Certificates
set PEM=%CERTS%\build.0.etcd\pem\ExampleDeviceServer.ABC.%SERIAL%.etcd.chain.pem
set KEY=%CERTS%\build.0.etcd\key\ExampleDeviceServer.ABC.%SERIAL%.etcd.key
set CA=%CERTS%\build.0\ExampleRootCA-bundle.pem

if not exist "%PEM%" (
  echo start-registry-dist-secure.bat: missing %PEM%
  exit /b 66
)

REM The Registration trust anchor is what selects RAP 1 from RAP 2, exactly as
REM in start-registry-dist-secure.sh.
set REG_CA_FLAGS=
if "%RAP%"=="2" set REG_CA_FLAGS=--registrationTrustedRootCA "%CA%"
if "%RAP%"=="0" (
  echo start-registry-dist-secure.bat: RAP=0 ^(plain HTTP^) is start-registry-dist.bat
  exit /b 64
)

REM One port block of 10 per member, matching the Linux launchers.
set /a REG_PORT=8444+%INDEX%*10
set /a QUERY_PORT=8443+%INDEX%*10
set /a WS_PORT=8448+%INDEX%*10

REM etcd client ports: 2381, 2391, 2401 -- the secured cluster's port block,
REM every member on the shared address its names resolve to.
set /a ETCD_PORT=2381+%INDEX%*10
set ENDPOINTS=XYZ-SNX10000:2381
if %MEMBERS% GEQ 3 set ENDPOINTS=XYZ-SNX10000:2381,XYZ-SNX10001:2391,XYZ-SNX10002:2401
if %MEMBERS% GEQ 5 set ENDPOINTS=XYZ-SNX10000:2381,XYZ-SNX10001:2391,XYZ-SNX10002:2401,XYZ-SNX10003:2411,XYZ-SNX10004:2421

set MEMBER_FLAGS=--registryAdvertisedHost XYZ-SNX1000%INDEX%:%ETCD_PORT%
for /l %%p in (0,1,4) do (
  if %%p LSS %MEMBERS% if not %%p==%INDEX% (
    set /a PEER_PORT=2381+%%p*10
    call set MEMBER_FLAGS=%%MEMBER_FLAGS%% --registryNeighbour XYZ-SNX1000%%p:%%PEER_PORT%%
  )
)

set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo Secured registry member %INDEX% of %MEMBERS%  ^(RAP=%RAP%^)
echo   Registration : https://XYZ-SNX1000%INDEX%:%REG_PORT%/x-nmos/registration/v1.3/
echo   Query        : https://XYZ-SNX1000%INDEX%:%QUERY_PORT%/x-nmos/query/v1.3/
echo   Identity     : %SERIAL%
echo   etcd         : %ENDPOINTS%  ^(mutual TLS, in WSL^)
echo.

"%PY%" nmos_registry.py ^
    --registryAddr 127.0.0.1 ^
    --registrySerialNumber %SERIAL% ^
    --registryCertificate "%PEM%" ^
    --registryKey "%KEY%" ^
    --registrationPort %REG_PORT% ^
    --queryPort %QUERY_PORT% ^
    --queryWebSocketPort %WS_PORT% ^
    %REG_CA_FLAGS% ^
    --queryTrustedRootCA "%CA%" ^
    --trustedRootCA "%CA%" ^
    --distributed ^
    --etcdExternal ^
    %MEMBER_FLAGS% ^
    --etcdEndpoints %ENDPOINTS% ^
    --etcdCertificate "%PEM%" ^
    --etcdKey "%KEY%" ^
    --etcdTrustedRootCA "%CA%" ^
    --logFile nmos-registry-%INDEX%.log
endlocal
