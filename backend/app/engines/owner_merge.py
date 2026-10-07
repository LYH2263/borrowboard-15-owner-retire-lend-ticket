"""Owner-name merging ("户名合并").

署名规则 (written into the merge commit):
- merge(source, target): source 户并入 target 户。target 为存续户名。
- 合并提交把当时挂在旧名 source 下的物品 *改写成* 新户名 target（在借行也随之
  显示新户名）；同时登记 source -> target 别名，此后：
    * 新上架只认 target：再用旧名 source 上架会被改记成 target，不允许旧名重新立户；
    * 可借栏物主列、物主一览人数、借还记录、顶细条筛选一律按 target 这一户聚合。
-  loans.due_date 等借用字段不在合并改写范围内。
- 空户名（种子脏数据「无主」）永不被并入任何户：别名解析对空名短路返回，
  无主不得被合并写成有主。
"""

class MergeError(ValueError):
    """Rejected merge; the caller must roll back (nothing is committed)."""


def is_blank(name: str | None) -> bool:
    return name is None or name.strip() == ""


def canonical_map(c) -> dict[str, str]:
    """alias -> 现用户名, following chains (A->B then B->C 折叠为 A->C)."""
    amap: dict[str, str] = {}
    for r in c.execute("SELECT alias, canonical FROM owner_aliases"):
        a, t = r["alias"], r["canonical"]
        # 若 target 本身已被并走，先指到它的现用户
        t = amap.get(t, t)
        amap[a] = t
    return amap


def canonical_owner(owner: str | None, amap: dict[str, str]) -> str:
    """空名短路：脏数据无主不做任何映射。其余沿别名链解析到现用户名（定点）。"""
    if is_blank(owner):
        return owner if owner is not None else ""
    seen: set[str] = set()
    while owner in amap and owner not in seen:
        seen.add(owner)
        owner = amap[owner]
    return owner


def apply_canonical(rows, amap, field="owner"):
    for x in rows:
        x[field] = canonical_owner(x.get(field), amap)
    return rows


def merge_owners(c, source: str, target: str) -> dict:
    """在已开启的写事务 c 内执行合并；非法时抛 MergeError（由调用方回滚）。"""
    if is_blank(source) or is_blank(target):
        raise MergeError("blank_owner_not_allowed")
    source, target = source.strip(), target.strip()
    if source == target:
        raise MergeError("same_owner")

    amap = canonical_map(c)
    # 已并入别户的名字不能再作为独立户被选
    src_now = canonical_owner(source, amap)
    tgt_now = canonical_owner(target, amap)
    if src_now != source:
        raise MergeError("source_already_merged")
    if tgt_now != target:
        # 目标若也是旧名，要求先并到同一现户名；这里直接拒绝，避免一户两写
        raise MergeError("target_is_alias")

    n_items = c.execute(
        "SELECT COUNT(*) n FROM items WHERE owner=?", (source,)
    ).fetchone()["n"]
    n_target = c.execute(
        "SELECT COUNT(*) n FROM items WHERE owner=?", (target,)
    ).fetchone()["n"]
    if n_items == 0 and n_target == 0:
        raise MergeError("neither_owner_exists")
    if n_items == 0:
        # 旧名下已无物品（可能此前被并过/已清空），只登记别名，不产生改写
        pass

    # 合并提交：旧名物品统一改写为新户名（在借行物主随之变化，due_date 不动）
    cur = c.execute("UPDATE items SET owner=? WHERE owner=?", (target, source))
    moved = cur.rowcount

    # 旧名 -> 新户 入别名表；顺带把指向 source 的更旧别名改指 target
    c.execute(
        "INSERT INTO owner_aliases(alias,canonical) VALUES(?,?) "
        "ON CONFLICT(alias) DO UPDATE SET canonical=excluded.canonical",
        (source, target),
    )
    c.execute("UPDATE owner_aliases SET canonical=? WHERE canonical=?", (target, source))

    return {"source": source, "target": target, "moved_items": moved}


def list_owners(c) -> list[dict]:
    """物主一览：按现用户名聚合（旧名不另立一户）。空户名单独列为「无主」脏数据。"""
    amap = canonical_map(c)
    groups: dict[str, dict] = {}
    for r in c.execute("SELECT owner, status FROM items"):
        name = canonical_owner(r["owner"], amap)
        key = name if name else ""
        g = groups.setdefault(key, {
            "name": name, "is_blank": name == "",
            "items": 0, "available": 0, "on_loan": 0,
        })
        g["items"] += 1
        if r["status"] == "available":
            g["available"] += 1
        elif r["status"] == "on_loan":
            g["on_loan"] += 1
    return sorted(groups.values(), key=lambda g: (g["is_blank"], g["name"]))


def stored_owners_are_canonical(c) -> bool:
    """不变量：库里物品物主列必须都是现用户名（旧名只能留在别名表，不得还挂在物品上）。
    空户名（无主）本就不映射，视为合法。"""
    amap = canonical_map(c)
    for r in c.execute("SELECT owner FROM items"):
        owner = r["owner"]
        if is_blank(owner):
            continue
        if canonical_owner(owner, amap) != owner:
            return False
    return True
