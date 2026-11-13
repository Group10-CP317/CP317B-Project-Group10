# Meridian Car Rental - Sprint 1 prototype (Group 10, CP317B)

Stories: UI-1 Create Account, UI-6 Log In, ADM-2 Manager Access, UI-2 Search Cars.
Stack: Python 3 + Flask + SQLite (the database file is created automatically).

## Run
    pip install -r requirements.txt
    python app.py          # then open http://127.0.0.1:5000

Demo manager login: manager@carrental.com / Manager123!
Customers register themselves at /register.

## Test
    python -m pytest -v

## Sprint 2 (in progress)
Stories: UI-3 Reserve Car, UI-4 Cancel Reservation, UI-5 Rental History, UI-7 Personal Data Control.

The `sprint2` branch starts from a shared skeleton (helper functions, styles, navigation and the automated tests) with each story's feature left as a TODO. Each story is implemented on its own branch (`ui-3-reserve`, `ui-4-cancel`, `ui-5-history`, `ui-7-mydata`) and merged into `sprint2` by pull request. The skeleton and tests were written with AI assistance.
