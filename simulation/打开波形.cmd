@echo off
cd /d "%~dp0"
set "MSIM=C:\intelFPGA\18.1\modelsim_ase\win32aloem"
if exist "%MSIM%\modelsim.exe" goto found
where modelsim.exe >nul 2>nul
if errorlevel 1 goto missing
set "MSIM="
start "" modelsim.exe -view waveform.wlf -do view.do
exit /b
:found
start "" "%MSIM%\modelsim.exe" -view waveform.wlf -do view.do
exit /b
:missing
echo ModelSim was not found. Set MSIM in this file to your installation folder.
pause
