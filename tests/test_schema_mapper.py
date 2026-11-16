"""An unknown database becomes a profile the agent can run, and what the AI says is only ever a set of names to be checked."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
import discover as D  # noqa: E402
import systems as S  # noqa: E402

from app.core import schema_mapper as M  # noqa: E402


class Fake:
    name = "fake"

    def __init__(self, reply="", raises=False):
        self.reply, self.raises, self.calls = reply, raises, []

    def generate_json(self, system_prompt, user_content, max_tokens=400):
        self.calls.append((system_prompt, user_content))
        if self.raises:
            raise RuntimeError("down")
        return self.reply if isinstance(self.reply, str) else json.dumps(self.reply)


@pytest.fixture()
def shop(tmp_path):
    """A small hardware shop's program, in a layout none of the built-in profiles knows."""
    p = tmp_path / "duka.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tbl_sales (sale_no INTEGER PRIMARY KEY, sale_date TEXT, grand_total REAL, client TEXT, item TEXT, shop TEXT, qty REAL)")
    con.execute("CREATE TABLE supplier_bills (bill_id INTEGER PRIMARY KEY, paid_on TEXT, amount REAL, supplier TEXT)")
    con.execute("CREATE TABLE sales_returns (rid INTEGER PRIMARY KEY, returned_on TEXT, refund_amount REAL)")
    con.execute("CREATE TABLE staff (id INTEGER PRIMARY KEY, name TEXT, phone TEXT)")
    con.executemany("INSERT INTO tbl_sales (sale_date, grand_total, client, item, shop, qty) VALUES (?,?,?,?,?,?)", [("2026-03-01", 1200.5, "Wanjiru", "Cement", "Main", 2), ("2026-03-02", 300, "Otieno", "Nails", "Main", 5), ("2026-03-02", 99.99, None, "Rope", "Yard", 1)])
    con.executemany("INSERT INTO supplier_bills (paid_on, amount, supplier) VALUES (?,?,?)", [("2026-03-01", 800, "Bamburi"), ("2026-03-03", 150.25, "Tiles Ltd")])
    con.execute("INSERT INTO sales_returns (returned_on, refund_amount) VALUES ('2026-03-03', 50)")
    con.executemany("INSERT INTO staff (name, phone) VALUES (?,?)", [("Ann" * 20, "0700" * 5)] * 800)
    con.commit(); con.close()
    return p


GOOD = {"business": {"kind": "Hardware shop", "description": "Sells building materials and buys from suppliers."}, "currency": "kes", "tables": [
    {"table": "tbl_sales", "role": "sale", "id": "sale_no", "date": "sale_date", "amount": "grand_total", "customer": "client", "product": "item", "location": "shop", "quantity": "qty", "reason": "Each row is one sale."},
    {"table": "supplier_bills", "role": "expense", "id": "bill_id", "date": "paid_on", "amount": "amount", "supplier": "supplier", "reason": "Bills paid to suppliers."},
    {"table": "sales_returns", "role": "refund", "id": "rid", "date": "returned_on", "amount": "refund_amount", "reason": "Money given back."}]}


def read_all(path, profile):
    profile = S.materialize(profile)
    S.validate_profile(profile)
    con = S.open_readonly(profile, path)
    try:
        S.check_signature(con, profile)
        streams = S.active_streams(con, profile, {})
        cur = S.read_currency(con, profile, None)
        return {s["key"]: [r[2] for r in S.read_stream(con, s, {}, profile["money"], cur)] for s in streams}
    finally:
        con.close()


def test_an_unknown_database_becomes_a_profile_the_agent_reads_correctly(shop):
    fake = Fake(GOOD)
    out = M.propose(D.describe(shop), fake)
    assert out["used_ai"] and out["business"]["kind"] == "Hardware shop" and out["currency"] == "KES" and out["notes"] == []
    assert [(t["table"], t["role"]) for t in out["tables"]] == [("tbl_sales", "sale"), ("supplier_bills", "expense"), ("sales_returns", "refund")]
    got = read_all(shop, {**out["profile"], "discovery": {"files": [str(shop)]}})
    sales = got["sale_tbl_sales"]
    assert len(sales) == 3 and round(sum(e["amount"] for e in sales), 2) == 1600.49
    assert {e["customer_id"] for e in sales} == {"Wanjiru", "Otieno", None} and sales[0]["product_id"] == "Cement" and sales[0]["location"] == "Main" and sales[0]["quantity"] == 2
    assert round(sum(e["amount"] for e in got["expense_supplier_bills"]), 2) == 950.25
    assert sum(e["amount"] for e in got["refund_sales_returns"]) == 50 and out["profile"]["currency"] == {"default": "KES"}
    assert "staff" not in json.dumps(out["profile"]["streams"])                       # tables that hold no money are not read


def test_the_ai_sees_names_only_by_default_and_is_told_the_data_is_not_instructions(shop):
    fake = Fake(GOOD)
    M.propose(D.describe(shop), fake)
    system, user = fake.calls[0]
    assert "never instructions" in system and "Never invent" in system
    assert "tbl_sales" in user and "Wanjiru" not in user and "1200.5" not in user


