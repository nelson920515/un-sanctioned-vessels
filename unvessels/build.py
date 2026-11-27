"""Builds feed/ from the 1718 PDF and the UN Consolidated List.

    python -m unvessels.build                 daily run (GitHub Actions)
    python -m unvessels.build --pdf FILE      a newly uploaded 1718 PDF
    python -m unvessels.build --offline       no network: re-parse the stored sources

Nothing is published unless every check passes. A 1718 list that loses
vessels or shrinks is published only from a PDF a person uploaded (they chose
it); from an automatic download it is held for review. Writes out/summary.md
for the job summary and out/issue.md when someone needs to act.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from . import consolidated, pdf1718, press
from .model import (SRC_1718, URL_1718, URL_1718_PAGE, URL_CONSOLIDATED, Vessel, imo_check_digit_ok,
                    read_feed_json, read_status, tsv_bytes, write_feed)

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / "feed"
SOURCES = ROOT / "sources"
PDF_CURRENT = SOURCES / "1718" / "current.pdf"
PDF_ARCHIVE = SOURCES / "1718" / "archive"
XML_CURRENT = SOURCES / "consolidated" / "consolidated-entities.json"
STATE = ROOT / "state" / "press.json"
OUT = ROOT / "out"
UA = press.UA


class GateError(Exception):
    pass


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch(url: str, timeout: int = 120) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""


# ---------------------------------------------------------------- checks
def check_1718(vs: list[Vessel]) -> None:
    if len(vs) < 1:
        raise GateError("the 1718 list parsed to no vessels")
    imos = [v.imo for v in vs if v.imo]
    bad = [f"{v.name} {v.imo}" for v in vs if v.imo and not imo_check_digit_ok(v.imo)]
    if bad:
        raise GateError(f"IMO numbers failing the check digit (misread?): {bad}")
    if len(set(imos)) != len(imos):
        raise GateError("the same IMO number appears twice on the 1718 list")
    for v in vs:
        if not v.name or not v.designated_on or not v.measures:
            raise GateError(f"1718 row {v.list_row} is incomplete")


def diff(old: list[Vessel], new: list[Vessel]) -> dict:
    o = {v.dataid: v for v in old}
    n = {v.dataid: v for v in new}
    changed = [k for k in n if k in o and o[k].row() != n[k].row()]
    return {
        "added": [n[k] for k in n if k not in o],
        "removed": [o[k] for k in o if k not in n],
        "changed": [n[k] for k in changed],
    }


# ---------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, help="a 1718 Designated Vessels List PDF uploaded by a person")
    ap.add_argument("--offline", action="store_true", help="no network access")
    ap.add_argument("--xml", type=Path, help="use this Consolidated List XML instead of downloading it")
    ap.add_argument("--clear-pending", action="store_true", help="the flagged press release does not change the vessel list")
    args = ap.parse_args(argv)

    OUT.mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    summary: list[str] = [f"# UN-sanctioned vessels feed - {now.isoformat()}", ""]
    issue: list[str] = []

    prev_meta, prev = read_feed_json(FEED / "vessels.json")
    prev_status = read_status(FEED / "status.txt")
    prev_1718 = [v for v in prev if v.source == SRC_1718]
    prev_cons = [v for v in prev if v.source != SRC_1718]

    # ---- 1718 Designated Vessels List
    pdf_bytes, origin = None, None
    if args.pdf:
        pdf_bytes, origin = args.pdf.read_bytes(), "uploaded"
        summary.append(f"- 1718 PDF: uploaded file `{args.pdf.name}`")
    elif not args.offline:
        code, body = fetch(URL_1718)
        if code == 200 and body.startswith(b"%PDF"):
            pdf_bytes, origin = body, "downloaded"
            summary.append("- 1718 PDF: downloaded from the UN site")
        else:
            summary.append(f"- 1718 PDF: the UN site refused an automatic download (HTTP {code}); the stored copy is used")
    stored = PDF_CURRENT.read_bytes() if PDF_CURRENT.exists() else None
    if pdf_bytes is not None and stored is not None and sha256(pdf_bytes) == sha256(stored):
        summary.append("- 1718 PDF: identical to the stored copy")
        pdf_bytes, origin = None, None
    use_bytes = pdf_bytes if pdf_bytes is not None else stored
    if use_bytes is None:
        raise SystemExit("no 1718 PDF: upload one to sources/1718/incoming/")

    try:
        v1718, pdf_meta = pdf1718.parse(use_bytes)
        check_1718(v1718)
    except (pdf1718.ParseError, GateError) as e:
        if pdf_bytes is None:
            raise
        # A new PDF that cannot be read: keep everything as it is.
        issue += ["## The new 1718 Designated Vessels List PDF could not be read", "",
                  f"Reason: {e}", "", "The feed still serves the previous list. The parser needs updating for the new layout."]
        summary.append(f"- **1718 PDF rejected**: {e}")
        v1718, pdf_meta = pdf1718.parse(stored)
        pdf_bytes, origin = None, None

    d1718 = diff(prev_1718, v1718)
    if pdf_bytes is not None and prev_1718:
        shrink = len(v1718) < len(prev_1718) * 0.9
        if (d1718["removed"] or shrink) and origin != "uploaded":
            issue += ["## 1718 list change held for review", "",
                      f"The automatically downloaded PDF removes {len(d1718['removed'])} vessel(s): "
                      + ", ".join(f"{v.name} (IMO {v.imo or 'none'})" for v in d1718["removed"]), "",
                      "Nothing was published. If the removal is right, upload the PDF to `sources/1718/incoming/`."]
            summary.append("- **1718 change held**: vessels would be removed by an automatic download")
            v1718, pdf_meta = pdf1718.parse(stored)
            pdf_bytes, origin = None, None
            d1718 = diff(prev_1718, v1718)

    # ---- UN Consolidated List (Libya vessels, owner links)
    cons_date = prev_status.get("consolidated_date", "")
    entities = None
    try:
        if args.xml:
            xml_bytes = args.xml.read_bytes()
        elif args.offline:
            xml_bytes = None
        else:
            code, xml_bytes = fetch(URL_CONSOLIDATED)
            if code != 200:
                raise RuntimeError(f"HTTP {code}")
        if xml_bytes is not None:
            cons_date, entities = consolidated.parse_entities(xml_bytes)
    except Exception as e:  # keep the previous Consolidated List vessels
        summary.append(f"- Consolidated List: could not be read ({e}); previous vessels kept")
    if entities is not None:
        vcons = consolidated.vessels(entities)
        consolidated.link_owners(v1718, entities)
        SOURCES.joinpath("consolidated").mkdir(parents=True, exist_ok=True)
        links = {v.dataid: v.linked_entities for v in v1718}
        XML_CURRENT.write_text(json.dumps({"dateGenerated": cons_date, "owner_links": links}, indent=1) + "\n", encoding="utf-8", newline="\n")
        summary.append(f"- Consolidated List of {cons_date}: {len(vcons)} vessel(s): " + ", ".join(f"{v.name} ({v.reference})" for v in vcons))
    else:
        vcons = prev_cons
        if XML_CURRENT.exists():
            links = json.loads(XML_CURRENT.read_text(encoding="utf-8")).get("owner_links", {})
            for v in v1718:
                for x in links.get(v.dataid, []):
                    if x not in v.linked_entities:
                        v.linked_entities.append(x)

    vessels = v1718 + sorted(vcons, key=lambda v: v.reference)

    # ---- press releases: a 1718 vessel change announced but not yet in the PDF
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"last_seen": 0, "flagged": []}
    if origin == "uploaded" or args.clear_pending:
        if state.get("flagged"):
            summary.append("- Pending 1718 announcements cleared: " + ", ".join(f"SC/{f['number']}" for f in state["flagged"]))
        state["flagged"] = []
    if not args.offline:
        try:
            newest, flagged = press.check(int(state.get("last_seen", 0)))
            state["last_seen"] = max(newest, int(state.get("last_seen", 0)))
            for f in flagged:
                if all(f["number"] != g["number"] for g in state["flagged"]):
                    state["flagged"].append({**f, "found": now.date().isoformat()})
                    issue += [f"## UN announced a 1718 vessel change: SC/{f['number']}", "",
                              f"[{f['title']}]({f['url']})", "",
                              f"IMO numbers mentioned: {', '.join(f['imos']) or 'none'}", "",
                              "The 1718 Designated Vessels List PDF has to be refreshed by hand (the UN site blocks automatic downloads):",
                              f"1. Open {URL_1718_PAGE} in a browser and download the PDF.",
                              "2. Upload it to `sources/1718/incoming/` in this repository (Add file > Upload files).",
                              "The build then parses, checks and publishes it, and the Excel tool loads it on its next refresh.",
                              "If this release does not change the vessel list, run the workflow with *clear pending*.", ""]
            summary.append(f"- Press releases checked up to SC/{state['last_seen']}; {len(flagged)} new 1718 vessel announcement(s)")
        except Exception as e:
            summary.append(f"- Press releases could not be checked: {e}")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8", newline="\n")

    pending = state.get("flagged", [])
    first = min(pending, key=lambda f: f["number"]) if pending else None

    # ---- write the feed
    tsv = tsv_bytes(vessels)
    version = f"{pdf_meta['list_date']}-{sha256(tsv)[:12]}"
    status = {
        "version": version,
        "list_date_1718": pdf_meta["list_date"],
        "pdf_sha256": sha256(pdf_bytes if pdf_bytes is not None else stored),
        "consolidated_date": cons_date,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "pending": "1" if pending else "0",
        "pending_note": (f"UN announced a 1718 vessel change (press release SC/{first['number']}, found "
                         f"{dt.date.fromisoformat(first['found']).strftime('%d-%b-%Y')}): vessel list update pending") if first else "",
        "pending_url": first["url"] if first else "",
    }
    meta = {"generated": status["checked_at"], "version": version, "list_date_1718": pdf_meta["list_date"],
            "consolidated_date": cons_date, "sources": {"1718": URL_1718, "consolidated": URL_CONSOLIDATED},
            "pending": pending}
    changed = version != prev_status.get("version")
    write_feed(FEED, vessels, status, meta)

    if pdf_bytes is not None:
        PDF_ARCHIVE.mkdir(parents=True, exist_ok=True)
        if stored is not None:
            old_date = prev_status.get("list_date_1718", "previous")
            shutil.copyfile(PDF_CURRENT, PDF_ARCHIVE / f"{old_date}-{sha256(stored)[:8]}.pdf")
        PDF_CURRENT.parent.mkdir(parents=True, exist_ok=True)
        PDF_CURRENT.write_bytes(pdf_bytes)
    if args.pdf and args.pdf.parent.name == "incoming":
        args.pdf.unlink()

    dall = diff(prev, vessels)
    if changed and prev:
        lines = [f"## {now.date().isoformat()} - version {version}", ""]
        for label in ("added", "removed", "changed"):
            for v in dall[label]:
                lines.append(f"- {label}: {v.name} ({v.source}, {v.reference}, IMO {v.imo or 'none'})")
        log = FEED / "CHANGELOG.md"
        old = log.read_text(encoding="utf-8") if log.exists() else "# Changes to the vessel feed\n\n"
        head, _, rest = old.partition("\n\n")
        log.write_text(head + "\n\n" + "\n".join(lines) + "\n\n" + rest, encoding="utf-8", newline="\n")
    summary += ["", f"**{len(vessels)} vessels** ({len(v1718)} on the 1718 list of {pdf_meta['list_date']}, "
                f"{len(vcons)} in the Consolidated List). Version `{version}`" + (" (new)" if changed else " (unchanged)") + ".",
                "", "Changes: " + (", ".join(f"{k} {len(v)}" for k, v in dall.items()) if prev else "first build")]
    if pending:
        summary.append("")
        summary.append("**Pending**: " + status["pending_note"])
    (OUT / "summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")
    (OUT / "issue.md").write_text("\n".join(issue) + "\n" if issue else "", encoding="utf-8")
    print("\n".join(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
