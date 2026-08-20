@echo off
REM One-time setup on Windows. Double-click this once, then use "Lancer Cardif".
title Installation de Cardif
cd /d "%~dp0"

echo.
echo   Installation de Cardif
echo   ======================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo   [X] Python n'est pas installe sur cet ordinateur.
    echo.
    echo       Telechargez-le sur https://www.python.org/downloads/
    echo       IMPORTANT : cochez "Add Python to PATH" pendant l'installation.
    echo.
    pause
    exit /b 1
)

echo   Installation des composants necessaires...
echo   (quelques minutes la premiere fois)
echo.
python -m pip install --upgrade pip --quiet
python -m pip install -e "%~dp0" --quiet
if errorlevel 1 (
    echo.
    echo   [X] L'installation a echoue. Prevenez la personne qui gere l'outil.
    pause
    exit /b 1
)

echo.
echo   [OK] Installation terminee.
echo.
echo   Vous pouvez maintenant double-cliquer sur "Lancer Cardif.bat".
echo.
pause
