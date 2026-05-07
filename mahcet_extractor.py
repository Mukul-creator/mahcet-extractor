#!/usr/bin/env python3
"""
MHT CET Engineering CAP Cutoff PDF to JSON Converter
=====================================================
Handles the official CET Cell PDF table format (as shown in the image):
 
  College: 01002 - Government College of Engineering, Amravati
  Branch:  0100219110 - Civil Engineering
  Status:  Government Autonomous Home University : Autonomous Institute
  Seat:    State Level
 
  +-------+--------+--------+--------+-----+
  | Stage | GOPENS |  GSCS  |  GSTS  | ... |   <- category header row
  +-------+--------+--------+--------+-----+
  |   I   | 37591  | 50510  | 94334  | ... |   <- merit numbers
  |       |(88.96) |(92.33) |(99.49) | ... |   <- percentiles
  +-------+--------+--------+--------+-----+
  |  VII  |  ...   |        |        |     |   <- betterment stage (if any)
  +-------+--------+--------+--------+-----+
 
Usage:
    python3 mhtcet_pdf_to_json.py <input.pdf> [options]
 
Examples:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf
    python3 mhtcet_pdf_to_json.py 2024ENGG_CAP2_AI_CutOff.pdf --quota AI
    python3 mhtcet_pdf_to_json.py myfile.pdf --year 2025 --round CAP1 --quota MS --pretty
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --sample 10
 
Requirements (install ONE):
    pip install pypdf
    pip install pdfplumber
    sudo apt install poppler-utils   (Linux)
    brew install poppler             (macOS)
"""
 
import re
import json
import sys
import os
import argparse
import subprocess
 
 
# ─────────────────────────────────────────────────────────
# 1. PDF TEXT EXTRACTION
# ─────────────────────────────────────────────────────────
 
def _try_pdftotext(pdf_path):
    try:
        r = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            capture_output=True, text=True, timeout=300
        )
        if r.returncode == 0 and len(r.stdout.strip()) > 100:
            return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
 
 
def _try_pdfplumber(pdf_path):
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                t = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
                pages.append(t)
                if (i + 1) % 50 == 0:
                    print(f"  Read {i+1}/{total} pages...", flush=True)
        return "\n".join(pages)
    except ImportError:
        pass
    except Exception as e:
        print(f"  [pdfplumber] {e}", file=sys.stderr)
    return None
 
 
def _try_pypdf(pdf_path):
    try:
        from pypdf import PdfReader
        reader = PdfReader(pdf_path)
        pages = []
        total = len(reader.pages)
        for i, page in enumerate(reader.pages):
            t = page.extract_text() or ""
            pages.append(t)
            if (i + 1) % 50 == 0:
                print(f"  Read {i+1}/{total} pages...", flush=True)
        return "\n".join(pages)
    except ImportError:
        pass
    except Exception as e:
        print(f"  [pypdf] {e}", file=sys.stderr)
    return None
 
 
def extract_pdf_text(pdf_path):
    """Try pdftotext → pdfplumber → pypdf, return first that works."""
    print(f"\nExtracting: {os.path.basename(pdf_path)}")
    for label, fn in [
        ("pdftotext (recommended)", _try_pdftotext),
        ("pdfplumber",              _try_pdfplumber),
        ("pypdf",                   _try_pypdf),
    ]:
        print(f"  Trying {label}...", end=" ", flush=True)
        text = fn(pdf_path)
        if text and len(text.strip()) > 100:
            print(f"OK  ({len(text):,} chars)")
            return text
        print("failed")
 
    print("\nERROR: No extraction method worked. Install one of:")
    print("  pip install pypdf")
    print("  pip install pdfplumber")
    print("  sudo apt install poppler-utils  (Linux)")
    print("  brew install poppler            (macOS)")
    sys.exit(1)
 
 
# ─────────────────────────────────────────────────────────
# 2. AUTO-DETECT METADATA FROM FILENAME
# ─────────────────────────────────────────────────────────
 
