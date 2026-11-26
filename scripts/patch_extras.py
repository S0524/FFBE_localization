"""
补充汉化项目原先未覆盖但可安全处理的资源：
1. static/client/game-assets.apk：
   将包内自带的 assets/localized_texts/zh/0ThfQQWd.csv（对调繁中列）与
   assets/cpk/zh/sd/resources/resources_pretutorial.cpk 覆盖到对应 en/ 路径。
2. native/exvius/equipment-enhancement/*.html：
   基于 work/localized_texts/en 繁中词库（兼容明文与加密 .dat）+ 审阅确认模板汉化 54 个铁城装备强化说明与奖励网页。
"""
import argparse
import html
import json
import re
import shutil
import zipfile
from pathlib import Path

from ffbe_utils import get_latest_files, load_manifest_cache_from_paths, read_dat_as_plains

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_EXACT_HTML_PHRASES = {
    "Enhance your weapon": "強化你的武器",
    "Edition-specific abilities": "本期限定能力",
    "Featured equipment": "精選裝備",
    "Completion rewards": "達成報酬",
    "Event points": "活動點數",
    "Reward": "報酬",
    "Reconstructed explanation layout. The original in-game explanation image has not been recovered.": "說明頁面版面已重構（原版遊戲內說明圖片未能回收）。",
    "Recovered historical reward data. Layout reconstructed from the retained edition source.": "歷史報酬資料已還原，版面依據保留的活動來源重建。",
    "This page lists historical rewards. It does not claim that viewing this page grants them.": "此頁面僅列出歷史報酬清單，瀏覽本頁不會直接領取獎勵。",
    "The historical completion-reward table for this edition has not been recovered. Rewards from another edition are not substituted here.": "本期活動的歷史達成報酬表未能回收，此處未以其他期數的報酬代填。",
    "Choose the weapon to enhance. Equipped weapons are removed from your parties when selected.": "選擇要強化的武器。選定後，該武器會自動從各隊伍裝備中卸下。",
    "Choose your party and companion, then challenge the dungeon. Each floor uses one equipment enhancement orb.": "編成隊伍並選擇同行者後挑戰迷宮。每挑戰一層需消耗 1 顆裝備強化體力珠。",
    "Clear a floor and choose one of three offered abilities. Keep up to three abilities on your weapon. Replace an existing ability or skip the new offers when the slots are full.": "通關樓層後可從 3 個能力中擇一賦予，每把武器最多可持有 3 個能力；欄位已滿時可選擇替換既有能力或放棄本次新能力。",
    "Complete all ten floors, or return early to keep the abilities you obtained. For a previously enhanced weapon, choose the old or the new ability set at the end.": "通關全部 10 層或中途撤退皆可保留已獲得的能力；若強化對象為已強化過的武器，結算時可選擇保留舊能力組或套用新能力組。",
}

_STATIC_REWARDS = {
    "Equipment Enhancement Orb Restore": "裝備強化體力回復藥",
    "Steel Castle Coin": "鐵城硬幣",
    "Collaboration Festival Coin": "合作祭典硬幣",
    "EX Coin": "EX 硬幣",
    "EX Summon Ticket": "EX 召喚券",
    "4★+ Guaranteed Ticket": "4★以上確保召喚券",
    "1/10 NV Summon Ticket": "1/10 NV 召喚券",
    "Super Trust Moogle Exchange Ticket": "超級信賴莫古利交換券",
    "1-star Trust Moogle (ALL 1%)": "★1 信賴莫古利 (ALL 1%)",
    "4-star Trust Moogle (ALL 5%)": "★4 信賴莫古利 (ALL 5%)",
    "4-star Trust Moogle (ALL 10%)": "★4 信賴莫古利 (ALL 10%)",
    "5-star Trust Moogle (ALL 10%)": "★5 信賴莫古利 (ALL 10%)",
    "Guaranteed Ticket (4★/5★/NV)": "確保召喚券 (4★/5★/NV)",
    "NV EX Ticket (3★/4★/5★/NV)": "NV EX 召喚券 (3★/4★/5★/NV)",
    "NV EX Ticket (4★/5★/NV)": "NV EX 召喚券 (4★/5★/NV)",
    "NV EX Ticket (5★/NV)": "NV EX 召喚券 (5★/NV)",
    "Gold Enlightenment Key": "金之啟迪鑰匙",
    "Silver Enlightenment Key": "銀之啟迪鑰匙",
    "Burst Pot": "極限技罐",
    "King Burst Pot": "極限技王罐",
    "Fire Megacryst": "火之大結晶",
    "Ice Megacryst": "冰之大結晶",
    "Lightning Megacryst": "雷之大結晶",
    "Water Megacryst": "水之大結晶",
    "Earth Megacryst": "土之大結晶",
    "Wind Megacryst": "風之大結晶",
    "Light Megacryst": "光之大結晶",
    "Dark Megacryst": "暗之大結晶",
    "Calamity Gem": "災禍的封神珠",
    "Calamity Writ": "終焉的預言書",
    "Divine Crystal": "神魔結晶",
    "Fairies' Writ": "妖精王密書",
    "Prismatic Horn": "樂土的幻錦角",
    "Rainbow Bloom": "神獸的極彩角",
}

