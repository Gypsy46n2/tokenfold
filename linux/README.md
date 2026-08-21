# TokenFold on Linux

1. `./install.sh`  (one time; requires python3.11+ and python3-venv)
2. `./start.sh` — foreground, or `./start-background.sh` to detach
3. Point your AI client's base URL at `http://localhost:9339/v1`
4. `./stop.sh` stops a background instance
5. `tokenfold.service` (optional) — systemd user unit; instructions inside

Full usage: ../USER-GUIDE.md
