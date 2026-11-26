"""
生成 FFBE 离线版系统文本表（localized_texts/en/*.dat 与 text_decoded/*.ndjson）。

从离线版 APK 的 libgame.so 解析加密表清单（含缓存），对英文原版文本表与档案馆繁中文本表
进行加密/解密及键对齐注入：
1. 支持明文 .dat 与 AES-128 加密 .dat（仅替换文本内容，保留英文原版加密协议与前缀结构）。
2. 自动对调繁中/英文多语言列顺序，并从低版本繁中表兜底补全最新版缺失或未汉化的条目。
3. 同步输出可直接修改和打包的 .ndjson 解码表至 work/text_decoded。
"""
import argparse
import json
import re
import shutil
import struct
import zipfile
from pathlib import Path

from ffbe_utils import (
    aes_ecb_encrypt,
    cbc_encrypt,
    decrypt_dat_lines,
    get_latest_files,
    is_plaintext_dat,  # noqa: F401
    is_unencrypted_dat,
    key16,
    read_dat_as_plains,
)

FILE_PAT = re.compile(r"^Ver(\d+)_([A-Za-z0-9]{8})\.dat$")
TRIPLET  = re.compile(rb"(F_[A-Z0-9_]+)\x00([A-Za-z0-9]{8})\x00([A-Za-z0-9]{8})\x00")


def getter_manifest(native: bytes) -> dict:
    """从 ELF 符号表的 ARM64 getter 函数中反推未直接以三元组明文出现的数据表映射。"""
    section_offset = struct.unpack_from("<Q", native, 40)[0]
    section_size, section_count = struct.unpack_from("<HH", native, 58)
    sections = [
        struct.unpack_from("<IIQQQQIIQq", native, section_offset + i * section_size)
        for i in range(section_count)
    ]

    def file_offset(address: int) -> int:
        for section in sections:
            if section[3] <= address < section[3] + section[5] and section[1] != 8:
                return section[4] + address - section[3]
        raise ValueError("Unmapped client address.")

    def string_getter(address: int) -> str:
        pos = file_offset(address)
        adrp, add, ret = struct.unpack_from("<III", native, pos)
        if adrp & 0x9F00001F != 0x90000000 or add & 0xFFC003FF != 0x91000000 or ret != 0xD65F03C0:
            raise ValueError("Not a literal-returning ARM64 getter.")
        immediate = ((adrp >> 5) & 0x7FFFF) << 2 | (adrp >> 29) & 3
        if immediate & (1 << 20):
            immediate -= 1 << 21
        target = (address & ~0xFFF) + (immediate << 12) + ((add >> 10) & 0xFFF)
        pos    = file_offset(target)
        end    = native.index(0, pos, pos + 200)
        return native[pos:end].decode("ascii")

    groups: dict[str, dict[str, str]] = {}
    for section in sections:
        if section[1] != 11:
            continue
        strings  = sections[section[6]]
        pool     = native[strings[4] : strings[4] + strings[5]]
        for pos in range(section[4], section[4] + section[5], section[9]):
            name_offset, _info, _other, index, address, _size = struct.unpack_from("<IBBHQQ", native, pos)
            name  = pool[name_offset : pool.index(0, name_offset)].decode("ascii")
            match = re.fullmatch(r"(_ZN.+)(10getMstName|14getMstFileName|13getMstFileKey)Ev", name)
            if not match or not index:
                continue
            try:
                value = string_getter(address)
            except (ValueError, UnicodeError):
                continue
            groups.setdefault(match[1], {})[match[2]] = value

    result = {}
    for group in groups.values():
        if len(group) != 3:
            continue
        name, token, key = (group[n] for n in ("10getMstName", "14getMstFileName", "13getMstFileKey"))
        if re.fullmatch(r"F_[A-Z0-9_]+", name) and all(re.fullmatch(r"[A-Za-z0-9]{8}", v) for v in (token, key)):
            result[token] = {"table": name, "token": token, "key": key}
    return result


