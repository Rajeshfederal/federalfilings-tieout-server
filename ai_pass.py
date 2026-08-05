"""AI judgment pass: sends the extracted MD&A + financial statements text and
the mechanical scan results to Claude, gets back caption-matched side-by-side
rows and a tie-out memo. Requires ANTHROPIC_API_KEY in the environment.
"""
import json
import os

MODEL = os.environ.get("TIEOUT_MODEL", "claude-sonnet-4-5")

SYSTEM = """You are the tie-out engine for Federal Filings LLC, an SEC filing agent.
You verify that every figure in a filing's MD&A agrees with the financial
statements and notes. Be accurate, concise, and always return valid JSON only."""

PROMPT = """Below are (1) the MD&A section and (2) the financial statements and
notes extracted from an SEC filing, plus (3) a mechanical scan.

Perform a complete tie-out.

Requirements:
- Match every material MD&A figure to the financial statements or notes.
- For each mismatch, provide the correct F/S value.
- Resolve every FLAG.
- Recompute change amounts and percentages.
- Check year labels.
- Return valid JSON ONLY.
- DO NOT include markdown fences.
- Keep reviewer notes short.
- Keep MATCH rows compact.
- Limit memo_markdown to **300 words maximum**.
- Finish the JSON completely.

Return exactly:

{
  "company": "...",
  "form": "...",
  "period": "...",
  "rows": [
    {
      "section":"",
      "location":"",
      "item":"",
      "mdna_value":0,
      "fs_value":0,
      "fs_source":"",
      "status":"",
      "note":""
    }
  ],
  "memo_markdown":"Maximum 300 words."
}

=== MECHANICAL SCAN RESULTS ===
{scan}

=== MD&A SECTION ===
{narrative}

=== FINANCIAL STATEMENTS ===
{fs}
"""


def run_ai_pass(narrative_text, fs_text, scan_results):
    if os.environ.get("TIEOUT_MOCK"):
        with open(os.environ["TIEOUT_MOCK"], encoding="utf-8") as f:
            return json.load(f)

    import anthropic

    client = anthropic.Anthropic()

    scan_slim = {
        "flags": [
            {"raw": f["raw"], "context": f["context"]}
            for f in scan_results["flags"]
        ],
        "derived": [
            {
                "raw": f["raw"],
                "basis": f.get("basis"),
                "context": f["context"][:160],
            }
            for f in scan_results["derived"]
        ],
        "matched_count": len(scan_results["matched"]),
    }

    prompt = (
        PROMPT
        .replace("{scan}", json.dumps(scan_slim, indent=1))
        .replace("{narrative}", narrative_text)
        .replace("{fs}", fs_text)
    )

    msg = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
    )

    text = "".join(
        b.text for b in msg.content if b.type == "text"
    ).strip()

    print("===== CLAUDE RESPONSE START =====")
    print(text)
    print("===== CLAUDE RESPONSE END =====")

    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]

    return json.loads(text)
