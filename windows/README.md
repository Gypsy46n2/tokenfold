# TokenFold on Windows

1. `powershell -ExecutionPolicy Bypass -File install.ps1`  (one time)
2. `.\start.ps1` — run in this terminal, or `.\start-background.ps1` to detach
3. Point your AI client's base URL at `http://localhost:9339/v1`
4. `.\stop.ps1` stops a background instance
5. `.\autostart.ps1` (optional) starts TokenFold at every logon

Requires Python 3.11+ on PATH. Full usage: ../USER-GUIDE.md
