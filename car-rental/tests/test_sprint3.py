"""Sprint 3 tests: fleet management (ADM-1) and the three manager reports (REP-1, REP-2, REP-3)."""
import sqlite3
from datetime import date, timedelta

import pytest

from app import create_app

TOKEN = "test-token"
USER = dict(full_name="Test User", email="test@example.com", phone="5195551234",
            licence_no="A1234-56789", password="Passw0rd1", confirm="Passw0rd1", consent="on")


@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db")})
    c = app.test_client()
    with c.session_transaction() as s:
        s["_csrf"] = TOKEN
    return c


def post(c, url, **data):
    data["_csrf"] = TOKEN
    return c.post(url, data=data)


def iso(days):
    return (date.today() + timedelta(days=days)).isoformat()


def db_of(c):
    return sqlite3.connect(c.application.config["DATABASE"])


def keep_token(c):
    with c.session_transaction() as s:
        s["_csrf"] = TOKEN


def sign_in(c, **over):
    post(c, "/register", **{**USER, **over})
    post(c, "/login", email=over.get("email", USER["email"]), password=USER["password"])
    keep_token(c)


def manager(c):
    post(c, "/login", email="manager@carrental.com", password="Manager123!")
    keep_token(c)


def book(c, car_id, start, end):
    return post(c, "/reserve", car_id=car_id, pickup=start, **{"return": end})


NEW_CAR = dict(make="Mazda", model="CX-5", car_type="SUV", seats="5", daily_rate="70.00")


# ---------- ADM-1 Manage Fleet ----------
def test_fleet_pages_blocked_for_customers_and_anonymous(client):
    assert client.get("/manager/cars").status_code == 302
    sign_in(client)
    for url in ("/manager/cars", "/manager/cars/new", "/manager/daily", "/manager/usage",
                "/manager/preferences"):
        assert client.get(url).status_code == 403


def test_fleet_lists_seeded_cars(client):
    manager(client)
    r = client.get("/manager/cars")
    assert r.status_code == 200 and b"Honda" in r.data and b"Add a car" in r.data


def test_add_car_appears_in_fleet_and_search(client):
    manager(client)
    r = post(client, "/manager/cars/new", **NEW_CAR)
    assert r.status_code == 302
    assert b"CX-5" in client.get("/manager/cars").data
    client.get("/logout")
    post(client, "/logout")
    keep_token(client)
    sign_in(client)
    r = client.get("/search", query_string={"car_type": "SUV", "pickup": iso(5), "return": iso(6)})
    assert b"CX-5" in r.data


@pytest.mark.parametrize("field,value", [("make", ""), ("model", " "), ("car_type", "Boat"),
                                         ("seats", "0"), ("seats", "abc"), ("daily_rate", "-5"),
                                         ("daily_rate", "free")])
def test_add_car_rejects_bad_input(client, field, value):
    manager(client)
    r = post(client, "/manager/cars/new", **{**NEW_CAR, field: value})
    assert r.status_code == 200  # form shown again with an error
    assert db_of(client).execute("SELECT COUNT(*) FROM cars WHERE model='CX-5'").fetchone()[0] == 0


def test_edit_car_changes_details(client):
    manager(client)
    post(client, "/manager/cars/2/edit", **{**NEW_CAR, "make": "Honda", "model": "CR-V", "daily_rate": "99.50"})
    row = db_of(client).execute("SELECT daily_rate FROM cars WHERE id=2").fetchone()
    assert row[0] == 99.5


def test_edit_form_is_prefilled(client):
    manager(client)
    r = client.get("/manager/cars/2/edit")
    assert r.status_code == 200 and b"CR-V" in r.data


def test_edit_unknown_car_is_404(client):
    manager(client)
    assert client.get("/manager/cars/999/edit").status_code == 404


def test_remove_car_hides_it_from_search_and_reserve(client):
    manager(client)
    r = post(client, "/manager/cars/2/remove")
    assert r.status_code == 302
    assert db_of(client).execute("SELECT active FROM cars WHERE id=2").fetchone()[0] == 0
    post(client, "/logout")
    keep_token(client)
    sign_in(client)
    r = client.get("/search", query_string={"car_type": "SUV", "pickup": iso(5), "return": iso(6)})
    assert b"CR-V" not in r.data
    book(client, 2, iso(5), iso(6))
    assert db_of(client).execute("SELECT COUNT(*) FROM reservations WHERE user_id > 1").fetchone()[0] == 0
    assert client.get(f"/reserve/review?car_id=2&pickup={iso(5)}&return={iso(6)}").status_code == 302


def test_cannot_remove_car_with_upcoming_booking(client):
    sign_in(client)
    book(client, 2, iso(5), iso(6))
    post(client, "/logout")
    keep_token(client)
    manager(client)
    post(client, "/manager/cars/2/remove")
    assert db_of(client).execute("SELECT active FROM cars WHERE id=2").fetchone()[0] == 1


def test_can_remove_car_after_booking_is_cancelled(client):
    sign_in(client)
    book(client, 2, iso(5), iso(6))
    rid = db_of(client).execute("SELECT id FROM reservations WHERE user_id > 1").fetchone()[0]
    post(client, f"/reservations/{rid}/cancel")
    post(client, "/logout")
    keep_token(client)
    manager(client)
    post(client, "/manager/cars/2/remove")
    assert db_of(client).execute("SELECT active FROM cars WHERE id=2").fetchone()[0] == 0


