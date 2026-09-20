@echo off
cd /d "%~dp0"
call winvenv\Scripts\activate.bat
python dashboard.py
pause
