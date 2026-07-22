# TAF AutoScanner

Internal web app for processing employee motor-credit applications: scan or fill in a form, OCR pulls the fields out of a photographed document, the employee reviews/corrects them and signs digitally, and HR/managers approve and export the results to Excel.

## How it works

1. **Staff/branch employee** logs in (either with an NPK + name + branch, or a shared demo account) and either:
   - uploads a photo of the paper form on `/scan` — OCR (EasyOCR, Indonesian + English) extracts text and regex-based parsing (`smart_parse` in `app.py`) fills in fields like NPK, name, position/grade, department, branch, chassis/engine number, and BPKB; known branch/department names are matched against fixed lists to correct OCR noise — or
   - fills in the same fields manually on `/form`.
2. All extracted fields are editable before submission, an installment calculation is shown live, and the employee signs on a canvas-based signature pad.
3. Submitting creates a loan record (`/api/submit`) with a generated application ID (`TAF-<timestamp>-<random>`), computes principal/interest/monthly installment/outstanding balance, and updates a per-employee loan history table.
4. **HR or Manager** reviews submissions on the dashboard, approves or rejects (with a reason) each one, and can export approved/pending/all submissions to Excel, or export an individual submission as an EDLIN-formatted amortization schedule.
5. **HR only** can change the interest rate applied to non-operational ("office") employees from `/settings` (operational/"field" employees are fixed at 5%); changes are audit-logged with who changed it and when.

## Roles

- **Staff** — submit/scan applications, see their own submission history.
- **Manager** — everything staff can do, plus approve/reject any submission and export reports.
- **HR** — everything manager can do, plus change the office interest rate.

Regular employees log in with just an NPK, name, and branch (no password) and are always assigned the `staff` role server-side. A small set of privileged demo accounts (`hr`, `manager`, `staff`) log in with a password from environment variables.

## Setup

```bash
pip install -r requirements.txt
export SECRET_KEY=...            # Flask session secret
export HR_PASSWORD=...
export MANAGER_PASSWORD=...
export STAFF_PASSWORD=...
python app.py
```

Data is stored in a local SQLite database (`data.db`), created automatically on first run. Uploaded images are processed in-memory and deleted immediately after OCR.

## Stack

Flask + SQLite, EasyOCR for text extraction, pandas/openpyxl for Excel reports, deployed via gunicorn (see `render.yaml` / `RENDER_GUIDE.md` for the Render.com deployment path).
