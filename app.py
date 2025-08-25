# app.py
from flask import Flask, request, redirect, render_template, g, url_for, jsonify
import sqlite3
import string
import random
import gspread
import gspread.exceptions as gexc
from oauth2client.service_account import ServiceAccountCredentials
import json
import os
import traceback
import threading, time
import base64

# --------------------
# Config - edit these
# --------------------
BASE_URL = "https://fictional-fiesta-qxj65rvv9qqfxpqq-5000.app.github.dev"
#CRED_FILE = "cred.json"   # your service account JSON (in repo root)
SHEET_ID = "14etqLqNgEpJG0Z4i0TPsBykwaxVhANyxS_y0Zw46Rzk"
DATABASE = "database.db"
# --------------------



app = Flask(__name__)

# --------------------
# Authenticate with Google Sheets
# --------------------
try:
    creds_json = base64.b64decode(os.environ["GOOGLE_CREDENTIALS_B64"])
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )

    sheet_client = gspread.authorize(creds)
    sheet = sheet_client.open_by_key(SHEET_ID).sheet1
    print("✅ Connected to Google Sheet successfully.")
except Exception as e:
    sheet = None
    print("⚠️ Could not connect to Google Sheet:", str(e))
    traceback.print_exc()

# sanity checks
if not os.path.exists(CRED_FILE):
    print(f"ERROR: credential file '{CRED_FILE}' not found in repo root. Place your service account JSON there.")
else:
    try:
        with open(CRED_FILE, "r", encoding="utf-8") as f:
            cred_json = json.load(f)
            print("Service account email (share the sheet with this email):", cred_json.get("client_email"))
    except Exception as e:
        print("Warning: couldn't read cred file:", e)

# Setup Google Sheets (gspread)
gc = None
sheet = None
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(CRED_FILE, scope)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    sheet = sh.sheet1
    print("Opened sheet:", sh.title)
except Exception as e:
    print("Warning: could not open Google Sheet. Check SHEET_ID and that the service account has Editor access.")
    traceback.print_exc()

# Ensure header exists on start (if sheet accessible)
if sheet is not None:
    try:
        first_row = sheet.row_values(1)
        if first_row[:3] != ["Short Code", "Original URL", "Clicks"]:
            print("Sheet header missing or different -> clearing and writing header")
            sheet.clear()
            sheet.append_row(["Short Code", "Original URL", "Clicks"])
    except Exception as e:
        print("Warning: could not validate/ensure sheet header:", e)

# DB helpers
def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        # use same DB file everywhere
        db = g._database = sqlite3.connect(DATABASE)
    return db

def init_db():
    with app.app_context():
        db = get_db()
        db.execute(
            """CREATE TABLE IF NOT EXISTS links
               (id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code TEXT UNIQUE,
                original_url TEXT UNIQUE,
                clicks INTEGER DEFAULT 0)"""
        )
        db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()

def generate_code(length=6):
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))

# Google Sheets push (row-level)
def push_to_google_sheets(short_code, original_url, clicks):
    if sheet is None:
        print("push_to_google_sheets: sheet client is not configured (sheet is None).")
        return False

    try:
        # Try to find by short_code
        cell = sheet.find(short_code)
        sheet.update_cell(cell.row, 1, short_code)
        sheet.update_cell(cell.row, 2, original_url)
        sheet.update_cell(cell.row, 3, clicks)
        print(f"✅ Updated sheet row {cell.row} for {short_code}")
        return True

    except Exception:
        # If short_code not found, fallback: do a full sync so old rows get cleaned
        try:
            rows = fetch_links_all()
            sheet.clear()
            sheet.append_row(["Short Code", "Original URL", "Clicks"])
            for r in rows:
                sheet.append_row(list(r))
            print(f"♻️ Full sync wrote {len(rows)} rows to sheet.")
            return True
        except Exception as e:
            print("❌ Error while resyncing sheet:", e)
            return False
    