def detect_from_filename(path):
    """
    2024ENGG_CAP1_CutOff.pdf     -> (2024, 'CAP1', 'MS')
    2025ENGG_CAP2_AI_CutOff.pdf  -> (2025, 'CAP2', 'AI')
    """
    name = os.path.basename(path).upper()
 
    m_year  = re.search(r"(20\d{2})", name)
    m_round = re.search(r"(CAP\d+)", name)
    quota   = "AI" if ("_AI_" in name or name.endswith("_AI.PDF")) else "MS"
 
    return (
        int(m_year.group(1))  if m_year  else None,
        m_round.group(1)      if m_round else None,
        quota,
    )
 
 
# ─────────────────────────────────────────────────────────
# 3. LINE HELPERS
# ─────────────────────────────────────────────────────────
 
# A line that is ONLY space-separated uppercase category codes
# (optionally prefixed with "Stage" which is the table's first column header)
# e.g. "GOPENS GSCS GSTS GVJS GNT1S GNT2S GOBCS EWS"
# e.g. "Stage GOPENS GSCS GSTS ..."
_CAT_LINE_RE = re.compile(
    r"^(?:Stage\s+)?([A-Z][A-Z0-9]+(?: [A-Z][A-Z0-9]+)+)\s*$"
)
 
# Pure integer (merit number)
_MERIT_RE = re.compile(r"^\d+$")
 
# Percentile in brackets
_PCT_RE = re.compile(r"^\((\d+\.\d+)\)$")
 
# Seat type header substrings
_SEAT_KEYWORDS = [
    "State Level",
    "Home University Seats Allotted to Home University",
    "Home University Seats Allotted to Other Than Home University",
    "Other Than Home University Seats Allotted to Other Than Home University",
    "Other Than Home University Seats Allotted to Home University",
    "Minority Seats",
]
 
# Stage markers (Roman numerals)
_STAGE_VALUES = {"I", "II", "III", "IV", "V", "VI", "VII"}
 
 
def classify_line(line):
    """
    Return (type, value) where type is one of:
      'college'    -> (code, name)
      'branch'     -> (code, name)
      'status'     -> status_string
      'seat_type'  -> seat_type_string
      'cat_header' -> [cat_code, ...]
      'stage'      -> stage_string  e.g. 'I', 'VII'
      'stage_non'  -> 'I-Non'  (I-Non PWD / I-Non Defence)
      'merit'      -> int
      'pct'        -> float
      'noise'      -> None  (skip this line)
      'unknown'    -> None
    """
    s = line.strip()
    if not s:
        return ("noise", None)
 
    # College header  (5-digit code)
    cm = re.match(r"^(\d{5})\s*-\s*(.+)$", s)
    if cm and not re.match(r"^\d{10}", s):
        return ("college", (cm.group(1).strip(), cm.group(2).strip()))
 
    # Branch header  (10-digit code)
    bm = re.match(r"^(\d{10})\s*-\s*(.+)$", s)
    if bm:
        return ("branch", (bm.group(1).strip(), bm.group(2).strip()))
 
    # Status line
    sm = re.search(r"Status:\s*(.+)", s)
    if sm:
        return ("status", sm.group(1).strip())
 
    # Seat type header
    for kw in _SEAT_KEYWORDS:
        if s.startswith(kw):
            return ("seat_type", s)
 
    # Stage: pure roman numeral
    if s in _STAGE_VALUES:
        return ("stage", s)
 
    # Stage: I-Non PWD / I-Non Defence
    if re.match(r"^I-Non\s+", s, re.I):
        return ("stage_non", "I-Non")
 
    # Category header row
    m = _CAT_LINE_RE.match(s)
    if m:
        return ("cat_header", m.group(1).split())
 
    # Merit number
    if _MERIT_RE.match(s):
        return ("merit", int(s))
 
    # Percentile
    pm = _PCT_RE.match(s)
    if pm:
        return ("pct", float(pm.group(1)))
 
    # Noise
    noise_prefixes = (
        "Cut Off List", "Degree Courses", "State Common Entrance",
        "Government of Maharashtra", "Legends", "D\ni\nr",
        "Maharashtra State Seats", "Figures in bracket",
    )
    if any(s.startswith(p) for p in noise_prefixes):
        return ("noise", None)
 
    return ("unknown", s)
 
 
