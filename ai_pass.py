"""AI judgment pass: sends the extracted MD&A + financial statements text and
the mechanical scan results to Claude, gets back caption-matched side-by-side
rows and a tie-out memo. Requires ANTHROPIC_API_KEY in the environment.
"""

import json
import os

MODEL = os.environ.get("TIEOUT_MODEL", "claude-sonnet-4-5")


SYSTEM = """You are the tie-out engine for Federal Filings LLC, an SEC filing agent.

You verify that every figure in a filing's MD&A agrees with the financial
statements and notes.

You are meticulous:
- recompute every change amount and percentage
- check every year label
- verify captions
- never claim a figure ties without locating it in the statements
"""


PROMPT = """Below are (1) the MD&A section and (2) the financial statements and
notes extracted from an SEC filing, plus (3) a mechanical scan that classified
every MD&A dollar amount as MATCH / DERIVED / FLAG against the F/S text.

Perform a full tie-out:

- Match every material MD&A figure to its financial-statement or note caption.
- For each mismatch, state the correct F/S value and where it comes from.
- Resolve every FLAG:
  discrepancy, legitimate MD&A-only figure, or multi-part aggregate.
- Recompute change amounts and percentages.
- Check every "for the year/period ended" sentence for wrong year labels.
- Note internal F/S inconsistencies separately.

Return ONLY a JSON object.
Do not use markdown.
Do not include ```json fences.
Do not include explanations before or after JSON.

Required JSON structure:

{
  "company": "",
  "form": "10-K|10-Q",
  "period": "",
  "rows": [
    {
      "section": "",
      "location": "Table|Narrative",
      "item": "",
      "mdna_value": 0,
      "fs_value": 0,
      "fs_source": "",
      "status": "MATCH|MISMATCH|YEAR LABEL ERROR|NOT IN F/S — CONFIRM",
      "note": ""
    }
  ],
  "memo_markdown": ""
}

Rows must cover every material figure.

=== MECHANICAL SCAN RESULTS ===
{scan}

=== MD&A SECTION ===
{narrative}

=== FINANCIAL STATEMENTS AND NOTES ===
{fs}
"""


def clean_json_response(text):
    """
    Cleans Claude response and extracts JSON.
    """

    text = text.strip()

    # Remove markdown code blocks
    if "```json" in text:
        text = text.replace("```json", "")

    if "```" in text:
        text = text.replace("```", "")

    text = text.strip()

    # Extract JSON object if Claude added extra text
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end != -1:
        text = text[start:end + 1]

    return text


def run_ai_pass(narrative_text, fs_text, scan_results):

    if os.environ.get("TIEOUT_MOCK"):
        with open(os.environ["TIEOUT_MOCK"], encoding="utf-8") as f:
            return json.load(f)

    import anthropic

    client = anthropic.Anthropic()


    scan_slim = {
        "flags": [
            {
                "raw": f["raw"],
                "context": f["context"]
            }
            for f in scan_results["flags"]
        ],

        "derived": [
            {
                "raw": f["raw"],
                "basis": f.get("basis"),
                "context": f["context"][:160]
            }
            for f in scan_results["derived"]
        ],

        "matched_count": len(scan_results["matched"])
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
        block.text
        for block in msg.content
        if block.type == "text"
    ).strip()


    try:

        cleaned = clean_json_response(text)

        return json.loads(cleaned)


    except json.JSONDecodeError as e:

        print("Claude returned invalid JSON")
        print("JSON error:", e)

        print("----- Claude Response Preview -----")
        print(text[:5000])
        print("-----------------------------------")


        return {
            "error": "Claude response was not valid JSON",
            "details": str(e),
            "raw_response": text[:2000]
        }
