@echo off
REM Launch the North Arrow web cockpit, bound to all interfaces so phones and
REM other devices on the same Wi-Fi can reach it at http://<PC-LAN-IP>:5001.
REM Requires a one-time Windows Firewall rule allowing inbound TCP 5001
REM (see the phone-access note in the README / run the netsh command once).
cd /d "%~dp0.."

REM Kill any previous instance still holding port 5001, so this always picks
REM up the latest code instead of reusing a stale already-running process.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :5001 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

start "" http://127.0.0.1:5001
py serve.py --host 0.0.0.0
