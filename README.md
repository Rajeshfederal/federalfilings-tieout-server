# Federal Filings — Tie-Out Server

Internal web app: upload an SEC filing (10-K / 10-Q HTML), get back the full
side-by-side tie-out — the same Excel workbook and memo format as the manually
prepared deliverables. The server runs a mechanical number scan, then sends the
extracted MD&A and financial statements to Claude for the judgment pass:
caption matching, correct F/S values on every mismatch, year-label checks, and
recomputed percentages.

## Where it can run (important)

This is a Python web application — it needs a host that **runs Python**.
**Netlify, Vercel static hosting, and GitHub Pages will NOT work**: they serve
static files only, so `app.py` never runs and uploads fail.

Working options, easiest first:

1. **Render.com (recommended for a shared team URL).** Push this folder to a
   GitHub repo → render.com → New → Blueprint → pick the repo (it reads
   `render.yaml`) → set `ANTHROPIC_API_KEY` and `TIEOUT_PASSCODE` when
   prompted → deploy. Costs ~$7/mo (the free tier sleeps and may time out on
   long reviews).
2. **Railway.app** — same idea: deploy from GitHub, add the two environment
   variables, done.
3. **An office PC** (most private — filings never leave your network except
   the API call): follow "Setup" below and share `http://<pc-name>:8788` on
   the LAN.

Since a cloud URL is reachable by anyone, set `TIEOUT_PASSCODE` — the page
will require it before accepting uploads.

## Setup (one machine, ~5 minutes)

1. Install Python 3.10+ and the dependencies:

       pip install flask anthropic openpyxl

2. Get an Anthropic API key at https://console.anthropic.com and set it:

       export ANTHROPIC_API_KEY=sk-ant-...        (Windows: setx ANTHROPIC_API_KEY sk-ant-...)

3. Start the server:

       python app.py

4. Team members open `http://<server-hostname>:8788` in a browser, drop a
   filing, and download the workbook + memo when the review finishes (2–5 min).

## Configuration

- `PORT` — listen port (default 8788)
- `TIEOUT_MODEL` — Claude model for the judgment pass (default `claude-sonnet-4-5`)
- `TIEOUT_MOCK=mock_response.json` — serve a canned response instead of calling
  the API; used for testing the pipeline without a key

## Files

- `app.py` — Flask app: upload page, /analyze pipeline, downloads
- `tieout_scan.py` — mechanical scanner (same engine as the browser portal and
  the filing-tieout Claude skill; 10-K and 10-Q layouts auto-detected)
- `ai_pass.py` — Claude judgment pass (prompt + JSON contract)
- `workbook.py` — renders the side-by-side .xlsx in the house format
- `mock_response.json` — canned VSee Q1 2026 result for pipeline testing

## Notes

- Filing content is sent to the Anthropic API for the judgment pass. Confirm
  that is acceptable for the filing in hand (public-filing drafts normally are;
  Anthropic's API does not train on API content by default).
- Each run costs roughly $0.50–$2 in API usage depending on filing size.
- The output is a review aid prepared by AI: a preparer should read the
  workbook before anything goes to a client. The browser portal
  (FederalFilings_TieOut_Portal.html) remains available for instant,
  fully-local mechanical scans.
