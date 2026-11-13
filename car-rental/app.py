"""Meridian Car Rental - Sprint 1 prototype (Group 10, CP317B).

Stories I built this sprint: UI-1 Create Account, UI-6 Log In,
ADM-2 Manager Access and UI-2 Search Cars.
"""
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# I keep the settings up here so they are easy to change later
CAR_TYPES = ["SUV", "Sedan", "Compact", "Truck"]
MAX_FAILED_LOGINS = 3   # wrong passwords allowed before the account locks
LOCK_MINUTES = 5        # how long a locked account has to wait
SESSION_MINUTES = 15    # logged out after this much inactivity
# simple email check: something@something.something with no spaces
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# starter fleet so the search page has data to show
SEED_CARS = [
    ("Toyota", "RAV4", "SUV", 5, 79.0), ("Honda", "CR-V", "SUV", 5, 76.0),
    ("Ford", "Escape", "SUV", 5, 74.0), ("Honda", "Civic", "Sedan", 5, 55.0),
    ("Toyota", "Corolla", "Sedan", 5, 52.0), ("Hyundai", "Elantra", "Sedan", 5, 50.0),
    ("Toyota", "Yaris", "Compact", 4, 40.0), ("Kia", "Rio", "Compact", 4, 38.0),
    ("Ford", "F-150", "Truck", 3, 95.0),
]


