from pathlib import Path

import pytest

from unvessels import pdf1718
from unvessels.model import imo_check_digit_ok

FIX = Path(__file__).parent / "fixtures" / "1718_designated_vessels_list_final.pdf"


@pytest.fixture(scope="module")
def parsed():
    return pdf1718.parse(FIX.read_bytes())


def test_count_and_order(parsed):
    vs, meta = parsed
    assert len(vs) == 59
    assert [v.list_row for v in vs] == list(range(1, 60))
    assert meta["list_date"] == "2020-06-04"


def test_every_imo_passes_check_digit(parsed):
    vs, _ = parsed
    assert all(imo_check_digit_ok(v.imo) for v in vs if v.imo)
    assert [v.name for v in vs if not v.imo] == ["MIN NING DE YOU 078"]


def test_known_rows(parsed):
    vs, _ = parsed
    by = {v.name: v for v in vs}
    v = by["Blue Nouvelle"]
    assert v.other_names == ["Greenlight", "Chong Bong"]
    assert v.imo == "8909575"
    assert v.mmsi == ["529734000", "515721000", "445114000", "445528000"]
    assert v.call_sign == ["T3NM2", "XUJX6", "HMXW"]
    assert v.flag[0] == "Kiribati" and v.flag[1] == "Cambodia" and v.flag[2].startswith("Democratic People")
    assert v.vessel_type == "General Cargo Ship"
    assert by["PETREL 8"].imo == "9562233" and by["PETREL 8"].mmsi == ["620233000"]
    assert by["PETREL 8"].designated_on == "2017-10-03"
    assert by["JIN HYE"].imo == "8518572"
    assert by["Mi Rim 2"].imo == "9361407"
    assert by["Jang Ja San Chong Nyon Ho"].other_names == ["Songjin", "Song Jin", "Hongwon 88"]
    assert by["Rak Won 2"].other_names == ["Se Pho", "Buryong3"]


def test_second_designation_on_details_row(parsed):
    """HAO FAN 6: port entry ban 2017-10-03, then de-flagging 2018-03-30."""
    vs, _ = parsed
    v = next(x for x in vs if x.name == "HAO FAN 6")
    assert v.designated_on == "2017-10-03"
    assert len(v.measures) == 2
    assert v.measures[0].startswith("Port entry ban") and v.measures[0].endswith("2017-10-03")
    assert v.measures[1].startswith("De-flagging") and v.measures[1].endswith("2018-03-30")


def test_owner_field(parsed):
    vs, _ = parsed
    v = next(x for x in vs if x.name == "HAP JANG GANG 6")
    assert v.linked_entities == ["KPe.058 Hapjanggang Shipping Corp"]


def test_stable_ids(parsed):
    vs, _ = parsed
    assert vs[0].dataid == "V1718-7303803"
    assert next(v for v in vs if not v.imo).dataid == "V1718-NOIMO-MINNINGDEYOU078"
    assert len({v.dataid for v in vs}) == 59


def test_rejects_non_pdf():
    with pytest.raises(Exception):
        pdf1718.parse(b"<html>challenge</html>")
