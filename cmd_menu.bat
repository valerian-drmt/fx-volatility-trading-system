@echo off
REM ---------------------------------------------------------------------------
REM  fx-volatility-trading-system - clickable ops launcher.
REM  Double-click this file (or run it from a terminal) to open the ops menu
REM  that wraps stack.ps1 / load_secrets.ps1 / ec2.ps1 / provision-*.ps1.
REM
REM  -NoExit keeps the window open after the menu is quit, so the secrets loaded
REM  into that session stay available for manual docker/compose commands.
REM ---------------------------------------------------------------------------
title fxvol ops

set "PS=powershell.exe"
where pwsh.exe >nul 2>&1 && set "PS=pwsh.exe"

"%PS%" -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0scripts\fxvol.ps1"

REM If PowerShell itself failed to start, keep the window open to show why.
if errorlevel 1 pause