def test_fleet_changes_require_csrf(client):
    manager(client)
    assert client.post("/manager/cars/new", data=NEW_CAR).status_code == 400
    assert client.post("/manager/cars/2/remove").status_code == 400


def test_old_bookings_of_removed_car_still_show_in_history(client):
    sign_in(client)
    db = db_of(client)
    uid = db.execute("SELECT id FROM users WHERE email='test@example.com'").fetchone()[0]
    db.execute("INSERT INTO reservations (user_id,car_id,start_date,end_date) VALUES (?,2,?,?)",
               (uid, iso(-10), iso(-8)))
    db.execute("UPDATE cars SET active=0 WHERE id=2")
    db.commit()
    assert b"CR-V" in client.get("/reservations").data


# ---------- REP-1 Daily Rentals ----------
def test_daily_rentals_lists_bookings_on_that_day(client):
    sign_in(client)
    book(client, 2, iso(5), iso(7))
    post(client, "/logout")
    keep_token(client)
    manager(client)
    r = client.get("/manager/daily", query_string={"date": iso(6)})
    assert b"Honda" in r.data and b"Test User" in r.data and b"On rental" in r.data
    assert b"Pickup today" in client.get("/manager/daily", query_string={"date": iso(5)}).data
    assert b"Return today" in client.get("/manager/daily", query_string={"date": iso(7)}).data


def test_daily_rentals_empty_day_and_bad_date(client):
    manager(client)
    assert b"No rentals on this day" in client.get("/manager/daily", query_string={"date": iso(300)}).data
    r = client.get("/manager/daily", query_string={"date": "nonsense"})
    assert r.status_code == 200 and b"not valid" in r.data


def test_daily_rentals_ignores_cancelled(client):
    sign_in(client)
    book(client, 2, iso(5), iso(7))
    rid = db_of(client).execute("SELECT id FROM reservations WHERE user_id > 1").fetchone()[0]
    post(client, f"/reservations/{rid}/cancel")
    post(client, "/logout")
    keep_token(client)
    manager(client)
    assert b"Test User" not in client.get("/manager/daily", query_string={"date": iso(6)}).data


# ---------- REP-2 Car Usage Report ----------
def test_usage_counts_days_inside_range_only(client):
    sign_in(client)
    book(client, 2, iso(5), iso(9))  # 5 days
    post(client, "/logout")
    keep_token(client)
    manager(client)
    r = client.get("/manager/usage", query_string={"from": iso(7), "to": iso(20)})
    html = r.data.decode()
    row = html[html.index("Honda CR-V"):]
    row = row[:row.index("</tr>")]
    assert "<td class=\"num-col\">1</td>" in row and "<td class=\"num-col\">3</td>" in row  # 1 booking, days 7-9


def test_usage_validates_dates(client):
    manager(client)
    r = client.get("/manager/usage", query_string={"from": iso(10), "to": iso(5)})
    assert r.status_code == 200 and b"on or after" in r.data
    r = client.get("/manager/usage", query_string={"from": "bad"})
    assert r.status_code == 200 and b"valid start date" in r.data


def test_usage_empty_range_message_and_default_range(client):
    manager(client)
    r = client.get("/manager/usage")
    assert r.status_code == 200 and b"30 days" in r.data


def test_usage_shows_removed_car_only_if_rented_in_range(client):
    db = db_of(client)
    db.execute("INSERT INTO reservations (user_id,car_id,start_date,end_date) VALUES (1,2,?,?)",
               (iso(-3), iso(-2)))
    db.execute("UPDATE cars SET active=0 WHERE id IN (2,3)")
    db.commit()
    manager(client)
    html = client.get("/manager/usage").data.decode()
    assert "(removed)" in html and html.count("(removed)") == 1


# ---------- REP-3 Customer Preferences ----------
def test_preferences_empty_state(client):
    db = db_of(client)
    db.execute("DELETE FROM reservations")  # the demo data has some bookings, so I clear them
    db.commit()
    manager(client)
    r = client.get("/manager/preferences")
    assert r.status_code == 200 and b"No bookings yet" in r.data


def test_preferences_counts_types_and_cars(client):
    db = db_of(client)
    db.execute("DELETE FROM reservations")
    sedan = db.execute("SELECT id FROM cars WHERE car_type='Sedan' LIMIT 1").fetchone()[0]
    for a, b in ((iso(-9), iso(-8)), (iso(-6), iso(-5)), (iso(-3), iso(-2))):
        db.execute("INSERT INTO reservations (user_id,car_id,start_date,end_date) VALUES (1,2,?,?)", (a, b))
    db.execute("INSERT INTO reservations (user_id,car_id,start_date,end_date) VALUES (1,?,?,?)",
               (sedan, iso(-20), iso(-18)))
    db.execute("INSERT INTO reservations (user_id,car_id,start_date,end_date,status) VALUES (1,2,?,?,'cancelled')",
               (iso(-30), iso(-29)))
    db.commit()
    manager(client)
    html = client.get("/manager/preferences").data.decode()
    assert "4 confirmed bookings" in html  # the cancelled one is not counted
    assert "75%" in html and "25%" in html and "Honda CR-V" in html