_MONTH_ZH = {
    "January": "1月", "February": "2月", "March": "3月", "April": "4月",
    "May": "5月", "June": "6月", "July": "7月", "August": "8月",
    "September": "9月", "October": "10月", "November": "11月", "December": "12月",
}

_STAT_WORDS = [("ATK", "攻擊"), ("MAG", "魔力"), ("DEF", "防禦"), ("SPR", "精神")]


def _swap_0thf_csv(raw: bytes) -> bytes:
    """将 0ThfQQWd.csv（KEY^EN^ZH）对调为 KEY^ZH^ZH 以便 en 模式直接显示繁中。"""
    out_lines: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if "^" in line:
            parts = line.split("^")
            if len(parts) >= 3 and parts[2].strip():
                parts[1] = parts[2]
                out_lines.append("^".join(parts))
                continue
        out_lines.append(line)
    return ("\n".join(out_lines) + "\n").encode("utf-8")


def _load_bundle_en_to_zh(work_texts_dir: Path) -> dict[str, str]:
    """从 F_TEXT_BUNDLE (*_4OBhnBKu.dat) 提取官方 英文->繁中 礼包名称映射（兼容明文与加密 .dat）。"""
    en_to_zh: dict[str, str] = {}
    if not work_texts_dir.is_dir():
        return en_to_zh

    root_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest_cache_from_paths(
        work_texts_dir.parent.parent / "manifest_cache.json",
        root_dir / "source" / "apk" / "manifest_cache.json",
    )
    bundle_tok = "4OBhnBKu"
    files = get_latest_files(work_texts_dir)
    if bundle_tok not in files:
        return en_to_zh

    _, plains = read_dat_as_plains(files[bundle_tok][1], manifest.get(bundle_tok))
    if not plains:
        return en_to_zh

    for blk in plains:
        for line in blk.splitlines():
            if "^" not in line:
                continue
            parts = line.split("^")
            if len(parts) < 3:
                continue
            k, zh, en = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if k.startswith("MST_BUNDLE_NAME_") and zh and en and zh != en and _CJK_RE.search(zh):
                en_to_zh.setdefault(en, zh)
    return en_to_zh


_STACKABLE_DROP_CATS = {"20", "28"}  # 20=道具/素材/活动币, 28=兵员碎片（可堆叠，不爆装备/兵员格）


