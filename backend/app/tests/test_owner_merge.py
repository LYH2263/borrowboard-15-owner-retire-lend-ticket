import os
import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.db import connect, write_tx
from app.engines.owner_merge import (
    MergeError, canonical_map, list_owners, merge_owners,
    stored_owners_are_canonical,
)
from app.main import ItemIn, LendIn, MergeIn, add_item, app, lend, merge


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as c:  # 触发 startup -> seed.init_db()
        yield c


def _owners():
    c = connect(); rows = list_owners(c); c.close(); return rows


def _names():
    return [g["name"] for g in _owners() if not g["is_blank"]]


# ---------- 基本署名规则：李四 并入 李四家 ----------

def test_merge_rewrites_owner_and_drops_headcount(client):
    before = set(_names())
    assert "李四" in before and "李四家" in before
    r = client.post("/api/owners/merge", json={"source": "李四", "target": "李四家"})
    assert r.status_code == 200 and r.json()["moved_items"] >= 1

    after = set(_names())
    assert "李四" not in after and "李四家" in after
    assert len(after) == len(before) - 1  # 物主一览人数减一

    c = connect()
    # 旧名不再挂在任何物品上，只留在别名表
    assert c.execute("SELECT COUNT(*) n FROM items WHERE owner='李四'").fetchone()["n"] == 0
    assert dict(c.execute(
        "SELECT * FROM owner_aliases WHERE alias='李四'").fetchone())["canonical"] == "李四家"
    c.close()


def test_old_name_listing_is_rewritten(client):
    assert client.post("/api/owners/merge",
                       json={"source": "李四", "target": "李四家"}).status_code == 200
    r = client.post("/api/items", json={"title": "旧名新钻", "owner": "李四"})
    assert r.status_code == 200
    body = r.json()
    assert body["owner"] == "李四家" and body["rewritten_from"] == "李四"  # 改记，不许旧户复活

    c = connect()
    row = c.execute("SELECT owner FROM items WHERE title='旧名新钻'").fetchone()
    assert row["owner"] == "李四家"
    c.close()
    assert "李四" not in _names()


def test_board_and_loans_show_new_name_for_active_loan(client):
    # 小陈 的折叠桌借出后再合并，在借行/借还记录物主必须改写成新户名
    client.post("/api/items", json={"title": "梯子", "owner": "王武"})
    c = connect(); iid = c.execute("SELECT id FROM items WHERE title='梯子'").fetchone()["id"]; c.close()
    client.post(f"/api/items/{iid}/lend", json={"borrower": "邻居乙", "due_date": "2030-01-05"})
    assert client.post("/api/owners/merge",
                       json={"source": "王武", "target": "王武家"}).status_code == 200

    b = client.get("/api/board").json()
    on_loan = [l for l in b["active"] + b["overdue"] if l["title"] == "梯子"]
    assert len(on_loan) == 1 and on_loan[0]["owner"] == "王武家"
    # 可借物主列不再出现旧名
    assert all(i["owner"] != "王武" for i in b["available"])

    hist = client.get("/api/loans").json()
    rec = [l for l in hist["active"] + hist["overdue"] if l["title"] == "梯子"][0]
    assert rec["owner"] == "王武家" and rec["due_date"] == "2030-01-05"  # due_date 不被改写


def test_merge_touches_no_due_date(client):
    # 种子里已外借样例（阿强）的 due_date
    c = connect(); before = c.execute(
        "SELECT due_date FROM loans WHERE item_id=5").fetchone()["due_date"]; c.close()
    client.post("/api/owners/merge", json={"source": "阿强", "target": "阿强家"})
    c = connect(); after = c.execute(
        "SELECT due_date FROM loans WHERE item_id=5").fetchone()["due_date"]; c.close()
    assert before == after == "2020-06-01"


# ---------- 合并失败：全部回到点击之前 ----------

def test_failed_merge_rolls_back_everything(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); seed.init_db()
    c = connect()
    owners_before = [dict(r) for r in c.execute("SELECT owner, COUNT(*) n FROM items GROUP BY owner")]
    aliases_before = c.execute("SELECT COUNT(*) n FROM owner_aliases").fetchone()["n"]
    c.close()

    # 模拟“合并改写已执行、但提交前失败”：必须整体回滚
    with pytest.raises(RuntimeError):
        with write_tx() as c:
            merge_owners(c, "李四", "李四家")
            raise RuntimeError("boom before commit")

    c = connect()
    owners_after = [dict(r) for r in c.execute("SELECT owner, COUNT(*) n FROM items GROUP BY owner")]
    aliases_after = c.execute("SELECT COUNT(*) n FROM owner_aliases").fetchone()["n"]
    c.close()
    assert owners_after == owners_before and aliases_after == aliases_before
    assert "李四" in _names()  # 人数与物主列回到点击之前


@pytest.mark.parametrize("source,target", [
    ("李四", "李四"),     # 同一名
    ("", "李四家"),        # 旧名为空
    ("李四", "  "),       # 新名为空
    ("不存在甲", "不存在乙"),  # 两户都不存在
])
def test_merge_endpoint_rejects_and_changes_nothing(client, source, target):
    before = sorted((r["name"], r["items"]) for r in _owners())
    r = client.post("/api/owners/merge", json={"source": source, "target": target})
    assert r.status_code == 409
    after = sorted((r["name"], r["items"]) for r in _owners())
    assert before == after