# ─────────────────────────────────────────────────────────
# 4. CORE PARSER
# ─────────────────────────────────────────────────────────
 
def parse_cutoff_text(text, year, cap_round, quota):
    """
    Parse the full extracted PDF text and return (records, college_count).
 
    Each record:
    {
      "year", "cap_round", "quota",
      "college_code", "college_name",
      "branch_code",  "branch_name",
      "status", "seat_type", "stage",
      "cutoffs": { "GOPENS": {"merit_no": 37591, "percentile": 88.96}, ... }
    }
    """
    records = []
    college_count = 0
 
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
 
    # State machine
    college_code = college_name = ""
    branch_code  = branch_name  = ""
    status       = ""
    seat_type    = "State Level"
    cat_codes    = []
    stage        = "I"
    values       = []   # [(merit_no, percentile), ...]
 
    def flush_values():
        """Save current (cat_codes, stage, values) as a record if non-empty."""
        if not values or not cat_codes or not branch_code:
            return
        cutoffs = {}
        for j, cat in enumerate(cat_codes):
            if j < len(values):
                cutoffs[cat] = values[j]
        if cutoffs:
            records.append({
                "year":         year,
                "cap_round":    cap_round,
                "quota":        quota,
                "college_code": college_code,
                "college_name": college_name,
                "branch_code":  branch_code,
                "branch_name":  branch_name,
                "status":       status,
                "seat_type":    seat_type,
                "stage":        stage,
                "cutoffs":      cutoffs,
            })
 
    i = 0
    while i < len(lines):
        kind, val = classify_line(lines[i])
        i += 1
 
        if kind == "noise" or kind == "unknown":
            continue
 
        elif kind == "college":
            # Flush any pending values
            flush_values()
            values = []
            cat_codes = []
            college_code, college_name = val
            college_count += 1
            branch_code = branch_name = ""
            status = ""
            seat_type = "State Level"
 
        elif kind == "branch":
            flush_values()
            values = []
            cat_codes = []
            branch_code, branch_name = val
            status = ""
            seat_type = "State Level"
            stage = "I"
 
        elif kind == "status":
            status = val
 
        elif kind == "seat_type":
            # New seat section — flush current accumulation
            flush_values()
            values = []
            cat_codes = []
            seat_type = val
            stage = "I"
 
        elif kind == "cat_header":
            # New category header row — flush previous
            flush_values()
            values = []
            cat_codes = val
            stage = "I"
 
        elif kind == "stage":
            # Stage marker within the same cat_header table
            # Flush the previous stage's values
            flush_values()
            values = []
            stage = val
 
        elif kind == "stage_non":
            flush_values()
            values = []
            stage = val
 
        elif kind == "merit":
            merit_no = val
            pct = None
            # Peek at next line for percentile
            if i < len(lines):
                nk, nv = classify_line(lines[i])
                if nk == "pct":
                    pct = nv
                    i += 1
            values.append({"merit_no": merit_no, "percentile": pct})
 
        elif kind == "pct":
            # Lone percentile without preceding merit — skip
            pass
 
    # Flush final pending values
    flush_values()
 
    return records, college_count
 
 
# ─────────────────────────────────────────────────────────
# 5. OUTPUT SCHEMA / METADATA
# ─────────────────────────────────────────────────────────
 
