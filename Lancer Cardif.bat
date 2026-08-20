@echo off
REM Double-click to start Cardif. Closing this window stops the tool.
title Cardif - ne fermez pas cette fenetre
cd /d "%~dp0"

echo.
echo   Cardif demarre...
echo.
echo   Une page va s'ouvrir dans votre navigateur.
echo   NE FERMEZ PAS cette fenetre noire tant que vous utilisez l'outil.
echo.

python -m streamlit run "%~dp0app\Accueil.py"

if errorlevel 1 (
    echo.
    echo   [X] Cardif n'a pas pu demarrer.
    echo       Lancez d'abord "Installer.bat".
    echo.
    pause
)
