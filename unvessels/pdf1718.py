"""Parser for the UN Security Council "1718 Designated Vessels List" PDF.

The PDF is a Word table: one numbered row per vessel (names, IMO number with
any MMSI numbers, date of designation, five "Yes"/"na" sanctions-measure
columns), followed by labelled rows (Flag, Call Sign, Type, Other information,
Picture link) whose value sits in the third column. A vessel's labelled rows
can continue on the next page.

Every structural assumption is checked; anything unexpected raises
ParseError rather than producing a partial list.
"""
from __future__ import annotations

import datetime as dt
import re

import fitz  # PyMuPDF

from .model import SRC_1718, URL_1718, Vessel, dataid_1718

MEASURE_COLUMNS = [
    "Asset freeze (paragraph 8(d) of resolution 1718 (2006) and paragraph 12 of resolution 2270 (2016))",
    "De-flagging (paragraph 12(a) of resolution 2321 (2016))",
    "Asset freeze of designated vessel (paragraph 12(d) of resolution 2321 (2016))",
    "Port entry ban (paragraph 6 of resolution 2371 (2017) and paragraph 6 of resolution 2375 (2017))",
    "Asset freeze as economic resource controlled or operated by Ocean Maritime Management (KPe.020)",
]
LABELS = {
    "flag": "flag", "call sign": "call_sign", "type": "type", "other information": "info",
    "picture link": "picture", "designated as economic resources of": "owner",
}


class ParseError(Exception):
    pass


def _one_line(cell: str | None) -> str:
    return " ".join((cell or "").split())


def _numbered(text: str) -> list[str]:
    """'1: A 2: B' or '1. A 2: B' -> ['A', 'B']; plain text -> [text]."""
    text = _one_line(text)
    if not text or text.lower() == "na":
        return []
    parts = re.split(r"(?:^|\s)\d{1,2}\s*[.:]\s*", text)
    parts = [p.strip(" ;,") for p in parts if p.strip(" ;,")]
    return parts or [text]


def _date(text: str) -> str:
    text = _one_line(text)
    for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ParseError(f"unreadable date of designation {text!r}")


def _list_date(doc: fitz.Document) -> str:
    """The list's own date: '... Vessels List 4 June 2020.docx' in the title,
    else the PDF creation date."""
    title = doc.metadata.get("title") or ""
    m = re.search(r"(\d{1,2}\s+[A-Z][a-z]+\s+20\d\d)", title)
    if m:
        try:
            return dt.datetime.strptime(m.group(1), "%d %B %Y").date().isoformat()
        except ValueError:
            pass
    cd = doc.metadata.get("creationDate") or ""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", cd)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    raise ParseError("cannot tell the list's date")


def _rows(doc: fitz.Document):
    for page in doc:
        tables = page.find_tables().tables
        for t in tables:
            if t.col_count != 10:
                raise ParseError(f"page {page.number + 1}: table with {t.col_count} columns, expected 10")
            for row in t.extract():
                yield page.number + 1, [c if c is not None else "" for c in row]


