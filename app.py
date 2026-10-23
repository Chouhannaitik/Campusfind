
from flask import Flask, request, redirect, url_for, render_template_string
import sqlite3
import os
import re
import secrets
from difflib import SequenceMatcher
from werkzeug.exceptions import RequestEntityTooLarge

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "campusfind.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAX_FILE_SIZE = 5 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


# ---------------- DATABASE ----------------

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            name TEXT NOT NULL,
            category TEXT,
            location TEXT,
            description TEXT,
            date TEXT
        )
    """)

    columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(items)").fetchall()
    }

    if "contact_number" not in columns:
        conn.execute("ALTER TABLE items ADD COLUMN contact_number TEXT")

    if "photo_filename" not in columns:
        conn.execute("ALTER TABLE items ADD COLUMN photo_filename TEXT")

    conn.commit()
    conn.close()


def clean_status(status):
    status = (status or "").strip().lower()
    return status if status in ("lost", "found") else ""


# ---------------- PHOTO VALIDATION ----------------

def get_image_type(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"

    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"

    return None


def save_photo(file):
    if not file or not file.filename:
        return None, None

    original_name = file.filename

    if "." not in original_name:
        return None, "Photo JPG, PNG ya WEBP format mein honi chahiye."

    extension = original_name.rsplit(".", 1)[1].lower()

    if extension not in ALLOWED_EXTENSIONS:
        return None, "Sirf JPG, JPEG, PNG aur WEBP photos allowed hain."

    data = file.stream.read(MAX_FILE_SIZE + 1)

    if not data:
        return None, "Selected photo empty hai."

    if len(data) > MAX_FILE_SIZE:
        return None, "Photo ka size 5 MB se kam hona chahiye."

    detected_type = get_image_type(data)

    if detected_type is None:
        return None, "Valid image file select karo."

    expected = "jpg" if extension in ("jpg", "jpeg") else extension

    if detected_type != expected:
        return None, "Photo ka file type sahi nahi hai."

    filename = secrets.token_hex(16) + "." + detected_type
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    with open(filepath, "wb") as output:
        output.write(data)

    return filename, None


# ---------------- SMART MATCHES ----------------

def find_matches(item):
    conn = get_db()

    others = conn.execute(
        "SELECT * FROM items WHERE id != ?",
        (item["id"],)
    ).fetchall()

    conn.close()
    results = []

    for other in others:
        if clean_status(other["status"]) == clean_status(item["status"]):
            continue

        score = 0
        reasons = []

        name_a = (item["name"] or "").lower()
        name_b = (other["name"] or "").lower()
        name_score = SequenceMatcher(None, name_a, name_b).ratio()

        if name_score > 0.25:
            score += name_score * 50
            reasons.append("Similar item name")

        cat_a = (item["category"] or "").strip().lower()
        cat_b = (other["category"] or "").strip().lower()

        if cat_a and cat_a == cat_b:
            score += 25
            reasons.append("Same category")

        loc_a = set(re.findall(r"\w+", (item["location"] or "").lower()))
        loc_b = set(re.findall(r"\w+", (other["location"] or "").lower()))

        if loc_a and loc_b:
            overlap = len(loc_a & loc_b) / len(loc_a | loc_b)
            score += overlap * 15
            if overlap:
                reasons.append("Similar location")

        desc_a = set(re.findall(r"\w+", (item["description"] or "").lower()))
        desc_b = set(re.findall(r"\w+", (other["description"] or "").lower()))

        if desc_a and desc_b:
            overlap = len(desc_a & desc_b) / len(desc_a | desc_b)
            score += overlap * 10
            if overlap:
                reasons.append("Similar description")

        if score >= 20:
            results.append({
                "item": other,
                "score": min(round(score), 99),
                "reasons": reasons
            })

    results.sort(key=lambda result: result["score"], reverse=True)
    return results[:5]


# ---------------- SHARED PAGE ----------------

BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ title }} | CampusFind</title>
<link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
<style>
* { box-sizing: border-box; }
body { margin: 0; font-family: Arial, sans-serif; color: #172033; }
.container { width: min(1100px, 92%); margin: auto; }
.navbar { padding: 16px 0; background: white; border-bottom: 1px solid #e5e7eb; }
.navbar .container { display: flex; align-items: center; justify-content: space-between; gap: 14px; flex-wrap: wrap; }
.brand { font-size: 25px; font-weight: 800; text-decoration: none; color: #172033; }
.brand span { color: #635bff; }
.nav-links { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
a { color: #5548e8; }
.nav-links a { text-decoration: none; }
.btn, button { display: inline-block; border: 0; border-radius: 9px; padding: 12px 16px; background: #5b50ed; color: white; font-weight: 700; text-decoration: none; cursor: pointer; }
.btn-outline { background: white; color: #5548e8; border: 1px solid #d8d5ff; }
.hero { margin: 28px 0; padding: 35px; border-radius: 20px; background: #f1f0ff; }
.hero h1 { font-size: clamp(30px, 6vw, 48px); line-height: 1.12; margin: 12px 0; }
.search-form { display: flex; gap: 10px; margin-top: 20px; }
.search-form input { flex: 1; min-width: 0; }
input, textarea, select { width: 100%; padding: 12px; border: 1px solid #d1d5db; border-radius: 9px; font: inherit; }
.form-card { max-width: 650px; margin: 30px auto; padding: 25px; border: 1px solid #e5e7eb; border-radius: 16px; }
.form-group { margin: 18px 0; }
.form-group label { display: block; font-weight: 700; margin-bottom: 8px; }
.items-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 260px), 1fr)); gap: 18px; margin: 20px 0; }
.item-card { min-width: 0; padding: 18px; border: 1px solid #e5e7eb; border-radius: 15px; background: white; overflow-wrap: anywhere; }
.item-card p { line-height: 1.5; }
.status-badge { display: inline-block; background: #fff0e9; color: #b94b17; padding: 6px 9px; border-radius: 7px; font-size: 12px; font-weight: 800; }
.status-badge.found { background: #dcfce7; color: #166534; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; margin: 20px 0; }
.filter-link { padding: 10px 14px; border: 1px solid #e5e7eb; border-radius: 9px; text-decoration: none; }
.filter-link.active { background: #5b50ed; color: white; }
.empty-state { padding: 24px; border-radius: 12px; background: #f8fafc; margin: 20px 0; }
footer { margin-top: 50px; padding: 25px 0; border-top: 1px solid #e5e7eb; color: #64748b; }
@media (max-width: 600px) {
  .hero { padding: 22px; }
  .search-form { flex-direction: column; }
  .form-card { padding: 18px; }
  .nav-links { width: 100%; }
  .nav-links a { font-size: 14px; }
}
</style>
</head>
<body>
<nav class="navbar">
  <div class="container">
    <a class="brand" href="{{ url_for('home') }}">Campus<span>Find</span></a>
    <div class="nav-links">
      <a href="{{ url_for('home') }}">Home</a>
      <a href="{{ url_for('report', status='lost') }}">Report Lost</a>
      <a class="btn" href="{{ url_for('report', status='found') }}">Report Found +</a>
    </div>
  </div>
</nav>
<main class="container">{{ content|safe }}</main>
<footer><div class="container">CampusFind · Helping lost items find their way home.</div></footer>
</body>
</html>
"""


