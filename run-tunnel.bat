@echo off
title OFFSIDE tunnel - DO NOT CLOSE
:loop
echo [%date% %time%] starting ngrok tunnel...
ngrok http --url=worsening-likewise-grudging.ngrok-free.dev 8000
echo [%date% %time%] tunnel stopped. Restarting in 5 seconds (Ctrl+C twice to abort)...
timeout /t 5 /nobreak >nul
goto loop