CATEGORY_LEGEND = {
    "how_to_read": (
        "Format: [Prefix][Category][Suffix]. "
        "Example: GOPENS = G(General seats) + OPEN(Open/General category) + S(State Level). "
        "LSCS = L(Ladies) + SC(Scheduled Caste) + S(State Level)."
    ),
    "prefix": {
        "G": "General seats (open to all genders)",
        "L": "Ladies-only seats",
    },
    "category": {
        "OPEN":   "Open / General category",
        "SC":     "Scheduled Caste",
        "ST":     "Scheduled Tribe",
        "VJ":     "Vimukta Jati (Denotified Tribes)",
        "NT1":    "Nomadic Tribe 1 (NT-A)",
        "NT2":    "Nomadic Tribe 2 (NT-B)",
        "NT3":    "Nomadic Tribe 3 (NT-C/D)",
        "OBC":    "Other Backward Class",
        "SEBC":   "Socially & Educationally Backward Class",
        "EWS":    "Economically Weaker Section",
        "PWD":    "Persons with Disability",
        "PWDR":   "PWD Reserved carry-forward",
        "DEF":    "Defence category",
        "DEFR":   "Defence Reserved carry-forward",
        "TFWS":   "Tuition Fee Waiver Scheme",
        "MI":     "Minority seats",
        "ORPHAN": "Orphan category",
    },
    "suffix": {
        "S": "State Level seat",
        "H": "Home University seat",
        "O": "Other than Home University seat",
    },
    "stage": {
        "I":     "Stage I — first preference allotment",
        "II":    "Stage II — second preference",
        "III":   "Stage III — third preference",
        "VII":   "Stage VII — betterment round",
        "I-Non": "I (Non-PWD) or I (Non-Defence) sub-stage",
    },
}
 
RECORD_SCHEMA = {
    "year":         "Academic year (2025 = AY 2025-26, 2024 = AY 2024-25)",
    "cap_round":    "CAP1 | CAP2 | CAP3 | CAP4",
    "quota":        "MS = Maharashtra State (85% seats) | AI = All India (15% seats)",
    "college_code": "5-digit CET Cell college identifier",
    "college_name": "Full name of the college",
    "branch_code":  "10-digit branch code (first 5 digits match college_code)",
    "branch_name":  "Engineering branch / specialisation name",
    "status":       "College type (Government / Aided / Un-Aided / Autonomous etc.)",
    "seat_type":    "Seat grouping (State Level / Home University / Other Than HU etc.)",
    "stage":        "Allotment stage: I | II | III | VII | I-Non",
    "cutoffs": {
        "_note": "Only categories that had seats allotted appear here.",
        "merit_no":   "Closing State General Merit Number (lower rank = better)",
        "percentile": "Closing MHT-CET PCM percentile (MS) or JEE Main score (AI)",
    },
}
 
 
def build_output(records, source_file, year, cap_round, quota, college_count):
    return {
        "metadata": {
            "source":          "Maharashtra State CET Cell — Government of Maharashtra",
            "portal":          "https://fe2025.mahacet.org",
            "source_file":     os.path.basename(source_file),
            "year":            year,
            "cap_round":       cap_round,
            "quota":           quota,
            "quota_note":      (
                "Maharashtra State seats (85% of intake). Cutoff = MHT-CET PCM percentile."
                if quota == "MS" else
                "All India seats (15% of intake). Cutoff = JEE Main score/rank."
            ),
            "total_records":   len(records),
            "colleges_parsed": college_count,
            "record_schema":   RECORD_SCHEMA,
            "category_legend": CATEGORY_LEGEND,
            "pdf_url_templates": {
                "MS": "https://fe2025.mahacet.org/{year}/{year}ENGG_{round}_CutOff.pdf",
                "AI": "https://fe2025.mahacet.org/{year}/{year}ENGG_{round}_AI_CutOff.pdf",
            },
        },
        "data": records,
    }
 
 
# ─────────────────────────────────────────────────────────
# 6. CLI
# ─────────────────────────────────────────────────────────
 
