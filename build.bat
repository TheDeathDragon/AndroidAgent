@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "BUILD_TYPE=%~1"
if "%BUILD_TYPE%"=="" set "BUILD_TYPE=release"

call .\gradlew.bat assemble%BUILD_TYPE% --no-daemon
if errorlevel 1 (
    echo [ERROR] Agent build failed.
    exit /b 1
)

if /i "%BUILD_TYPE%"=="release" (
    set "APK_PATH=build\outputs\apk\release\agent-server-release-unsigned.apk"
) else (
    set "APK_PATH=build\outputs\apk\debug\agent-server-debug.apk"
)

if not exist "%APK_PATH%" (
    for /r "build\outputs\apk" %%f in (*.apk) do (
        set "APK_PATH=%%f"
        goto :found
    )
    echo [ERROR] No APK produced.
    exit /b 1
)
:found

if not exist "build\dist" mkdir "build\dist"
copy /y "%APK_PATH%" "build\dist\agent-server.jar" >nul
echo Output: %~dp0build\dist\agent-server.jar

if exist "..\tools\" (
    copy /y "%APK_PATH%" "..\tools\agent-server.jar" >nul
    echo Also wrote: %~dp0..\tools\agent-server.jar
)

endlocal
