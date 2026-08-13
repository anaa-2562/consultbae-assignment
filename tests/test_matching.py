"""The tests that actually matter: the planted identity traps."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.matching import Record, names_compatible, resolve  # noqa: E402
from src.pipeline import build  # noqa: E402


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "test.db"
    build(db, verbose=False)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def people_named(conn, name):
    return conn.execute("SELECT * FROM person WHERE full_name = ?", (name,)).fetchall()


# ---- name similarity ------------------------------------------------------
def test_initial_expands_to_full_first_name():
    assert names_compatible("R. Verma", "Rohit Verma")


def test_different_people_same_surname_are_not_compatible():
    assert not names_compatible("Rohit Verma", "Kavya Verma")


# ---- transitive union -----------------------------------------------------
def test_email_and_phone_links_are_transitive():
    recs = [
        Record("s1:2", "source1", 2, "A B", "a@x.com", "+919000000001", "Pune"),
        Record("s2:2", "source2", 2, "A B", "a@x.com", None, "Pune"),
        Record("s3:2", "source3", 2, "A B", None, "+919000000001", "Pune"),
    ]
    clusters = resolve(recs).clusters
    assert len(clusters) == 1 and len(clusters[0]) == 3


# ---- the planted traps ----------------------------------------------------
def test_abbreviated_name_duplicate_merges(conn):
    """`R. Verma` and `Rohit Verma` share an email and a phone -> one person."""
    rows = people_named(conn, "Rohit Verma")
    assert len(rows) == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM person_name_alias WHERE person_id = ?", (rows[0]["person_id"],)
    ).fetchone()[0] == 2


def test_two_mailboxes_one_human_merge_on_phone(conn):
    """`alt.nikhil.chopra70@` and `nikhil.chopra70@` share a phone."""
    rows = people_named(conn, "Nikhil Chopra")
    assert len(rows) == 1
    emails = [
        r[0] for r in conn.execute(
            "SELECT email FROM person_email WHERE person_id = ? ORDER BY email", (rows[0]["person_id"],)
        )
    ]
    assert emails == ["alt.nikhil.chopra70@example.com", "nikhil.chopra70@example.com"]
    # the non-alias address is the one promoted to primary
    assert rows[0]["primary_email"] == "nikhil.chopra70@example.com"


def test_same_name_different_city_stays_two_people(conn):
    """Two `Deepak Nair`s: Bengaluru (in all 3 sources) and Delhi (source2 only)."""
    rows = people_named(conn, "Deepak Nair")
    assert len(rows) == 2
    cities = sorted(r["city"] for r in rows)
    assert cities == ["Bengaluru", "Delhi"]


def test_ambiguous_same_name_same_city_is_not_merged(conn):
    """Three `Arjun Mehta` records in Noida with no shared identifier.

    source1+source3 link on a phone number. The source2 record and the second
    source3 record must NOT be swept into that person just because the name and
    city match - they stay separate and the block is queued for a human.
    """
    rows = people_named(conn, "Arjun Mehta")
    assert len(rows) == 3
    assert conn.execute(
        "SELECT COUNT(*) FROM match_review WHERE reason LIKE '%Arjun Mehta%'"
    ).fetchone()[0] == 1


def test_genuine_source2_to_source3_links_are_made(conn):
    """source2 has no phone and source3 has no email, so these two can only be
    joined on name+city. Where the block is unambiguous, the link IS made."""
    for name in ("Manish Bhatia", "Divya Chopra", "Karan Chopra", "Vikram Mehta"):
        row = people_named(conn, name)
        assert len(row) == 1, name
        assert row[0]["in_source2"] == 1 and row[0]["in_source3"] == 1, name
        assert row[0]["primary_email"] and row[0]["primary_phone"], name
        assert row[0]["match_confidence"] == 0.80, name


# ---- structural repairs ---------------------------------------------------
def test_repeated_header_and_blank_row_are_skipped(conn):
    skipped = conn.execute(
        "SELECT source_file, source_row, status FROM source_record WHERE status = 'skipped' ORDER BY source_file"
    ).fetchall()
    assert {(r["source_file"], r["source_row"]) for r in skipped} == {
        ("source2_gig_workers.csv", 12),
        ("source3_cbnexus_contacts.csv", 16),
    }


def test_shifted_row_is_repaired_then_recognised_as_a_duplicate(conn):
    row = conn.execute(
        "SELECT status, note FROM source_record WHERE source_file='source2_gig_workers.csv' AND source_row=20"
    ).fetchone()
    assert row["status"] == "duplicate_row"
    assert "column repair" in row["note"]
    assert conn.execute(
        "SELECT COUNT(*) FROM data_issue WHERE issue_code='COLUMN_SHIFT'"
    ).fetchone()[0] == 1


# ---- invariants -----------------------------------------------------------
def test_no_email_belongs_to_two_people(conn):
    assert conn.execute(
        "SELECT COUNT(*) FROM (SELECT email FROM person_email GROUP BY email HAVING COUNT(DISTINCT person_id) > 1)"
    ).fetchone()[0] == 0


def test_no_phone_belongs_to_two_people(conn):
    assert conn.execute(
        "SELECT COUNT(*) FROM (SELECT phone FROM person_phone GROUP BY phone HAVING COUNT(DISTINCT person_id) > 1)"
    ).fetchone()[0] == 0


def test_every_ingested_row_is_attached_to_a_person(conn):
    assert conn.execute(
        "SELECT COUNT(*) FROM source_record WHERE status='ingested' AND person_id IS NULL"
    ).fetchone()[0] == 0


def test_pipeline_is_deterministic(tmp_path):
    a = build(tmp_path / "a.db", verbose=False)
    b = build(tmp_path / "b.db", verbose=False)
    assert a == b
