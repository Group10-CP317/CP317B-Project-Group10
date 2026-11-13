"""My automated tests for the Sprint 2 stories: UI-3, UI-4, UI-5 and UI-7.

Each test checks one acceptance criterion from the product backlog.
"""
import sqlite3
from datetime import date, timedelta

import pytest

from app import create_app

TOKEN = "test-token"
USER = dict(full_name="Test User", email="test@example.com", phone="5195551234",
            licence_no="A1234-56789", password="Passw0rd1", confirm="Passw0rd1", consent="on")


# every test gets a brand new app and an empty temporary database
@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})
    c = app.test_client()
    with c.session_transaction() as s:
        s["_csrf"] = TOKEN  # known CSRF token so my posts are accepted
    return c


def post(c, url, **data):
    data["_csrf"] = TOKEN
    return c.post(url, data=data)


def iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


# helper: register and log in a customer (the login clears the session, so I put the token back)
def sign_in(c, **over):
    post(c, "/register", **{**USER, **over})
    post(c, "/login", email=over.get("email", USER["email"]), password=USER["password"])
    with c.session_transaction() as s:
        s["_csrf"] = TOKEN


def db_of(c):
    return sqlite3.connect(c.application.config["DATABASE"])


# helper: book a car through the real pages, like a customer would
def book(c, car_id, start, end):
    return post(c, "/reserve", car_id=car_id, pickup=start, **{"return": end})


def res_rows(c):
    return db_of(c).execute("SELECT id,user_id,car_id,start_date,end_date,status FROM reservations "
                            "WHERE user_id > 1").fetchall()


# ---------- UI-3 Reserve Car ----------
def test_reserve_pages_require_login(client):
    assert client.get("/reserve/review?car_id=2&pickup=%s&return=%s" % (iso(10), iso(12))).status_code == 302
    assert book(client, 2, iso(10), iso(12)).status_code == 302


def test_review_page_shows_car_dates_and_total(client):
    sign_in(client)
    r = client.get(f"/reserve/review?car_id=2&pickup={iso(10)}&return={iso(12)}")
    assert r.status_code == 200
    assert b"Honda" in r.data and b"3 days" in r.data and b"$228.00" in r.data  # 3 days x $76


def test_reserve_creates_confirmed_booking(client):
    sign_in(client)
    r = book(client, 2, iso(10), iso(12))
    assert r.status_code == 302 and "/reservations/" in r.headers["Location"]
    rows = res_rows(client)
    assert len(rows) == 1 and rows[0][5] == "confirmed"


def test_confirmation_page_shows_booking(client):
    sign_in(client)
    r = client.get(book(client, 2, iso(10), iso(12)).headers["Location"])
    assert b"Reservation #" in r.data and b"CR-V" in r.data


def test_cannot_reserve_car_booked_for_overlapping_dates(client):
    sign_in(client)
    # the seed data books the RAV4 (car 1) from day 3 to day 6
    r = book(client, 1, iso(5), iso(8))
    assert r.status_code == 302 and "/search" in r.headers["Location"]
    assert res_rows(client) == []


def test_second_customer_cannot_double_book_same_car(client):
    sign_in(client)
    book(client, 2, iso(10), iso(12))
    other = create_app({"TESTING": True, "DATABASE": client.application.config["DATABASE"]}).test_client()
    with other.session_transaction() as s:
        s["_csrf"] = TOKEN
    sign_in(other, email="second@example.com")
    r = other.post("/reserve", data={"_csrf": TOKEN, "car_id": 2, "pickup": iso(11), "return": iso(13)})
    assert r.status_code == 302 and "/search" in r.headers["Location"]
    assert len(res_rows(client)) == 1


def test_reserved_car_disappears_from_search(client):
    sign_in(client)
    book(client, 2, iso(10), iso(12))
    r = client.get("/search", query_string={"car_type": "SUV", "pickup": iso(11), "return": iso(11)})
    assert b"CR-V" not in r.data and b"Escape" in r.data


def test_reserve_rejects_bad_dates_and_unknown_car(client):
    sign_in(client)
    book(client, 2, iso(12), iso(10))   # return before pickup
    book(client, 2, iso(-3), iso(2))    # pickup in the past
    book(client, 999, iso(10), iso(12)) # car does not exist
    assert res_rows(client) == []


def test_manager_cannot_reserve(client):
    post(client, "/login", email="manager@carrental.com", password="Manager123!")
    with client.session_transaction() as s:
        s["_csrf"] = TOKEN
    assert book(client, 2, iso(10), iso(12)).status_code == 403


def test_reserve_requires_csrf_token(client):
    sign_in(client)
    r = client.post("/reserve", data={"car_id": 2, "pickup": iso(10), "return": iso(12)})
    assert r.status_code == 400


def test_search_results_have_reserve_button(client):
    sign_in(client)
    r = client.get("/search", query_string={"car_type": "SUV", "pickup": iso(20), "return": iso(22)})
    assert b"Reserve" in r.data and b"/reserve/review" in r.data


# ---------- UI-4 Cancel Reservation ----------
def make_booking(client, start=10, end=12, car=2):
    loc = book(client, car, iso(start), iso(end)).headers["Location"]
    return int(loc.rsplit("/", 1)[1])


def test_cancel_upcoming_booking(client):
    sign_in(client)
    rid = make_booking(client)
    r = post(client, f"/reservations/{rid}/cancel")
    assert r.status_code == 302
    assert db_of(client).execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()[0] == "cancelled"