# I use an app factory so the tests can create a fresh app with a temporary database
def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        # the secret key signs the session cookie; a real deployment would set its own
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        DATABASE=os.path.join(BASE_DIR, "carrental.db"),
        PERMANENT_SESSION_LIFETIME=timedelta(minutes=SESSION_MINUTES),
        SESSION_COOKIE_HTTPONLY=True,   # JavaScript cannot read the cookie
        SESSION_COOKIE_SAMESITE="Lax",  # cookie is not sent on cross-site posts
    )
    if test_config:
        app.config.update(test_config)

    # ---------- database ----------
    def get_db():
        # one connection per request, stored on Flask's g object
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row  # lets me read columns by name
            g.db.execute("PRAGMA foreign_keys = ON")  # SQLite skips foreign keys unless I turn this on
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    def init_db():
        # creates the tables and seeds the demo data the first time the app runs
        db = get_db()
        with open(os.path.join(BASE_DIR, "schema.sql")) as f:
            db.executescript(f.read())
        # only add the demo manager if there is not one already
        if not db.execute("SELECT 1 FROM users WHERE role='manager'").fetchone():
            db.execute(
                "INSERT INTO users (full_name,email,phone,licence_no,password_hash,role)"
                " VALUES (?,?,?,?,?,'manager')",
                ("Demo Manager", "manager@carrental.com", "5195550100", "N/A",
                 generate_password_hash("Manager123!")))
        if not db.execute("SELECT 1 FROM cars").fetchone():
            db.executemany(
                "INSERT INTO cars (make,model,car_type,seats,daily_rate) VALUES (?,?,?,?,?)",
                SEED_CARS)
            # I add two bookings so I can test that search hides cars that are taken
            mgr = db.execute("SELECT id FROM users WHERE role='manager'").fetchone()["id"]
            today = date.today()
            for car_id, a, b in ((1, 3, 6), (4, 2, 4)):
                db.execute(
                    "INSERT INTO reservations (user_id,car_id,start_date,end_date) VALUES (?,?,?,?)",
                    (mgr, car_id, (today + timedelta(days=a)).isoformat(),
                     (today + timedelta(days=b)).isoformat()))
        db.commit()

    app.init_db = init_db
    with app.app_context():
        init_db()

    # ---------- security helpers ----------
    # CSRF protection: every form carries a random token that must match the one in my session
    def csrf_token():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        return session["_csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("_csrf", "")
            # compare_digest avoids leaking information through timing differences
            if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                abort(400, "Invalid form token. Reload the page and try again.")

    # runs before every request: looks up who is logged in (if anyone)
    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("user_id")
        if uid is not None:
            g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            session.permanent = True  # restarts the 15 minute inactivity timer on each request

    # a few safe default headers on every response
    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    # decorator: sends people who are not logged in to the login page
    def login_required(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            return view(*a, **kw)
        return wrapped

    # decorator for ADM-2: the role is checked on the server, so typing the URL does not bypass it
    def manager_required(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if g.user["role"] != "manager":
                abort(403)  # logged in but not a manager
            return view(*a, **kw)
        return wrapped

    # decorator for the customer-only pages (reserve, history, my data); managers do not rent cars
    def customer_required(view):
        @wraps(view)
        @login_required
        def wrapped(*a, **kw):
            if g.user["role"] != "customer":
                return render_template("error.html", code=403, title="Access denied",
                                       msg="This page is for customer accounts."), 403
            return view(*a, **kw)
        return wrapped

    # ---------- Sprint 2 helpers ----------
    # checks the pickup and return dates the same way for the search and the reservation
    def parse_dates(pickup, ret):
        errors, d1, d2 = {}, None, None
        try:
            d1 = date.fromisoformat(pickup)
            if d1 < date.today():
                errors["pickup"] = "Pickup date cannot be in the past."
        except ValueError:
            errors["pickup"] = "Choose a pickup date."
        try:
            d2 = date.fromisoformat(ret)
        except ValueError:
            errors["return"] = "Choose a return date."
        if d1 and d2 and d2 < d1:
            errors["return"] = "Return date must be on or after the pickup date."
        return d1, d2, errors

    # the same overlap rule the search uses: a car is taken if a confirmed booking overlaps the dates
    def car_is_free(db, car_id, d1, d2):
        taken = db.execute(
            """SELECT 1 FROM reservations WHERE car_id = ? AND status = 'confirmed'
               AND start_date <= ? AND end_date >= ?""",
            (car_id, d2.isoformat(), d1.isoformat())).fetchone()
        return taken is None

    # the return date counts as a rental day, so 3rd to 5th is 3 days
    def rental_days(d1, d2):
        return (d2 - d1).days + 1

    # turns a reservation row into a dict with the extra things the pages need
    def describe(row):
        b = dict(row)
        today = date.today().isoformat()
        if b["status"] == "cancelled":
            b["label"] = "Cancelled"
        elif b["end_date"] < today:
            b["label"] = "Completed"
        elif b["start_date"] > today:
            b["label"] = "Upcoming"
        else:
            b["label"] = "Current"
        b["days"] = rental_days(date.fromisoformat(b["start_date"]), date.fromisoformat(b["end_date"]))
        b["total"] = b["days"] * b["daily_rate"]
        b["can_cancel"] = b["label"] == "Upcoming"  # only bookings that have not started
        return b

    RES_SQL = """SELECT r.*, c.make, c.model, c.car_type, c.daily_rate
                 FROM reservations r JOIN cars c ON c.id = r.car_id"""

    # a customer can only open their own reservations (someone else's gives a 404)
    def my_reservation(db, res_id):
        row = db.execute(RES_SQL + " WHERE r.id = ? AND r.user_id = ?",
                         (res_id, g.user["id"])).fetchone()
        if row is None:
            abort(404)
        return describe(row)

    # managers and customers land on different pages after login
    def home_for(user):
        return url_for("manager_home") if user["role"] == "manager" else url_for("search")

    # ---------- routes ----------
    @app.route("/")
    def index():
        return redirect(home_for(g.user)) if g.user else redirect(url_for("login"))

    # UI-1 Create Account
    @app.route("/register", methods=["GET", "POST"])
    def register():
        form, errors = {}, {}
        if request.method == "POST":
            form = {k: request.form.get(k, "").strip() for k in
                    ("full_name", "email", "phone", "licence_no")}
            password = request.form.get("password", "")
            confirm = request.form.get("confirm", "")
            form["email"] = form["email"].lower()  # so Test@x.com and test@x.com are the same account
            if len(form["full_name"]) < 2:
                errors["full_name"] = "Enter your full name."
            if not EMAIL_RE.match(form["email"]):
                errors["email"] = "Enter a valid email address, like name@example.com."
            digits = re.sub(r"\D", "", form["phone"])  # strip dashes/spaces and keep only the digits
            if len(digits) < 10 or len(digits) > 15:
                errors["phone"] = "Enter a phone number with at least 10 digits."
            if not re.fullmatch(r"[A-Za-z0-9\- ]{5,20}", form["licence_no"]):
                errors["licence_no"] = "Enter your licence number (5 to 20 letters or numbers)."
            # password rule: 8+ characters with at least one letter and one number
            if len(password) < 8 or not re.search(r"\d", password) or not re.search(r"[A-Za-z]", password):
                errors["password"] = "Use at least 8 characters, with letters and numbers."
            if password != confirm:
                errors["confirm"] = "The passwords do not match."
            if not request.form.get("consent"):
                errors["consent"] = "You must agree to how we use your data to create an account."
            db = get_db()
            # only check for a duplicate if the email itself looked valid
            if "email" not in errors and db.execute(
                    "SELECT 1 FROM users WHERE email=?", (form["email"],)).fetchone():
                errors["email"] = "An account with this email already exists."
            if not errors:
                # "?" placeholders keep user input out of the SQL string (prevents SQL injection)
                # and I store a hash of the password, never the password itself
                db.execute(
                    "INSERT INTO users (full_name,email,phone,licence_no,password_hash)"
                    " VALUES (?,?,?,?,?)",
                    (form["full_name"], form["email"], digits, form["licence_no"],
                     generate_password_hash(password)))
                db.commit()
                flash("Account created. You can log in now.", "success")
                return redirect(url_for("login"))
        return render_template("register.html", form=form, errors=errors)

    # UI-6 Log In / Log Out
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(home_for(g.user))
        email = ""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            now = datetime.now()
            # step 1: if the account is locked, refuse even a correct password
            if user and user["locked_until"] and datetime.fromisoformat(user["locked_until"]) > now:
                mins = int((datetime.fromisoformat(user["locked_until"]) - now).total_seconds() // 60) + 1
                flash(f"Too many failed attempts. Try again in {mins} minute(s).", "error")
            # step 2: correct password, so reset the failure counter and start the session
            elif user and check_password_hash(user["password_hash"], password):
                db.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?",
                           (user["id"],))
                db.commit()
                session.clear()  # drop any old session data first (prevents session fixation)
                session["user_id"] = user["id"]
                session.permanent = True
                return redirect(home_for(user))
            # step 3: wrong password, so count the failure and lock after the limit
            else:
                if user:
                    attempts = user["failed_attempts"] + 1
                    locked = None
                    if attempts >= MAX_FAILED_LOGINS:
                        locked = (now + timedelta(minutes=LOCK_MINUTES)).isoformat()
                        attempts = 0  # counter restarts once the lock is set
                    db.execute("UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
                               (attempts, locked, user["id"]))
                    db.commit()
                # same message whether or not the email exists, so nobody can guess which emails are registered
                flash("Incorrect email or password.", "error")
        return render_template("login.html", email=email)

    # logout is a POST so another site cannot log someone out with a plain link
    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        flash("You have been logged out.", "success")
        return redirect(url_for("login"))

    # ADM-2 Manager Access
    @app.route("/manager")
    @manager_required
    def manager_home():
        db = get_db()
        # simple counts for the manager home page
        stats = {
            "customers": db.execute("SELECT COUNT(*) FROM users WHERE role='customer'").fetchone()[0],
            "cars": db.execute("SELECT COUNT(*) FROM cars").fetchone()[0],
            "reservations": db.execute(
                "SELECT COUNT(*) FROM reservations WHERE status='confirmed'").fetchone()[0],
        }
        return render_template("manager_home.html", stats=stats)

    # UI-2 Search Cars
    @app.route("/search")
    @login_required
    def search():
        if g.user["role"] == "manager":
            return redirect(url_for("manager_home"))  # managers do not rent cars
        car_type = request.args.get("car_type", "")
        pickup = request.args.get("pickup", "")
        ret = request.args.get("return", "")
        errors, cars, searched = {}, [], False
        # only validate and search once the form has actually been submitted
        if car_type or pickup or ret:
            searched = True
            if car_type not in CAR_TYPES:
                errors["car_type"] = "Choose a car type."
            try:
                d1 = date.fromisoformat(pickup)
                if d1 < date.today():
                    errors["pickup"] = "Pickup date cannot be in the past."
            except ValueError:
                errors["pickup"] = "Choose a pickup date."
                d1 = None
            try:
                d2 = date.fromisoformat(ret)
            except ValueError:
                errors["return"] = "Choose a return date."
                d2 = None
            if d1 and d2 and d2 < d1:
                errors["return"] = "Return date must be on or after the pickup date."
            # Availability query: a car is free if it has NO confirmed booking that overlaps the dates.
            # Two ranges overlap when start <= other end AND end >= other start.
            if not errors:
                cars = get_db().execute(
                    """SELECT * FROM cars c WHERE c.car_type = ? AND NOT EXISTS (
                           SELECT 1 FROM reservations r
                           WHERE r.car_id = c.id AND r.status = 'confirmed'
                             AND r.start_date <= ? AND r.end_date >= ?)
                       ORDER BY c.daily_rate, c.id""",
                    (car_type, d2.isoformat(), d1.isoformat())).fetchall()
        return render_template("search.html", car_types=CAR_TYPES, cars=cars, errors=errors,
                               searched=searched, car_type=car_type, pickup=pickup, ret=ret,
                               today=date.today().isoformat())

    # UI-3 Reserve Car, step 1: the customer reviews the booking before confirming it
    @app.route("/reserve/review")
    @customer_required
    def reserve_review():
        # TODO (UI-3.2): show the Confirm Reservation page: car, dates, number of days and total price.
        # Go back to search with an error if the car or dates are invalid or the car is already booked.
        return redirect(url_for("search"))

    # UI-3 Reserve Car, step 2: save the booking
    @app.route("/reserve", methods=["POST"])
    @customer_required
    def reserve():
        # TODO (UI-3.3): save the booking. Check the dates, lock the database, check the car is still free,
        # insert a confirmed reservation, then go to the confirmation page. Turn the customer away if it was just taken.
        return redirect(url_for("search"))

    # confirmation page shown after a booking (also used to look at a booking later)
    @app.route("/reservations/<int:res_id>")
    @customer_required
    def reservation_detail(res_id):
        # TODO (UI-3.4): show the confirmation page for one of this customer's own bookings.
        return redirect(url_for("reservations"))

    # UI-5 Rental History: current and upcoming bookings first, then past and cancelled ones
    @app.route("/reservations")
    @customer_required
    def reservations():
        # TODO (UI-5.1/5.2): load this customer's bookings, describe() each one, and split them into
        # "active" (current and upcoming, earliest first) and "past" (completed and cancelled, newest first).
        return render_template("reservations.html", active=[], past=[])

    # UI-4 Cancel Reservation: only the owner can cancel, and only before the pickup date
    @app.route("/reservations/<int:res_id>/cancel", methods=["POST"])
    @customer_required
    def cancel_reservation(res_id):
        db = get_db()
        b = my_reservation(db, res_id)
        if not b["can_cancel"]:
            flash("Only upcoming bookings can be cancelled.", "error")
        else:
            # search only blocks 'confirmed' bookings, so this frees the car straight away
            db.execute("UPDATE reservations SET status = 'cancelled' WHERE id = ?", (res_id,))
            db.commit()
            flash("Booking cancelled. The car is available again for those dates.", "success")
        return redirect(url_for("reservations"))

    # UI-7 Personal Data Control: view what is stored
    @app.route("/account")
    @customer_required
    def account():
        return render_template("account.html")

    # UI-7: delete personal data. I keep the user row because old reservations point to it,
    # but every personal detail is wiped, so the kept rental records no longer identify anyone
    @app.route("/account/delete", methods=["POST"])
    @customer_required
    def delete_account():
        # TODO (UI-7.2/7.3): require the tick box and the correct password, then cancel upcoming bookings,
        # wipe the personal details on the user row (keep the row for old bookings), clear the session
        # and send the customer to the login page with a message.
        return redirect(url_for("account"))

    # friendly pages for errors instead of the default plain ones
    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, title="Access denied",
                               msg="This page is for rental managers only."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, title="Page not found",
                               msg="We could not find that page."), 404

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("error.html", code=400, title="Something went wrong",
                               msg=getattr(e, "description", "Bad request.")), 400

    return app


if __name__ == "__main__":
    create_app().run(debug=True)