def _load_monster_skill_zh_maps(work_texts_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """从 work/localized_texts/en 提取怪物技能 ID->繁中 及 英文技能名(小写)->繁中 映射。"""
    by_id: dict[str, str] = {}
    by_en: dict[str, str] = {}
    if not work_texts_dir.is_dir():
        return by_id, by_en

    root_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest_cache_from_paths(
        work_texts_dir.parent.parent / "manifest_cache.json",
        root_dir / "source" / "apk" / "manifest_cache.json",
    )
    files = get_latest_files(work_texts_dir)
    sources = (
        ("F1z92dkt", "MST_MONSTER_SKILL_NAME_"),
        ("1ZqISaBp", "MST_MAGIC_NAME_"),
        ("eSB5Ry3E", "MST_ABILITY_NAME_"),
        ("XBS8hLZD", "MST_LIMITBURST_NAME_"),
    )
    for tok, pfx in sources:
        if tok not in files:
            continue
        _, plains = read_dat_as_plains(files[tok][1], manifest.get(tok))
        if not plains:
            continue
        for blk in plains:
            for line in blk.splitlines():
                if "^" not in line:
                    continue
                parts = line.split("^")
                if len(parts) < 3:
                    continue
                k, zh, en = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if not k.startswith(pfx) or not zh or not _CJK_RE.search(zh):
                    continue
                if tok == "F1z92dkt":
                    by_id[k[len(pfx) :]] = zh
                if en and en != "<NA>":
                    by_en.setdefault(en.lower(), zh)
    return by_id, by_en


def _localize_monster_skill_tsv(
    raw_bytes: bytes,
    by_id: dict[str, str],
    by_en: dict[str, str],
    bst_names: dict[str, str],
) -> tuple[bytes, int]:
    """汉化 F_MONSTER_SKILL_MST.tsv 中的敌方技能名称 (G4L0YIB2)。"""
    lines = raw_bytes.decode("utf-8").splitlines()
    if not lines or not lines[0].startswith("#"):
        return raw_bytes, 0
    fields = [str(f) for f in (json.loads(lines[0][1:].strip()).get("fields") or [])]
    if "6E5u1xrs" not in fields or "G4L0YIB2" not in fields:
        return raw_bytes, 0
    idx_id = fields.index("6E5u1xrs")
    idx_nm = fields.index("G4L0YIB2")

    changed = 0
    out_lines = [lines[0]]
    for ln in lines[1:]:
        if not ln:
            continue
        cells = ln.split("\t")
        if len(cells) == len(fields):
            sid = cells[idx_id].strip()
            nm = cells[idx_nm].strip()
            zh = by_id.get(sid) or by_en.get(nm.lower())
            if not zh and nm in bst_names:
                zh = by_en.get(bst_names[nm].strip().lower())
            if zh and zh != cells[idx_nm]:
                cells[idx_nm] = zh
                changed += 1
            out_lines.append("\t".join(cells))
        else:
            out_lines.append(ln)
    if changed == 0:
        return raw_bytes, 0
    return ("\n".join(out_lines) + "\n").encode("utf-8"), changed


def _scale_drop_csv(csv_text: str, rate_mult: float, count_mult: float) -> tuple[str, int]:
    """按倍率调整掉落串（cat:id:count:rate,...），返回 (新串, 修改条目数)。"""
    if not csv_text or (rate_mult == 1.0 and count_mult == 1.0):
        return csv_text, 0
    changed = 0
    out_items: list[str] = []
    for it in csv_text.split(","):
        if not it:
            continue
        f = it.split(":")
        if len(f) >= 4 and f[2].isdigit() and f[3].isdigit():
            item_changed = False
            if rate_mult != 1.0:
                old_r = int(f[3])
                if old_r > 0:
                    new_r = min(100, max(1, round(old_r * rate_mult)))
                    if new_r != old_r:
                        f[3] = str(new_r)
                        item_changed = True
            if count_mult != 1.0 and f[0] in _STACKABLE_DROP_CATS:
                old_c = int(f[2])
                if old_c > 0:
                    new_c = max(1, round(old_c * count_mult))
                    if new_c != old_c:
                        f[2] = str(new_c)
                        item_changed = True
            if item_changed:
                changed += 1
            out_items.append(":".join(f))
        else:
            out_items.append(it)
    return ",".join(out_items), changed


def _scale_pos_int_str(raw: str, mult: float, max_val: int = 2_000_000_000) -> str | None:
    """将正整数字符串按倍率放大（上限 20 亿防 32 位整型溢出）；异常值或无需修改时返回 None。"""
    s = str(raw).strip()
    if not s.isdigit():
        return None
    v = int(s)
    if v <= 0 or mult == 1.0 or v >= max_val:
        return None
    return str(min(max_val, max(1, round(v * mult))))


_DROP_TOKENS = ("9BetM2Ds", "MZn8LC6H", "Vf5DGw07", "3frn4ILX")


def _scale_monster_parts_tsv(
    raw_bytes: bytes,
    rate_mult: float,
    count_mult: float,
    reward_mult: float = 1.0,
) -> tuple[bytes, int, int]:
    """修改 F_MONSTER_PARTS_MST.tsv 的掉落/偷窃通道及 Gil(7UR4J2SE) / 兵员EXP(B6H34Mea)。"""
    lines = raw_bytes.decode("utf-8").splitlines()
    if not lines or not lines[0].startswith("#"):
        return raw_bytes, 0, 0
    meta = json.loads(lines[0][1:].strip())
    fields = [str(f) for f in meta.get("fields") or []]
    drop_cols = [i for i, name in enumerate(fields) if name in _DROP_TOKENS]
    reward_cols = [i for i, name in enumerate(fields) if name in ("B6H34Mea", "7UR4J2SE")]
    if not drop_cols and not reward_cols:
        return raw_bytes, 0, 0

    total_drops = 0
    total_rewards = 0
    out_lines = [lines[0]]
    for line in lines[1:]:
        if not line:
            continue
        cells = line.split("\t")
        if len(cells) == len(fields):
            if rate_mult != 1.0 or count_mult != 1.0:
                for ci in drop_cols:
                    if cells[ci]:
                        new_val, cnt = _scale_drop_csv(cells[ci], rate_mult, count_mult)
                        cells[ci] = new_val
                        total_drops += cnt
            if reward_mult != 1.0:
                for ci in reward_cols:
                    scaled = _scale_pos_int_str(cells[ci], reward_mult)
                    if scaled is not None:
                        cells[ci] = scaled
                        total_rewards += 1
            out_lines.append("\t".join(cells))
        else:
            out_lines.append(line)
    return ("\n".join(out_lines) + "\n").encode("utf-8"), total_drops, total_rewards


def _scale_json_battle_fields(
    raw_bytes: bytes,
    rate_mult: float,
    count_mult: float,
    reward_mult: float = 1.0,
    scale_drops: bool = True,
    scale_rewards: bool = True,
) -> tuple[bytes, int, int]:
    """递归调整 JSON 中的掉落/偷窃通道（9BetM2Ds/MZn8LC6H/Vf5DGw07/3frn4ILX）、固定掉落 quantity 及 EXP/Gil（B6H34Mea/7UR4J2SE）。"""
    data = json.loads(raw_bytes.decode("utf-8"))
    total_drops = 0
    total_rewards = 0

    def _walk(obj):
        nonlocal total_drops, total_rewards
        if isinstance(obj, dict):
            for k, v in obj.items():
                if scale_drops and k in _DROP_TOKENS and isinstance(v, str) and v:
                    if rate_mult != 1.0 or count_mult != 1.0:
                        new_val, cnt = _scale_drop_csv(v, rate_mult, count_mult)
                        obj[k] = new_val
                        total_drops += cnt
                elif scale_drops and k == "quantity" and isinstance(v, int) and "enemy" in obj:
                    if count_mult != 1.0:
                        scaled = _scale_pos_int_str(str(v), count_mult)
                        if scaled is not None:
                            obj[k] = int(scaled)
                            total_drops += 1
                elif scale_rewards and k in ("B6H34Mea", "7UR4J2SE") and isinstance(v, (str, int)):
                    if reward_mult != 1.0:
                        scaled = _scale_pos_int_str(str(v), reward_mult)
                        if scaled is not None:
                            obj[k] = scaled if isinstance(v, str) else int(scaled)
                            total_rewards += 1
                else:
                    _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(data)
    if total_drops == 0 and total_rewards == 0:
        return raw_bytes, 0, 0
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), total_drops, total_rewards


