@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
title AR-песочница

rem AR-песочница: запуск на Windows двойным щелчком по этому файлу.
rem
rem Что делает по шагам:
rem   1) находит Python 3.11 или новее;
rem   2) при первом запуске создаёт окружение .venv и ставит библиотеки —
rem      нужен интернет, это разово;
rem   3) если список библиотек requirements.txt с тех пор изменился,
rem      обновляет окружение само: сравнивает отпечаток файла с сохранённым;
rem   4) проверяет, собрана ли программа захвата с датчика;
rem   5) поднимает программу и открывает пульт в браузере.
rem
rem Остановить: Ctrl+C в этом окне.
rem Файл сохранён в UTF-8 без BOM, поэтому первой строкой идёт chcp 65001 —
rem без неё русский текст в окне превратится в кракозябры.

cd /d "%~dp0.."
set "ROOT=%CD%"
set "VENV=%ROOT%\.venv"
set "PYEXE=%VENV%\Scripts\python.exe"
set "REQ=%ROOT%\requirements.txt"
set "STAMP=%VENV%\requirements-stamp.txt"

echo AR-песочница: подготовка…
echo.

rem ---------- 1. Python 3.11 или новее ----------
set "PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto py_found
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY goto py_found
goto no_python
:py_found

rem ---------- 2. Окружение ----------
if exist "%PYEXE%" goto venv_ready
echo Первый запуск: готовлю окружение. Нужен интернет, это разово…
%PY% -m venv "%VENV%"
if errorlevel 1 goto venv_failed
:venv_ready

rem ---------- 3. Библиотеки: ставим заново, только если список изменился ----------
rem certutil есть в любой Windows и считает отпечаток файла без лишних программ.
set "WANT="
for /f "skip=1 delims=" %%H in ('certutil -hashfile "%REQ%" SHA256 2^>nul') do if not defined WANT set "WANT=%%H"
set "WANT=!WANT: =!"
set "HAVE="
if exist "%STAMP%" set /p HAVE=<"%STAMP%"
if "!WANT!"=="" goto install_deps
if "!WANT!"=="!HAVE!" goto deps_ready
:install_deps
if "!HAVE!"=="" echo Ставлю библиотеки. Нужен интернет, это разово…
if not "!HAVE!"=="" echo Список библиотек изменился — обновляю окружение…
"%PYEXE%" -m pip install --upgrade pip >nul
"%PYEXE%" -m pip install -r "%REQ%"
if errorlevel 1 goto pip_failed
if not "!WANT!"=="" >"%STAMP%" echo !WANT!
:deps_ready

rem ---------- 4. Программа захвата с датчика ----------
rem На Windows датчик читается не так, как на Маке и Linux: программу захвата
rem kinect_grabber.exe надо собрать отдельно под Windows. Как — написано
rem в README.md, раздел «Датчик на Windows». Без неё программа работает,
rem но показывает демо-рельеф вместо настоящего песка.
if exist "%ROOT%\build\kinect_grabber.exe" goto grabber_ready
echo.
echo   Программа захвата с датчика не собрана: нет файла build\kinect_grabber.exe
echo   Запускаюсь на демо-рельефе: интерфейс и проекцию посмотреть можно,
echo   настоящий песок — нет.
echo.
echo   Чтобы работал настоящий датчик, соберите программу захвата один раз.
echo   По шагам — в файле README.md, раздел «Датчик на Windows»:
echo     - драйвер libusbK для Kinect v2 через программу Zadig;
echo     - libfreenect2 собирается в Visual Studio, версия Community подойдёт;
echo     - готовый kinect_grabber.exe положить в папку build.
echo.
:grabber_ready

rem ---------- 5. Запуск ----------
"%PYEXE%" -m sandbox.console --open
if errorlevel 1 goto run_failed
goto finish

rem ---------- сообщения об ошибках ----------
:no_python
echo.
echo   Не нашёл Python 3.11 или новее.
echo   Что сделать: поставьте Python с сайта python.org/downloads
echo   При установке обязательно поставьте галочку "Add python.exe to PATH".
echo   После установки закройте это окно и запустите файл заново.
goto stop

:venv_failed
echo.
echo   Не удалось создать окружение Python в папке .venv
echo   Что сделать: проверьте, что папка программы не в OneDrive и не только
echo   для чтения, и что на диске есть свободное место.
goto stop

:pip_failed
echo.
echo   Не удалось поставить библиотеки.
echo   Что сделать: проверьте интернет и запустите файл заново.
echo   Если компьютер в сети клиники — возможно, нужен доступ к pypi.org
goto stop

:run_failed
echo.
echo   Программа остановилась с ошибкой. Что произошло — написано выше.
goto stop

:stop
echo.
pause
exit /b 1

:finish
echo.
echo AR-песочница остановлена.
pause
exit /b 0
