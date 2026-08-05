"""AI judgment pass: sends the extracted MD&A + financial statements text and
the mechanical scan results to Claude, gets back caption-matched side-by-side
rows and a tie-out memo. Requires ANTHROPIC_API_KEY in the environment.
"""
import json
import os

MODEL = os.environ.get("TIEOUT_MODEL", "claude-sonnet-4-5")

SYSTEM = """You are the tie-out engine for Federal Filings LLC, an SEC filing agent.
You verify that every figure in a filing's MD&A agrees with the financial
statements and notes. You are meticulous: you recompute every change amount and
percentage, check every year label, and never claim a figure ties without
locating it in the statements."""

PROMPT = """Below are (1) the MD&A section and (2) the financial statements and
notes extracted from an SEC filing, plus (3) a mechanical scan that classified
every MD&A dollar amount as MATCH / DERIVED / FLAG against the F/S text.

Perform a full tie-out:
- Match every material MD&A figure to its financial-statement or note caption.
- For each mismatch, state the correct F/S value and where it comes from.
- Resolve every FLAG: discrepancy, legitimate MD&A-only figure, or a
  multi-part aggregate that actually ties (show the computation).
- Recompute change amounts and percentages; flag ones that only work with a
  wrong input.
- Check every "for the year/period ended" sentence for wrong year labels
  (status YEAR LABEL ERROR when the number is right but the period is wrong).
- Note internal F/S inconsistencies separately in the memo (section C).

Return ONLY a JSON object, no markdown fences, with this shape:
{
  "company": "...", "form": "10-K|10-Q", "period": "...",
  "rows": [
    {"section": "MD&A Results of Operations table (p.NN)",
     "location": "Table|Narrative",
     "item": "caption — period / description",
     "mdna_value": -14735984,          // number, sign as economically signed; null if N/A
     "fs_value": -14712850,            // correct F/S value; null when NOT IN F/S
     "fs_source": "Income statement / Note NN: computation",
     "status": "MATCH" | "MISMATCH" | "YEAR LABEL ERROR" | "NOT IN F/S — CONFIRM",
     "note": "reviewer note; empty string if none"}
  ],
  "memo_markdown": "full tie-out memo: # title, ## A. DISCREPANCIES,
                    ## B. ITEMS THAT CANNOT BE TRACED, ## C. Observations,
                    ## D. ITEMS VERIFIED"
}
Rows must be grouped by MD&A section in document order, covering every material
figure (verified ones included — the workbook must show what was checked, not
just what failed). Keep MATCH rows compact by combining a caption's periods and
change into one row when they all tie.

=== MECHANICAL SCAN RESULTS ===
{scan}

=== MD&A SECTION ===
{narrative}

=== FINANCIAL STATEMENTS AND NOTES ===
{fs}
"""


def run_ai_pass(narrative_text, fs_text, scan_results):
    if os.environ.get("TIEOUT_MOCK"):
        with open(os.environ["TIEOUT_MOCK"], encoding="utf-8") as f:
            return json.load(f)
    import anthropic
    client = anthropic.Anthropic()
    scan_slim = {
        "flags": [{"raw": f["raw"], "context": f["context"]} for f in scan_results["flags"]],
        "derived": [{"raw": f["raw"], "basis": f.get("basis"), "context": f["context"][:160]}
                    for f in scan_results["derived"]],
        "matched_count": len(scan_results["matched"]),
    }
    prompt = (PROMPT
              .replace("{scan}", json.dumps(scan_slim, indent=1))
              .replace("{narrative}", narrative_text)
              .replace("{fs}", fs_text))
    msg = client.messages.create(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)