def parse(pdf_bytes: bytes) -> tuple[list[Vessel], dict]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    list_date = _list_date(doc)
    vessels: list[Vessel] = []
    current: Vessel | None = None
    last_label: str | None = None
    seen_header = False

    for page_no, row in _rows(doc):
        c0, c1, c2, c3, c4 = (_one_line(x) for x in row[:5])
        measures = [_one_line(x) for x in row[5:10]]
        if c0 == "1718 Designated Vessels List" or c1 == "Vessel Name" or c0 == "#":
            seen_header = True
            continue
        if not c0 and not c1 and measures and measures[0].lower().startswith("paragraph"):
            continue  # the measures sub-header
        if c0.isdigit():
            if not seen_header:
                raise ParseError("vessel rows before the table header")
            n = int(c0)
            names = _numbered(c1)
            if not names:
                raise ParseError(f"row {n}: no vessel name")
            imo_cell = _one_line(row[3])
            m = re.match(r"(\d{7})\b", imo_cell)
            if m:
                imo = m.group(1)
            elif imo_cell.lower().startswith("does not exist"):
                imo = ""
            else:
                raise ParseError(f"row {n}: unreadable IMO number cell {imo_cell!r}")
            mmsi = re.findall(r"\b(\d{9})\b", imo_cell.split("MMSI", 1)[1]) if "MMSI" in imo_cell else []
            designated = _date(c4)
            current = Vessel(
                dataid=dataid_1718(imo, names[0]),
                source=SRC_1718,
                regime="DPRK",
                reference=f"1718 list no. {n}",
                name=names[0],
                other_names=names[1:],
                imo=imo,
                mmsi=mmsi,
                designated_on=designated,
                source_url=URL_1718,
                list_row=n,
            )
            _add_measures(current, measures, designated)
            vessels.append(current)
            last_label = None
            continue
        if current is None:
            raise ParseError(f"page {page_no}: details before the first vessel: {row}")
        if c4:
            # A later designation of the same vessel: its own date and measures
            # on a details row (e.g. HAO FAN 6: port entry ban 2017, de-flagging 2018).
            when = _date(c4)
            _add_measures(current, measures, when)
            current.designated_on = min(current.designated_on, when)
        label = LABELS.get(c1.lower())
        value = c2
        if label is None and not c1 and value:
            label = last_label  # a field continued on the next page
            if label is None:
                raise ParseError(f"page {page_no}: text without a label after row {current.list_row}: {value!r}")
            _append(current, label, value)
            continue
        if label is None:
            if not c1 and not value:
                continue
            raise ParseError(f"page {page_no}: unknown field {c1!r} after row {current.list_row}")
        _set(current, label, value)
        last_label = label

    _check(vessels)
    meta = {"list_date": list_date, "pages": doc.page_count, "title": doc.metadata.get("title") or ""}
    return vessels, meta


def _add_measures(v: Vessel, measures: list[str], when: str) -> None:
    flags = [x.lower() for x in measures]
    if any(x not in ("yes", "na") for x in flags):
        raise ParseError(f"row {v.list_row}: unexpected sanctions-measure values {measures}")
    for i, x in enumerate(flags):
        if x == "yes":
            v.measures.append(f"{MEASURE_COLUMNS[i]} - designated {when}")


def _owner(value: str) -> str:
    """'Hapjanggang Shipping Corp (Kpe.058)' -> 'KPe.058 Hapjanggang Shipping Corp'."""
    value = _one_line(value)
    m = re.search(r"\(\s*k\s*pe\s*\.?\s*(\d{3})\s*\)", value, re.I)
    if not m:
        return value
    return f"KPe.{m.group(1)} " + value[:m.start()].strip()


def _set(v: Vessel, label: str, value: str) -> None:
    if label == "owner":
        v.linked_entities.append(_owner(value))
    elif label == "flag":
        v.flag = _numbered(value)
    elif label == "call_sign":
        v.call_sign = _numbered(value)
    elif label == "type":
        v.vessel_type = "" if value.lower() == "na" else value
    elif label == "info":
        v.other_information = "" if value.lower() == "na" else value
    elif label == "picture":
        pass  # third-party photo links, broken across lines in the PDF: not kept


def _append(v: Vessel, label: str, value: str) -> None:
    if label == "owner":
        v.linked_entities[-1] = _owner(v.linked_entities[-1] + " " + value)
    elif label == "flag":
        v.flag = _numbered(" ".join(v.flag) + " " + value) if not re.match(r"\d", value) else v.flag + _numbered(value)
    elif label == "call_sign":
        v.call_sign = v.call_sign + _numbered(value)
    elif label == "type":
        v.vessel_type = (v.vessel_type + " " + value).strip()
    elif label == "info":
        v.other_information = (v.other_information + " " + value).strip()


def _check(vessels: list[Vessel]) -> None:
    if not vessels:
        raise ParseError("no vessels found")
    rows = [v.list_row for v in vessels]
    if rows != list(range(1, len(vessels) + 1)):
        raise ParseError(f"row numbers are not 1..{len(vessels)} in order: {rows}")
    ids = [v.dataid for v in vessels]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ParseError(f"duplicate vessels: {dup}")
    for v in vessels:
        if not v.measures:
            raise ParseError(f"row {v.list_row}: no sanctions measure marked")
