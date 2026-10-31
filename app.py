from flask import (
    Flask, request, redirect, url_for,
    render_template_string, flash
)
from flask_login import (
    LoginManager, UserMixin, login_user,
    logout_user, login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import RequestEntityTooLarge
from difflib import SequenceMatcher

import sqlite3
import os
import re
import secrets


# ---------------- APP CONFIGURATION ----------------

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get(
    "SECRET_KEY",
    "campusfind-local-secret-change-this-before-deployment"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(BASE_DIR, "campusfind.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

MAX_FILE_SIZE = 5 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


# ---------------- LOGIN CONFIGURATION ----------------

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please login to continue."
login_manager.init_app(app)


# ---------------- DATABASE ----------------

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


class User(UserMixin):
    def __init__(self, user_id, name, email):
        self.id = user_id
        self.name = name
        self.email = email


@login_manager.user_loader
def load_user(user_id):
    conn = get_db()

    user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    conn.close()

    if user:
        return User(user["id"], user["name"], user["email"])

    return None


def init_db():
    conn = get_db()

    # Users table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL
        )
    """)

    # Items table
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
        conn.execute(
            "ALTER TABLE items ADD COLUMN contact_number TEXT"
        )

    if "photo_filename" not in columns:
        conn.execute(
            "ALTER TABLE items ADD COLUMN photo_filename TEXT"
        )

    if "user_id" not in columns:
        conn.execute(
            "ALTER TABLE items ADD COLUMN user_id INTEGER"
        )

    conn.commit()
    conn.close()


# ---------------- COMMON FUNCTIONS ----------------

def clean_status(status):
    status = (status or "").strip().lower()

    if status in ("lost", "found"):
        return status

    return ""


# ---------------- PHOTO UPLOAD ----------------

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

        name_score = SequenceMatcher(
            None, name_a, name_b
        ).ratio()

        if name_score > 0.25:
            score += name_score * 50
            reasons.append("Similar item name")

        cat_a = (item["category"] or "").strip().lower()
        cat_b = (other["category"] or "").strip().lower()

        if cat_a and cat_a == cat_b:
            score += 25
            reasons.append("Same category")

        loc_a = set(
            re.findall(r"\w+", (item["location"] or "").lower())
        )
        loc_b = set(
            re.findall(r"\w+", (other["location"] or "").lower())
        )

        if loc_a and loc_b:
            overlap = len(loc_a & loc_b) / len(loc_a | loc_b)
            score += overlap * 15

            if overlap:
                reasons.append("Similar location")

        desc_a = set(
            re.findall(r"\w+", (item["description"] or "").lower())
        )
        desc_b = set(
            re.findall(r"\w+", (other["description"] or "").lower())
        )

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

    results.sort(
        key=lambda result: result["score"],
        reverse=True
    )

    return results[:5]


# ---------------- SHARED PAGE DESIGN ----------------

BASE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#2563eb">

<title>{{ title }} | CampusFind</title>

<link rel="stylesheet"
href="{{ url_for('static', filename='style.css') }}">

<style>
:root {
    --blue: #2563eb;
    --blue-dark: #1d4ed8;
    --ink: #172033;
    --muted: #64748b;
    --line: #e5eaf2;
    --page: #f5f8ff;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: var(--page);
    color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont,
    "Segoe UI", Roboto, Arial, sans-serif;
    line-height: 1.6;
    padding-bottom: 20px;
}

a {
    color: var(--blue);
    text-decoration: none;
}

button, input, select, textarea {
    font: inherit;
}

.container {
    width: min(1120px, calc(100% - 40px));
    margin: 0 auto;
}

.navbar {
    position: sticky;
    top: 0;
    z-index: 20;
    background: rgba(255,255,255,.96);
    border-bottom: 1px solid var(--line);
}

.navbar .container {
    min-height: 72px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
}

.brand {
    color: var(--ink);
    font-size: 25px;
    font-weight: 850;
}

.brand span {
    color: var(--blue);
}

.nav-links {
    display: flex;
    align-items: center;
    gap: 15px;
    flex-wrap: wrap;
}

.nav-links a {
    font-size: 14px;
    font-weight: 700;
    color: var(--ink);
}

.btn, button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 44px;
    padding: 11px 17px;
    border: 1px solid transparent;
    border-radius: 12px;
    background: var(--blue);
    color: white;
    font-weight: 750;
    cursor: pointer;
}

.btn:hover, button:hover {
    background: var(--blue-dark);
    color: white;
}

.btn-outline {
    background: white;
    color: var(--blue);
    border-color: #cbdafc;
}

main.container {
    min-height: 65vh;
}

.hero {
    margin: 30px 0;
    padding: clamp(26px, 5vw, 54px);
    border-radius: 28px;
    background: linear-gradient(
        135deg, #eaf2ff, #f7f9ff, #dce9ff
    );
    border: 1px solid #dce7ff;
}

.hero h1 {
    font-size: clamp(34px, 6vw, 58px);
    line-height: 1.08;
}

.hero p {
    color: #586780;
}

.hero-actions, .search-form {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-top: 22px;
}

.search-form input {
    flex: 1;
}

input, textarea, select {
    width: 100%;
    min-width: 0;
    padding: 13px 14px;
    border: 1px solid #dce3ef;
    border-radius: 12px;
    background: white;
}

.items-grid {
    display: grid;
    grid-template-columns: repeat(
        auto-fit, minmax(min(100%, 250px), 1fr)
    );
    gap: 20px;
    margin: 20px 0 32px;
}

.item-card {
    min-width: 0;
    overflow: hidden;
    padding: 18px;
    border: 1px solid var(--line);
    border-radius: 20px;
    background: white;
    overflow-wrap: anywhere;
}

.item-card h3 {
    font-size: 20px;
}

.item-card p {
    color: var(--muted);
    font-size: 14px;
}

.item-card img {
    display: block;
    width: 100%;
    height: 210px;
    object-fit: cover;
    border-radius: 13px;
}

.status-badge {
    display: inline-block;
    padding: 6px 10px;
    border-radius: 999px;
    background: #fff0e9;
    color: #b94b17;
    font-size: 11px;
    font-weight: 850;
}

.status-badge.found {
    background: #dcfce7;
    color: #166534;
}

.filters {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin: 18px 0;
}

.filter-link {
    padding: 10px 16px;
    border: 1px solid var(--line);
    border-radius: 999px;
    background: white;
    color: #475569;
    font-size: 14px;
    font-weight: 700;
}

.filter-link.active {
    background: var(--blue);
    color: white;
}

.form-card {
    max-width: 680px;
    margin: 34px auto;
    padding: clamp(20px, 5vw, 34px);
    border: 1px solid var(--line);
    border-radius: 24px;
    background: white;
}

.form-group {
    margin: 20px 0;
}

.form-group label {
    display: block;
    margin-bottom: 8px;
    font-weight: 750;
}

.form-group small {
    display: block;
    margin-top: 7px;
    color: var(--muted);
    font-size: 12px;
}

.empty-state {
    margin: 22px 0;
    padding: 25px;
    border: 1px dashed #cbd5e1;
    border-radius: 18px;
    background: white;
    text-align: center;
}

.flash {
    margin: 15px 0;
    padding: 12px 16px;
    border-radius: 12px;
    background: #eff6ff;
    color: #1d4ed8;
}

footer {
    margin-top: 45px;
    padding: 28px 0;
    border-top: 1px solid var(--line);
    color: var(--muted);
    text-align: center;
    font-size: 13px;
}

@media (max-width: 650px) {
    body {
        padding-bottom: 20px;
    }

    .container {
        width: calc(100% - 28px);
    }

    .navbar .container {
        padding: 12px 0;
        align-items: flex-start;
        flex-direction: column;
    }

    .nav-links {
        gap: 10px;
    }

    .nav-links a {
        font-size: 12px;
    }

    .hero {
        margin: 20px 0;
        padding: 24px 18px;
    }

    .hero h1 {
        font-size: 36px;
    }

    .search-form {
        flex-direction: column;
    }

    .items-grid {
        grid-template-columns: 1fr;
    }

    .form-card {
        margin: 22px auto;
        padding: 21px 17px;
    }
}
</style>
</head>

<body>

<nav class="navbar">
  <div class="container">

    <a class="brand" href="{{ url_for('home') }}">
        Campus<span>Find</span>
    </a>

    <div class="nav-links">

      <a href="{{ url_for('home') }}">Home</a>

      {% if current_user.is_authenticated %}

        <a href="{{ url_for('my_reports') }}">My Reports</a>

        <a href="{{ url_for('report', status='lost') }}">
            Report Lost
        </a>

        <a href="{{ url_for('report', status='found') }}">
            Report Found
        </a>

        <a href="{{ url_for('logout') }}">Logout</a>

      {% else %}

        <a href="{{ url_for('login') }}">Login</a>

        <a class="btn" href="{{ url_for('signup') }}">
            Sign Up
        </a>

      {% endif %}

    </div>
  </div>
</nav>

<main class="container">

  {% with messages = get_flashed_messages() %}
    {% if messages %}
      {% for message in messages %}
        <div class="flash">{{ message }}</div>
      {% endfor %}
    {% endif %}
  {% endwith %}

  {{ content|safe }}

</main>

<footer>
  <div class="container">
    CampusFind · Helping lost items find their way home.
  </div>
</footer>

</body>
</html>
"""


def page(title, template, **context):
    content = render_template_string(template, **context)

    return render_template_string(
        BASE_HTML,
        title=title,
        content=content
    )


# ---------------- SIGNUP ----------------

SIGNUP_HTML = """
<section class="form-card">

<h1>Create Account</h1>
<p>Join CampusFind and help reconnect lost belongings.</p>

{% if error %}
<div class="empty-state">{{ error }}</div>
{% endif %}

<form method="POST">

    <div class="form-group">
        <label>Full Name</label>
        <input
            type="text"
            name="name"
            required
            maxlength="80"
            placeholder="Enter your name"
            value="{{ name }}"
        >
    </div>

    <div class="form-group">
        <label>Email Address</label>
        <input
            type="email"
            name="email"
            required
            maxlength="150"
            placeholder="Enter your email"
            value="{{ email }}"
        >
    </div>

    <div class="form-group">
        <label>Password</label>
        <input
            type="password"
            name="password"
            required
            minlength="8"
            placeholder="Minimum 8 characters"
        >
    </div>

    <div class="form-group">
        <label>Confirm Password</label>
        <input
            type="password"
            name="confirm_password"
            required
            minlength="8"
            placeholder="Enter password again"
        >
    </div>

    <button type="submit">Create Account</button>

</form>

<p>Already have an account?
<a href="{{ url_for('login') }}">Login here</a>
</p>

</section>
"""


@app.route("/signup", methods=["GET", "POST"])
def signup():

    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":

        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not name or not email or not password:
            return page(
                "Sign Up",
                SIGNUP_HTML,
                error="Please fill in all fields.",
                name=name,
                email=email
            )

        if len(name) > 80:
            return page(
                "Sign Up",
                SIGNUP_HTML,
                error="Name is too long.",
                name=name,
                email=email
            )

        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return page(
                "Sign Up",
                SIGNUP_HTML,
                error="Enter a valid email address.",
                name=name,
                email=email
            )

        if len(password) < 8:
            return page(
                "Sign Up",
                SIGNUP_HTML,
                error="Password must contain at least 8 characters.",
                name=name,
                email=email
            )

        if password != confirm_password:
            return page(
                "Sign Up",
                SIGNUP_HTML,
                error="Passwords do not match.",
                name=name,
                email=email
            )

        hashed_password = generate_password_hash(password)

        conn = get_db()

        try:
            cursor = conn.execute(
                """
                INSERT INTO users (name, email, password)
                VALUES (?, ?, ?)
                """,
                (name, email, hashed_password)
            )

            conn.commit()

            user = User(cursor.lastrowid, name, email)
            login_user(user)

        except sqlite3.IntegrityError:
            conn.close()

            return page(
                "Sign Up",
                SIGNUP_HTML,
                error="This email is already registered. Please login.",
                name=name,
                email=email
            )

        conn.close()

        flash("Account created successfully!")
        return redirect(url_for("home"))

    return page(
        "Sign Up",
        SIGNUP_HTML,
        error="",
        name="",
        email=""
    )


# ---------------- LOGIN ----------------

LOGIN_HTML = """
<section class="form-card">

<h1>Welcome Back</h1>
<p>Login to your CampusFind account.</p>

{% if error %}
<div class="empty-state">{{ error }}</div>
{% endif %}

<form method="POST">

    <div class="form-group">
        <label>Email Address</label>
        <input
            type="email"
            name="email"
            required
            placeholder="Enter your email"
            value="{{ email }}"
        >
    </div>

    <div class="form-group">
        <label>Password</label>
        <input
            type="password"
            name="password"
            required
            placeholder="Enter your password"
        >
    </div>

    <button type="submit">Login</button>

</form>

<p>Don't have an account?
<a href="{{ url_for('signup') }}">Create account</a>
</p>

</section>
"""


@app.route("/login", methods=["GET", "POST"])
def login():

    if current_user.is_authenticated:
        return redirect(url_for("home"))

    if request.method == "POST":

        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()

        user_data = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        if user_data and check_password_hash(
            user_data["password"], password
        ):

            user = User(
                user_data["id"],
                user_data["name"],
                user_data["email"]
            )

            login_user(user)

            flash("Login successful!")
            return redirect(url_for("home"))

        return page(
            "Login",
            LOGIN_HTML,
            error="Incorrect email or password.",
            email=email
        )

    return page(
        "Login",
        LOGIN_HTML,
        error="",
        email=""
    )


# ---------------- LOGOUT ----------------

@app.route("/logout")
@login_required
def logout():

    logout_user()

    flash("You have been logged out.")

    return redirect(url_for("login"))


# ---------------- ITEM CARD ----------------

CARD = """
<article class="item-card">

  {% if item['photo_filename'] %}

    <img
      src="{{ url_for('static', filename='uploads/' + item['photo_filename']) }}"
      alt="Photo of {{ item['name'] }}"
      loading="lazy"
    >

  {% else %}

    <div style="
        height:130px;
        background:#eff6ff;
        border-radius:13px;
        display:grid;
        place-items:center;
        font-size:45px
    ">📦</div>

  {% endif %}

  <p>
    <span class="status-badge {{ 'found' if item['status']|lower|trim == 'found' else '' }}">
      {{ item['status']|upper }}
    </span>
  </p>

  <h3>{{ item['name'] }}</h3>

  <p>📦 <strong>Category:</strong>
  {{ item['category'] or 'Other' }}</p>

  <p>📍 <strong>Location:</strong>
  {{ item['location'] or 'Not specified' }}</p>

  <p>📝 {{ item['description'] or 'No description provided.' }}</p>

  <p>📅 <strong>Date:</strong>
  {{ item['date'] or 'Not specified' }}</p>

  <div style="background:#eff6ff;padding:12px;border-radius:12px">

    <strong>📞 Contact Number</strong><br>

    {% if item['contact_number'] %}

      <a href="tel:{{ item['contact_number'] }}">
        {{ item['contact_number'] }}
      </a>

    {% else %}

      <span>Not provided for this report.</span>

    {% endif %}

  </div>

  <p>
    <a class="btn btn-outline"
       href="{{ url_for('matches_page', item_id=item['id']) }}">
       ✨ Find Possible Matches
    </a>
  </p>

</article>
"""


# ---------------- HOME ----------------

HOME_HTML = """
<section class="hero">

  <p>YOUR CAMPUS. YOUR COMMUNITY.</p>

  <h1>Lost something?<br>Let's find it together.</h1>

  <p>
    Report lost belongings, share found items
    and help reconnect people with what matters.
  </p>

  <form action="/" method="get" class="search-form">

    <input
      name="search"
      value="{{ search }}"
      placeholder="Search item, category or location..."
    >

    {% if status %}
      <input type="hidden" name="status" value="{{ status }}">
    {% endif %}

    <button type="submit">Search Reports →</button>

  </form>

  <div class="hero-actions">

    <a class="btn" href="{{ url_for('report', status='lost') }}">
      🔍 I Lost Something
    </a>

    <a class="btn btn-outline"
       href="{{ url_for('report', status='found') }}">
       📸 I Found Something
    </a>

  </div>

</section>

<h2>Explore Reports</h2>

<div class="filters">

  <a class="filter-link {{ 'active' if not status else '' }}"
     href="{{ url_for('home', search=search) }}">
     All Items
  </a>

  <a class="filter-link {{ 'active' if status == 'lost' else '' }}"
     href="{{ url_for('home', status='lost', search=search) }}">
     Lost Items
  </a>

  <a class="filter-link {{ 'active' if status == 'found' else '' }}"
     href="{{ url_for('home', status='found', search=search) }}">
     Found Items
  </a>

</div>

<div class="items-grid">

  <div class="item-card">
    <p>Total Reports</p>
    <h2>{{ total }}</h2>
  </div>

  <div class="item-card">
    <p>Lost Items</p>
    <h2>{{ lost_count }}</h2>
  </div>

  <div class="item-card">
    <p>Found Items</p>
    <h2>{{ found_count }}</h2>
  </div>

</div>

<h2>Latest Reports</h2>

<div class="items-grid">
  {{ cards|safe }}
</div>

{% if not items %}

<div class="empty-state">
  <h3>No reports found</h3>
  <p>Try another search or submit a report.</p>
</div>

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
        sql += """
        AND (
            name LIKE ?
            OR category LIKE ?
            OR location LIKE ?
            OR description LIKE ?
        )
        """

        term = "%" + search + "%"

        params.extend([term, term, term, term])

    sql += " ORDER BY id DESC"

    conn = get_db()

    items = conn.execute(sql, params).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) FROM items"
    ).fetchone()[0]

    lost_count = conn.execute(
        "SELECT COUNT(*) FROM items WHERE LOWER(TRIM(status))='lost'"
    ).fetchone()[0]

    found_count = conn.execute(
        "SELECT COUNT(*) FROM items WHERE LOWER(TRIM(status))='found'"
    ).fetchone()[0]

    conn.close()

    cards = "".join(
        render_template_string(CARD, item=item)
        for item in items
    )

    return page(
        "Home",
        HOME_HTML,
        items=items,
        cards=cards,
        search=search,
        status=status,
        total=total,
        lost_count=lost_count,
        found_count=found_count
    )


# ---------------- MY REPORTS ----------------

@app.route("/my-reports")
@login_required
def my_reports():

    conn = get_db()

    items = conn.execute(
        """
        SELECT * FROM items
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (current_user.id,)
    ).fetchall()

    conn.close()

    cards = "".join(
        render_template_string(CARD, item=item)
        for item in items
    )

    template = """
    <h1>My Reports</h1>

    <p>
      Welcome, {{ current_user.name }}.
      Here you can see the reports submitted by your account.
    </p>

    <div class="items-grid">
      {{ cards|safe }}
    </div>

    {% if not items %}

    <div class="empty-state">
      <h3>You have not submitted any reports yet.</h3>
      <p>Submit your first lost or found item report.</p>
    </div>

    {% endif %}
    """

    return page(
        "My Reports",
        template,
        items=items,
        cards=cards
    )


# ---------------- REPORT FORM ----------------

CATEGORIES = [
    "Electronics",
    "Books & Stationery",
    "Bags",
    "Clothing",
    "Accessories",
    "Water Bottles",
    "Keys",
    "Other"
]


FORM_HTML = """
<section class="form-card">

  <h1>
    {{ 'Report a Lost Item' if status == 'lost'
       else 'Report a Found Item' }}
  </h1>

  <p>Fill in the details of your item.</p>

  {% if error %}
    <div class="empty-state">{{ error }}</div>
  {% endif %}

  <form method="post" enctype="multipart/form-data">

    <div class="form-group">
      <label for="name">Item Name *</label>

      <input
        id="name"
        name="name"
        required
        maxlength="120"
        placeholder="e.g. Blue water bottle"
        value="{{ values.get('name', '') }}"
      >
    </div>

    <div class="form-group">
      <label for="category">Category</label>

      <select id="category" name="category">

        <option value="">Choose category</option>

        {% for category in categories %}

          <option value="{{ category }}"
          {% if values.get('category') == category %}selected{% endif %}>
            {{ category }}
          </option>

        {% endfor %}

      </select>
    </div>

    <div class="form-group">
      <label for="location">Location *</label>

      <input
        id="location"
        name="location"
        required
        maxlength="150"
        placeholder="e.g. Library, Block A"
        value="{{ values.get('location', '') }}"
      >
    </div>

    <div class="form-group">
      <label for="description">Description</label>

      <textarea
        id="description"
        name="description"
        maxlength="1000"
        placeholder="Colour, brand or other details"
      >{{ values.get('description', '') }}</textarea>
    </div>

    <div class="form-group">
      <label for="date">Date Lost or Found</label>

      <input
        id="date"
        name="date"
        type="date"
        value="{{ values.get('date', '') }}"
      >
    </div>

    <div class="form-group">
      <label for="contact_number">Contact Number *</label>

      <input
        id="contact_number"
        name="contact_number"
        type="tel"
        required
        maxlength="20"
        pattern="[0-9+() -]{7,20}"
        placeholder="e.g. 98765 43210"
        value="{{ values.get('contact_number', '') }}"
      >

      <small>
        Your contact number will be visible on the public report.
      </small>
    </div>

    <div class="form-group">
      <label for="photo">📸 Take Photo / Choose Photo</label>

      <input
        id="photo"
        name="photo"
        type="file"
        accept="image/jpeg,image/png,image/webp"
        capture="environment"
      >

      <small>
        JPG, PNG or WEBP · Maximum 5 MB.
      </small>

      <img
        id="photo-preview"
        alt="Photo preview"
        style="
          display:none;
          width:100%;
          max-height:300px;
          object-fit:contain;
          margin-top:12px;
          border-radius:12px;
        "
      >
    </div>

    <button type="submit">Submit Report →</button>

    <a class="btn btn-outline" href="{{ url_for('home') }}">
      Cancel
    </a>

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

    const allowedTypes = [
        "image/jpeg",
        "image/png",
        "image/webp"
    ];

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

    const objectUrl = URL.createObjectURL(file);

    preview.src = objectUrl;
    preview.style.display = "block";

});

</script>
"""


def render_report_form(status, error="", values=None):

    return page(
        "Report Item",
        FORM_HTML,
        status=status,
        error=error,
        values=values or {},
        categories=CATEGORIES
    )


@app.route("/report/<status>", methods=["GET", "POST"])
@login_required
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

    if (
        not values["name"]
        or not values["location"]
        or not values["contact_number"]
    ):
        return render_report_form(
            status,
            "Please fill in item name, location and contact number.",
            values
        )

    if not re.fullmatch(
        r"[0-9+() -]{7,20}",
        values["contact_number"]
    ):
        return render_report_form(
            status,
            "Enter a valid contact number.",
            values
        )

    photo_file = request.files.get("photo")

    photo_filename, photo_error = save_photo(photo_file)

    if photo_error:
        return render_report_form(status, photo_error, values)

    conn = get_db()

    try:

        conn.execute(
            """
            INSERT INTO items
            (
                status,
                name,
                category,
                location,
                description,
                date,
                contact_number,
                photo_filename,
                user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                status,
                values["name"],
                values["category"],
                values["location"],
                values["description"],
                values["date"],
                values["contact_number"],
                photo_filename,
                current_user.id
            )
        )

        conn.commit()

    except Exception:

        conn.rollback()

        if photo_filename:
            saved_path = os.path.join(
                UPLOAD_FOLDER,
                photo_filename
            )

            if os.path.exists(saved_path):
                os.remove(saved_path)

        raise

    finally:
        conn.close()

    flash("Your report has been submitted successfully!")

    return redirect(url_for("my_reports"))


# ---------------- MATCH RESULTS ----------------

MATCHES_HTML = """
<h2>✨ Smart Possible Matches</h2>

<p>
These suggestions compare item names, categories,
locations and descriptions.
</p>

<h3>Your Report</h3>

<div class="items-grid">
  {{ original_card|safe }}
</div>

<h3>Suggested Reports</h3>

{% if matches %}

  {% for match in matches %}

    <div style="margin:20px 0">

      <p>
        <strong>Match score: {{ match.score }}%</strong>
        · {{ match.reasons|join(', ') }}
      </p>

      <div class="items-grid">
        {{ match.card|safe }}
      </div>

    </div>

  {% endfor %}

{% else %}

  <div class="empty-state">
    <h3>No possible matches yet</h3>
    <p>New reports may produce a match later.</p>
  </div>

{% endif %}

<a class="btn btn-outline" href="{{ url_for('home') }}">
  ← Back to Home
</a>
"""


@app.route("/matches/<int:item_id>")
def matches_page(item_id):

    conn = get_db()

    item = conn.execute(
        "SELECT * FROM items WHERE id = ?",
        (item_id,)
    ).fetchone()

    conn.close()

    if item is None:
        return redirect(url_for("home"))

    matches = []

    for result in find_matches(item):

        matches.append({
            "score": result["score"],
            "reasons": result["reasons"],
            "card": render_template_string(
                CARD,
                item=result["item"]
            )
        })

    return page(
        "Smart Possible Matches",
        MATCHES_HTML,
        original_card=render_template_string(CARD, item=item),
        matches=matches
    )


# ---------------- ERROR HANDLING ----------------

@app.errorhandler(RequestEntityTooLarge)
def handle_large_upload(error):

    return (
        "<h2>Upload too large</h2>"
        "<p>Please choose a photo smaller than 5 MB.</p>"
        '<p><a href="/">Back to CampusFind</a></p>',
        413
    )


# ---------------- START APPLICATION ----------------

init_db()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5001,
        debug=False
    )