def test_cancelled_car_is_available_again(client):
    sign_in(client)
    rid = make_booking(client)
    post(client, f"/reservations/{rid}/cancel")
    r = client.get("/search", query_string={"car_type": "SUV", "pickup": iso(10), "return": iso(12)})
    assert b"CR-V" in r.data


def test_cannot_cancel_someone_elses_booking(client):
    sign_in(client)
    rid = make_booking(client)
    other = create_app({"TESTING": True, "DATABASE": client.application.config["DATABASE"]}).test_client()
    with other.session_transaction() as s:
        s["_csrf"] = TOKEN
    sign_in(other, email="second@example.com")
    r = other.post(f"/reservations/{rid}/cancel", data={"_csrf": TOKEN})
    assert r.status_code == 404
    assert db_of(client).execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()[0] == "confirmed"


def test_cannot_cancel_a_booking_that_already_started(client):
    sign_in(client)
    rid = make_booking(client, start=0, end=2)   # picks up today
    post(client, f"/reservations/{rid}/cancel")
    assert db_of(client).execute("SELECT status FROM reservations WHERE id=?", (rid,)).fetchone()[0] == "confirmed"


def test_cancel_requires_login_and_csrf(client):
    assert post(client, "/reservations/1/cancel").status_code == 302
    sign_in(client)
    rid = make_booking(client)
    assert client.post(f"/reservations/{rid}/cancel").status_code == 400


# ---------- UI-5 Rental History ----------
def add_past_booking(client, user_id, car_id=3):
    db = db_of(client)
    db.execute("INSERT INTO reservations (user_id,car_id,start_date,end_date) VALUES (?,?,?,?)",
               (user_id, car_id, iso(-10), iso(-8)))
    db.commit()


def test_history_shows_upcoming_past_and_cancelled(client):
    sign_in(client)
    add_past_booking(client, 2)
    book(client, 2, iso(10), iso(12))
    rid = make_booking(client, 20, 22, car=4)
    post(client, f"/reservations/{rid}/cancel")
    r = client.get("/reservations")
    for word in (b"Upcoming", b"Completed", b"Cancelled", b"Current and upcoming", b"Past and cancelled"):
        assert word in r.data


def test_history_only_shows_own_bookings(client):
    sign_in(client)
    book(client, 2, iso(10), iso(12))
    other = create_app({"TESTING": True, "DATABASE": client.application.config["DATABASE"]}).test_client()
    with other.session_transaction() as s:
        s["_csrf"] = TOKEN
    sign_in(other, email="second@example.com")
    assert b"CR-V" not in other.get("/reservations").data
    assert b"No bookings yet" in other.get("/reservations").data


def test_history_requires_login(client):
    assert client.get("/reservations").status_code == 302


def test_history_cancel_button_only_for_upcoming(client):
    sign_in(client)
    add_past_booking(client, 2)
    r = client.get("/reservations")
    assert b"Cancel booking" not in r.data
    book(client, 2, iso(10), iso(12))
    assert b"Cancel booking" in client.get("/reservations").data


# ---------- UI-7 Personal Data Control ----------
def test_account_page_shows_stored_details(client):
    sign_in(client)
    r = client.get("/account")
    for text in (b"Test User", b"test@example.com", b"5195551234", b"A1234-56789"):
        assert text in r.data


def test_account_pages_require_login_and_block_managers(client):
    assert client.get("/account").status_code == 302
    post(client, "/login", email="manager@carrental.com", password="Manager123!")
    assert client.get("/account").status_code == 403


def test_delete_needs_correct_password_and_tick_box(client):
    sign_in(client)
    post(client, "/account/delete", password="wrong", understand="on")
    post(client, "/account/delete", password="Passw0rd1")   # box not ticked
    row = db_of(client).execute("SELECT full_name,email FROM users WHERE id=2").fetchone()
    assert row == ("Test User", "test@example.com")


def test_delete_wipes_personal_details(client):
    sign_in(client)
    r = post(client, "/account/delete", password="Passw0rd1", understand="on")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    name, email, phone, lic = db_of(client).execute(
        "SELECT full_name,email,phone,licence_no FROM users WHERE id=2").fetchone()
    assert name == "Deleted customer" and "test@example.com" not in email and phone == "" and lic == ""


def test_deleted_customer_cannot_log_in_and_session_is_over(client):
    sign_in(client)
    post(client, "/account/delete", password="Passw0rd1", understand="on")
    assert client.get("/account").status_code == 302   # the session was ended
    with client.session_transaction() as s:
        s["_csrf"] = TOKEN   # deleting clears the session, so I put the form token back
    r = post(client, "/login", email="test@example.com", password="Passw0rd1")
    assert b"Incorrect email or password" in r.data


def test_delete_cancels_upcoming_but_keeps_past_records(client):
    sign_in(client)
    add_past_booking(client, 2)
    rid = make_booking(client)
    post(client, "/account/delete", password="Passw0rd1", understand="on")
    rows = dict(db_of(client).execute("SELECT id,status FROM reservations WHERE user_id=2").fetchall())
    assert rows[rid] == "cancelled" and "confirmed" in rows.values()  # the past booking stays


def test_email_can_be_used_again_after_deletion(client):
    sign_in(client)
    post(client, "/account/delete", password="Passw0rd1", understand="on")
    with client.session_transaction() as s:
        s["_csrf"] = TOKEN
    r = post(client, "/register", **USER)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