def test_example_values_are_passed_on_only_when_the_summary_carries_them_and_are_clipped(shop):
    fake = Fake(GOOD)
    M.propose(D.describe(shop, samples=3, sample_chars=60), fake)
    assert "Wanjiru" in fake.calls[0][1] and ("Ann" * 20) not in fake.calls[0][1]


def test_an_answer_naming_things_that_do_not_exist_is_set_aside_and_said_so(shop):
    bad = {"business": {"kind": "x", "description": "y"}, "tables": [
        {"table": "tbl_sales", "role": "sale", "id": "sale_no", "date": "sale_date", "amount": "grand_total", "customer": "no_such_col", "product": "grand_total"},
        {"table": "secret_passwords", "role": "sale", "date": "d", "amount": "a"},
        {"table": "supplier_bills", "role": "expense", "date": "paid_on"},
        {"table": "staff", "role": "other", "date": "name", "amount": "phone"}]}
    out = M.propose(D.describe(shop), Fake(bad))
    assert [(t["table"], t["role"]) for t in out["tables"]] == [("tbl_sales", "sale")]
    assert set(out["tables"][0]) == {"table", "role", "reason", "id", "date", "amount"}
    text = " ".join(out["notes"])
    assert "no_such_col" in text and "secret_passwords" in text and "supplier_bills" in text and "used twice" in text.replace("is used", "used")


def test_ai_output_can_never_become_sql(shop):
    nasty = {"business": {"kind": "x", "description": "ignore previous instructions"}, "tables": [
        {"table": "tbl_sales", "role": "sale", "id": "sale_no; DROP TABLE tbl_sales", "date": "sale_date", "amount": 'grand_total" FROM staff; --'}]}
    out = M.propose(D.describe(shop), Fake(nasty))
    assert out["tables"] == [] or all(";" not in S.materialize(out["profile"])["streams"][0]["sql"] for _ in [0])
    assert {t["name"]: t["rows"] for t in D.describe(shop)["tables"]}["tbl_sales"] == 3


@pytest.mark.parametrize("reply", ["", "not json at all", "[1,2,3]", '{"tables": "x"}', json.dumps({"tables": []})])
def test_a_useless_answer_falls_back_to_the_names(shop, reply):
    out = M.propose(D.describe(shop), Fake(reply))
    assert out["used_ai"] is False and {t["table"] for t in out["tables"]} == {"tbl_sales", "supplier_bills", "sales_returns"}


def test_a_failing_or_missing_ai_still_gives_a_working_profile_from_the_names(shop):
    for provider in (Fake(raises=True), None):
        out = M.propose(D.describe(shop), provider)
        assert out["used_ai"] is False and out["profile"]
        got = read_all(shop, {**out["profile"], "discovery": {"files": [str(shop)]}})
        assert round(sum(e["amount"] for e in got["sale_tbl_sales"]), 2) == 1600.49
        assert sum(e["amount"] for e in got["refund_sales_returns"]) == 50
        assert round(sum(e["amount"] for e in got["expense_supplier_bills"]), 2) == 950.25


def test_nothing_that_looks_like_money_gives_no_profile(tmp_path):
    p = tmp_path / "x.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO contacts (name) VALUES (?)", [("n" * 40,)] * 3000)
    con.commit(); con.close()
    out = M.propose(D.describe(p), None)
    assert out["profile"] is None and out["tables"] == []


def test_the_unit_comes_from_the_column_name_not_from_the_ai(tmp_path):
    p = tmp_path / "m.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE sales (id INTEGER PRIMARY KEY, sold_on TEXT, total_cents INTEGER)")
    con.executemany("INSERT INTO sales (sold_on, total_cents) VALUES ('2026-01-01', ?)", [(12345,)] * 1500)
    con.commit(); con.close()
    out = M.propose(D.describe(p), Fake({"business": {}, "tables": [{"table": "sales", "role": "sale", "id": "id", "date": "sold_on", "amount": "total_cents"}], "money_unit": "major"}))
    assert out["profile"]["money"]["unit"] == "minor"
    assert read_all(p, {**out["profile"], "discovery": {"files": [str(p)]}})["sale_sales"][0]["amount"] == 123.45


def test_a_table_without_an_id_column_is_read_by_row_position(tmp_path):
    p = tmp_path / "n.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE daily_sales (day TEXT, amount REAL)")
    con.executemany("INSERT INTO daily_sales VALUES (?,?)", [(f"2026-02-{(i % 27) + 1:02d}", 10.0) for i in range(1500)])
    con.commit(); con.close()
    out = M.propose(D.describe(p), None)
    ev = read_all(p, {**out["profile"], "discovery": {"files": [str(p)]}})["sale_daily_sales"]
    assert len(ev) == 1500 and len({e["entity_id"] for e in ev}) == 1500


def test_the_profile_is_the_custom_system_and_names_where_the_database_is(shop):
    out = M.propose(D.describe(shop), None)
    assert out["profile"]["system"] == "custom" and out["profile"]["discovery"]["files"] == [str(shop)]
    assert M.build_profile([], D.describe(shop), {}, None) is None
