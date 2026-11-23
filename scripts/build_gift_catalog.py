#!/usr/bin/env python3
"""Build the gift-package catalog used by shop and payment flows.

The client only ships the *current* package payloads in GPGoodList.  The
payment product table, however, keeps every historical store id.  This script
joins the authoritative current payloads with the historical payloads that
were shipped by earlier local-backend releases and fills the remaining legacy
ids with a deterministic, name/price based mapping.  The generated file is
checked in so runtime behaviour does not depend on this script.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = ROOT / "backend" / "fixtures" / "product_list.json"
GP_GOODS = ROOT / "backend" / "fixtures" / "shop" / "GPGoodList.json"
SEED = ROOT / "backend" / "data" / "gift_packages_seed.json"
OUTPUT = ROOT / "backend" / "data" / "gift_packages.json"


CHIP_ITEMS = {
    "先锋": ("3213", "3211"),
    "近卫": ("3223", "3221"),
    "重装": ("3233", "3231"),
    "狙击": ("3243", "3241"),
    "术师": ("3253", "3251"),
    "医疗": ("3263", "3261"),
    "辅助": ("3273", "3271"),
    "特种": ("3283", "3281"),
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def walk_gp(payload):
    rows = {}
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            good_id = node.get("goodId")
            if good_id and isinstance(node.get("items"), list):
                rows.setdefault(str(good_id), node)
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return rows


def item(item_id, item_type, count):
    return {"id": str(item_id), "type": str(item_type), "count": int(count)}


def chip_items(name):
    match = re.search(r"(先锋|近卫|重装|狙击|术师|医疗|辅助|特种)芯片", name)
    if not match:
        return None
    double_chip, chip = CHIP_ITEMS[match.group(1)]
    return [
        item(double_chip, "MATERIAL", 4),
        item(chip, "MATERIAL", 5),
        item("3303", "MATERIAL", 45),
        item("3302", "MATERIAL", 25),
        item("3301", "MATERIAL", 10),
    ]


def normalized(name):
    return re.sub(r"[\s·・\-_—（）()αβγ\[\]【】]+$", "", str(name or "")).strip()


def remove_variant_suffix(name):
    return re.sub(r"[-—_]?[αβγ]$|·(中坚|α|β|γ)$", "", normalized(name)).strip()


def nearest(canonical, price, keyword=None):
    pool = list(canonical.values())
    if keyword:
        tagged = [row for row in pool if keyword in str(row.get("displayName") or "")]
        if tagged:
            pool = tagged
    return min(
        pool,
        key=lambda row: (
            abs(int(row.get("price") or 0) * 100 - int(price or 0)),
            row["goodId"],
        ),
    )


def derive(name, price, canonical):
    name = str(name or "")
    price = int(price or 0)
    chips = chip_items(name)
    if chips:
        return chips, "derived:chip"
    if any(key in name for key in ("数据包", "数据套组", "数据整合箱")):
        if "套组" in name or "整合箱" in name:
            return [
                item("mod_unlock_token", "MATERIAL", 24),
                item("mod_update_token_2", "MATERIAL", 30),
                item("mod_update_token_1", "MATERIAL", 90),
            ], "derived:mod-set"
        return [item("mod_unlock_token", "MATERIAL", 12 if price <= 3000 else 24)], "derived:mod"
    if "中坚" in name and "寻访" in name:
        return [
            item("classic_gacha_10", "CLASSIC_TKT_GACHA_10", 1),
            item("classic_gacha", "CLASSIC_TKT_GACHA", 5),
        ], "derived:classic-gacha"
    if "寻访" in name or "定向甄选" in name:
        count = 1 if price <= 12800 else 2
        return [item("7004", "TKT_GACHA_10", count)], "derived:gacha"
    if any(key in name for key in ("资金箱", "零钱包", "手提箱", "资金包")):
        gold = max(30000, price * 100)
        return [item("4001", "GOLD", gold)], "derived:fund"
    if "特训" in name:
        if price >= 168:
            return [
                item("voucher_elite_II_6", "VOUCHER_ELITE_II_6", 1),
                item("voucher_elite_II_5", "VOUCHER_ELITE_II_5", 1),
            ], "derived:elite-training"
        if price >= 98:
            return [item("voucher_elite_II_5", "VOUCHER_ELITE_II_5", 1)], "derived:elite-training"
        return [item("voucher_levelmax_5", "VOUCHER_LEVELMAX_5", 1)], "derived:level-training"
    if "作战记录" in name:
        factor = max(1, price // 3000)
        return [
            item("2004", "CARD_EXP", 80 * factor),
            item("2003", "CARD_EXP", 100 * factor),
            item("2002", "CARD_EXP", 160 * factor),
        ], "derived:exp"
    if any(key in name for key in ("家具", "主题包", "形象")):
        return [item("3401", "MATERIAL", max(600, price * 8))], "derived:furniture"
    template = nearest(canonical, price, keyword="组合包" if "组合包" in name else None)
    return [dict(entry) for entry in template.get("items") or []], f"legacy-nearest:{template['goodId']}"


def main():
    current = walk_gp(load(GP_GOODS))
    seed_payload = load(SEED) if SEED.exists() else {}
    seed_rows = walk_gp(seed_payload) if isinstance(seed_payload, (dict, list)) else {}
    canonical = {}
    canonical.update(seed_rows)
    canonical.update(current)
    by_name = {}
    by_norm = {}
    for row in canonical.values():
        name = str(row.get("displayName") or "")
        if name:
            by_name.setdefault(name, row)
            by_norm.setdefault(remove_variant_suffix(name), row)

    products = load(PRODUCTS).get("productList") or []
    output = {}
    for product in products:
        good_id = str(product.get("product_id") or "")
        if not good_id.startswith("GP_"):
            continue
        name = str(product.get("name") or "")
        price = int(product.get("price") or 0)
        row = canonical.get(good_id) or by_name.get(name) or by_norm.get(remove_variant_suffix(name))
        if row:
            items = [dict(entry) for entry in row.get("items") or []]
            source = "canonical" if good_id in current else "historical"
        else:
            items, source = derive(name, price, canonical)
        output[good_id] = {
            "goodId": good_id,
            "displayName": name,
            "price": price,
            "currencyUnit": "CASH",
            "items": items,
            "source": source,
        }
    for good_id, row in canonical.items():
        output.setdefault(
            good_id,
            {
                "goodId": good_id,
                "displayName": str(row.get("displayName") or good_id),
                "price": int(row.get("price") or 0),
                "currencyUnit": str(row.get("currencyUnit") or "CASH"),
                "items": [dict(entry) for entry in row.get("items") or []],
                "source": "canonical",
            },
        )
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(output)} packages to {OUTPUT}")


if __name__ == "__main__":
    main()
