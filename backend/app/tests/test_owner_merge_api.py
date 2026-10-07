import sqlite3
import threading

from app import seed
from app.db import connect

def _list_items(client):
    return client.get("/api/items").json()

def _owner_map(client):
    """absorbed-name -> canonical, from GET /api/owners."""
    m = {}
    for o in client.get("/api/owners").json()["owners"]:
        for a in o.get("absorbed", []):
            m[a] = o["name"]
    return m

# ---------- owners overview ----------

def test_owners_headcount_excludes_ownerless(client):
    data = client.get("/api/owners").json()
    names = {o["name"] for o in data["owners"]}
    assert data["count"] == 3
    assert names == {"老周", "小陈", "阿强"}
    assert "" not in names
    qiang = next(o for o in data["owners"] if o["name"] == "阿强")
    assert qiang["on_loan_count"] == 1 and qiang["available_count"] == 0

def test_board_filter_keeps_global_counts(client):
    r = client.get("/api/board", params={"owner": "老周"}).json()
    assert len(r["available"]) == 1 and r["available"][0]["owner"] == "老周"
    assert r["counts"]["available"] == 3  # ownerless + 老周 + 小陈
    assert set(r["owners"]) == {"老周", "小陈", "阿强"}

# ---------- keep rule ----------

def test_merge_keep_preserves_signed_old_word(client):
    client.post("/api/items", json={"title": "电钻甲", "owner": "李四"})
    client.post("/api/items", json={"title": "电钻乙", "owner": "李四家"})
    drill = next(i for i in _list_items(client) if i["title"] == "电钻甲")
    client.post(f"/api/items/{drill['id']}/lend", json={"borrower": "邻居甲", "due_date": "2030-01-01"})

    r = client.post("/api/owners/merge", json={"old_name": "李四", "new_name": "李四家", "attribution": "keep"})
    assert r.status_code == 200

    owners = {i["owner"] for i in _list_items(client)}
    assert "李四" not in owners
    active = client.get("/api/loans").json()["active"]
    loan = next(l for l in l_for(active, "电钻甲"))
    assert loan["owner"] == "李四家" and loan["owner_signed"] == "李四"  # 在借行保留旧字

    # filter follows the household, not the snapshot string
    assert any(l["title"] == "电钻甲" for l in client.get("/api/board", params={"owner": "李四家"}).json()["active"])
    assert any(l["title"] == "电钻甲" for l in client.get("/api/board", params={"owner": "李四"}).json()["active"])

def l_for(rows, title):
    return [l for l in rows if l["title"] == title]

def test_merge_keep_backfills_null_legacy_snapshot(client):
    # Seed loan (item 4 / 阿强) has owner_signed NULL.
    r = client.post("/api/owners/merge", json={"old_name": "阿强", "new_name": "阿强家", "attribution": "keep"})
    assert r.status_code == 200
    overdue = client.get("/api/loans").json()["overdue"]
    loan = next(l for l in overdue if l["title"] == "已外借样例")
    assert loan["owner"] == "阿强家" and loan["owner_signed"] == "阿强"

# ---------- rewrite rule + due_date immutability ----------

def test_merge_rewrite_and_due_date_untouched(client):
    client.post("/api/items", json={"title": "电钻", "owner": "李四"})
    drill = next(i for i in _list_items(client) if i["title"] == "电钻" and i["owner"] == "李四")
    client.post(f"/api/items/{drill['id']}/lend", json={"borrower": "甲", "due_date": "2030-03-15"})
    before = [(l["id"], l["due_date"]) for l in _all_loans(client)]

    r = client.post("/api/owners/merge", json={"old_name": "李四", "new_name": "李四家", "attribution": "rewrite"})
    assert r.status_code == 200
    after = [(l["id"], l["due_date"]) for l in _all_loans(client)]
    assert before == after  # 全表 due_date 逐行不变

    active = client.get("/api/loans").json()["active"]
    loan = next(l for l in active if l["title"] == "电钻")
    assert loan["owner_signed"] == "李四家"

