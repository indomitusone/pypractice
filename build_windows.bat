@echo off
setlocal
set PYINSTALLER_VERSION=6.22.3

echo ========================================================
echo   Packaging PyPractice for Windows (Version 0.2.0)
echo ========================================================

REM Check whether PyInstaller is installed
python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo PyInstaller is not installed.
    echo Installing pyinstaller==%PYINSTALLER_VERSION%...
    python -m pip install "pyinstaller==%PYINSTALLER_VERSION%"
    if %errorlevel% neq 0 (
        echo ERROR: Could not install PyInstaller.
        exit /b 1
    )
)

echo.
echo Building application using PyPractice.spec (upx=False to minimize false positives)...
python -m PyInstaller --noconfirm --clean PyPractice.spec
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed.
    exit /b 1
)

echo.
echo Build completed successfully.
echo Executable path: dist\PyPractice\PyPractice.exe
if not defined CI pause