def main():
    ap = argparse.ArgumentParser(
        description="Convert MHT CET Engineering CAP cutoff PDF to JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
EXAMPLES
  Auto-detect everything from filename:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf
 
  All India seats:
    python3 mhtcet_pdf_to_json.py 2024ENGG_CAP2_AI_CutOff.pdf
 
  Override metadata manually:
    python3 mhtcet_pdf_to_json.py myfile.pdf --year 2025 --round CAP1 --quota MS
 
  Pretty-print JSON (human-readable):
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --pretty
 
  Compact JSON (smallest file size):
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --compact
 
  Test — inspect just first 20 records:
    python3 mhtcet_pdf_to_json.py 2025ENGG_CAP1_CutOff.pdf --sample 20
 
QUOTA
  MS  Maharashtra State seats (85%%)  — cutoff shows MHT-CET percentile
  AI  All India seats         (15%%)  — cutoff shows JEE Main score
 
CATEGORY CODES  [Prefix][Category][Suffix]
  Prefix   G = General   L = Ladies
  Category OPEN SC ST VJ NT1 NT2 NT3 OBC SEBC EWS PWD DEF TFWS ORPHAN MI
  Suffix   S = State   H = Home University   O = Other than Home University
  Example  GOPENS = General + Open/General category + State Level
        """,
    )
    ap.add_argument("pdf",                          help="Path to the MHT CET cutoff PDF")
    ap.add_argument("--year",    type=int,          help="Academic year, e.g. 2025 (auto-detected from filename)")
    ap.add_argument("--round",   dest="cap_round",  help="CAP1 | CAP2 | CAP3 | CAP4  (auto-detected)")
    ap.add_argument("--quota",   choices=["MS","AI"], help="MS or AI  (auto-detected from filename)")
    ap.add_argument("--out",                        help="Output JSON file  (default: <pdf_name>.json)")
    ap.add_argument("--pretty",  action="store_true", help="4-space indent — human-readable, larger file")
    ap.add_argument("--compact", action="store_true", help="No whitespace — smallest file size")
    ap.add_argument("--sample",  type=int, metavar="N", default=0,
                    help="Only write first N records (for testing)")
    args = ap.parse_args()
 
    if not os.path.isfile(args.pdf):
        print(f"ERROR: File not found: {args.pdf}", file=sys.stderr)
        sys.exit(1)
 
    auto_year, auto_round, auto_quota = detect_from_filename(args.pdf)
    year      = args.year      or auto_year      or 0
    cap_round = args.cap_round or auto_round     or "UNKNOWN"
    quota     = args.quota     or auto_quota     or "MS"
 
    if not (args.year or auto_year):
        print("WARNING: year not detected — use --year 2025", file=sys.stderr)
    if not (args.cap_round or auto_round):
        print("WARNING: round not detected — use --round CAP1", file=sys.stderr)
 
    print(f"Settings  ->  year={year}  round={cap_round}  quota={quota}")
 
    out_path = args.out or (os.path.splitext(args.pdf)[0] + ".json")
 
    raw = extract_pdf_text(args.pdf)
 
    print("\nParsing...", flush=True)
    records, college_count = parse_cutoff_text(raw, year, cap_round, quota)
    print(f"  Colleges : {college_count}")
    print(f"  Records  : {len(records):,}")
 
    if args.sample > 0:
        records = records[:args.sample]
        print(f"  (sample mode — first {args.sample} records only)")
 
    output = build_output(records, args.pdf, year, cap_round, quota, college_count)
 
    indent     = 4 if args.pretty else (None if args.compact else 2)
    separators = (",", ":") if args.compact else None
    json_str   = json.dumps(output, indent=indent, separators=separators, ensure_ascii=False)
 
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json_str)
 
    kb = os.path.getsize(out_path) / 1024
    print(f"\nDone! -> {out_path}")
    print(f"  Records   : {len(records):,}")
    print(f"  File size : {kb:,.1f} KB")
 
    if records:
        r = records[0]
        print("\n--- Sample record ---")
        print(f"  College   : [{r['college_code']}] {r['college_name']}")
        print(f"  Branch    : [{r['branch_code']}] {r['branch_name']}")
        print(f"  Seat type : {r['seat_type']}")
        print(f"  Stage     : {r['stage']}")
        for cat, val in list(r["cutoffs"].items())[:6]:
            pct = f"{val['percentile']:.7f}" if val["percentile"] is not None else "N/A"
            print(f"  {cat:14s}: merit={val['merit_no']:>7,}  pct={pct}")
        rest = len(r["cutoffs"]) - 6
        if rest > 0:
            print(f"  ... +{rest} more categories")
        print("---------------------")
 
 
if __name__ == "__main__":
    main()