def test_merge_never_touches_returned_loans(client):
    client.post("/api/items", json={"title": "旧桌", "owner": "小赵"})
    table = next(i for i in _list_items(client) if i["title"] == "旧桌")
    lend = client.post(f"/api/items/{table['id']}/lend", json={"borrower": "乙", "due_date": "2030-01-01"}).json()
    client.post(f"/api/loans/{lend['loan_id']}/return", json={})
    client.post("/api/owners/merge", json={"old_name": "小赵", "new_name": "赵家", "attribution": "rewrite"})
    returned = client.get("/api/loans").json()["returned"]
    loan = next(l for l in returned if l["title"] == "旧桌")
    assert loan["owner"] == "赵家" and loan["owner_signed"] == "小赵"  # 历史署名冻结

def _all_loans(client):
    d = client.get("/api/loans").json()
    return d["active"] + d["overdue"] + d["returned"]

# ---------- listing after merge ----------

def test_listing_under_absorbed_name_fails(client):
    client.post("/api/items", json={"title": "x", "owner": "李四"})
    client.post("/api/owners/merge", json={"old_name": "李四", "new_name": "李四家", "attribution": "keep"})
    n = len(_list_items(client))

    r = client.post("/api/items", json={"title": "电钻新", "owner": "李四"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "name_merged"
    assert r.json()["detail"]["canonical"] == "李四家"
    assert len(_list_items(client)) == n  # nothing created

    assert client.post("/api/items", json={"title": "y", "owner": "李四家"}).status_code == 200
    assert client.post("/api/items", json={"title": "无主新", "owner": ""}).status_code == 200

# ---------- failed merge rollback ----------

def test_failed_merge_rolls_back(client):
    client.post("/api/items", json={"title": "x", "owner": "李四"})
    client.post("/api/owners/merge", json={"old_name": "李四", "new_name": "李四家", "attribution": "keep"})

    owners_before = client.get("/api/owners").json()
    items_before = _list_items(client)
    merges_before = _merges_table()

    bad = [
        ("李四家", "李四家", "keep"),                 # same name
        ("不存在", "李四家", "keep"),                # old missing
        ("李四", "王五", "keep"),                    # old already absorbed
        ("李四家", "李四", "keep"),                  # new is absorbed (cycle)
        ("", "李四家", "keep"),                      # empty
        ("李四家", "王户", "bad"),                   # bad attribution
    ]
    for old, new, attr in bad:
        r = client.post("/api/owners/merge", json={"old_name": old, "new_name": new, "attribution": attr})
        assert r.status_code in (400, 409, 422), (old, new, r.status_code, r.text)
        assert client.get("/api/owners").json() == owners_before
        assert _list_items(client) == items_before
        assert _merges_table() == merges_before

def _merges_table():
    c = connect()
    rows = [tuple(r) for r in c.execute("SELECT absorbed,canonical,attribution FROM owner_merges")]
    c.close()
    return sorted(rows)

# ---------- chains / flattening ----------

def test_merge_chain_flattens_and_reverse_rejected(client):
    client.post("/api/items", json={"title": "a", "owner": "李四"})
    client.post("/api/items", json={"title": "b", "owner": "李四家"})
    client.post("/api/owners/merge", json={"old_name": "李四", "new_name": "李四家", "attribution": "keep"})
    # merge into a brand-new canonical name
    r = client.post("/api/owners/merge", json={"old_name": "李四家", "new_name": "李四家族", "attribution": "rewrite"})
    assert r.status_code == 200

    data = client.get("/api/owners").json()
    # seed 3 households + 李四/李四家; two merges leave 老周,小陈,阿强,李四家族
    assert data["count"] == 4
    fam = next(o for o in data["owners"] if o["name"] == "李四家族")
    assert set(fam["absorbed"]) == {"李四", "李四家"}
    assert {i["owner"] for i in _list_items(client) if i["title"] in ("a", "b")} == {"李四家族"}

    r = client.post("/api/owners/merge", json={"old_name": "李四家族", "new_name": "李四", "attribution": "keep"})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "canonical_is_absorbed"

def test_merge_two_existing_households(client):
    r = client.post("/api/owners/merge", json={"old_name": "老周", "new_name": "小陈", "attribution": "keep"})
    assert r.status_code == 200
    data = client.get("/api/owners").json()
    chen = next(o for o in data["owners"] if o["name"] == "小陈")
    assert "老周" in chen["absorbed"]
    assert all(i["owner"] != "老周" for i in _list_items(client))

# ---------- dirty ownerless seed ----------

def test_ownerless_dirty_seed_never_merges(client):
    # empty owner is rejected on both sides
    r1 = client.post("/api/owners/merge", json={"old_name": "", "new_name": "小陈", "attribution": "keep"})
    r2 = client.post("/api/owners/merge", json={"old_name": "小陈", "new_name": "", "attribution": "keep"})
    assert r1.status_code in (400, 422) and r2.status_code in (400, 422)
    dirty = [i for i in _list_items(client) if i["data_quality"] == "dirty"]
    assert len(dirty) == 1 and dirty[0]["owner"] == ""
    names = {o["name"] for o in client.get("/api/owners").json()["owners"]}
    assert "" not in names

# ---------- migration ----------

def test_init_db_idempotent(client):
    seed.init_db()
    seed.init_db()
    assert client.get("/api/health").json()["ok"]

def test_migration_from_legacy_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.db import db_path
    legacy = sqlite3.connect(db_path())
    legacy.executescript("""
    CREATE TABLE items(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, owner TEXT, status TEXT, data_quality TEXT);
    CREATE TABLE loans(id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, borrower TEXT, status TEXT,
      due_date TEXT, lent_at TEXT, returned_at TEXT);
    CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT);
    """)
    legacy.execute("INSERT INTO items VALUES (1,'老物','老户','available','clean')")
    legacy.commit(); legacy.close()

    seed.init_db()  # should ADD COLUMN + create table/index without reseed

    c = sqlite3.connect(db_path())
    cols = {r[1] for r in c.execute("PRAGMA table_info(loans)")}
    assert "owner_signed" in cols
    n_idx = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='loans_one_active'").fetchone()[0]
    assert n_idx == 1
    row = c.execute("SELECT title,owner FROM items").fetchone()
    assert row == ("老物", "老户")  # data intact, seed not replayed
    c.close()

# ---------- concurrency ----------

def test_concurrent_merge_list_lend(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        rounds = 12
        for n in range(rounds):
            old, new = f"李四{n}", f"李四家{n}"
            drill = client.post("/api/items", json={"title": f"电钻{n}", "owner": old}).json()["id"]
            results = {}
            barrier = threading.Barrier(3)

            def do_merge():
                barrier.wait()
                results["m"] = client.post("/api/owners/merge",
                                           json={"old_name": old, "new_name": new, "attribution": "keep"}).status_code

            def do_list():
                barrier.wait()
                results["l"] = client.post("/api/items", json={"title": f"电钻二{n}", "owner": old}).status_code

            def do_lend():
                barrier.wait()
                results["b"] = client.post(f"/api/items/{drill}/lend",
                                           json={"borrower": "邻", "due_date": "2030-01-01"}).status_code

            ts = [threading.Thread(target=fn) for fn in (do_merge, do_list, do_lend)]
            for t in ts: t.start()
            for t in ts: t.join()

            assert results["m"] == 200 and results["b"] == 200
            assert results["l"] in (200, 409)

            aliases = _owner_map(client)
            items = _list_items(client)
            assert not any(i["owner"] == old for i in items), "旧户不得残留在 items.owner"
            by_id = {i["id"]: i for i in items}
            for l in _all_loans(client):
                # signed label must resolve to the item's canonical household
                signed = l["owner_signed"] or l["owner"]
                canon = signed
                while canon in aliases:
                    canon = aliases[canon]
                assert canon == by_id[l["item_id"]]["owner"], (signed, by_id[l["item_id"]]["owner"])
                if by_id[l["item_id"]]["status"] == "on_loan":
                    assert l["status"] == "active"

def test_concurrent_double_lend_one_winner(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        iid = client.post("/api/items", json={"title": "唯一钻", "owner": "老周"}).json()["id"]
        statuses = []
        barrier = threading.Barrier(2)

        def lend():
            barrier.wait()
            statuses.append(client.post(f"/api/items/{iid}/lend",
                                        json={"borrower": "x", "due_date": "2030-01-01"}).status_code)

        ts = [threading.Thread(target=lend), threading.Thread(target=lend)]
        for t in ts: t.start()
        for t in ts: t.join()
        assert sorted(statuses) == [200, 409]
        c = connect()
        n = c.execute("SELECT COUNT(*) FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()[0]
        st = c.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()[0]
        c.close()
        assert n == 1 and st == "on_loan"
