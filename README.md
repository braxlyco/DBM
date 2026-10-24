# Workshop DMS (MVP)

A lightweight Dealer Management System for automotive workshops in Pakistan. It runs
workshop operations (intake → estimate → repair → parts → invoice) and posts financial
results to SAP FI. In this MVP the SAP posting is stubbed: payloads go to the **SAP posting log** table.

## Run it (Windows, PowerShell)

```powershell
cd "D:\VS CODE PROJECTS\DBM"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo --with-invoice
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/ and log in with the user you just created.
Admin (master data): http://127.0.0.1:8000/admin/

If `Activate.ps1` is blocked ("running scripts is disabled"), run this once and then activate again:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

Run the tests: `python manage.py test workshop`

## Where things are

| File | What it does |
|---|---|
| `workshop/models.py` | Data model and the rules that must never break (immutability, estimate lock, append-only audit). |
| `workshop/workflow.py` | Job-card state machine (`ALLOWED_TRANSITIONS`, `guard_transition`) and every operation that changes a job card. |
| `workshop/finance.py` | `FinancePoster` interface, `LoggingAdapter`, and the builders for invoice and parts posting events. |
| `workshop/views.py`, `templates/` | Screens: job list, new job, job card, printable invoice. |
| `workshop/admin.py` | Master data CRUD; operational and financial records are view-only. |
| `workshop/management/commands/seed_demo.py` | Demo data. |
| `dms_project/settings.py` | Settings, Postgres switch, SAP GL mapping (placeholders). |

## NOTES: placeholders and assumptions to verify

**Tax / FBR (verify with a tax adviser)**
1. Invoice fields follow my understanding of a sales tax invoice under the Sales Tax Act 1990: serial number, date, seller name, address, NTN and STRN, buyer details, description, quantity, value excl. tax, rate, tax and value incl. tax. They are not verified against current FBR rules.
2. FBR digital invoicing / POS integration (FBR-issued invoice number, QR code, PRAL API) is not built. The invoice shows a `[PLACEHOLDER]` row for it.
3. Rates are 18% on goods (parts) and 16% on services (labour and sublet), set per Branch. Sales tax on services is provincial (PRA, SRB, KPRA, BRA). Check the rates, whether workshop labour and sublet are taxable in your province, and whether goods and services may share one invoice.
4. Tax is rounded per line (half-up) so the printed lines add up. Check that this is acceptable.
5. Extra or further tax on supplies to unregistered buyers may apply. It is not implemented.
6. The seeded NTN, STRN and PRA numbers are dummies. The HS codes are 6-digit, while Pakistan uses 8-digit PCT codes.
7. Not built: credit notes or invoice cancellation, amount in words, the required invoice-number format (current format is `LHR01-INV-000001`, gap-free per branch).

**SAP FI**

8. GL accounts, company code, document types (DR/SA), profit center and the one-time customer are placeholders (`DMS_SAP_*` in settings, `Branch.sap_profit_center`, `Customer.sap_customer_no`).
9. If SAP MM manages inventory, a parts issue should be an MM goods movement, not an FI COGS/inventory posting, and DMS stock would only mirror SAP. This is a design decision to confirm.
10. Postings are written to `SapPostingLog` in the same database transaction as the business change (an outbox). A real adapter should send them from that log asynchronously, retry, and set status POSTED or FAILED with the SAP document number. The idempotency key already prevents double posting.
11. Sublet vendor cost (AP) and customer payments or receipts are not posted. They are out of scope.

**Business-rule assumptions**

12. The estimate total and the variance exclude tax.
13. Issuing more parts than are in stock is blocked (stock never goes negative), and the message suggests putting the job ON HOLD. Falling below the reorder level only shows a warning.
14. Removing a consumed part line while the job is IN_PROGRESS returns the part to stock and posts a reversal.
15. CANCELLED job cards are read-only, like INVOICED ones. Comebacks can only be raised from INVOICED job cards.
16. There are no roles or branch restrictions: any logged-in user can approve, invoice, and act on any branch.
17. Stock receipts and adjustments are made by editing `stock_on_hand` in the admin, and those edits are not audited.
18. Prices come from the catalogue only. There are no discounts or price overrides.
19. Not production-ready: DEBUG is on, the dev secret key is in use, and the database is SQLite.
