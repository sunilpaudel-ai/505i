"""
505i - AI Candidate & Document Screening Tool
--------------------------------------------------
MVP prototype for recruitment / travel agencies.

What it does:
1. Staff uploads a candidate's CV / document (PDF).
2. The app extracts the text from the PDF.
3. It sends the text to Claude (Anthropic API) to produce:
   - A structured candidate summary
   - Missing / incomplete document flags
   - A quick eligibility-style checklist
4. Result is shown on a clean dashboard page.

If no ANTHROPIC_API_KEY is set, the app falls back to a basic
offline extractor (regex-based) so the UI/flow can still be
demoed without an API key.

Run locally:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...   (optional, for real AI screening)
    python app.py
Then open http://localhost:5000
"""

import os
import re
import sqlite3
import uuid
from datetime import datetime

from flask import Flask, request, render_template, redirect, url_for, flash
import pdfplumber
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DB_PATH = os.path.join(BASE_DIR, "505i.db")

os.makedirs(UPLOAD_DIR, exist_ok=True)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL,
            raw_text TEXT,
            summary TEXT,
            flags TEXT,
            mode TEXT
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------
def extract_text_from_pdf(filepath):
    text_parts = []
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
    return "\n".join(text_parts).strip()


# ---------------------------------------------------------------------------
# Screening logic
# ---------------------------------------------------------------------------
def screen_with_claude(text):
    """Use Anthropic's Claude API to screen the document. Requires ANTHROPIC_API_KEY."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt = f"""You are a recruitment/visa document screening assistant for a
Gulf-based HR & travel agency processing candidates for overseas jobs.

Read the candidate document text below and produce:
1. A short candidate summary (name, likely role/skills, experience level) if identifiable.
2. A list of missing or unclear information that a recruiter would need to
   follow up on (e.g. passport validity, contact number, education proof).
3. A short "next step" recommendation for the recruiter.

Keep it concise and practical. Use plain text with clear headings, no markdown
symbols like # or *.

DOCUMENT TEXT:
---
{text[:8000]}
---
"""

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=700,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def screen_offline(text):
    """Basic offline fallback so the demo works without an API key."""
    email = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", text)
    phone = re.search(r"(\+?\d[\d\s\-]{7,}\d)", text)
    name_guess = text.strip().split("\n")[0][:60] if text.strip() else "Not detected"

    flags = []
    if not email:
        flags.append("Email address not found")
    if not phone:
        flags.append("Phone number not found")
    if len(text) < 200:
        flags.append("Document text is very short - may be a scanned image or incomplete upload")
    if "passport" not in text.lower():
        flags.append("No mention of passport - confirm passport details separately")

    summary = (
        f"[Offline demo mode - no ANTHROPIC_API_KEY set]\n\n"
        f"Detected name/first line: {name_guess}\n"
        f"Email found: {email.group(0) if email else 'None'}\n"
        f"Phone found: {phone.group(0) if phone else 'None'}\n"
        f"Document length: {len(text)} characters\n\n"
        f"Note: set ANTHROPIC_API_KEY to get a real AI-generated summary and "
        f"eligibility checklist here instead of this basic extraction."
    )
    return summary, flags


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, filename, uploaded_at, mode FROM candidates ORDER BY uploaded_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    return render_template("index.html", candidates=rows, ai_enabled=bool(ANTHROPIC_API_KEY))


@app.route("/upload", methods=["POST"])
def upload():
    file = request.files.get("document")
    if not file or file.filename == "":
        flash("Kripaya ek PDF file select garnus.")
        return redirect(url_for("index"))

    if not file.filename.lower().endswith(".pdf"):
        flash("Hal ko lagi PDF matra support garcha.")
        return redirect(url_for("index"))

    candidate_id = str(uuid.uuid4())
    saved_path = os.path.join(UPLOAD_DIR, f"{candidate_id}.pdf")
    file.save(saved_path)

    try:
        text = extract_text_from_pdf(saved_path)
    except Exception as e:
        flash(f"PDF padhda error aayo: {e}")
        return redirect(url_for("index"))

    if ANTHROPIC_API_KEY:
        try:
            summary = screen_with_claude(text)
            flags_text = ""
            mode = "ai"
        except Exception as e:
            summary, flags_list = screen_offline(text)
            summary += f"\n\n[AI call failed, showing offline result instead: {e}]"
            flags_text = "; ".join(flags_list)
            mode = "offline"
    else:
        summary, flags_list = screen_offline(text)
        flags_text = "; ".join(flags_list)
        mode = "offline"

    conn = get_db()
    conn.execute(
        "INSERT INTO candidates (id, filename, uploaded_at, raw_text, summary, flags, mode) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (candidate_id, file.filename, datetime.utcnow().isoformat(), text, summary, flags_text, mode),
    )
    conn.commit()
    conn.close()

    return redirect(url_for("result", candidate_id=candidate_id))


@app.route("/result/<candidate_id>")
def result(candidate_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    conn.close()
    if not row:
        flash("Candidate record fela parena.")
        return redirect(url_for("index"))
    return render_template("result.html", c=row)


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
