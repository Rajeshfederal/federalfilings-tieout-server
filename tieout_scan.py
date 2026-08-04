#!/usr/bin/env python3
"""
tieout_scan.py - Mechanical number tie-out scanner for SEC filing HTML (10-K, 10-Q, S-1, etc.)

Extracts every dollar amount from the narrative section (default: MD&A / Item 7)
and checks whether each one is supported by the financial statements and notes:

  MATCH  - the exact number appears in the F/S section
  DERIVED- the number equals the difference or sum of two F/S numbers
           (typical for "increased $X" change amounts)
  FLAG   - the number appears nowhere in the F/S and cannot be derived
           -> highest priority for human/Claude review

Usage:
  python tieout_scan.py filing.htm                     # full report to stdout
  python tieout_scan.py filing.htm --json out.json     # also write JSON
  python tieout_scan.py filing.htm --min 1000          # ignore numbers < 1,000
  python tieout_scan.py filing.htm --narrative "Item 2." --fs "Item 1."   # 10-Q captions

Exit code: number of FLAG items (0 = everything tied).
"""
import argparse
import json
import re
import sys
from html.parser import HTMLParser


# ----------------------------------------------------------------------------
# HTML -> text
# ----------------------------------------------------------------------------
class TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "tr", "table", "br", "h1", "h2", "h3", "h4", "li"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "td":
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def text_from_html(raw):
    """Convert an HTML string to the scanner's plain-text form."""
    p = TextExtractor()
    p.feed(raw)
    text = "".join(p.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


def html_to_text(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return text_from_html(f.read())


# ----------------------------------------------------------------------------
# Section detection
# ----------------------------------------------------------------------------
def find_section(text, start_patterns, end_patterns, min_pos=0):
    """Return (start, end) of the first region beginning at a start pattern
    (after min_pos) and ending at the first end pattern after it.

    Table-of-contents hits are skipped by looking BACKWARD: a TOC entry sits
    inside a table of other TOC rows (many '|' cell markers before it), while
    a real section heading follows ordinary prose or a page break."""
    start = None
    for pat in start_patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            if m.start() < min_pos:
                continue
            window = text[max(0, m.start() - 250): m.start()]
            if window.count("|") > 6:
                continue
            start = m.start()
            break
        if start is not None:
            break
    if start is None:
        return None, None
    end = len(text)
    for pat in end_patterns:
        m = re.search(pat, text[start + 100:], re.IGNORECASE)
        if m:
            end = min(end, start + 100 + m.start())
    return start, end


# 10-K: MD&A is Item 7, F/S are Item 8 (F/S after MD&A).
# 10-Q: F/S are Item 1, MD&A is Item 2 (F/S before MD&A). Both are handled.
DEFAULT_NARRATIVE_START = [
    r"Item\s*7\.?\s*\n?\s*Management.{0,3}s?\s*Discussion",
    r"Item\s*2\.?\s*\n?\s*Management.{0,3}s?\s*Discussion",
    r"Management.{0,3}s\s+Discussion\s+and\s+Analysis",
]
DEFAULT_NARRATIVE_END = [
    r"Item\s*7A\.?", r"Item\s*8\.?\s*\n?\s*Financial Statements",
    r"Item\s*3\.?\s*\n?\s*Quantitative", r"Item\s*4\.?\s*\n?\s*Controls",
]
DEFAULT_FS_START = [
    r"Item\s*8\.?\s*\n?\s*Financial Statements",
    r"Item\s*1\.?\s*\n?\s*Financial Statements",
    r"INDEX\s+TO\s+CONSOLIDATED\s+FINANCIAL\s+STATEMENTS",
    r"REPORT\s+OF\s+INDEPENDENT\s+REGISTERED",
]
DEFAULT_FS_END = [
    r"Item\s*9\.?\s*\n?\s*Changes\s+in\s+and\s+Disagreements",
    r"SIGNATURES\s*\n",
]


# ----------------------------------------------------------------------------
# Number extraction
# ----------------------------------------------------------------------------
# Comma-formatted numbers (1,234 / 1,234,567.89) or plain decimals >= 3 digits.
NUM_RE = re.compile(r"\(?\$?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)\)?")

# Things that look like numbers but are not financial amounts
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def normalize(tok):
    return float(tok.replace(",", ""))


def looks_like_share_count(context):
    c = context.lower()
    return any(w in c for w in ("shares", "share s", "warrant", "options",
                                "weighted average", "authorized", "issued and outstanding"))


def extract_numbers(text, offset, min_value):
    """Return list of dicts: value, raw, pos, context."""
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group(1)
        if YEAR_RE.match(raw.replace(",", "")):
            continue
        val = normalize(raw)
        if val < min_value:
            continue
        ctx = text[max(0, m.start() - 110): m.end() + 110].replace("\n", " ").strip()
        out.append({"value": val, "raw": raw, "pos": offset + m.start(), "context": ctx})
    return out


# ----------------------------------------------------------------------------
# Matching
# ----------------------------------------------------------------------------
def classify(narrative_nums, fs_nums, proximity=600):
    """For each narrative number: MATCH (exact value in F/S), DERIVED (equals
    a-b or a+b of two F/S values that appear within `proximity` chars of each
    other -- i.e. plausibly the two year-columns of the same line item), FLAG.

    The proximity requirement matters: with thousands of F/S numbers, almost
    any amount can be expressed as a difference of two *unrelated* values,
    which would silently mask real errors. Real change amounts come from the
    same table row, so the two source numbers sit close together."""
    positions = {}
    for n in fs_nums:
        positions.setdefault(n["value"], []).append(n["pos"])
    values = set(positions)

    def near(a, b):
        return min(abs(pa - pb) for pa in positions[a] for pb in positions[b]) <= proximity

    results = []
    for item in narrative_nums:
        v = item["value"]
        status, basis = "FLAG", None
        if v in values:
            status = "MATCH"
        else:
            for b in values:
                a = v + b
                if a in values and near(a, b):
                    status, basis = "DERIVED", f"{a:,.0f} - {b:,.0f}"
                    break
            if status == "FLAG":
                for b in values:
                    if b < v and (v - b) in values and near(v - b, b):
                        status, basis = "DERIVED_SUM", f"{v - b:,.0f} + {b:,.0f}"
                        break
        item = dict(item)
        item["status"] = status
        if basis:
            item["basis"] = basis
        results.append(item)
    return results


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html_file")
    ap.add_argument("--min", type=float, default=1000, help="minimum value to check (default 1000)")
    ap.add_argument("--json", help="write JSON results to this path")
    ap.add_argument("--narrative", help="regex for start of narrative section (overrides MD&A default)")
    ap.add_argument("--fs", help="regex for start of financial statements section")
    ap.add_argument("--text-out", help="also save extracted plain text here")
    args = ap.parse_args()

    text = html_to_text(args.html_file)
    if args.text_out:
        with open(args.text_out, "w", encoding="utf-8") as f:
            f.write(text)

    nstart_pats = [args.narrative] if args.narrative else DEFAULT_NARRATIVE_START
    fstart_pats = [args.fs] if args.fs else DEFAULT_FS_START

    n_start, n_end = find_section(text, nstart_pats, DEFAULT_NARRATIVE_END,
                                  min_pos=len(text) // 20)
    if n_start is None:
        sys.exit("ERROR: could not locate narrative (MD&A) section. Use --narrative REGEX.")
    f_start, f_end = find_section(text, fstart_pats, DEFAULT_FS_END, min_pos=len(text) // 50)
    if f_start is None:
        sys.exit("ERROR: could not locate financial statements section. Use --fs REGEX.")
    if f_start < n_start:
        # 10-Q layout: financial statements precede MD&A
        f_end = min(f_end, n_start)

    narrative = text[n_start:n_end]
    fs = text[f_start:f_end]

    narrative_nums = extract_numbers(narrative, n_start, args.min)
    fs_nums = extract_numbers(fs, f_start, 0)

    results = classify(narrative_nums, fs_nums)

    flags = [r for r in results if r["status"] == "FLAG"]
    derived = [r for r in results if r["status"].startswith("DERIVED")]
    matched = [r for r in results if r["status"] == "MATCH"]

    print(f"Narrative section: chars {n_start:,}-{n_end:,}  |  F/S section: chars {f_start:,}-{f_end:,}")
    print(f"Numbers checked in narrative (>= {args.min:,.0f}): {len(results)}")
    print(f"  MATCH (exact in F/S):        {len(matched)}")
    print(f"  DERIVED (a±b of F/S values): {len(derived)}")
    print(f"  FLAG (no F/S support):       {len(flags)}")
    print()
    if flags:
        print("=" * 78)
        print("FLAGGED — no support found in financial statements or notes:")
        print("=" * 78)
        for r in flags:
            print(f"\n  ${r['raw']}")
            print(f"    ...{r['context']}...")
    if derived:
        print()
        print("=" * 78)
        print("DERIVED — equals a difference/sum of two F/S numbers (verify the pairing")
        print("makes sense; a coincidental pair can mask an error):")
        print("=" * 78)
        for r in derived:
            print(f"\n  ${r['raw']}  =  {r.get('basis')}")
            print(f"    ...{r['context'][:160]}...")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"flags": flags, "derived": derived, "matched": matched}, f, indent=1)
        print(f"\nJSON written to {args.json}")

    sys.exit(min(len(flags), 100))


if __name__ == "__main__":
    main()
