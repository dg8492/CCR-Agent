@echo off
echo ============================================
echo  Meridian -- Windows Build
echo ============================================
echo.

echo [1/4] Installing PyInstaller...
pip install pyinstaller --quiet
if %errorlevel% neq 0 (echo ERROR: pip failed. Make sure Python is in PATH. & pause & exit /b 1)

echo [2/4] Building executable...
pyinstaller meridian.spec --clean --noconfirm
if %errorlevel% neq 0 (echo ERROR: Build failed. See output above. & pause & exit /b 1)

echo [3/4] Copying runtime files...
copy .env dist\Meridian\.mrd >nul 2>&1
copy clients.json dist\Meridian\clients.json >nul 2>&1
if not exist dist\Meridian\docs mkdir dist\Meridian\docs
echo   NOTE: Copy your CCR research documents into dist\Meridian\docs\

echo [4/4] Packaging for distribution...
cd dist
powershell -Command "Compress-Archive -Path 'Meridian' -DestinationPath 'Meridian-Windows.zip' -Force"
cd ..

echo.
echo ============================================
echo  BUILD COMPLETE
echo ============================================
echo.
echo  Output:   dist\Meridian-Windows.zip
echo.
echo  Package contents:
echo    Meridian\Meridian.exe     ^<-- double-click to launch
echo    Meridian\_internal\       ^<-- bundled dependencies (don't touch)
echo    Meridian\docs\            ^<-- place CCR research documents here
echo    Meridian\.env             ^<-- API keys (already included)
echo    Meridian\clients.json     ^<-- access tokens (edit to add clients)
echo.
echo  To add a client: edit clients.json, add {"token": "Client Name"}
echo.
echo  NOTE: Windows SmartScreen will warn on first run because the app
echo  is not code-signed. Click "More info" then "Run anyway" to proceed.
echo  To remove this warning permanently, purchase a code-signing
echo  certificate (DigiCert / Sectigo, ~$200-300/yr).
echo.
pause
