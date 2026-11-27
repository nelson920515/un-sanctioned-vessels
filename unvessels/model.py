"""The vessel record and the feed files written from it.

feed/vessels.tsv and feed/status.txt are read by the Excel screening tool
(VBA, Windows and Mac), so they stay deliberately simple: UTF-8, LF line
endings, one header row, tab-separated, no tabs or line breaks inside a field,
and a CRC-32 of the TSV bytes in status.txt. feed/vessels.json carries the same
records for other consumers (such as PRAECERNO).
"""
from __future__ import annotations

import dataclasses
import json
import zlib
from pathlib import Path

HEADERS = [
    "DATAID", "SOURCE", "REGIME", "REFERENCE", "VESSEL_NAME", "OTHER_NAMES", "IMO", "MMSI",
    "CALL_SIGN", "FLAG", "VESSEL_TYPE", "DESIGNATED_ON", "MEASURES", "OTHER_INFORMATION",
    "LINKED_ENTITIES", "LISTING_EXPIRES", "SOURCE_URL",
]

SRC_1718 = "1718 Designated Vessels List"
SRC_CONSOLIDATED = "UN Consolidated List"
URL_1718 = "https://main.un.org/securitycouncil/sites/default/files/1718_designated_vessels_list_final.pdf"
URL_1718_PAGE = "https://main.un.org/securitycouncil/en/sanctions/1718/materials/1718-Designated-Vessels-List"
URL_CONSOLIDATED = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"

FEED_FORMAT = "1"


@dataclasses.dataclass
class Vessel:
    dataid: str
    source: str
    regime: str
    reference: str
    name: str
    other_names: list[str] = dataclasses.field(default_factory=list)
    imo: str = ""
    mmsi: list[str] = dataclasses.field(default_factory=list)
    call_sign: list[str] = dataclasses.field(default_factory=list)
    flag: list[str] = dataclasses.field(default_factory=list)
    vessel_type: str = ""
    designated_on: str = ""          # ISO yyyy-mm-dd
    measures: list[str] = dataclasses.field(default_factory=list)
    other_information: str = ""
    linked_entities: list[str] = dataclasses.field(default_factory=list)
    listing_expires: str = ""        # ISO yyyy-mm-dd, Libya listings only
    source_url: str = ""
    list_row: int | None = None      # row number on the 1718 PDF

    def row(self) -> list[str]:
        return [
            self.dataid, self.source, self.regime, self.reference, self.name,
            ";".join(self.other_names), self.imo, ";".join(self.mmsi), ";".join(self.call_sign),
            ";".join(self.flag), self.vessel_type, self.designated_on, ";".join(self.measures),
            self.other_information, ";".join(self.linked_entities), self.listing_expires, self.source_url,
        ]

    def as_json(self) -> dict:
        d = dataclasses.asdict(self)
        return d


def imo_check_digit_ok(imo: str) -> bool:
    """Ship IMO numbers carry a check digit; company numbers do not."""
    if len(imo) != 7 or not imo.isdigit():
        return False
    return sum(int(imo[i]) * (7 - i) for i in range(6)) % 10 == int(imo[6])


def clean_field(s: str) -> str:
    """One line, no tabs: the TSV contract with the Excel tool."""
    return " ".join(str(s).replace("\t", " ").split())


def dataid_1718(imo: str, name: str) -> str:
    """Stable across list reissues: the IMO number, not the row number."""
    if imo:
        return f"V1718-{imo}"
    slug = "".join(ch for ch in name.upper() if ch.isalnum())
    return f"V1718-NOIMO-{slug}"


def tsv_bytes(vessels: list[Vessel]) -> bytes:
    lines = ["\t".join(HEADERS)]
    for v in vessels:
        cells = [clean_field(c) for c in v.row()]
        assert len(cells) == len(HEADERS)
        lines.append("\t".join(cells))
    return ("\n".join(lines) + "\n").encode("utf-8")


def crc32_hex(data: bytes) -> str:
    return f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"


def write_feed(outdir: Path, vessels: list[Vessel], status: dict[str, str], meta: dict) -> None:
    """Writes vessels.tsv, vessels.json and status.txt. status must already
    hold everything except the checksum and counts, which are filled in here."""
    outdir.mkdir(parents=True, exist_ok=True)
    tsv = tsv_bytes(vessels)
    (outdir / "vessels.tsv").write_bytes(tsv)
    doc = {"meta": meta, "vessels": [v.as_json() for v in vessels]}
    (outdir / "vessels.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    status = dict(status)
    status["format"] = FEED_FORMAT
    status["count"] = str(len(vessels))
    status["count_1718"] = str(sum(1 for v in vessels if v.source == SRC_1718))
    status["tsv_crc32"] = crc32_hex(tsv)
    status["tsv_bytes"] = str(len(tsv))
    write_status(outdir / "status.txt", status)


STATUS_ORDER = [
    "format", "version", "list_date_1718", "count", "count_1718", "tsv_crc32", "tsv_bytes",
    "pdf_sha256", "consolidated_date", "checked_at", "pending", "pending_note", "pending_url",
]


def write_status(path: Path, status: dict[str, str]) -> None:
    keys = [k for k in STATUS_ORDER if k in status] + sorted(k for k in status if k not in STATUS_ORDER)
    lines = ["# UN-sanctioned vessels feed. key=value; read by the Excel screening tool."]
    lines += [f"{k}={clean_field(status[k])}" for k in keys]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def read_status(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip().lower()] = v.strip()
    return out


def read_feed_json(path: Path) -> tuple[dict, list[Vessel]]:
    if not path.exists():
        return {}, []
    doc = json.loads(path.read_text(encoding="utf-8"))
    names = {f.name for f in dataclasses.fields(Vessel)}
    return doc.get("meta", {}), [Vessel(**{k: v for k, v in d.items() if k in names}) for d in doc.get("vessels", [])]
