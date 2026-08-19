# backend/app.py

"""
ESD Log App - Backend (Flask + SQLite) with CLI admin commands and extended admin API
Phase 2: Failure logging, admin bulk indicators, enhanced filtering, 8:30 AM daily reset
Phase 3: Enhanced export with date ranges, weekend skipping for absences
"""

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from datetime import datetime, timedelta
from sqlalchemy import create_engine, Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base
from contextlib import contextmanager
import os
import sys
import csv
import io
import sqlite3
from pytz import timezone as pytz_timezone, utc
import pytz

# -----------------------------
# DATABASE SETUP
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.normpath(os.path.join(BASE_DIR, "..", "frontend"))
DB_PATH = os.path.join(BASE_DIR, "esd.db")
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    active = Column(Boolean, default=True)
    badge_id = Column(String, unique=True, nullable=True)
    is_admin = Column(Boolean, default=False)

    # 🔴 ESD Failure Tracking
    fail_count = Column(Integer, default=0)
    is_flagged = Column(Boolean, default=False)


class ESDLog(Base):
    __tablename__ = "esd_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    timestamp = Column(DateTime, default=lambda: datetime.now(utc))
    device = Column(String, nullable=True)
    status = Column(String, default="pass")  # "pass" or "fail"
    absence_type = Column(String, nullable=True)  # "business_trip", "personal_leave", "administrator_relief", or null
    admin_logged = Column(Boolean, default=False)  # True if logged by admin, False if user logged

    user = relationship("User")

Base.metadata.create_all(engine)

def ensure_admin_column():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}
        if "is_admin" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
            conn.commit()

ensure_admin_column()

# -----------------------------
# FLASK APP SETUP
# -----------------------------
app = Flask(__name__)
CORS(app)

# Default timezone (can be configured)
DEFAULT_TIMEZONE = pytz_timezone('America/New_York')  # Change to your timezone