def page(title, template, **context):
    content = render_template_string(template, **context)
    return render_template_string(BASE_HTML, title=title, content=content)


# ---------------- ITEM CARD ----------------

CARD = """
<article class="item-card">
  {% if item['photo_filename'] %}
    <img src="{{ url_for('static', filename='uploads/' + item['photo_filename']) }}"
         alt="Photo of {{ item['name'] }}"
         loading="lazy"
         style="width:100%;height:210px;object-fit:cover;border-radius:10px">
  {% else %}
    <div style="height:120px;background:#f1f0ff;border-radius:10px;display:grid;place-items:center;font-size:42px">📦</div>
  {% endif %}

  <p><span class="status-badge {{ 'found' if item['status']|lower|trim == 'found' else '' }}">{{ item['status']|upper }}</span></p>
  <h3>{{ item['name'] }}</h3>
  <p>📦 <strong>Category:</strong> {{ item['category'] or 'Other' }}</p>
  <p>📍 <strong>Location:</strong> {{ item['location'] or 'Not specified' }}</p>
  <p>📝 {{ item['description'] or 'No description provided.' }}</p>
  <p>📅 <strong>Date:</strong> {{ item['date'] or 'Not specified' }}</p>

  <div style="background:#f0fdf4;padding:12px;border-radius:10px">
    <strong>📞 Contact Number</strong><br>
    {% if item['contact_number'] %}
      <a href="tel:{{ item['contact_number'] }}">{{ item['contact_number'] }}</a>
    {% else %}
      <span>Not provided for this report.</span>
    {% endif %}
  </div>

  <p><a class="btn btn-outline" href="{{ url_for('matches_page', item_id=item['id']) }}">✨ Find Possible Matches</a></p>
</article>
"""