def patch_game_assets_apk(
    src_apk: Path,
    out_apk: Path,
    work_texts_dir: Path | None = None,
    drop_rate_mult: float = 10.0,
    drop_count_mult: float = 10.0,
    reward_mult: float | None = None,
) -> bool:
    """将 game-assets.apk 内置的 zh 启动文案、引导 UI 包覆盖到 en 路径，并按需汉化官方礼包名与调整掉落/经验/金币倍率。"""
    if not src_apk.is_file():
        print(f"[跳过] 未找到内置资源包 {src_apk}（请先运行步骤 [3] 提取底包）")
        return False

    if reward_mult is None:
        reward_mult = drop_count_mult

    zh_csv_entry       = "assets/localized_texts/zh/0ThfQQWd.csv"
    en_csv_entry       = "assets/localized_texts/en/0ThfQQWd.csv"
    zh_cpk_entry       = "assets/cpk/zh/sd/resources/resources_pretutorial.cpk"
    en_cpk_entry       = "assets/cpk/en/sd/resources/resources_pretutorial.cpk"
    bundles_json_entry = "assets/rules/global_bundles.json"
    parts_tsv_entry    = "assets/rules/content/battle/F_MONSTER_PARTS_MST.tsv"
    skill_tsv_entry    = "assets/rules/content/battle/F_MONSTER_SKILL_MST.tsv"
    bst_json_entry     = "assets/rules/content/battle_skill_translations.json"
    json_battle_entries = (
        ("assets/rules/event_battles.json", True, False),
        ("assets/rules/event_encounters.json", True, True),
        ("assets/rules/colosseum_encounters.json", True, True),
        ("assets/rules/content/mission_battle_rows.json", True, True),
        ("assets/rules/content/reconstructed_reward_sources.json", True, False),
    )

    bundle_en_to_zh = _load_bundle_en_to_zh(work_texts_dir) if work_texts_dir else {}
    skill_by_id, skill_by_en = _load_monster_skill_zh_maps(work_texts_dir) if work_texts_dir else ({}, {})

    out_apk.parent.mkdir(parents=True, exist_ok=True)
    tmp_apk = out_apk.with_suffix(".apk.tmp")
    bundle_patched = skill_patched = drop_entries_scaled = reward_fields_scaled = 0

    with zipfile.ZipFile(src_apk, "r") as zin:
        names = set(zin.namelist())
        replacements = {
            en_csv_entry: _swap_0thf_csv(zin.read(zh_csv_entry)),
            en_cpk_entry: zin.read(zh_cpk_entry),
        }
        if bundle_en_to_zh and bundles_json_entry in names:
            gb = json.loads(zin.read(bundles_json_entry).decode("utf-8"))
            for b in gb.get("bundles", []):
                nm = b.get("name")
                if isinstance(nm, str) and nm in bundle_en_to_zh:
                    b["name"] = bundle_en_to_zh[nm]
                    bundle_patched += 1
            if bundle_patched > 0:
                replacements[bundles_json_entry] = json.dumps(gb, ensure_ascii=False, indent=2).encode("utf-8")

        if (skill_by_id or skill_by_en) and skill_tsv_entry in names:
            bst_names = {}
            if bst_json_entry in names:
                try:
                    bst_names = json.loads(zin.read(bst_json_entry).decode("utf-8")).get("names") or {}
                except Exception:
                    bst_names = {}
            new_skill_tsv, skill_patched = _localize_monster_skill_tsv(
                zin.read(skill_tsv_entry), skill_by_id, skill_by_en, bst_names
            )
            if skill_patched > 0:
                replacements[skill_tsv_entry] = new_skill_tsv

        if drop_rate_mult != 1.0 or drop_count_mult != 1.0 or reward_mult != 1.0:
            if parts_tsv_entry in names:
                new_tsv, c_drop, c_rew = _scale_monster_parts_tsv(
                    zin.read(parts_tsv_entry), drop_rate_mult, drop_count_mult, reward_mult
                )
                if c_drop > 0 or c_rew > 0:
                    replacements[parts_tsv_entry] = new_tsv
                    drop_entries_scaled += c_drop
                    reward_fields_scaled += c_rew
            for entry_name, do_drop, do_rew in json_battle_entries:
                if entry_name in names:
                    new_json, c_drop, c_rew = _scale_json_battle_fields(
                        zin.read(entry_name),
                        drop_rate_mult,
                        drop_count_mult,
                        reward_mult,
                        scale_drops=do_drop,
                        scale_rewards=do_rew,
                    )
                    if c_drop > 0 or c_rew > 0:
                        replacements[entry_name] = new_json
                        drop_entries_scaled += c_drop
                        reward_fields_scaled += c_rew

        with zipfile.ZipFile(tmp_apk, "w", allowZip64=False) as zout:
            for info in zin.infolist():
                out_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                out_info.compress_type = info.compress_type
                out_info.external_attr = info.external_attr
                if info.filename in replacements:
                    zout.writestr(out_info, replacements[info.filename])
                elif info.is_dir():
                    zout.writestr(out_info, b"")
                else:
                    with zin.open(info, "r") as src_f, zout.open(out_info, "w") as dst_f:
                        shutil.copyfileobj(src_f, dst_f, 4 * 1024 * 1024)

    tmp_apk.replace(out_apk)
    print(
        f"game-assets.apk 汉化完成！已覆盖引导 UI 包、启动提示文案，汉化 {bundle_patched} 条官方礼包名与 {skill_patched:,} 条敌方技能名，"
        f"按 {drop_rate_mult:g}x 掉率 / {drop_count_mult:g}x 掉落量调整 {drop_entries_scaled:,} 条掉落项，"
        f"按 {reward_mult:g}x 调整 {reward_fields_scaled:,} 条 Gil/兵员EXP 字段 -> {out_apk}"
    )
    return True


