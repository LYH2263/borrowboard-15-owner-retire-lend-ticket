from app.engines.owner_merge import resolve_canonical, validate_merge

def test_resolve_passthrough():
    assert resolve_canonical("李四家", {}) == "李四家"
    assert resolve_canonical("", {"李四": "李四家"}) == ""

def test_resolve_flat_map_and_chain():
    aliases = {"李四": "李四家", "小陈": "李四家"}
    assert resolve_canonical("李四", aliases) == "李四家"
    chained = {"李四": "李四家", "李四家": "李四家族"}
    assert resolve_canonical("李四", chained) == "李四家族"

def test_resolve_cycle_guard():
    cycle = {"a": "b", "b": "a"}
    assert resolve_canonical("a", cycle) == "a"

def test_validate_ok_and_brand_new_canonical():
    households = {"李四", "李四家"}
    r = validate_merge("李四", "李四家", "keep", households, {})
    assert r["ok"]
    r2 = validate_merge("李四", "李四家族", "rewrite", households, {})
    assert r2["ok"]  # brand-new canonical name allowed

def test_validate_rejects():
    households = {"李四", "李四家"}
    assert validate_merge("李四", "李四", "keep", households, {})["code"] == "same_owner"
    assert validate_merge("  ", "李四家", "keep", households, {})["code"] == "empty_owner"
    assert validate_merge("", "李四家", "keep", households, {})["code"] == "empty_owner"
    assert validate_merge("李四", "李四家", "x", households, {})["code"] == "bad_attribution"
    assert validate_merge("不存在", "李四家", "keep", households, {})["code"] == "old_household_not_found"
    aliases = {"李四": "李四家"}
    r = validate_merge("李四", "王五", "keep", {"李四家"}, aliases)
    assert r["code"] == "already_merged" and r["status"] == 409
    # reverse merge into an absorbed name is a cycle attempt
    r2 = validate_merge("李四家", "李四", "keep", {"李四家", "李四"}, aliases)
    assert r2["code"] == "canonical_is_absorbed"