# ---------------- HOME / SEARCH / STATS ----------------

HOME_HTML = """
<section class="hero">
  <p><strong>YOUR CAMPUS. YOUR COMMUNITY.</strong></p>
  <h1>Lost something?<br>Let's find it together.</h1>
  <p>Report lost belongings, share found items and help reconnect people with what matters.</p>

  <form action="/" method="get" class="search-form">
    <input name="search" value="{{ search }}" placeholder="Search item, category or location...">
    {% if status %}<input type="hidden" name="status" value="{{ status }}">{% endif %}
    <button type="submit">Search</button>
  </form>

  <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">
    <a class="btn" href="{{ url_for('report', status='lost') }}">🔍 I Lost Something</a>
    <a class="btn btn-outline" href="{{ url_for('report', status='found') }}">📸 I Found Something</a>
  </div>
</section>

<h2>Explore Reports</h2>
<div class="filters">
  <a class="filter-link {{ 'active' if not status else '' }}" href="{{ url_for('home', search=search) }}">All Items</a>
  <a class="filter-link {{ 'active' if status == 'lost' else '' }}" href="{{ url_for('home', status='lost', search=search) }}">Lost Items</a>
  <a class="filter-link {{ 'active' if status == 'found' else '' }}" href="{{ url_for('home', status='found', search=search) }}">Found Items</a>
</div>

<div class="items-grid">
  <div class="item-card"><p>Total Reports</p><h2>{{ total }}</h2></div>
  <div class="item-card"><p>Lost Items</p><h2>{{ lost_count }}</h2></div>
  <div class="item-card"><p>Found Items</p><h2>{{ found_count }}</h2></div>
</div>

<h2>Latest Reports</h2>
<div class="items-grid">{{ cards|safe }}</div>
{% if not items %}
<div class="empty-state"><h3>No reports found</h3><p>Try another search or submit a report.</p></div>
{% endif %}
"""


@app.route("/")
def home():
    search = request.args.get("search", "").strip()
    status = clean_status(request.args.get("status", ""))

    sql = "SELECT * FROM items WHERE 1=1"
    params = []

    if status:
        sql += " AND LOWER(TRIM(status)) = ?"
        params.append(status)

    if search:
        sql += " AND (name LIKE ? OR category LIKE ? OR location LIKE ? OR description LIKE ?)"
        term = "%" + search + "%"
        params.extend([term, term, term, term])

    sql += " ORDER BY id DESC"

    conn = get_db()
    items = conn.execute(sql, params).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    lost_count = conn.execute(
        "SELECT COUNT(*) FROM items WHERE LOWER(TRIM(status))='lost'"
    ).fetchone()[0]
    found_count = conn.execute(
        "SELECT COUNT(*) FROM items WHERE LOWER(TRIM(status))='found'"
    ).fetchone()[0]

    conn.close()

    cards = "".join(render_template_string(CARD, item=item) for item in items)

    return page(
        "Home", HOME_HTML,
        items=items, cards=cards, search=search, status=status,
        total=total, lost_count=lost_count, found_count=found_count
    )