def load_manifest_no_hash(apk_path: Path) -> dict:
    """从 APK 的 arm64 libgame.so 提取所有 F_* 数据表的表名、文件名 token 与加密 key。"""
    with zipfile.ZipFile(apk_path) as z:
        native = z.read("lib/arm64-v8a/libgame.so")
    by_token = {}
    for m in TRIPLET.finditer(native):
        name, token, key = (v.decode("ascii") for v in m.groups())
        by_token[token]  = {"table": name, "token": token, "key": key}
    for token, val in getter_manifest(native).items():
        by_token[token]  = val
    for name, token, key in (
        ("F_TEXT_ANALYTICS_ITEMS", "8e6PGb3p", "7nyO4pC9"),
        ("F_TEXT_TEXT_EN",         "0ThfQQWd", "s9E34w78"),
    ):
        by_token[token] = {"table": name, "token": token, "key": key}
    return by_token


# ──────────────────────────────────────────────────────────────────────────────
# 优化：APK 清单缓存（避免每次重解析 50 MB so 文件）
# ──────────────────────────────────────────────────────────────────────────────

def load_manifest_cached(apk_path: Path, cache_dir: Path) -> dict:
    """
    加载 APK 清单，优先从缓存文件读取。
    缓存键为 APK 的文件大小 + 修改时间戳；APK 未变化时直接使用缓存，
    否则重新解析并更新缓存（通常只有首次或 APK 升级时才触发解析）。
    """
    cache_file = cache_dir / "manifest_cache.json"
    apk_stat   = apk_path.stat()
    cache_key  = f"{apk_stat.st_size}:{int(apk_stat.st_mtime * 1000)}"

    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("_cache_key") == cache_key:
                manifest = {k: v for k, v in cached.items() if k != "_cache_key"}
                print(f"[缓存命中] 使用已缓存的清单（共 {len(manifest)} 条表项）")
                return manifest
        except Exception:
            pass  # 缓存损坏，重新生成

    print("正在从 APK 解析清单（首次运行或 APK 已更新）...")
    manifest = load_manifest_no_hash(apk_path)
    try:
        cache_data = dict(manifest)
        cache_data["_cache_key"] = cache_key
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps(cache_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[缓存已保存] {cache_file}（共 {len(manifest)} 条）")
    except Exception as exc:
        print(f"[警告] 无法写入清单缓存: {exc}")
    return manifest


def swap_json_lang(text: str) -> tuple[str, bool]:
    """将 JSON 文本字段内 '^' 分隔的繁中列（第 2 段）提升到第 1 列（默认显示位）。"""
    changed = False
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            for k, val in obj.items():
                if isinstance(val, str) and "^" in val:
                    segs = val.split("^")
                    if len(segs) >= 2 and segs[1].strip():
                        segs[0] = segs[1]
                        obj[k]  = "^".join(segs)
                        changed = True
            if changed:
                return json.dumps(obj, ensure_ascii=False, separators=(",", ":")), True
    except json.JSONDecodeError:
        pass
    return text, False


def normalize_zh_plains(plains: list[str]) -> tuple[list[str], bool]:
    """将解密后的历史 zh 文本统一对调为繁中优先。"""
    new_plains   = []
    file_swapped = False
    for p in plains:
        sub_lines = []
        for sub in p.splitlines():
            if sub.lstrip().startswith("{"):
                np, chg = swap_json_lang(sub)
                sub_lines.append(np)
                file_swapped = file_swapped or chg
            elif "^" in sub:
                parts = sub.split("^")
                if len(parts) >= 3 and parts[2].strip():
                    parts[1] = parts[2]
                    sub_lines.append("^".join(parts))
                    file_swapped = True
                else:
                    sub_lines.append(sub)
            else:
                sub_lines.append(sub)
        new_plains.append("\n".join(sub_lines))
    return new_plains, file_swapped


def _inject_json_battle_script_blocks(en_blocks: list[str], raw_zh_plains: list[str]) -> tuple[list[str], int]:
    """针对 JSON 行格式战斗脚本表（如 Ver127_dhR7sSxt.dat）按行签名与英文原文精准注入繁中。"""
    from collections import defaultdict

    kana_letter_re = re.compile(r"[\u3040-\u309f\u30a1-\u30fa\u30fd-\u30ff]")
    exact_map: dict[tuple[tuple[str, str, str, str], str], str] = {}

    def _row_sig(obj: dict) -> tuple[str, str, str, str]:
        return (
            str(obj.get("P_BATTLE_SCRIPT_INFO", "")),
            str(obj.get("P_COND", "")),
            str(obj.get("P_COND_PARAM", "")),
            str(obj.get("P_COMMAND", "")),
        )

    for block in raw_zh_plains:
        for line in block.splitlines():
            s = line.strip()
            if not s or not s.startswith("{"):
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            val = obj.get("P_COMMAND_PARAM_LOCAL")
            if not isinstance(val, str) or "^" not in val:
                continue
            parts = val.split("^")
            zh_en_seg = parts[0]
            zh_zh_seg = parts[1] if len(parts) >= 2 else parts[0]
            sig = _row_sig(obj)
            key = (sig, zh_en_seg)
            if key not in exact_map or (_has_cjk_in_line(zh_zh_seg) and not _has_cjk_in_line(exact_map[key])):
                exact_map[key] = zh_zh_seg

    injected = 0
    out_blocks: list[str] = []

    for en_text in en_blocks:
        out_lines = []
        for line in en_text.splitlines():
            s = line.strip()
            if not s or not s.startswith("{"):
                out_lines.append(line)
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if not isinstance(obj, dict) or not isinstance(obj.get("P_COMMAND_PARAM_LOCAL"), str):
                out_lines.append(line)
                continue

            sig = _row_sig(obj)
            en_val = obj["P_COMMAND_PARAM_LOCAL"]
            en_parts = en_val.split("^")
            en_seg = en_parts[0]

            zh_s: str | None = None
            cand = exact_map.get((sig, en_seg))
            if cand is not None and cand.strip() and cand != en_seg:
                if _has_cjk_in_line(cand) or not kana_letter_re.search(cand):
                    zh_s = cand

            if zh_s is not None:
                repeat_cnt = max(2, len(en_parts))
                obj["P_COMMAND_PARAM_LOCAL"] = "^".join([zh_s] * repeat_cnt)
                out_lines.append(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
                injected += 1
            else:
                out_lines.append(line)

        out_blocks.append("\n".join(out_lines) + ("\n" if en_text.endswith("\n") else ""))

    return out_blocks, injected


def _inject_blocks(en_blocks: list[str], zh_plains: list[str], raw_zh_plains: list[str] | None = None) -> tuple[list[str], int]:
    """统一注入逻辑（兼容单块明文/ECB 与多块 CBC 解密块）：以 en 原文为底，按 KEY + 英文原文兜底注入繁中文本。"""
    sample_lines = [ln for blk in en_blocks for ln in blk.splitlines()[:20] if ln.strip()][:20]
    if any(ln.lstrip().startswith("{") for ln in sample_lines):
        return _inject_json_battle_script_blocks(en_blocks, raw_zh_plains if raw_zh_plains is not None else zh_plains)

    zh_kv: dict[str, str] = {}
    for block in zh_plains:
        for line in block.splitlines():
            s = line.strip()
            if not s or s.startswith("{"):
                continue
            if "^" in line:
                parts = line.split("^")
                if len(parts) >= 2 and parts[0] and parts[1].strip():
                    if parts[0] not in zh_kv or _has_cjk_in_line(parts[1]):
                        zh_kv[parts[0]] = parts[1]

    # 从原始 zh 语料 (KEY^EN^ZH) 构建同表英文兜底映射：
    # 1) 剥离尾缀字母 (如 MST_MONSTER_NAME_101001000A -> 101001000) 且英文一致时复用繁中
    # 2) 同表唯一英文->繁中映射（如不同 ID 的同名怪兽/能力/关卡，保证不误翻多义词）
    zh_en_by_k: dict[str, str] = {}
    en_cands: dict[str, set[str]] = {}
    for block in (raw_zh_plains or ()):
        for line in block.splitlines():
            s = line.strip()
            if not s or s.startswith("{") or "^" not in line:
                continue
            p = line.split("^")
            if len(p) >= 3 and p[0] and p[1].strip() and p[2].strip() and _has_cjk_in_line(p[2]):
                en_s, zh_s = p[1].strip(), p[2]
                zh_en_by_k.setdefault(p[0], en_s)
                en_cands.setdefault(en_s, set()).add(zh_s)
    en_unique = {e: next(iter(zs)) for e, zs in en_cands.items() if len(zs) == 1}

    injected = 0
    out_blocks: list[str] = []
    for en_text in en_blocks:
        out_lines = []
        for line in en_text.splitlines():
            s = line.strip()
            if not s:
                out_lines.append(line)
                continue
            if "^" in line:
                parts = line.split("^")
                if len(parts) >= 2 and parts[0]:
                    k = parts[0]
                    en_s = parts[1].strip()
                    zh_hit: str | None = None
                    if k in zh_kv:
                        zh_hit = zh_kv[k]
                    else:
                        base_k = re.sub(r"[A-Z]+$", "", k)
                        if base_k != k and base_k in zh_kv and zh_en_by_k.get(base_k) == en_s:
                            zh_hit = zh_kv[base_k]
                        elif en_s in en_unique:
                            zh_hit = en_unique[en_s]
                    if zh_hit is not None:
                        parts[1] = zh_hit
                        out_lines.append("^".join(parts))
                        injected += 1
                    else:
                        out_lines.append(line)
                else:
                    out_lines.append(line)
            else:
                out_lines.append(line)
        out_blocks.append("\n".join(out_lines) + ("\n" if en_text.endswith("\n") else ""))

    return out_blocks, injected


def inject_plaintext_by_key(en_text: str, zh_plains: list[str], raw_zh_plains: list[str] | None = None) -> tuple[str, int]:
    """单块文本注入兼容包装函数。"""
    out_blocks, injected = _inject_blocks([en_text], zh_plains, raw_zh_plains=raw_zh_plains)
    return out_blocks[0], injected


def write_ndjson(blocks: list[str], table_name: str, out_ndjson_dir: Path) -> bool:
    """将 F_TEXT* 文本表的明文块转换为一行一条 JSON 的 .ndjson 文件供离线版加载。"""
    if not table_name.startswith("F_TEXT"):
        return False
    records = []
    for block in blocks:
        for line in block.splitlines():
            if not line:
                continue
            parts = line.split("^")
            if len(parts) >= 2 and parts[0]:
                records.append({"key": parts[0], "v": parts[1:]})
    if not records:
        return False
    ndjson_text = "".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
        for r in records
    )
    (out_ndjson_dir / f"{table_name}.ndjson").write_text(ndjson_text, encoding="utf-8")
    return True


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def load_all_zh_by_token(zh_dir: Path) -> dict[str, list[tuple[int, Path]]]:
    """收集 zh 目录下每个 token 的全部版本 .dat（按版本号升序排列）。"""
    by_tok: dict[str, list[tuple[int, Path]]] = {}
    for f in zh_dir.glob("*.dat"):
        m = FILE_PAT.match(f.name)
        if not m:
            continue
        ver, tok = int(m.group(1)), m.group(2)
        by_tok.setdefault(tok, []).append((ver, f))
    for tok in by_tok:
        by_tok[tok].sort(key=lambda x: x[0])
    return by_tok


def _read_zh_plains(zh_file: Path, entry: dict | None) -> tuple[str | None, list[str] | None]:
    """读取或解密单个 zh .dat 文件（复用 ffbe_utils.read_dat_as_plains）。"""
    return read_dat_as_plains(zh_file, entry)


def _has_cjk_in_line(line: str) -> bool:
    """判断行文本（JSON 或 ^ 分隔）中是否含汉字，避免旧版英文兜底覆盖。"""
    return bool(_CJK_RE.search(line))


def main():
    """命令行入口：解析 APK 清单并批量生成汉化 .dat 与 .ndjson 文本表。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--apk",  required=True)
    ap.add_argument("--en",   required=True)
    ap.add_argument("--zh",   required=True)
    ap.add_argument("--out",  required=True)
    args = ap.parse_args()

    apk_path = Path(args.apk)
    out_dir  = Path(args.out)

    # ── 优化：优先读取 APK 同目录缓存清单，即使清空 work 也能命中缓存 ─────────────
    manifest = load_manifest_cached(apk_path, apk_path.parent)

    en_map     = get_latest_files(Path(args.en))
    zh_all_map = load_all_zh_by_token(Path(args.zh))
    zh_map     = {tok: items[-1] for tok, items in zh_all_map.items()}

    out_dat_dir   = out_dir / "localized_texts" / "en"
    out_ndjson_dir = out_dir / "text_decoded"
    out_dat_dir.mkdir(parents=True, exist_ok=True)
    out_ndjson_dir.mkdir(parents=True, exist_ok=True)

    enc_matched = plain_matched = swapped_tables = total_injected = ndjson_count = 0

    for token, (_, en_file) in sorted(en_map.items()):
        if token not in zh_map:
            continue
        _, zh_file  = zh_map[token]
        target_dat  = out_dat_dir / en_file.name
        entry       = manifest.get(token)

        en_mode, en_blocks = read_dat_as_plains(en_file, entry)
        zh_mode, plains    = read_dat_as_plains(zh_file, entry)

        if not en_blocks:
            # 无法解密历史 en（缺少 key）时，仅在 zh 同为加密文件时保留旧兼容兜底
            if zh_mode and zh_mode != "plain":
                shutil.copy2(zh_file, target_dat)
                enc_matched += 1
            continue

        if not plains:
            continue  # 无法解密历史 zh 时跳过，绝不用未解密/错误数据覆盖

        # ── 统一准备繁中语料与低版本兜底（同时服务明文与加密 en）──────────
        zh_plains, _ = normalize_zh_plains(plains)
        latest_cjk_keys: set[str] = set()
        for blk in zh_plains:
            for ln in blk.splitlines():
                if "^" in ln and _has_cjk_in_line(ln):
                    latest_cjk_keys.add(ln.split("^", 1)[0])

        fallback_lines: list[str] = []
        raw_zh_all = list(plains)
        for _, old_zf in zh_all_map[token][:-1]:
            _, old_plains = read_dat_as_plains(old_zf, entry)
            if not old_plains:
                continue
            raw_zh_all.extend(old_plains)
            old_norm, _ = normalize_zh_plains(old_plains)
            for blk in old_norm:
                for ln in blk.splitlines():
                    if "^" in ln and _has_cjk_in_line(ln):
                        k = ln.split("^", 1)[0]
                        if k not in latest_cjk_keys:
                            fallback_lines.append(ln)

        merged_zh_plains = list(zh_plains)
        if fallback_lines:
            merged_zh_plains.append("\n".join(fallback_lines))

        out_blocks, cnt = _inject_blocks(en_blocks, merged_zh_plains, raw_zh_plains=raw_zh_all)
        if cnt <= 0:
            continue

        if en_mode == "plain":
            target_dat.write_text("\n".join(out_blocks), encoding="utf-8")
            plain_matched += 1
        else:
            k = key16(entry["key"])
            enc_lines = [
                cbc_encrypt(k, blk.encode("utf-8")) if en_mode == "cbc" else aes_ecb_encrypt(k, blk.encode("utf-8"))
                for blk in out_blocks
            ]
            target_dat.write_text("\n".join(enc_lines) + "\n", encoding="ascii")
            enc_matched    += 1
            swapped_tables += 1

        total_injected += cnt
        if entry and write_ndjson(out_blocks, entry["table"], out_ndjson_dir):
            ndjson_count += 1

    print("完成！")
    print(f"- 基准 en 表数: {len(en_map)}")
    if plain_matched:
        print(f"- [Beta 明文模式] 按 KEY 注入生成明文 .dat: {plain_matched} 个（共注入 {total_injected:,} 条繁中文本）")
    if enc_matched:
        print(f"- [正式版加密模式] 基于 en 解密注入并重加密 .dat: {enc_matched} 个（其中 {swapped_tables} 个已按 KEY 注入后重加密）")
    if plain_matched and enc_matched:
        print(f"- 混合模式总注入繁中文本: {total_injected:,} 条")
    print(f"- 生成明文 .ndjson: {ndjson_count} 个")
    print(f"- 输出目录: {args.out}")


if __name__ == "__main__":
    main()
