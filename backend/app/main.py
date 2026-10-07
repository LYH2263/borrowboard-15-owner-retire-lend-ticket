from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect, write_tx
from app.engines.borrow_rules import can_lend, classify_loans
from app.engines.owner_merge import (
    MergeError, apply_canonical, canonical_map, list_owners, merge_owners,
)

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    c = connect()
    rows = [dict(r) for r in c.execute("SELECT * FROM items")]
    apply_canonical(rows, canonical_map(c))
    c.close()
    return rows

def _board_rows(c):
    available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
    loans = [dict(r) for r in c.execute(
        """SELECT loans.*, items.title, items.owner FROM loans
           JOIN items ON items.id=loans.item_id WHERE loans.status='active'""")]
    amap = canonical_map(c)
    apply_canonical(available, amap)
    apply_canonical(loans, amap)
    return available, loans

@app.get("/api/board")
def board():
    c = connect()
    available, loans = _board_rows(c)
    c.close()
    cls = classify_loans(loans, date.today().isoformat())
    return {
        "available": available,
        "active": cls["active"],
        "overdue": cls["overdue"],
        "counts": {"available": len(available), "active": len(cls["active"]), "overdue": len(cls["overdue"])},
    }

class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    # 新上架只认合并后的现用户名：旧名一律改记到其现户，不允许旧名重新立户。
    # 空户名（无主）短路不改记 —— 无主不会被写成有主。
    # IMMEDIATE：必须在写锁内读别名表，否则与并发的合并交叉时会按旧快照把旧名写回、
    # 让已并走的户复活。
    raw_owner = body.owner or ""
    with write_tx() as c:
        amap = canonical_map(c)
        owner = raw_owner if raw_owner.strip() == "" else amap.get(raw_owner.strip(), raw_owner.strip())
        cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                        (body.title, owner, "available", "clean"))
        iid = cur.lastrowid
    return {"id": iid, "owner": owner,
            "rewritten_from": raw_owner if owner != raw_owner else None}

class LendIn(BaseModel):
    borrower: str
    due_date: str

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    # IMMEDIATE 写事务：与户名合并互斥。合并进行中，另一人按新名借出同一电钻时，
    # 这里会在锁内重读物品状态，杜绝「可借已空但在借行挂旧户」或一户两挂。
    try:
        with write_tx() as c:
            item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
            if not item:
                raise HTTPException(404, "item")
            active = c.execute(
                "SELECT COUNT(*) n FROM loans WHERE item_id=? AND status='active'", (iid,)
            ).fetchone()["n"]
            check = can_lend(item["status"], active)
            if not check["ok"]:
                raise HTTPException(409, check["reason"])
            cur = c.execute(
                "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
                (iid, body.borrower, "active", body.due_date, datetime.now(timezone.utc).isoformat()))
            c.execute("UPDATE items SET status='on_loan' WHERE id=?", (iid,))
            lid = cur.lastrowid
    except HTTPException:
        raise
    return {"loan_id": lid}

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    c = connect()
    loan = c.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
    if not loan: c.close(); raise HTTPException(404, "loan")
    if loan["status"] != "active":
        c.close(); raise HTTPException(400, "not_active")
    c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=?",
              (datetime.now(timezone.utc).isoformat(), lid))
    c.execute("UPDATE items SET status='available' WHERE id=?", (loan["item_id"],))
    c.commit(); c.close(); return {"ok": True}

@app.get("/api/loans")
def loans():
    c = connect()
    rows = [dict(r) for r in c.execute(
        """SELECT loans.*, items.title, items.owner FROM loans JOIN items ON items.id=loans.item_id
           ORDER BY loans.id DESC""")]
    apply_canonical(rows, canonical_map(c))
    c.close()
    return classify_loans(rows, date.today().isoformat())

@app.get("/api/owners")
def owners():
    c = connect()
    rows = list_owners(c)
    c.close()
    # 「人数」只计有名户；无主脏数据仍列出但不计入。
    people = [g for g in rows if not g["is_blank"]]
    return {"owners": rows, "count": len(people)}

class MergeIn(BaseModel):
    source: str
    target: str

@app.post("/api/owners/merge")
def merge(body: MergeIn):
    # 署名规则落进这一个合并提交：改写旧名物品 -> 新户名 + 登记别名。
    # 任何校验失败都抛 MergeError，write_tx 回滚，物主人数/可借物主列回到点击之前。
    try:
        with write_tx() as c:
            result = merge_owners(c, body.source, body.target)
    except MergeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, **result}

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
