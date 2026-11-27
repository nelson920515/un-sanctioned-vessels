"""Vessels and IMO numbers inside the UN Security Council Consolidated List.

The Consolidated List XML is machine-readable and open to scripts. Ships
listed there (so far only under the Libya regime, resolution 2146) are entity
records whose COMMENTS1 starts with "IMO: nnnnnnn". DPRK shipping companies
carry their IMO company number in COMMENTS1 too; those are not vessels but
are used to link the 1718 vessels to the companies that own or manage them.
"""
from __future__ import annotations

import datetime as dt
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .model import SRC_CONSOLIDATED, URL_CONSOLIDATED, Vessel, imo_check_digit_ok

IMO_RE = re.compile(r"\bIMO\b[^0-9]{0,40}?(\d{7})(?!\d)", re.I)
MONTHS = "January February March April May June July August September October November December".split()


@dataclass
class Entity:
    dataid: str
    reference: str
    name: str
    regime: str
    listed_on: str
    comments: str
    aliases: list[str] = field(default_factory=list)
    imos: list[str] = field(default_factory=list)


def parse_entities(xml_bytes: bytes) -> tuple[str, list[Entity]]:
    root = ET.fromstring(xml_bytes)
    if root.tag != "CONSOLIDATED_LIST":
        raise ValueError("not the UN Consolidated List")
    generated = root.attrib.get("dateGenerated", "")
    out: list[Entity] = []
    for e in root.iter("ENTITY"):
        comments = (e.findtext("COMMENTS1") or "").strip()
        out.append(Entity(
            dataid=(e.findtext("DATAID") or "").strip(),
            reference=(e.findtext("REFERENCE_NUMBER") or "").strip(),
            name=(e.findtext("FIRST_NAME") or "").strip(),
            regime=(e.findtext("UN_LIST_TYPE") or "").strip(),
            listed_on=(e.findtext("LISTED_ON") or "").strip()[:10],
            comments=comments,
            aliases=[a.strip() for a in (x.text or "" for x in e.iter("ALIAS_NAME")) if a.strip()],
            imos=list(dict.fromkeys(IMO_RE.findall(comments))),
        ))
    if len(out) < 50:
        raise ValueError(f"the Consolidated List has only {len(out)} entities")
    return generated, out


def is_vessel(e: Entity) -> bool:
    """A ship listing: the comments open with its IMO number, which passes the
    ship check digit."""
    m = re.match(r"\s*IMO\s*[:#]?\s*(\d{7})(?!\d)", e.comments, re.I)
    return bool(m and imo_check_digit_ok(m.group(1)))


def _iso(day: str, month: str, year: str) -> str:
    return dt.date(int(year), MONTHS.index(month.capitalize()) + 1, int(day)).isoformat()


def vessels(entities: list[Entity]) -> list[Vessel]:
    out = []
    for e in entities:
        if not is_vessel(e):
            continue
        flag = ""
        m = re.search(r"Flag State:\s*([^.;\n]+)", e.comments)
        if m and m.group(1).strip().lower() not in ("unknown", "n/a", "na"):
            flag = m.group(1).strip()
        expires = ""
        m = re.search(r"valid from \d{1,2} \w+ \d{4} to (\d{1,2}) (\w+) (\d{4})", e.comments)
        if m:
            expires = _iso(*m.groups())
        measures = []
        m = re.search(r"Listed pursuant to (.+?\)) \((.+?)\)\.", e.comments, re.S)
        if m:
            measures = [p.strip().capitalize() for p in m.group(2).split(";") if p.strip()]
        info = re.sub(r"^\s*IMO\s*[:#]?\s*\d{7}\s*", "", e.comments).strip()
        out.append(Vessel(
            dataid=e.dataid,
            source=SRC_CONSOLIDATED,
            regime=e.regime,
            reference=e.reference,
            name=e.name,
            other_names=e.aliases,
            imo=re.match(r"\s*IMO\s*[:#]?\s*(\d{7})", e.comments, re.I).group(1),
            flag=[flag] if flag else [],
            designated_on=e.listed_on,
            measures=measures,
            other_information=info,
            listing_expires=expires,
            source_url=URL_CONSOLIDATED,
        ))
    return out


def link_owners(vessels_1718: list[Vessel], entities: list[Entity]) -> None:
    """Adds 'KPe.056 CHONMYONG SHIPPING CO' to a 1718 vessel when that UN-listed
    entity's comments name the vessel or carry its IMO number."""
    for v in vessels_1718:
        names = [v.name] + v.other_names
        # Whole name only: "Mi Rim" must not match a text about "Mi Rim 2".
        pats = [re.compile(r"(?<![A-Za-z0-9])" + re.escape(n) + r"(?![A-Za-z0-9])(?!\s+\d)", re.I) for n in names if len(n) >= 4]
        for e in entities:
            if is_vessel(e) or not e.comments:
                continue
            hit = (v.imo and v.imo in e.comments) or any(p.search(e.comments) for p in pats)
            if hit:
                label = f"{e.reference} {e.name}".strip()
                if not any(x.split(" ", 1)[0].upper() == e.reference.upper() for x in v.linked_entities):
                    v.linked_entities.append(label)
