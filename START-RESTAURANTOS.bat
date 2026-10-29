@echo off
setlocal
cd /d "%~dp0"
title RestaurantOS - JS + Python + SQL
color 0B

echo ==================================================
echo              RestaurantOS Launcher
echo          JavaScript + Python + SQL
 echo ==================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python was not found.
  echo Install Python 3.10+ from python.org and run this file again.
  echo.
  pause
  exit /b 1
)

python -c "import sys; print('Python', sys.version.split()[0])"
echo Starting RestaurantOS...
echo.
start "RestaurantOS Server" cmd /k "cd /d "%~dp0" && python backend\server.py"

timeout /t 2 /nobreak >nul
start "RestaurantOS" "http://127.0.0.1:4000/"
echo.
echo RestaurantOS is starting at http://127.0.0.1:4000/
echo Close the RestaurantOS Server window to stop the application.
echo.
exit /b 0
