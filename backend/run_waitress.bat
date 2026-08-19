@echo off
setlocal
waitress-serve --listen=0.0.0.0:5001 app:app

endlocal
