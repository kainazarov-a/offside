@echo off
title OFFSIDE server - DO NOT CLOSE
cd /d C:\Users\totti\Downloads\PAR\offside
:loop
echo [%date% %time%] starting OFFSIDE server...
python backend\server.py --live
echo [%date% %time%] server stopped. Restarting in 5 seconds (Ctrl+C twice to abort)...
timeout /t 5 /nobreak >nul
goto loop