# fetch all links (used by manual sync)
def fetch_links_all():
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute("SELECT short_code, original_url, clicks FROM links ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows

def sync_to_google_sheets_loop():
    while True:
        try:
            rows = fetch_links_all()
            if sheet is None:
                print("Sheet not available; skipping sync.")
            else:
                # update/append each row instead of clearing everything
                for short_code, original_url, clicks in rows:
                    push_to_google_sheets(short_code, original_url, clicks)

                print(f"✅ Synced {len(rows)} rows to Google Sheets")
        except Exception as e:
            print("❌ Error syncing:", e)

        time.sleep(5)  # every 2 seconds
# Routes


@app.route("/edit/<short_code>", methods=["POST"])
def edit_link(short_code):
    new_code = request.form.get("short_code").strip()
    new_url = request.form.get("original_url").strip()

    db = get_db()
    db.execute("UPDATE links SET short_code=?, original_url=? WHERE short_code=?", (new_code, new_url, short_code))
    db.commit()

    # Push update to Google Sheets too
    push_to_google_sheets(new_code, new_url, 0)

    return redirect(url_for("dashboard"))  # <-- forces refresh with latest DB data

@app.route("/update_link", methods=["POST"])
def update_link():
    data = request.get_json(force=True)
    old_code = data.get("old_code", "").strip()
    new_code = data.get("new_code", "").strip()
    new_url = data.get("new_url", "").strip()

    if not old_code or not new_code or not new_url:
        return jsonify({"ok": False, "error": "Missing required fields"}), 400

    db = get_db()

    try:
        # update in DB
        db.execute(
            "UPDATE links SET short_code=?, original_url=? WHERE short_code=?",
            (new_code, new_url, old_code)
        )
        db.commit()

        # update in Google Sheets (best-effort)
        push_to_google_sheets(new_code, new_url, 0)

        return jsonify({"ok": True})
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "Short code already exists"}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/", methods=["GET", "POST"])
def dashboard():
    db = get_db()
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if not url:
            return redirect(url_for("dashboard"))

        # Check if the URL already exists; reuse short code if it does
        existing = db.execute("SELECT short_code, clicks FROM links WHERE original_url=?", (url,)).fetchone()
        if existing:
            code = existing[0]
            clicks = existing[1]
        else:
            # generate code and ensure it's unique (tiny collision loop)
            for _ in range(10):
                code = generate_code()
                coll = db.execute("SELECT 1 FROM links WHERE short_code=?", (code,)).fetchone()
                if not coll:
                    break
            else:
                # fallback: deterministic fallback
                code = str(random.getrandbits(40))[:8]

            db.execute("INSERT INTO links (short_code, original_url, clicks) VALUES (?, ?, ?)", (code, url, 0))
            db.commit()
            clicks = 0
            # Immediately push the new row to Google Sheets (best-effort)
            ok = push_to_google_sheets(code, url, clicks)
            if not ok:
                print("push_to_google_sheets returned False for new link; manual /sync can force a full write.")

    links = db.execute("SELECT short_code, original_url, clicks FROM links ORDER BY id DESC").fetchall()
    return render_template("dashboard.html", links=links, base_url=BASE_URL)

@app.route("/clear", methods=["POST"])
def clear_links():
    db = get_db()
    db.execute("DELETE FROM links")
    db.commit()
    # Clear sheet and re-add header (best-effort)
    if sheet is not None:
        try:
            sheet.clear()
            sheet.append_row(["Short Code", "Original URL", "Clicks"])
            print("Cleared both DB and Google Sheet header.")
        except Exception as e:
            print("Error clearing sheet:", e)
    return redirect(url_for("dashboard"))

@app.route("/sync", methods=["POST"])
def manual_sync():
    """
    Manual full DB -> Google Sheet sync.
    Useful for debugging: rewrites the sheet with current DB state.
    """
    rows = fetch_links_all()
    if sheet is None:
        return jsonify({"ok": False, "error": "sheet not configured"}), 500

    try:
        sheet.clear()
        sheet.append_row(["Short Code", "Original URL", "Clicks"])
        for r in rows:
            sheet.append_row(list(r))
        print(f"Manual full sync wrote {len(rows)} rows to sheet.")
        return jsonify({"ok": True, "rows": len(rows)})
    except Exception as e:
        print("Error during manual sync:", e)
        traceback.print_exc()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/<short_code>")
def redirect_link(short_code):
    db = get_db()
    row = db.execute("SELECT original_url, clicks FROM links WHERE short_code=?", (short_code,)).fetchone()
    if row:
        original_url, clicks = row
        new_clicks = clicks + 1
        db.execute("UPDATE links SET clicks=? WHERE short_code=?", (new_clicks, short_code))
        db.commit()

        # update sheet for this short_code (best-effort)
        ok = push_to_google_sheets(short_code, original_url, new_clicks)
        if not ok:
            print("Warning: sheet update failed on click; you can POST /sync to correct sheet state.")

        return redirect(original_url)
    return "Link not found", 404

if __name__ == "__main__":
    init_db()
    print("Starting Flask app. DB:", DATABASE)
    sync_thread = threading.Thread(target=sync_to_google_sheets_loop, daemon=True)
    sync_thread.start()
    app.run(host="0.0.0.0", port=5000, debug=True)
