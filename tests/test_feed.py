import zlib
from pathlib import Path

from unvessels import consolidated, press
from unvessels.build import GateError, check_1718, diff
from unvessels.model import HEADERS, Vessel, crc32_hex, imo_check_digit_ok, read_status, tsv_bytes, write_feed

XML = b"""<?xml version="1.0"?><CONSOLIDATED_LIST dateGenerated="2026-11-27T00:00:00Z"><ENTITIES>%s</ENTITIES></CONSOLIDATED_LIST>"""


def ent(dataid, ref, name, regime, comments, alias=""):
    return (f"<ENTITY><DATAID>{dataid}</DATAID><FIRST_NAME>{name}</FIRST_NAME><UN_LIST_TYPE>{regime}</UN_LIST_TYPE>"
            f"<REFERENCE_NUMBER>{ref}</REFERENCE_NUMBER><LISTED_ON>2026-11-24</LISTED_ON><COMMENTS1>{comments}</COMMENTS1>"
            f"<ENTITY_ALIAS><ALIAS_NAME>{alias}</ALIAS_NAME></ENTITY_ALIAS></ENTITY>")


def sample_xml(extra=""):
    body = ent("6909750", "LYe.007", "Aegean", "Libya",
               "IMO: 8027705\nListed pursuant to paragraphs 10(a) of resolution 2146 (2014) (prohibition to load, transport or discharge; "
               "prohibition to enter ports). Pursuant to paragraph 11, this designation is valid from 24 November 2026 to 24 November 2027, "
               "unless terminated earlier. Flag State: Unknown.")
    body += ent("6908670", "KPe.056", "CHONMYONG SHIPPING CO", "DPRK",
                "Registered owner of CHON MYONG 1, a DPRK-flagged vessel. IMO number: 5571322.")
    for i in range(60):
        body += ent(str(7000000 + i), f"XXe.{i:03d}", f"FILLER {i}", "Iraq", "")
    return (XML % (body + extra).encode())


def test_check_digit():
    assert imo_check_digit_ok("9562233") and imo_check_digit_ok("8027705")
    assert not imo_check_digit_ok("5571322") and not imo_check_digit_ok("123456") and not imo_check_digit_ok("12345678")


def test_consolidated_vessels_and_links():
    date, entities = consolidated.parse_entities(sample_xml())
    assert date.startswith("2026-11-27")
    vs = consolidated.vessels(entities)
    assert len(vs) == 1
    v = vs[0]
    assert (v.dataid, v.reference, v.imo, v.listing_expires, v.flag) == ("6909750", "LYe.007", "8027705", "2027-11-24", [])
    assert v.measures == ["Prohibition to load, transport or discharge", "Prohibition to enter ports"]
    target = Vessel(dataid="V1718-8712362", source="1718 Designated Vessels List", regime="DPRK", reference="1718 list no. 6",
                    name="CHON MYONG 1", imo="8712362")
    consolidated.link_owners([target], entities)
    assert target.linked_entities == ["KPe.056 CHONMYONG SHIPPING CO"]


def test_link_needs_whole_name():
    _, entities = consolidated.parse_entities(sample_xml())
    v = Vessel(dataid="V1718-1", source="1718 Designated Vessels List", regime="DPRK", reference="r", name="CHON MYONG")
    consolidated.link_owners([v], entities)
    assert v.linked_entities == []  # "CHON MYONG 1" in the text is a different vessel name


def test_tsv_contract(tmp_path: Path):
    v = Vessel(dataid="V1718-9562233", source="1718 Designated Vessels List", regime="DPRK", reference="1718 list no. 34",
               name="PETREL 8", imo="9562233", mmsi=["620233000"], other_information="line one\nline\ttwo")
    b = tsv_bytes([v])
    lines = b.decode().split("\n")
    assert lines[0].split("\t") == HEADERS
    assert len(lines[1].split("\t")) == len(HEADERS)
    assert "line one line two" in lines[1]
    assert b.endswith(b"\n") and b"\r" not in b
    write_feed(tmp_path, [v], {"version": "x"}, {})
    st = read_status(tmp_path / "status.txt")
    assert st["format"] == "1" and st["count"] == "1"
    assert st["tsv_crc32"] == f"{zlib.crc32((tmp_path / 'vessels.tsv').read_bytes()) & 0xFFFFFFFF:08X}"
    assert crc32_hex(b"abc") == "352441C2"  # the value the Excel tool's Crc32Hex gives


def test_gates():
    good = Vessel(dataid="V1718-9562233", source="s", regime="DPRK", reference="r", name="PETREL 8", imo="9562233",
                  designated_on="2017-10-03", measures=["m"])
    check_1718([good])
    bad = Vessel(dataid="V1718-9562234", source="s", regime="DPRK", reference="r", name="X", imo="9562234",
                 designated_on="2017-10-03", measures=["m"])
    try:
        check_1718([good, bad])
        raise AssertionError("misread IMO accepted")
    except GateError:
        pass
    d = diff([good], [bad])
    assert [v.name for v in d["added"]] == ["X"] and [v.name for v in d["removed"]] == ["PETREL 8"]


def test_press_flags_only_new_vessel_releases():
    listing = ('<a href="/en/2026/sc16600.doc.htm">Security Council 1718 Sanctions Committee Designates Two Vessels</a>'
               '<a href="/en/2026/sc16599.doc.htm">Security Council 1718 Sanctions Committee Updates One Entry</a>'
               '<a href="/en/2026/sc16598.doc.htm">Security Council 1533 Committee Amends Entry</a>'
               '<a href="/en/2026/sc16519.doc.htm">Committee concerning Libya Adds Vessel</a>')
    pages = {
        press.LIST_URL: listing, press.LIST_URL + "?page=1": "", press.LIST_URL + "?page=2": "",
        "https://press.un.org/en/2026/sc16600.doc.htm": "The Committee designated the vessel SEA STAR (IMO number: 9123457) for a port entry ban.",
        "https://press.un.org/en/2026/sc16599.doc.htm": "The Committee amended the entry of an individual.",
    }
    newest, flagged = press.check(16519, fetch=lambda u: pages[u])
    assert newest == 16600
    assert [f["number"] for f in flagged] == [16600]
    assert flagged[0]["imos"] == ["9123457"]
    # first run: baseline only
    assert press.check(0, fetch=lambda u: pages[u]) == (16600, [])