# ---------------- REPORT FORM ----------------

CATEGORIES = [
    "Electronics", "Books & Stationery", "Bags", "Clothing",
    "Accessories", "Water Bottles", "Keys", "Other"
]

FORM_HTML = """
<section class="form-card">
  <h1>{{ 'Report a Lost Item' if status == 'lost' else 'Report a Found Item' }}</h1>
  <p>Fill in the details. You can take a photo directly using your phone camera.</p>

  {% if error %}
    <div class="empty-state">{{ error }}</div>
  {% endif %}

  <form method="post" enctype="multipart/form-data">
    <div class="form-group">
      <label for="name">Item Name *</label>
      <input id="name" name="name" required maxlength="120"
             placeholder="e.g. Blue water bottle" value="{{ values.get('name', '') }}">
    </div>

    <div class="form-group">
      <label for="category">Category</label>
      <select id="category" name="category">
        <option value="">Choose category</option>
        {% for category in categories %}
          <option value="{{ category }}" {% if values.get('category') == category %}selected{% endif %}>{{ category }}</option>
        {% endfor %}
      </select>
    </div>

    <div class="form-group">
      <label for="location">Location *</label>
      <input id="location" name="location" required maxlength="150"
             placeholder="e.g. Library, Block A" value="{{ values.get('location', '') }}">
    </div>

    <div class="form-group">
      <label for="description">Description</label>
      <textarea id="description" name="description" maxlength="1000"
                placeholder="Colour, brand or other details">{{ values.get('description', '') }}</textarea>
    </div>

    <div class="form-group">
      <label for="date">Date Lost or Found</label>
      <input id="date" name="date" type="date" value="{{ values.get('date', '') }}">
    </div>

    <div class="form-group">
      <label for="contact_number">Contact Number *</label>
      <input id="contact_number" name="contact_number" type="tel" required maxlength="20"
             pattern="[0-9+() -]{7,20}" placeholder="e.g. 98765 43210"
             value="{{ values.get('contact_number', '') }}">
      <small>Your contact number will be visible on the public report.</small>
    </div>

    <div class="form-group">
      <label for="photo">📸 Take Photo / Choose Photo</label>
      <input id="photo" name="photo" type="file"
             accept="image/*" capture="environment">
      <small style="display:block;margin-top:8px">
        JPG, PNG or WEBP · Maximum 5 MB. Avoid uploading ID cards,
        private documents or images containing sensitive personal information.
      </small>
      <img id="photo-preview" alt="Photo preview"
           style="display:none;width:100%;max-height:300px;object-fit:contain;margin-top:12px;border-radius:12px;background:#f1f5f9">
    </div>

    <button type="submit">Submit Report</button>
    <a class="btn btn-outline" href="{{ url_for('home') }}">Cancel</a>
  </form>
</section>

<script>
const photoInput = document.getElementById("photo");
const preview = document.getElementById("photo-preview");

photoInput.addEventListener("change", function () {
  const file = this.files && this.files[0];

  if (!file) {
    preview.style.display = "none";
    preview.removeAttribute("src");
    return;
  }

  const allowedTypes = ["image/jpeg", "image/png", "image/webp"];

  if (!allowedTypes.includes(file.type)) {
    alert("Please select a JPG, PNG or WEBP image.");
    this.value = "";
    preview.style.display = "none";
    return;
  }

  if (file.size > 5 * 1024 * 1024) {
    alert("Photo size must be 5 MB or less.");
    this.value = "";
    preview.style.display = "none";
    return;
  }

  if (preview.dataset.objectUrl) {
    URL.revokeObjectURL(preview.dataset.objectUrl);
  }

  const objectUrl = URL.createObjectURL(file);
  preview.dataset.objectUrl = objectUrl;
  preview.src = objectUrl;
  preview.style.display = "block";
});
</script>
"""