@contextmanager
def get_db():
    """Context manager for database sessions"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def check_duplicate_log(db, user_id):
    """
    Check if user has already logged a PASS in the current scan window.

    Two scan windows per day:
    - Window 1 (morning):   8:30 AM → 12:59 PM
    - Window 2 (afternoon): 1:00 PM → 8:29 AM next day

    Each window allows exactly one PASS scan.
    FAIL logs can always be retried immediately.
    Both scans are preserved in the database for audit trail.
    """
    now_local = datetime.now(DEFAULT_TIMEZONE)
    hour = now_local.hour
    minute = now_local.minute

    today_830am = now_local.replace(hour=8,  minute=30, second=0, microsecond=0)
    today_1pm   = now_local.replace(hour=13, minute=0,  second=0, microsecond=0)

    if hour < 8 or (hour == 8 and minute < 30):
        # Before 8:30 AM — in the afternoon window that started yesterday at 1 PM
        cutoff_local       = (now_local - timedelta(days=1)).replace(hour=13, minute=0, second=0, microsecond=0)
        next_allowed_local = today_830am
    elif hour < 13:
        # Morning window (8:30 AM – 12:59 PM)
        cutoff_local       = today_830am
        next_allowed_local = today_1pm
    else:
        # Afternoon window (1 PM onward)
        cutoff_local       = today_1pm
        next_allowed_local = (now_local + timedelta(days=1)).replace(hour=8, minute=30, second=0, microsecond=0)

    # Convert to UTC for database query
    cutoff_utc = cutoff_local.astimezone(utc).replace(tzinfo=None)

    # Only check for PASS logs — fails can always retry
    last_pass_log = db.query(ESDLog).filter(
        ESDLog.user_id == user_id,
        ESDLog.timestamp >= cutoff_utc,
        ESDLog.admin_logged == False,
        ESDLog.status == "pass"
    ).order_by(ESDLog.timestamp.desc()).first()

    if last_pass_log:
        return True, last_pass_log.timestamp, last_pass_log.status, next_allowed_local
    return False, None, None, next_allowed_local

def convert_to_timezone(utc_dt, tz=None):
    """Convert UTC datetime to specified timezone"""
    if tz is None:
        tz = DEFAULT_TIMEZONE
    if utc_dt.tzinfo is None:
        utc_dt = utc.localize(utc_dt)
    return utc_dt.astimezone(tz)

def get_date_range_start(date_obj):
    """Get the start of day in UTC for a given date"""
    local_dt = DEFAULT_TIMEZONE.localize(datetime.combine(date_obj, datetime.min.time()))
    return local_dt.astimezone(utc).replace(tzinfo=None)

def is_weekend(date_obj):
    """Check if a date is Saturday (5) or Sunday (6)"""
    return date_obj.weekday() in [5, 6]

def get_business_days(start_date, num_days):
    """
    Generate a list of business days (Mon-Fri) starting from start_date.
    Skips weekends automatically.
    Returns list of date objects.
    """
    business_days = []
    current_date = start_date
    
    while len(business_days) < num_days:
        if not is_weekend(current_date):
            business_days.append(current_date)
        current_date += timedelta(days=1)
    
    return business_days

# -----------------------------
# FRONTEND ROUTES
# -----------------------------
@app.route("/")
def serve_frontend():
    # serve kiosk interface
    return send_from_directory(FRONTEND_DIR, "interface.html")

@app.route("/admin")
def serve_admin():
    return send_from_directory(FRONTEND_DIR, "admin.html")

@app.route("/frontend/<path:filename>")
def serve_frontend_static(filename):
    return send_from_directory(FRONTEND_DIR, filename)

# -----------------------------
# API: USERS
# -----------------------------
@app.route("/api/users", methods=["GET"])
def list_users():
    """
    Return all users (active and inactive) for the admin UI.
    """
    ensure_admin_column()
    with get_db() as db:
        users = db.query(User).order_by(User.name).all()
        return jsonify([
            {
                "id": u.id,
                "name": u.name,
                "active": bool(u.active),
                "badge_id": u.badge_id,
                "is_admin": bool(u.is_admin)
            }
            for u in users
        ])

@app.route("/api/users/badge/<badge_id>", methods=["GET"])
def get_user_by_badge(badge_id):
    """
    Look up a user by their badge ID.
    Returns user info if badge is found and user is active.
    """
    ensure_admin_column()
    with get_db() as db:
        user = db.query(User).filter_by(badge_id=badge_id, active=True).first()
        if not user:
            return jsonify({"error": "Badge not found or user inactive"}), 404

        # 🚫 Block flagged users
        if user.is_flagged:
            return jsonify({
                "error": "User is flagged for ESD issues",
                "flagged": True
            }), 403

        return jsonify({
            "id": user.id,
            "name": user.name,
            "active": bool(user.active),
            "badge_id": user.badge_id,
            "is_admin": bool(user.is_admin)
        })

@app.route("/api/users", methods=["POST"])
def add_user():
    """
    Add a new user. Accepts JSON { "name": "John Doe", "active": true? }.
    Names can contain spaces. Default active=True.
    """
    data = request.json or {}
    name = data.get("name", "")
    if not isinstance(name, str) or not name.strip():
        return jsonify({"error": "Name required"}), 400
    name = name.strip()

    active_val = data.get("active", True)
    if not isinstance(active_val, bool):
        active_val = True

    try:
        with get_db() as db:
            new_user = User(name=name, active=active_val)
            db.add(new_user)
            db.flush()
            result = {"id": new_user.id, "name": new_user.name, "active": bool(new_user.active)}
        return jsonify(result), 201
    except Exception as e:
        msg = str(e)
        if "UNIQUE constraint failed" in msg or "unique constraint" in msg.lower():
            return jsonify({"error": "User with this name already exists"}), 400
        return jsonify({"error": msg}), 400

@app.route("/api/users/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    """
    Update user properties. Expects JSON like { "active": false, "badge_id": "042a4de287" }.
    """
    ensure_admin_column()
    data = request.json or {}
    with get_db() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404

        if "active" in data:
            try:
                if bool(data["active"]) is False and user.is_admin:
                    return jsonify({"error": "Admin users cannot be deactivated"}), 400
                user.active = bool(data["active"])
            except:
                pass
        
        if "badge_id" in data:
            badge = data["badge_id"]
            if badge and badge.strip():
                # Check if badge is already in use by another user
                existing = db.query(User).filter(User.badge_id == badge, User.id != user_id).first()
                if existing:
                    return jsonify({"error": f"Badge already assigned to {existing.name}"}), 400
                user.badge_id = badge.strip()
            else:
                # Clear badge if empty string
                user.badge_id = None

        return jsonify({
            "id": user.id,
            "name": user.name,
            "active": bool(user.active),
            "badge_id": user.badge_id,
            "is_admin": bool(user.is_admin)
        })

@app.route("/api/users/<int:user_id>", methods=["DELETE"])
def deactivate_user(user_id):
    ensure_admin_column()
    with get_db() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        if user.is_admin:
            return jsonify({"error": "Admin users cannot be deactivated"}), 400
        user.active = False
        return jsonify({"message": "User deactivated (logs preserved)"}), 200

def ensure_flag_columns():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(users)")
        columns = {row[1] for row in cursor.fetchall()}

        if "fail_count" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN fail_count INTEGER DEFAULT 0")

        if "is_flagged" not in columns:
            cursor.execute("ALTER TABLE users ADD COLUMN is_flagged INTEGER DEFAULT 0")

        conn.commit()

ensure_flag_columns()


# -----------------------------
# API: ADMIN BULK ABSENCE LOGGING
# -----------------------------
@app.route("/api/admin/log-absence", methods=["POST"])
def admin_log_absence():
    """
    Admin endpoint to log business trip or personal leave for multiple BUSINESS days.
    JSON: {
        "user_id": 1,
        "absence_type": "business_trip" or "personal_leave" or "administrator_relief",
        "days": 5,
        "start_date": "2025-12-17" (optional, defaults to today)
    }
    
    This creates log entries for each BUSINESS day (Mon-Fri) with absence_type and admin_logged=True.
    Weekends are automatically skipped.
    These entries provide "immunity" from email reminders.
    """
    data = request.json or {}
    user_id = data.get("user_id")
    absence_type = data.get("absence_type")
    days = data.get("days", 1)
    start_date_str = data.get("start_date")
    
    # Validation
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    if absence_type not in ["business_trip", "personal_leave", "administrator_relief"]:
        return jsonify({"error": "absence_type must be 'business_trip', 'personal_leave', or 'administrator_relief'"}), 400
    if not isinstance(days, int) or days < 1 or days > 365:
        return jsonify({"error": "days must be between 1 and 365"}), 400
    
    # Parse start date
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except:
            return jsonify({"error": "start_date must be in format YYYY-MM-DD"}), 400
    else:
        # Default to today in local timezone
        start_date = datetime.now(DEFAULT_TIMEZONE).date()
    
    with get_db() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404
        
        # Get business days (automatically skips weekends)
        business_days = get_business_days(start_date, days)
        created_entries = []
        
        # Create a log entry for each business day
        for current_date in business_days:
            timestamp = get_date_range_start(current_date)
            
            # Check if there's already an entry for this day
            existing = db.query(ESDLog).filter(
                ESDLog.user_id == user_id,
                ESDLog.timestamp >= timestamp,
                ESDLog.timestamp < timestamp + timedelta(days=1)
            ).first()
            
            if existing:
                # Update existing entry
                existing.absence_type = absence_type
                existing.admin_logged = True
                created_entries.append({
                    "date": current_date.isoformat(),
                    "day_of_week": current_date.strftime("%A"),
                    "status": "updated"
                })
            else:
                # Create new entry
                entry = ESDLog(
                    user_id=user_id,
                    timestamp=timestamp,
                    device="admin-logged",
                    status="absent",  # Changed from "pass" to "absent"
                    absence_type=absence_type,
                    admin_logged=True
                )
                db.add(entry)
                created_entries.append({
                    "date": current_date.isoformat(),
                    "day_of_week": current_date.strftime("%A"),
                    "status": "created"
                })
        
        return jsonify({
            "message": f"Logged {absence_type.replace('_', ' ')} for {user.name}",
            "user": user.name,
            "absence_type": absence_type,
            "business_days": days,
            "entries": created_entries
        }), 201

# -----------------------------
# API: LOGGING
# -----------------------------
@app.route("/api/log", methods=["POST"])
def log_event():
    """
    Log ESD check-in for a user.
    JSON: { "user_id": 1, "device": "panel-1", "status": "pass" or "fail" }
    
    Phase 3 Logic:
    - PASS logs lock the user until 8:30 AM next day (one pass per day)
    - FAIL logs can be retried immediately (user can try again)
    - All logs are preserved for admin audit trail
    """
    data = request.json or {}
    user_id = data.get("user_id")
    device = data.get("device", "panel-1")
    status = data.get("status", "pass")
    
    if not user_id:
        return jsonify({"error": "user_id required"}), 400
    
    if status not in ["pass", "fail"]:
        return jsonify({"error": "status must be 'pass' or 'fail'"}), 400

    with get_db() as db:
        user = db.query(User).filter_by(id=user_id, active=True).first()
        if not user:
            return jsonify({"error": "User not found or inactive"}), 404

        # Check for duplicate PASS log since today's 8:30 AM
        # FAIL logs are NOT checked - users can retry after failing
        is_duplicate, last_log_time, last_log_status, next_allowed = check_duplicate_log(db, user_id)
        
        if is_duplicate:
            # User already logged a PASS today - they're locked out
            local_time = convert_to_timezone(last_log_time)
            return jsonify({
                "error": "Already logged a PASS today",
                "last_log": local_time.strftime("%Y-%m-%d %I:%M %p"),
                "last_status": "pass",
                "next_allowed": next_allowed.strftime("%A, %B %d at %I:%M %p")
            }), 409

        # Create new log entry (user-initiated, not admin)
        # All logs are saved - multiple failures are allowed and tracked
        entry = ESDLog(
            user_id=user_id, 
            timestamp=datetime.now(utc), 
            device=device,
            status=status,
            admin_logged=False
        )
        db.add(entry)
                
        # 🔴 Failure tracking logic
        if status == "fail":
            user.fail_count = (user.fail_count or 0) + 1
            if user.fail_count >= 3:
                user.is_flagged = True
        elif status == "pass":
            # Reset fail counter, but DO NOT auto-resolve flag
            user.fail_count = 0

        db.flush()

        # Convert timestamp to local timezone for response
        local_timestamp = convert_to_timezone(entry.timestamp)

        return jsonify({
            "status": "success",
            "user": user.name,
            "log_status": status,
            "timestamp": entry.timestamp.isoformat(),
            "local_time": local_timestamp.strftime("%Y-%m-%d %I:%M %p")
        })

@app.route("/api/admin/flagged-users", methods=["GET"])
def get_flagged_users():
    with get_db() as db:
        users = db.query(User).filter(User.is_flagged == True).all()

        results = []
        for u in users:
            last_fail = db.query(ESDLog).filter(
                ESDLog.user_id == u.id,
                ESDLog.status == "fail"
            ).order_by(ESDLog.timestamp.desc()).first()

            results.append({
                "id": u.id,
                "name": u.name,
                "fail_count": u.fail_count,
                "last_fail": last_fail.timestamp.isoformat() if last_fail else None
            })

        return jsonify(results)

@app.route("/api/admin/resolve-flag", methods=["POST"])
def resolve_flag():
    data = request.json or {}
    user_id = data.get("user_id")

    if not user_id:
        return jsonify({"error": "user_id required"}), 400

    with get_db() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            return jsonify({"error": "User not found"}), 404

        user.is_flagged = False
        user.fail_count = 0

        return jsonify({"message": f"Flag resolved for {user.name}"})


@app.route("/api/logs", methods=["GET"])
def get_logs():
    """
    Return all logs, newest first.
    Supports pagination: ?page=1&per_page=50
    Supports time filter: ?filter=daily|weekly|monthly|yearly|all
    Also supports timezone conversion: ?tz=America/New_York
    """
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)
    tz_str = request.args.get("tz", None)
    filter_type = (request.args.get("filter") or "all").lower()

    # Limit per_page to prevent excessive queries
    per_page = min(per_page, 500)

    # Parse timezone if provided
    user_tz = DEFAULT_TIMEZONE
    if tz_str:
        try:
            user_tz = pytz_timezone(tz_str)
        except:
            pass

    # Calculate time filter
    now = datetime.now(utc)
    start = None
    
    if filter_type == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == "yearly":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    with get_db() as db:
        query = db.query(ESDLog).order_by(ESDLog.timestamp.desc())
        
        # Apply time filter if specified
        if start is not None:
            query = query.filter(ESDLog.timestamp >= start)

        # Get total count
        total = query.count()

        # Apply pagination
        offset = (page - 1) * per_page
        logs = query.limit(per_page).offset(offset).all()

        # Convert timestamps
        log_list = []
        for log in logs:
            local_time = convert_to_timezone(log.timestamp, user_tz)
            log_list.append({
                "id": log.id,
                "user_id": log.user_id,
                "user": log.user.name,
                "timestamp": log.timestamp.isoformat(),
                "local_time": local_time.strftime("%Y-%m-%d %I:%M %p"),
                "device": log.device,
                "status": log.status,
                "absence_type": log.absence_type,
                "admin_logged": log.admin_logged
            })

        return jsonify({
            "logs": log_list,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page
            }
        })

@app.route("/api/logs/<int:user_id>", methods=["GET"])
def get_user_logs(user_id):
    """
    Return logs for a specific user.
    Supports query param 'filter' = daily|weekly|monthly|yearly|all (default all).
    Also supports timezone conversion: ?tz=America/New_York
    """
    filter_type = (request.args.get("filter") or "all").lower()
    tz_str = request.args.get("tz", None)
    now = datetime.now(utc)

    # Parse timezone if provided
    user_tz = DEFAULT_TIMEZONE
    if tz_str:
        try:
            user_tz = pytz_timezone(tz_str)
        except:
            pass

    if filter_type == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == "monthly":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif filter_type == "yearly":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = None

    with get_db() as db:
        q = db.query(ESDLog).filter(ESDLog.user_id == user_id)
        if start is not None:
            q = q.filter(ESDLog.timestamp >= start)
        logs = q.order_by(ESDLog.timestamp.desc()).all()

        log_list = []
        for log in logs:
            local_time = convert_to_timezone(log.timestamp, user_tz)
            log_list.append({
                "id": log.id,
                "user_id": log.user_id,
                "user": log.user.name,
                "timestamp": log.timestamp.isoformat(),
                "local_time": local_time.strftime("%Y-%m-%d %I:%M %p"),
                "device": log.device,
                "status": log.status,
                "absence_type": log.absence_type,
                "admin_logged": log.admin_logged
            })

        return jsonify(log_list)

# -----------------------------
# API: EXPORT
# -----------------------------
@app.route("/api/export/logs", methods=["GET"])
def export_logs():
    """
    Export logs as CSV with flexible date range filtering.
    Supports:
    - filter=daily|weekly|monthly|yearly|all
    - start_date=YYYY-MM-DD
    - end_date=YYYY-MM-DD
    - user_id=N (optional)
    """
    filter_type = (request.args.get("filter") or "all").lower()
    user_id = request.args.get("user_id", type=int)
    tz_str = request.args.get("tz", None)
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    
    now = datetime.now(utc)

    user_tz = DEFAULT_TIMEZONE
    if tz_str:
        try:
            user_tz = pytz_timezone(tz_str)
        except:
            pass

    # Determine date range
    start = None
    end = None
    
    # Custom date range takes precedence
    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            start = get_date_range_start(start_date)
        except:
            pass
    
    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            end = get_date_range_start(end_date) + timedelta(days=1)
        except:
            pass
    
    # If no custom range, use filter
    if start is None and end is None:
        if filter_type == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        elif filter_type == "weekly":
            start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        elif filter_type == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        elif filter_type == "yearly":
            start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    with get_db() as db:
        q = db.query(ESDLog)
        if user_id:
            q = q.filter(ESDLog.user_id == user_id)
        if start is not None:
            q = q.filter(ESDLog.timestamp >= start)
        if end is not None:
            q = q.filter(ESDLog.timestamp < end)
        logs = q.order_by(ESDLog.timestamp.desc()).all()

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(['Log ID', 'User Name', 'Date', 'Time', 'Device', 'Status', 'Absence Type', 'Admin Logged', 'UTC Timestamp'])

        for log in logs:
            local_time = convert_to_timezone(log.timestamp, user_tz)
            
            # Format absence type for display
            absence_display = '-'
            if log.absence_type == 'business_trip':
                absence_display = 'Business Trip (BT)'
            elif log.absence_type == 'personal_leave':
                absence_display = 'Personal Leave (PL)'
            elif log.absence_type == 'administrator_relief':
                absence_display = 'Administrator Relief (AR)'
            
            # Format status - distinguish between BT, ABS, and AR
            status_display = log.status.upper()
            if log.admin_logged and log.absence_type == 'business_trip':
                status_display = 'BT'
            elif log.admin_logged and log.absence_type == 'personal_leave':
                status_display = 'ABS'
            elif log.admin_logged and log.absence_type == 'administrator_relief':
                status_display = 'AR'
            
            writer.writerow([
                log.id,
                log.user.name,
                local_time.strftime('%Y-%m-%d'),
                local_time.strftime('%I:%M %p'),
                log.device or 'N/A',
                status_display,
                absence_display,
                'Yes' if log.admin_logged else 'No',
                log.timestamp.isoformat()
            ])

        output.seek(0)
        
        # Generate filename based on date range
        if start_date_str and end_date_str:
            filename = f"esd_logs_{start_date_str}_to_{end_date_str}.csv"
        else:
            filename = f"esd_logs_{filter_type}_{datetime.now().strftime('%Y%m%d')}.csv"

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

@app.route("/api/export/compliance", methods=["GET"])
def export_compliance_report():
    """
    Export compliance report showing who has/hasn't logged today.
    """
    tz_str = request.args.get("tz", None)

    user_tz = DEFAULT_TIMEZONE
    if tz_str:
        try:
            user_tz = pytz_timezone(tz_str)
        except:
            pass

    now_local = datetime.now(user_tz)
    today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_local.astimezone(utc).replace(tzinfo=None)

    with get_db() as db:
        users = db.query(User).filter_by(active=True).order_by(User.name).all()

        today_logs = db.query(ESDLog).filter(
            ESDLog.timestamp >= today_start_utc
        ).all()

        logged_user_ids = {}
        for log in today_logs:
            if log.user_id not in logged_user_ids:
                logged_user_ids[log.user_id] = log

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(['User Name', 'Status', 'Log Time', 'Device', 'Result', 'Absence Type'])

        for user in users:
            if user.id in logged_user_ids:
                log = logged_user_ids[user.id]
                local_time = convert_to_timezone(log.timestamp, user_tz)
                
                # Format absence type
                absence_display = '-'
                if log.absence_type == 'business_trip':
                    absence_display = 'Business Trip (BT)'
                elif log.absence_type == 'personal_leave':
                    absence_display = 'Personal Leave (PL)'
                elif log.absence_type == 'administrator_relief':
                    absence_display = 'Administrator Relief (AR)'
                
                # Determine status - distinguish BT from ABS from AR
                if log.absence_type == 'business_trip':
                    status = f"Excused (BT)"
                elif log.absence_type == 'personal_leave':
                    status = f"Excused (ABS)"
                elif log.absence_type == 'administrator_relief':
                    status = f"Excused (AR)"
                else:
                    status = 'Compliant'
                
                # Format result - distinguish BT from ABS from AR
                result = log.status.upper()
                if log.admin_logged and log.absence_type == 'business_trip':
                    result = 'BT'
                elif log.admin_logged and log.absence_type == 'personal_leave':
                    result = 'ABS'
                elif log.admin_logged and log.absence_type == 'administrator_relief':
                    result = 'AR'
                
                writer.writerow([
                    user.name,
                    status,
                    local_time.strftime('%I:%M %p'),
                    log.device or 'N/A',
                    result,
                    absence_display
                ])
            else:
                writer.writerow([
                    user.name,
                    'Not Logged',
                    '-',
                    '-',
                    '-',
                    '-'
                ])

        output.seek(0)
        filename = f"esd_compliance_{datetime.now().strftime('%Y%m%d')}.csv"

        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={filename}'}
        )

# -----------------------------
# CLI ADMIN FUNCTIONS
# -----------------------------
def cli_add_user(name):
    try:
        with get_db() as db:
            new_user = User(name=name, active=True)
            db.add(new_user)
            db.flush()
            print(f"User added: {new_user.name} (ID: {new_user.id})")
    except Exception as e:
        print(f"Error: {e}")

def cli_deactivate_user(user_id):
    with get_db() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            print("User not found")
            return
        user.active = False
        print(f"User deactivated: {user.name} (ID: {user.id})")

def cli_list_users():
    with get_db() as db:
        users = db.query(User).order_by(User.id).all()
        for u in users:
            status = "Active" if u.active else "Inactive"
            role = "Admin" if u.is_admin else "User"
            print(f"{u.id}: {u.name} [{status}, {role}]")

def cli_set_admin(user_id, is_admin=True):
    with get_db() as db:
        user = db.query(User).filter_by(id=user_id).first()
        if not user:
            print("User not found")
            return
        user.is_admin = bool(is_admin)
        role = "Admin" if user.is_admin else "User"
        print(f"User updated: {user.name} (ID: {user.id}) -> {role}")

def cli_list_logs():
    with get_db() as db:
        logs = db.query(ESDLog).order_by(ESDLog.timestamp.desc()).all()
        for log in logs:
            local_time = convert_to_timezone(log.timestamp)
            indicators = []
            
            # Format absence type
            if log.absence_type == 'business_trip':
                indicators.append('Business Trip (BT)')
            elif log.absence_type == 'personal_leave':
                indicators.append('Personal Leave (PL)')
            elif log.absence_type == 'administrator_relief':
                indicators.append('Administrator Relief (AR)')
            
            if log.admin_logged:
                indicators.append("Admin Logged")
            
            indicator_str = f" [{', '.join(indicators)}]" if indicators else ""
            
            # Format status
            status_display = log.status.upper()
            if log.status == 'absent' or (log.admin_logged and log.absence_type):
                if log.absence_type == 'business_trip':
                    status_display = 'BT'
                elif log.absence_type == 'administrator_relief':
                    status_display = 'AR'
                else:
                    status_display = 'ABS'
            
            print(f"{log.id}: {log.user.name} - {local_time.strftime('%Y-%m-%d %I:%M %p')} - Device: {log.device} - Status: {status_display}{indicator_str}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "adduser" and len(sys.argv) == 3:
            cli_add_user(sys.argv[2])
        elif cmd == "deactivate" and len(sys.argv) == 3:
            cli_deactivate_user(int(sys.argv[2]))
        elif cmd == "listusers":
            cli_list_users()
        elif cmd == "listlogs":
            cli_list_logs()
        elif cmd == "setadmin" and len(sys.argv) >= 3:
            is_admin = True
            if len(sys.argv) == 4:
                is_admin = sys.argv[3].lower() in ["true", "1", "yes", "y"]
            cli_set_admin(int(sys.argv[2]), is_admin)
        else:
            print("Commands:\n  adduser 'Name'\n  deactivate ID\n  listusers\n  listlogs\n  setadmin ID [true|false]")
    else:
        print("Running ESD Backend on http://127.0.0.1:5001")
        # App will be run with Waitress on Windows!! Please setup your environment accordingly.