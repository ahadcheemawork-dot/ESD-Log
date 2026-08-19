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
- **Operators**: use the kiosk UI for daily ESD check-ins.

## Quick Installation (Windows)
1. **Download/Copy** the project folder to the server (e.g., `C:\ESD-LOG-APP`).
2. **Create venv & install deps**:
   ```powershell
   cd C:\ESD-LOG-APP\backend
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   pip install flask flask-cors sqlalchemy pytz waitress
   ```
3. **Initialize/upgrade database** (first-time or upgrade):
   ```powershell
   python migrate_db.py
   python migrate_admin.py
   ```

## Run with Waitress (recommended)
From `backend` with venv active:
```powershell
waitress-serve --host=0.0.0.0 --port=5001 app:app
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
   - **Arguments**: `--host=0.0.0.0 --port=5001 app:app`
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
- **Admin PIN**: `frontend/admin.html` (`ADMIN_PIN`).
- **Timezone**: `backend/app.py` (`DEFAULT_TIMEZONE`).
- **Daily reset time**: `backend/app.py` (`check_duplicate_log`).
- **Port**: `backend/app.py` (if not using waitress CLI).

## Records (Outputs)
- SQLite database file: `backend/esd_logs.db` (retained for audit).
- CSV exports via Admin UI.

## Troubleshooting
- **Access from other devices**: ensure port 5001 open and use server IP.
- **DB schema error**: run `python migrate_db.py` and `python migrate_admin.py`.
- **Wrong timezone**: update `DEFAULT_TIMEZONE` in `backend/app.py`.

## Change Control
Record changes to:
- `backend/app.py` (business rules)
- `frontend/admin.html` (admin PIN)
- database migrations (`migrate_db.py`, `migrate_admin.py`)

## Support
Contact your system administrator or IT department.
