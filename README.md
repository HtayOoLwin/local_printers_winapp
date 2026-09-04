# Local Printers Windows App

Windows middleware for durable ERPNext printing. ERPNext persists and renders each
`Local Print Job`; Socket.IO only wakes this app. The app then claims jobs over the
authenticated REST API, prints each PDF to its exact Windows printer name, and
acknowledges success or failure.

## Requirements

- Python 3.10+
- Windows with the target printers installed
- [SumatraPDF](https://www.sumatrapdfreader.org)
- ERPNext/Frappe with the `local_printers` durable print-job APIs deployed
- An ERPNext service user with the `Local Printer Worker` role

## Install

```powershell
git clone https://github.com/HtayOoLwin/local_printers_winapp.git
cd local_printers_winapp
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item "config copy.json" config.json
```

## Configure Our City

Edit the untracked `config.json`. Do not put real passwords, API secrets, cookies,
or authorization headers in `config copy.json`, source control, or support logs.

```json
{
  "FRAPPE_BASE_URL": "https://ourcity.s.frappe.cloud",
  "FRAPPE_SOCKET_URL": "https://ourcity.s.frappe.cloud",
  "LOGIN_URL": "https://ourcity.s.frappe.cloud/api/method/login",
  "AUTH_DATA": {
    "usr": "your-local-printer-worker-user",
    "pwd": "your-local-printer-worker-password"
  },
  "API_KEY": "",
  "API_SECRET": "",
  "WORKER_ID": "ourcity-windows-printer-01",
  "SPOOL_TIMEOUT_SECONDS": 120,
  "SUMATRA_PDF_PATH": "C:\\Program Files\\SumatraPDF\\SumatraPDF.exe"
}
```

Configuration rules:

- `FRAPPE_BASE_URL` is the HTTPS origin used for durable claim and acknowledgement
  requests. For Our City it must be `https://ourcity.s.frappe.cloud`.
- `FRAPPE_SOCKET_URL` is the Socket.IO origin. It is a wake channel only; no event
  body is printed.
- `LOGIN_URL` and `AUTH_DATA` establish the session cookie used by both REST and
  Socket.IO.
- `WORKER_ID` is required. Give every Windows print-service installation a unique,
  stable identifier and keep it unchanged across restarts. Do not reuse one worker
  ID on multiple PCs.
- `API_KEY` and `API_SECRET` are optional when the logged-in session may register
  printers. If token authentication is used, configure both values together.
- `SUMATRA_PDF_PATH` must point to the installed SumatraPDF executable.
- `SPOOL_TIMEOUT_SECONDS` is the maximum SumatraPDF call duration. It must be an
  integer from 5 through 600; the default and sample value is 120 seconds.

The Printer value configured in ERPNext must exactly match the Windows printer name,
including case and spacing.

## Run

```powershell
.\.venv\Scripts\python.exe socket_app.py
```

On every connection or reconnection the middleware drains persisted work missed
while offline. Duplicate Socket.IO wakes are coalesced into serialized follower
passes, so no two drain loops print concurrently and a wake at drain shutdown is not
lost.

For each claimed job the app:

1. validates the job ID, exact printer, base64 PDF payload, ticket type, and attempt;
2. writes a temporary PDF;
3. waits for SumatraPDF to return after submitting to the selected printer;
4. acknowledges success only after that call succeeds, otherwise acknowledges a
   bounded failure; and
5. removes the temporary file.

## Timeout and duplicate-print uncertainty

A SumatraPDF timeout is acknowledged as a failed attempt, but Windows may have
accepted the document before the process timed out. The spool outcome is therefore
unknown and the server's automatic retry can produce a duplicate ticket. After a
timeout, inspect the Windows print queue and physical output before manually
retrying. Choose a timeout long enough for the slowest expected printer while staying
within the 5–600 second bound.

## Verification

Run the middleware tests before deployment:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m compileall -q print_job_client.py socket_app.py printer_handlers.py tests
```

Then create one test job and verify the ERPNext transition
`Pending -> Printing -> Success` and one physical print on the exact configured
Windows printer.

## Troubleshooting

| Symptom | Check |
|---|---|
| Cannot connect | Our City URLs, worker credentials, network, and system clock |
| Worker ID rejected | `WORKER_ID` format, uniqueness, stability, and service-user ownership |
| Printer registration retries | Windows spooler availability and exact installed names |
| Job remains `Printing` | REST connectivity and acknowledgement logs |
| Print fails immediately | `SUMATRA_PDF_PATH`, exact printer name, and PDF permissions |
| SumatraPDF timeout | Windows queue/output first; a retry may duplicate the ticket |
