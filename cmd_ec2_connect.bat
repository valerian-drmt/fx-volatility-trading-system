@echo off
REM ---------------------------------------------------------------------------
REM  fx-volatility-trading-system - open a shell on the PROD EC2 host.
REM  Double-click this file (or run it) to open a dedicated window that starts
REM  an interactive SSM Session Manager shell on the server (port 22 is closed;
REM  everything goes through AWS SSM, no SSH).
REM
REM  Needs: AWS CLI v2 + Session Manager plugin, AWS profile 'admin'.
REM  In the shell:  cd /opt/fxvol   before any 'sudo docker compose ...'.
REM  Leave with:  exit  (or Ctrl-D). -NoExit keeps this window open afterwards.
REM ---------------------------------------------------------------------------
title fxvol EC2 shell (SSM)

set "PS=powershell.exe"
where pwsh.exe >nul 2>&1 && set "PS=pwsh.exe"

"%PS%" -NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "%~dp0scripts\aws\ec2.ps1" connect

if errorlevel 1 pause
