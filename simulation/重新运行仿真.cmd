@echo off
cd /d "%~dp0"
set "MSIM=C:\intelFPGA\18.1\modelsim_ase\win32aloem"
if exist "%MSIM%\vsim.exe" goto found
where vsim.exe >nul 2>nul
if errorlevel 1 goto missing
vsim.exe -c -wlf waveform.wlf -l simulation.log -do generate.do
goto finished
:found
"%MSIM%\vsim.exe" -c -wlf waveform.wlf -l simulation.log -do generate.do
:finished
echo Check simulation.log for PASS tb_afdx_system delivered=2.
pause
exit /b
:missing
echo ModelSim was not found. Set MSIM in this file to your installation folder.
pause