def render_report_form(status, error="", values=None):
    return page(
        "Report Item", FORM_HTML,
        status=status,
        error=error,
        values=values or {},
        categories=CATEGORIES
    )


@app.route("/report/<status>", methods=["GET", "POST"])
def report(status):
    status = clean_status(status)

    if not status:
        return redirect(url_for("home"))

    if request.method == "GET":
        return render_report_form(status)

    values = {
        "name": request.form.get("name", "").strip(),
        "category": request.form.get("category", "").strip(),
        "location": request.form.get("location", "").strip(),
        "description": request.form.get("description", "").strip(),
        "date": request.form.get("date", "").strip(),
        "contact_number": request.form.get("contact_number", "").strip()
    }

    if not values["name"] or not values["location"] or not values["contact_number"]:
        return render_report_form(
            status, "Please fill in item name, location and contact number.", values
        )

    if not re.fullmatch(r"[0-9+() -]{7,20}", values["contact_number"]):
        return render_report_form(status, "Enter a valid contact number.", values)

    photo_file = request.files.get("photo")
    photo_filename, photo_error = save_photo(photo_file)

    if photo_error:
        return render_report_form(status, photo_error, values)

    conn = get_db()

    try:
        conn.execute("""
            INSERT INTO items
            (status, name, category, location, description, date,
             contact_number, photo_filename)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            status,
            values["name"],
            values["category"],
            values["location"],
            values["description"],
            values["date"],
            values["contact_number"],
            photo_filename
        ))
        conn.commit()

    except Exception:
        conn.rollback()

        if photo_filename:
            saved_path = os.path.join(UPLOAD_FOLDER, photo_filename)
            if os.path.exists(saved_path):
                os.remove(saved_path)

        raise

    finally:
        conn.close()

    return redirect(url_for("home"))


# ---------------- MATCH RESULTS ----------------

MATCHES_HTML = """
<h2>✨ Smart Possible Matches</h2>
<p>These suggestions compare item names, categories, locations and descriptions.</p>

<h3>Your Report</h3>
<div class="items-grid">{{ original_card|safe }}</div>

<h3>Suggested Reports</h3>
{% if matches %}
  {% for match in matches %}
    <div style="margin:20px 0">
      <p><strong>Match score: {{ match.score }}%</strong> · {{ match.reasons|join(', ') }}</p>
      <div class="items-grid">{{ match.card|safe }}</div>
    </div>
  {% endfor %}
{% else %}
  <div class="empty-state">
    <h3>No possible matches yet</h3>
    <p>New reports may produce a match later.</p>
  </div>
{% endif %}

<a class="btn btn-outline" href="{{ url_for('home') }}">← Back to Home</a>
"""


@app.route("/matches/<int:item_id>")
def matches_page(item_id):
    conn = get_db()
    item = conn.execute(
        "SELECT * FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    conn.close()

    if item is None:
        return redirect(url_for("home"))

    matches = []

    for result in find_matches(item):
        matches.append({
            "score": result["score"],
            "reasons": result["reasons"],
            "card": render_template_string(CARD, item=result["item"])
        })

    return page(
        "Smart Possible Matches",
        MATCHES_HTML,
        original_card=render_template_string(CARD, item=item),
        matches=matches
    )


# ---------------- ERRORS ----------------

@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):
    return (
        "<h2>Upload too large</h2>"
        "<p>Please choose a photo smaller than 5 MB.</p>"
        '<p><a href="/">Back to CampusFind</a></p>',
        413
    )


init_db()

if __name__ == "__main__":
  app.run(host="0.0.0.0", port=5001, debug=False)