def _find_release_mst_sources(
    release_dir: Path,
    dat_suffix: str,
    idx_arc: str,
    ndjson_arc: str,
) -> tuple[Path | None, str | None, bytes | None, Path | None]:
    """按 release/latest.json 顺序定位最新版本的客户端 .dat、配套 .i 索引及服务端 .ndjson 所在包。"""
    manifest = release_dir / "latest.json"
    if not manifest.is_file():
        return None, None, None, None

    data = json.loads(manifest.read_text(encoding="utf-8"))
    target_zip = target_arc = orig_idx = ndjson_zip = None
    for p in data.get("packs", []):
        if str(p.get("id", "")).startswith("zh-tw-"):
            continue
        zp = release_dir / str(p.get("url", "")).rsplit("/", 1)[-1]
        if not zp.is_file():
            continue
        with zipfile.ZipFile(zp, "r") as zf:
            names = set(zf.namelist())
            for name in names:
                if name.startswith("native/mst/Ver") and name.endswith(dat_suffix):
                    target_arc = name
                    target_zip = zp
                    if idx_arc in names:
                        orig_idx = zf.read(idx_arc)
            if ndjson_arc in names:
                ndjson_zip = zp
    return target_zip, target_arc, orig_idx, ndjson_zip


def _write_mst_with_index(
    out_mst_dir: Path,
    dat_name: str,
    dat_bytes: bytes,
    idx_name: str,
    orig_idx: bytes | None,
    idx_records: list[tuple[int, int, int]],
    table_label: str,
) -> None:
    """写出修改后的客户端 MST .dat 并按首条去重与 ID 升序同步重建 .i 索引。"""
    import struct

    out_mst_dir.mkdir(parents=True, exist_ok=True)
    (out_mst_dir / dat_name).write_bytes(dat_bytes)

    if orig_idx and len(orig_idx) >= 10:
        magic, _ver, old_cnt = struct.unpack("<IIH", orig_idx[:10])
        dedup: dict[int, tuple[int, int]] = {}
        for rec_id, off, ln in idx_records:
            if rec_id not in dedup:
                dedup[rec_id] = (off, ln)
        if magic == 0xFFBE0000 and old_cnt == len(dedup):
            table1 = b"".join(struct.pack("<III", rid, *dedup[rid]) for rid in sorted(dedup))
            new_idx = orig_idx[:10] + table1 + orig_idx[10 + old_cnt * 12 :]
            (out_mst_dir / idx_name).write_bytes(new_idx)
            print(f"{table_label} 索引 ({idx_name}) 已同步重建 ({len(dedup):,} 条偏移)")


