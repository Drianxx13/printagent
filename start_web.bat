@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
if not exist .venv\Scripts\activate.bat (
  echo Primero ejecuta install_windows.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
pip install --quiet -e . || goto :error
echo Abriendo printagent en el navegador... (cierra esta ventana para pararlo)
python -m printagent.web || goto :error
exit /b 0
:error
echo Algo ha fallado. Haz una captura de esta ventana y pasasela a Claude.
pause
exit /b 1
