@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
echo === printagent: instalacion ===
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  python --version >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo No encuentro Python en este PC.
  echo Instalalo desde python.org marcando "Add python.exe to PATH"
  echo y vuelve a hacer doble clic en este archivo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [1/5] Creando entorno virtual...
if not exist .venv (
  %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat

echo [2/5] Instalando dependencias (1-2 minutos)...
python -m pip install --quiet --upgrade pip
pip install --quiet -e ".[dev]" || goto :error

echo [3/5] Ejecutando tests...
python -m pytest -q || goto :error

echo [4/5] Demo completa (producto, precio, revision, arreglo, pedido)...
python demo.py samples\03_no_bleed.png

echo [5/5] Conectando printagent a Claude Desktop...
python scripts\setup_claude_desktop.py || goto :error

echo.
echo ============================================================
echo  LISTO. Cierra Claude Desktop del todo (tambien el icono
echo  junto al reloj) y vuelve a abrirlo.
echo  Para la web: doble clic en start_web.bat
echo ============================================================
pause
exit /b 0

:error
echo.
echo Algo ha fallado. Haz una captura de esta ventana y pasasela a Claude.
pause
exit /b 1
