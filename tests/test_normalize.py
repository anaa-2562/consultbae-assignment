import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.normalize import (  # noqa: E402
    normalize_bool,
    normalize_city,
    normalize_ctc,
    normalize_date,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_rate,
    normalize_skills,
    normalize_status,
    phone_key,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+919000000254", "+919000000254"),
        ("9000000237", "+919000000237"),
        ("09000000287", "+919000000287"),
        ("+91-9000000131", "+919000000131"),
        ("919000000231", "+919000000231"),
        (" 9000000268 ", "+919000000268"),
    ],
)
def test_phone_formats_all_collapse(raw, expected):
    assert normalize_phone(raw).value == expected


def test_phone_junk_is_rejected_not_guessed():
    f = normalize_phone("12345")
    assert f.value is None
    assert f.issues[0].code == "PHONE_UNPARSEABLE"


def test_phone_key_is_the_join_key():
    assert phone_key("+91-9000000131") == phone_key("09000000131") == "9000000131"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("GURGAON", "Gurugram"),
        ("gurugram ", "Gurugram"),
        ("bangalore", "Bengaluru"),
        ("Bengaluru", "Bengaluru"),
        ("Delhi NCR", "Delhi"),
        ("new delhi", "Delhi"),
        ("Noida ", "Noida"),
    ],
)
def test_city_aliases(raw, expected):
    assert normalize_city(raw).value == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-08-08", "2026-08-08"),
        ("24-07-2026", "2026-07-24"),      # DD-MM-YYYY
        ("07/13/2026", "2026-07-13"),      # MM/DD/YYYY, proved by day 13
        ("7 Jul 2026", "2026-07-07"),
        ("21-08-2026", "2026-08-21"),      # day 21 proves DD-MM
    ],
)
def test_date_shapes(raw, expected):
    assert normalize_date(raw).value == expected


def test_ambiguous_date_is_flagged_not_silently_picked():
    f = normalize_date("07/03/2026")
    assert f.value == "2026-07-03"
    assert any(i.code == "DATE_AMBIGUOUS" for i in f.issues)


def test_future_applied_date_is_flagged():
    f = normalize_date("22-08-2026")
    assert any(i.code == "DATE_IN_FUTURE" for i in f.issues)


def test_ctc_two_units_land_on_one_scale():
    assert normalize_ctc("4.2").value == (420_000, "lpa")
    assert normalize_ctc("417964").value == (417_964, "inr_annual")


def test_rate_units():
    hourly, monthly, unit = normalize_rate("1415/hr").value
    assert (hourly, unit) == (1415.0, "per_hour")
    assert monthly == 226_400
    hourly, monthly, unit = normalize_rate("15k/month").value
    assert (monthly, unit) == (15_000, "per_month")


def test_rate_scale_conflict_is_reported():
    f = normalize_rate("1415/hr")
    assert any(i.code == "RATE_SCALE_CONFLICT" for i in f.issues)


@pytest.mark.parametrize("raw", ["Active", "active", "ACTIVE"])
def test_status_casefold(raw):
    assert normalize_status(raw).value == "active"


@pytest.mark.parametrize("raw,expected", [("Y", True), ("yes", True), ("Yes", True), ("N", False), ("No", False)])
def test_bool_variants(raw, expected):
    assert normalize_bool(raw).value is expected


def test_unknown_bool_is_null_not_false():
    assert normalize_bool("maybe").value is None


def test_email_casefold_and_reject():
    assert normalize_email("ISHA.CHOPRA95@MAILTEST.EXAMPLE.ORG").value == "isha.chopra95@mailtest.example.org"
    assert normalize_email("react, javascript, mysql").value is None


def test_skills_vocabulary_collapses_case():
    a = normalize_skills("n8n, LangChain, REST APIs, MongoDB, SQL").value
    b = normalize_skills("n8n, langchain, rest apis, mongodb, sql").value
    assert a == b


def test_name_casing_and_titles():
    assert normalize_name("KARAN BHATIA").value == "Karan Bhatia"
    assert normalize_name("  ritu   sharma ").value == "Ritu Sharma"