def patch_mission_mst_rank_gil(
    release_dir: Path,
    out_mst_dir: Path,
    out_mst_decoded_dir: Path,
    reward_mult: float = 10.0,
) -> bool:
    """
    从 release/*.zip 提取最新 F_MISSION_MST（客户端 native/mst/Ver*_24Eka6wL.dat + 24Eka6wL.i，
    以及离线服务端读取的 static/mst_decoded/F_MISSION_MST.ndjson），
    同步放大 Rank EXP (B6H34Mea) 与关卡 Gil (7UR4J2SE)。
    """
    if reward_mult == 1.0 or not release_dir.is_dir():
        return False

    ndjson_arc = "static/mst_decoded/F_MISSION_MST.ndjson"
    target_zip, target_arc, orig_idx, ndjson_zip = _find_release_mst_sources(
        release_dir, "_24Eka6wL.dat", "native/mst/24Eka6wL.i", ndjson_arc
    )
    if not target_zip or not target_arc:
        print("[跳过] 官方包中未找到 native/mst/Ver*_24Eka6wL.dat")
        return False

    def _scale_lines(raw_lines: list[str], sep: bytes) -> tuple[bytes, list[tuple[int, int, int]], int]:
        scaled_rows = 0
        out_parts: list[bytes] = []
        idx_recs: list[tuple[int, int, int]] = []
        off = 0
        for ln in raw_lines:
            if not ln.strip():
                continue
            obj = json.loads(ln)
            row_changed = False
            for key in ("B6H34Mea", "7UR4J2SE"):
                if key in obj:
                    scaled = _scale_pos_int_str(str(obj[key]), reward_mult)
                    if scaled is not None:
                        obj[key] = scaled
                        row_changed = True
            if row_changed:
                scaled_rows += 1
            line_b = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            idx_recs.append((int(obj["qo3PECw6"]), off, len(line_b)))
            out_parts.append(line_b)
            off += len(line_b) + len(sep)
        return sep.join(out_parts) + sep, idx_recs, scaled_rows

    with zipfile.ZipFile(target_zip, "r") as zf:
        raw_dat = zf.read(target_arc)
    sep = b"\r\n" if b"\r\n" in raw_dat[:1024] else b"\n"
    out_bytes, idx_records, scaled_rows = _scale_lines(raw_dat.decode("utf-8").splitlines(), sep)
    dat_name = Path(target_arc).name
    _write_mst_with_index(out_mst_dir, dat_name, out_bytes, "24Eka6wL.i", orig_idx, idx_records, "F_MISSION_MST")
    print(f"F_MISSION_MST ({dat_name}) 已按 {reward_mult:g}x 放大 {scaled_rows:,} 条关卡 Rank EXP / Gil -> {out_mst_dir / dat_name}")

    if ndjson_zip:
        with zipfile.ZipFile(ndjson_zip, "r") as zf:
            nd_lines = zf.read(ndjson_arc).decode("utf-8").splitlines()
        nd_bytes, _, nd_scaled = _scale_lines(nd_lines, b"\n")
        out_mst_decoded_dir.mkdir(parents=True, exist_ok=True)
        out_nd = out_mst_decoded_dir / "F_MISSION_MST.ndjson"
        out_nd.write_bytes(nd_bytes)
        print(f"F_MISSION_MST.ndjson (服务端表) 已按 {reward_mult:g}x 放大 {nd_scaled:,} 条关卡 Rank EXP / Gil -> {out_nd}")

    return True


def _scale_challenge_reward_csv(csv_str: str, count_mult: float, lapis_mult: float) -> tuple[str, int, int]:
    """缩放挑战任务 dX6cor8j 奖励：50:* 宝石按 lapis_mult，可堆叠 20/28 道具碎片按 count_mult。"""
    if not csv_str or (count_mult == 1.0 and lapis_mult == 1.0):
        return csv_str, 0, 0
    out_items: list[str] = []
    lapis_cnt = item_cnt = 0
    for it in csv_str.split(","):
        f = it.split(":")
        if len(f) >= 3 and f[0] == "50" and lapis_mult != 1.0:
            scaled = _scale_pos_int_str(f[2], lapis_mult)
            if scaled is not None:
                f[2] = scaled
                lapis_cnt += 1
        elif len(f) >= 3 and f[0] in _STACKABLE_DROP_CATS and count_mult != 1.0:
            scaled = _scale_pos_int_str(f[2], count_mult)
            if scaled is not None:
                f[2] = scaled
                item_cnt += 1
        out_items.append(":".join(f))
    return ",".join(out_items), lapis_cnt, item_cnt


def patch_challenge_mst_rewards(
    release_dir: Path,
    out_mst_dir: Path,
    out_mst_decoded_dir: Path,
    count_mult: float = 10.0,
    lapis_mult: float = 10.0,
) -> bool:
    """同步放大 F_CHALLENGE_MST（Ver*_GL9t6cMF.dat + GL9t6cMF.i 与 F_CHALLENGE_MST.ndjson）的挑战宝石与道具奖励。"""
    if (count_mult == 1.0 and lapis_mult == 1.0) or not release_dir.is_dir():
        return False

    ndjson_arc = "static/mst_decoded/F_CHALLENGE_MST.ndjson"
    target_zip, target_arc, orig_idx, ndjson_zip = _find_release_mst_sources(
        release_dir, "_GL9t6cMF.dat", "native/mst/GL9t6cMF.i", ndjson_arc
    )
    if not target_zip or not target_arc:
        print("[跳过] 官方包中未找到 native/mst/Ver*_GL9t6cMF.dat")
        return False

    def _scale_lines(raw_lines: list[str], sep: bytes) -> tuple[bytes, list[tuple[int, int, int]], int, int]:
        lapis_rows = item_rows = 0
        out_parts: list[bytes] = []
        idx_recs: list[tuple[int, int, int]] = []
        off = 0
        for ln in raw_lines:
            if not ln.strip():
                continue
            obj = json.loads(ln)
            dx = obj.get("dX6cor8j")
            if isinstance(dx, str) and dx:
                new_dx, lc, ic = _scale_challenge_reward_csv(dx, count_mult, lapis_mult)
                obj["dX6cor8j"] = new_dx
                lapis_rows += lc
                item_rows += ic
            line_b = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            idx_recs.append((int(obj["V36ygRYs"]), off, len(line_b)))
            out_parts.append(line_b)
            off += len(line_b) + len(sep)
        return sep.join(out_parts) + sep, idx_recs, lapis_rows, item_rows

    with zipfile.ZipFile(target_zip, "r") as zf:
        raw_dat = zf.read(target_arc)
    sep = b"\r\n" if b"\r\n" in raw_dat[:1024] else b"\n"
    out_bytes, idx_records, lapis_rows, item_rows = _scale_lines(raw_dat.decode("utf-8").splitlines(), sep)
    dat_name = Path(target_arc).name
    _write_mst_with_index(out_mst_dir, dat_name, out_bytes, "GL9t6cMF.i", orig_idx, idx_records, "F_CHALLENGE_MST")
    print(
        f"F_CHALLENGE_MST ({dat_name}) 已放大 {lapis_rows:,} 条宝石({lapis_mult:g}x) / "
        f"{item_rows:,} 条道具({count_mult:g}x) -> {out_mst_dir / dat_name}"
    )

    if ndjson_zip:
        with zipfile.ZipFile(ndjson_zip, "r") as zf:
            nd_lines = zf.read(ndjson_arc).decode("utf-8").splitlines()
        nd_bytes, _, nd_lapis, nd_items = _scale_lines(nd_lines, b"\n")
        out_mst_decoded_dir.mkdir(parents=True, exist_ok=True)
        out_nd = out_mst_decoded_dir / "F_CHALLENGE_MST.ndjson"
        out_nd.write_bytes(nd_bytes)
        print(
            f"F_CHALLENGE_MST.ndjson (服务端表) 已放大 {nd_lapis:,} 条宝石({lapis_mult:g}x) / "
            f"{nd_items:,} 条道具({count_mult:g}x) -> {out_nd}"
        )

    return True


