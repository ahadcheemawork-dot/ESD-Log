# ESD Log App

ESD (Electrostatic Discharge) compliance logging system for manufacturing environments. Tracks daily check-ins with admin management and CSV export.

## Scope
This document provides installation, operation, and support instructions suitable for ISO 9001 documentation requirements: scope, responsibilities, procedure, records, and change control.

## System Requirements
- Windows 10/11 or Windows Server 2019+ (Linux/macOS supported for development)
- Python 3.8+
- Web browser (Chrome/Edge/Firefox)

## Responsibilities
- **System Owner**: approves deployment and change control.
- **Administrator**: installs, configures, and maintains the system.
- **Operators**: tap their badge on the kiosk for daily ESD check-ins — no other interaction required.

## Kiosk Interface
The check-in screen (`frontend/interface.html`) runs full-screen on a QBIC tablet mounted at the door.

- **Layout**: a 3×3 grid of active workers, each shown as a ring/avatar with their name and current status ("Not checked in", "AM passed", "PM check due", or "AM + PM logged"). The screen auto-scales to fill whatever display it's running on.
- **Check-in flow**: tapping a badge on the reader is the check — there is no Pass/Fail prompt. A successful scan automatically logs a **Pass**, animates the worker's tile to the center of the screen, fills in their ring, and returns it to the grid. If someone taps their badge again after already passing for the current AM/PM window, the kiosk just confirms "Already passed" rather than logging a duplicate.
- **LED status**: the QBIC tablet's side LED reflects overall compliance for the day — solid green once everyone has passed, breathing amber/red if people are still outstanding or a check failed, via calls to the tablet's local LED API (`localhost:8080/v1/led/side_led`). This is a browser-side integration in `interface.html`, not routed through the Flask backend.
- **Connection monitoring**: the kiosk polls the backend every few seconds; if it loses contact with the server it shows a banner and keeps retrying automatically.
- **Admin access**: if an administrator's badge is scanned, an "Admin console" link appears during the confirmation screen, linking to `/admin`.

A Fail result is no longer logged from the kiosk itself. To flag someone for an ESD issue, use the Admin console (`frontend/admin.html`).

## Quick Installation (Windows)
1. **Download/Copy** the project folder to the server (e.g., `C:\ESD-LOG-APP`).
2. **Create venv & install deps**:
   ```powershell
   cd C:\ESD-LOG-APP\backend
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
3. **Initialize/upgrade database** (first-time or upgrade):
   ```powershell
   python migrate_db.py
   python migrate_admin.py
   ```

## Run with Waitress (recommended)
From `backend` with venv active:
```powershell
waitress-serve --listen=0.0.0.0:5001 app:app
```
Or use the included script:
```powershell
.\run_waitress.bat
```
Access:
- Kiosk UI: `http://<server-ip>:5001/`
- Admin UI: `http://<server-ip>:5001/admin`

## Install as a Windows Service
Use **NSSM** (Non-Sucking Service Manager). Download from https://nssm.cc.

1. Open an elevated PowerShell and run:
   ```powershell
   nssm install ESDLogApp
   ```
2. In the NSSM UI:
   - **Path**: `C:\ESD-LOG-APP\backend\venv\Scripts\waitress-serve.exe`
   - **Startup directory**: `C:\ESD-LOG-APP\backend`
   - **Arguments**: `--listen=0.0.0.0:5001 app:app`
3. Click **Install service**.
4. Start the service:
   ```powershell
   nssm start ESDLogApp
   ```
5. To stop/remove:
   ```powershell
   nssm stop ESDLogApp
   nssm remove ESDLogApp confirm
   ```

## Configuration (Essentials)
- **Admin PIN**: `frontend/admin.html` (`ADMIN_PIN`, currently line ~498).
- **Timezone**: `backend/app.py` (`DEFAULT_TIMEZONE`, currently line 80 — defaults to `America/New_York`).
- **Daily reset time**: `backend/app.py` (`check_duplicate_log`).
- **Port**: set via the Waitress `--listen` argument (see above), or `run_waitress.bat`.
- **LED reader endpoint**: `frontend/interface.html` (`LED_ENDPOINT`, points at the QBIC tablet's local API).

## Records (Outputs)
- SQLite database file: `backend/esd_logs.db` (retained for audit, excluded from version control).
- CSV exports via Admin UI.

## Troubleshooting
- **Access from other devices**: ensure port 5001 is open and use the server's IP.
- **DB schema error**: run `python migrate_db.py` and `python migrate_admin.py`.
- **Wrong timezone**: update `DEFAULT_TIMEZONE` in `backend/app.py`.
- **Kiosk shows "Lost connection to server"**: confirm the Waitress service/task is running and reachable at the configured port; the kiosk retries automatically once the server is back.
- **LED not responding**: confirm the QBIC tablet's local LED API is reachable at `localhost:8080` — this call is made directly from the browser, not the Flask backend, so it only works when the kiosk is loaded on the QBIC tablet itself.

## Change Control
Record changes to:
- `backend/app.py` (business rules, timezone, duplicate-check logic)
- `frontend/interface.html` (kiosk behavior, LED integration, layout)
- `frontend/admin.html` (admin PIN, admin workflows)
- database migrations (`migrate_db.py`, `migrate_admin.py`)

## Support
Contact your system administrator or IT department.