def test_cannot_merge_already_merged_alias(client):
    client.post("/api/owners/merge", json={"source": "李四", "target": "李四家"})
    r = client.post("/api/owners/merge", json={"source": "李四", "target": "别的户"})
    assert r.status_code == 409
    # 别名指向不被第二次请求改掉
    c = connect()
    assert dict(c.execute("SELECT * FROM owner_aliases WHERE alias='李四'").fetchone())[
        "canonical"] == "李四家"
    c.close()


# ---------- 种子脏数据：无主不得被写成有主 ----------

def test_blank_owner_dirty_data_never_merged(client):
    # 无主户数显示但不计入人数
    payload = client.get("/api/owners").json()
    assert any(g["is_blank"] for g in payload["owners"])
    assert all(not g["is_blank"] for g in payload["owners"][:payload["count"]])

    # 任何带空名的合并都失败
    r = client.post("/api/owners/merge", json={"source": "", "target": "李四家"})
    assert r.status_code == 409
    r = client.post("/api/owners/merge", json={"source": "李四家", "target": ""})
    assert r.status_code == 409

    # 无主物品仍无主，没有被并进李四家
    c = connect()
    rows = c.execute("SELECT owner FROM items WHERE data_quality='dirty'").fetchall()
    assert rows and all(r["owner"] == "" for r in rows)
    c.close()

    # 空名上架仍记无主，不被改记成某户
    resp = client.post("/api/items", json={"title": "又一件无主", "owner": ""})
    assert resp.json()["owner"] == "" and resp.json()["rewritten_from"] is None


def test_merge_into_brand_new_target_name(client):
    r = client.post("/api/owners/merge", json={"source": "小陈", "target": "小陈的新家"})
    assert r.status_code == 200
    payload = client.get("/api/owners").json()
    names = [g["name"] for g in payload["owners"] if not g["is_blank"]]
    assert "小陈的新家" in names and "小陈" not in names
    # 旧名上架落到新户
    assert client.post("/api/items", json={"title": "桌2", "owner": "小陈"}).json()["owner"] == "小陈的新家"


def test_chained_merge_follows_to_final_household(client):
    # 李四 -> 李四家，再 李四家 -> 周家：旧名「李四」最终应解析到周家
    client.post("/api/owners/merge", json={"source": "李四", "target": "李四家"})
    client.post("/api/owners/merge", json={"source": "李四家", "target": "周家"})
    c = connect()
    amap = canonical_map(c)
    from app.engines.owner_merge import canonical_owner
    assert canonical_owner("李四", amap) == "周家"
    assert canonical_owner("李四家", amap) == "周家"
    c.close()
    # 用最旧名上架，直接落到周家
    r = client.post("/api/items", json={"title": "老物件", "owner": "李四"})
    assert r.json()["owner"] == "周家"
    names = [g["name"] for g in client.get("/api/owners").json()["owners"] if not g["is_blank"]]
    assert "周家" in names and "李四" not in names and "李四家" not in names


# ---------- 并发：合并进行中，旧名上架 + 新名借出同一电钻 ----------

def test_concurrent_merge_list_lend_single_household(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path)); seed.init_db()
    trials = 12

    for i in range(trials):
        old, new = f"试户{i}", f"试户{i}家"
        add_item(ItemIn(title=f"电钻{i}", owner=old))
        c = connect()
        drill = c.execute("SELECT id FROM items WHERE title=?", (f"电钻{i}",)).fetchone()["id"]
        c.close()

        barrier = threading.Barrier(3)
        errors = []

        def do_merge():
            barrier.wait()
            try: merge(MergeIn(source=old, target=new))
            except Exception as e: errors.append(("merge", e))

        def do_list_old_name():
            barrier.wait()
            try: add_item(ItemIn(title=f"并发旧名钻{i}", owner=old))
            except Exception as e: errors.append(("list", e))

        def do_lend_new_name():
            barrier.wait()
            try: lend(drill, LendIn(borrower="抢借人", due_date="2030-06-30"))
            except Exception as e: errors.append(("lend", e))

        threads = [threading.Thread(target=f) for f in (do_merge, do_list_old_name, do_lend_new_name)]
        for t in threads: t.start()
        for t in threads: t.join()
        # 三个操作在 IMMEDIATE 锁下串行，语义上都应成功（仅锁等待，不报错）
        assert [k for k, _ in errors] == [], errors

        c = connect()
        # 不变量1：同一电钻至多一笔在借
        n_active = c.execute(
            "SELECT COUNT(*) n FROM loans WHERE item_id=? AND status='active'", (drill,)).fetchone()["n"]
        assert n_active <= 1
        # 不变量2：可借栏不可能还挂旧户——只要已借出，物主列必须是新户名（绝不可能是旧户）
        stored = c.execute("SELECT owner,status FROM items WHERE id=?", (drill,)).fetchone()
        assert stored["owner"] != old
        if stored["status"] == "on_loan":
            assert stored["owner"] == new
        # 不变量3：旧名上架的那件也不得让旧户复活
        for r in c.execute("SELECT owner FROM items WHERE title=?", (f"并发旧名钻{i}",)).fetchall():
            assert r["owner"] == new
        c.close()

    # 全局不变量：所有物品物主列都是现用户名，无两户挂同一件，无主仍是空
    c = connect()
    assert stored_owners_are_canonical(c)
    dup = c.execute(
        """SELECT item_id FROM loans WHERE status='active' GROUP BY item_id HAVING COUNT(*)>1""").fetchall()
    assert not dup
    assert c.execute("SELECT COUNT(*) n FROM items WHERE data_quality='dirty' AND owner!=''").fetchone()["n"] == 0
    c.close()
