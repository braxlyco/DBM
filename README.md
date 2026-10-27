# Workshop DMS

A Django application for managing a workshop job from intake through invoicing. It brings job cards, estimates, actual work, parts consumption, tax calculations, and an audit trail into one workflow.

This is a working prototype for a Pakistan-based automotive workshop. It is intended for local evaluation and extension; tax rules and SAP account mappings must be verified before operational use.

## What it does

- Tracks job cards through **draft → approved → in progress → completed → invoiced**, with hold and cancellation paths.
- Locks the approved estimate and preserves invoiced job cards as read-only records.
- Records labour, parts and sublet work; consuming a part reduces stock and a reversal restores it.
- Shows estimate-versus-actual variance and flags parts below their reorder level.
- Calculates an itemised PKR invoice with separate goods and services tax rates configured per branch.
- Writes balanced, idempotent accounting payloads to a local SAP posting log. No live SAP connection is included.
- Supports searching and filtering job cards by number, vehicle, customer, status and branch.

## Run locally

Requires Python 3.11 or newer. SQLite is configured by default; no separate database server is needed.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo --with-invoice
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Open [http://127.0.0.1:8000/](http://127.0.0.1:8000/) and sign in. Master data is managed at [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/). The seed command creates fictional data and can be run again without duplicating its main records.

On macOS or Linux, activate the virtual environment with `source .venv/bin/activate`, then use `python` in place of `.\.venv\Scripts\python.exe`.

## Checks

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test workshop
```

GitHub Actions runs the same checks on each push and pull request.

## How it is organised

| Area | Responsibility |
| --- | --- |
| `workshop/models.py` | Domain records, calculated amounts and immutability rules |
| `workshop/workflow.py` | Valid status transitions and transactional business operations |
| `workshop/finance.py` | Balanced accounting events and the replaceable posting adapter |
| `workshop/views.py`, `workshop/templates/` | Operator screens and printable invoice |
| `workshop/admin.py` | Master data management and read-only operational records |
| `workshop/management/commands/seed_demo.py` | Fictional demonstration data |

The workflow writes the job change, status history, stock adjustment and accounting log in a database transaction. A failure rolls the operation back. The default `LoggingAdapter` records an outbox entry; it does not send data to SAP.

## Configuration and scope

Development defaults are in `dms_project/settings.py`. Set `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0` and `DJANGO_ALLOWED_HOSTS` before deploying. Do not publish `db.sqlite3` or real customer information; the database and virtual environment are excluded from Git.

The tax rates and registration details in the sample data are **illustrative**, not verified compliance advice. FBR digital invoicing, credit notes, payments, branch-level permissions, audited stock receipts and live SAP integration are not implemented. The SAP GL numbers, company code and profit centre are placeholders. A real deployment needs verified tax rules, production database and security review.