def _build_text_dicts(work_texts_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """从 work/localized_texts/en 构建 equip_by_id 与英文->繁中名词映射（兼容明文与加密 .dat）。"""
    equip_by_id: dict[str, str] = {}
    en_to_zh:    dict[str, str] = {}
    if not work_texts_dir.is_dir():
        return equip_by_id, en_to_zh

    root_dir = Path(__file__).resolve().parent.parent
    manifest = load_manifest_cache_from_paths(
        work_texts_dir.parent.parent / "manifest_cache.json",
        root_dir / "source" / "apk" / "manifest_cache.json",
    )

    for tok, (_, f) in sorted(get_latest_files(work_texts_dir).items()):
        _, plains = read_dat_as_plains(f, manifest.get(tok))
        if not plains:
            continue
        for blk in plains:
            for line in blk.splitlines():
                if "^" not in line:
                    continue
                parts = line.split("^")
                if len(parts) < 3:
                    continue
                k, zh, en = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if not k or not zh or not _CJK_RE.search(zh):
                    continue
                if k.startswith("MST_EQUIP_ITEM_NAME_"):
                    equip_by_id[k[len("MST_EQUIP_ITEM_NAME_"):]] = zh
                if en and en not in en_to_zh:
                    en_to_zh[en] = zh
    return equip_by_id, en_to_zh


def _translate_event_title(title: str, en_to_zh: dict[str, str]) -> str:
    is_rewards = title.endswith(" rewards")
    core = title[:-len(" rewards")] if is_rewards else title
    if core in en_to_zh:
        zh_core = en_to_zh[core]
    elif core == "Collaboration Festival":
        zh_core = "合作祭典"
    else:
        m = re.match(r"^(.+?)\s+Equipment Enhancement Event$", core)
        if m:
            prefix = _MONTH_ZH.get(m.group(1), m.group(1))
            prefix = re.sub(r"^(\d+)(?:st|nd|rd|th)\s+Anniversary$", r"\1週年", prefix)
            zh_core = f"{prefix} 裝備強化活動"
        else:
            zh_core = core
    return f"{zh_core} 報酬" if is_rewards else zh_core


def _translate_reward(raw_text: str, en_to_zh: dict[str, str]) -> str:
    txt = html.unescape(raw_text).strip()
    m = re.match(r"^(.+?)\s*\((\d+)\)$", txt)
    if m and not re.search(r"(ALL \d+%|★|FF[A-Z0-9]+|WOTV FFBE)", txt):
        base, count_suffix = m.group(1).strip(), f" ({m.group(2)})"
    else:
        base, count_suffix = txt, ""

    if base in _STATIC_REWARDS:
        return _STATIC_REWARDS[base] + count_suffix
    if base in en_to_zh:
        return en_to_zh[base] + count_suffix

    m_lapis = re.fullmatch(r"([\d,]+)\s+Lapis", txt)
    if m_lapis:
        return f"{m_lapis.group(1)} 寶石"
    m_gil = re.fullmatch(r"([\d,]+)\s+gil", txt)
    if m_gil:
        return f"{m_gil.group(1)} 金幣"

    m_rec = re.match(r"^(.+?)\s*-\s*Recipe$", base)
    if m_rec and m_rec.group(1) in en_to_zh:
        return f"{en_to_zh[m_rec.group(1)]} - 製作圖" + count_suffix
    m_enh = re.match(r"^Enhancite \((.+)\)$", base)
    if m_enh and m_enh.group(1) in en_to_zh:
        return f"強化礦石 ({en_to_zh[m_enh.group(1)]})" + count_suffix

    return txt


def _translate_stats(text: str) -> str:
    for en_s, zh_s in _STAT_WORDS:
        text = text.replace(en_s, zh_s)
    return text


def _translate_html(raw_html: str, equip_by_id: dict[str, str], en_to_zh: dict[str, str]) -> str:
    out = raw_html.replace('<html lang="en">', '<html lang="zh-Hant">', 1)

    out = re.sub(
        r"<title>([^<]+)</title>",
        lambda m: f"<title>{html.escape(_translate_event_title(html.unescape(m.group(1)), en_to_zh))}</title>",
        out,
    )
    out = re.sub(
        r"<h1>([^<]+)</h1>",
        lambda m: f"<h1>{html.escape(_translate_event_title(html.unescape(m.group(1)), en_to_zh))}</h1>",
        out,
    )

    for en_p, zh_p in _EXACT_HTML_PHRASES.items():
        out = out.replace(html.escape(en_p), html.escape(zh_p))
        if "'" in en_p:
            out = out.replace(en_p, html.escape(zh_p))

    out = re.sub(
        r"<p>Available weapon types: (\d+)\. This edition has (\d+) floors\.</p>",
        r"<p>可強化武器種類：\1 種。本次活動共 \2 層。</p>",
        out,
    )

    def _sub_eq_li(m: re.Match) -> str:
        eid, stat_txt = m.group(1), html.unescape(m.group(2))
        name = equip_by_id.get(eid, f"裝備 {eid}")
        return f"<li>{html.escape(name)}：{html.escape(_translate_stats(stat_txt))}</li>"

    out = re.sub(r"<li>Equipment (\d+): ([^<]+)</li>", _sub_eq_li, out)

    def _sub_feat_p(m: re.Match) -> str:
        raw_name = html.unescape(m.group(1))
        zh_name = en_to_zh.get(raw_name, raw_name)
        return f"<p>{html.escape(zh_name)}</p>"

    out = re.sub(r"<p>(?!<|可強化)([^<]+)</p>", _sub_feat_p, out)

    def _sub_reward_td(m: re.Match) -> str:
        pts, rew = m.group(1), m.group(2)
        return f"<td>{pts}</td><td>{html.escape(_translate_reward(rew, en_to_zh))}</td>"

    out = re.sub(r"<td>([^<]+)</td><td>([^<]+)</td>", _sub_reward_td, out)
    return out


def patch_equipment_enhancement(src_dir: Path, out_dir: Path, work_texts_dir: Path) -> int:
    if not src_dir.is_dir():
        print(f"[跳过] 未找到装备强化网页目录 {src_dir}（请先运行步骤 [3] 提取底包）")
        return 0
    equip_by_id, en_to_zh = _build_text_dicts(work_texts_dir)
    count = 0
    for p in sorted(src_dir.rglob("*.html")):
        zh_html = _translate_html(p.read_text(encoding="utf-8", errors="replace"), equip_by_id, en_to_zh)
        dst = out_dir / p.relative_to(src_dir)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(zh_html, encoding="utf-8")
        count += 1
    print(f"装备强化活动网页汉化完成！已生成 {count} 个中文 HTML -> {out_dir}")
    return count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline-root",    required=True, help="纯净底包根目录（source/offline）")
    ap.add_argument("--work",            required=True, help="汉化输出根目录（work）")
    ap.add_argument("--drop-rate-mult",  type=float, default=10.0, help="战斗掉落概率倍率（默认 10.0，上限 100%%，填 1 保留原版）")
    ap.add_argument("--drop-count-mult", type=float, default=10.0, help="战斗掉落数量倍率（默认 10.0，作用于可堆叠道具/碎片，填 1 保留原版）")
    ap.add_argument("--reward-mult",     type=float, default=None, help="Gil / 兵员EXP / Rank EXP 倍率（默认跟随 --drop-count-mult，填 1 保留原版）")
    ap.add_argument("--lapis-mult",      type=float, default=10.0, help="关卡挑战宝石倍率（默认 10.0，填 1 保留原版）")
    ap.add_argument("--release-dir",     default="",               help="官方 release 目录（默认与 offline-root 同级的 release）")
    args = ap.parse_args()

    offline_root = Path(args.offline_root)
    work_root    = Path(args.work)
    release_dir  = Path(args.release_dir) if args.release_dir else (offline_root.parent.parent / "release")
    reward_mult  = args.drop_count_mult if args.reward_mult is None else args.reward_mult

    patch_game_assets_apk(
        offline_root / "static" / "client" / "game-assets.apk",
        work_root / "client" / "game-assets.apk",
        work_root / "localized_texts" / "en",
        drop_rate_mult=args.drop_rate_mult,
        drop_count_mult=args.drop_count_mult,
        reward_mult=reward_mult,
    )
    patch_mission_mst_rank_gil(
        release_dir,
        work_root / "mst",
        work_root / "mst_decoded",
        reward_mult=reward_mult,
    )
    patch_challenge_mst_rewards(
        release_dir,
        work_root / "mst",
        work_root / "mst_decoded",
        count_mult=args.drop_count_mult,
        lapis_mult=args.lapis_mult,
    )
    patch_equipment_enhancement(
        offline_root / "native" / "exvius" / "equipment-enhancement",
        work_root / "exvius" / "equipment-enhancement",
        work_root / "localized_texts" / "en",
    )


if __name__ == "__main__":
    main()
