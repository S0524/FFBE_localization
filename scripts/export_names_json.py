"""
从 work 明文 .dat 提取繁中 ID 对照表，生成 save_editor_web/names.json。
输出中包含 _meta 字段（生成时间与脚本版本），方便存档修改器判断词典是否需要更新。
"""
import argparse
import datetime
import json
from pathlib import Path

from ffbe_utils import get_latest_files, load_manifest_cache_from_paths, read_dat_as_plains

ROOT_DIR = Path(__file__).resolve().parent.parent

PREFIX_MAP = [
    ("MST_ITEM_NAME_",       "items"),
    ("MST_EQUIP_ITEM_NAME_", "equipment"),
    ("MST_MATERIA_NAME_",    "materia"),
    ("MST_UNIT_NAME_",       "units"),
]


def main():
    ap = argparse.ArgumentParser(description="从 work 明文 .dat 提取繁中 ID 对照表生成 names.json")
    ap.add_argument(
        "--work",
        default=str(ROOT_DIR / "work" / "localized_texts" / "en"),
        help="汉化输出 work 的 en 目录",
    )
    ap.add_argument(
        "--out",
        default=str(ROOT_DIR / "save_editor_web" / "names.json"),
        help="输出 names.json 路径",
    )
    ap.add_argument(
        "--manifest-cache",
        default=str(ROOT_DIR / "source" / "apk" / "manifest_cache.json"),
        help="manifest_cache.json 路径（用于解密加密 .dat 表）",
    )
    args = ap.parse_args()

    work_dir = Path(args.work)
    if not work_dir.is_dir():
        raise SystemExit(f"找不到目录: {work_dir}")

    manifest = load_manifest_cache_from_paths(
        Path(args.manifest_cache),
        ROOT_DIR / "work" / "manifest_cache.json",
        ROOT_DIR / "source" / "apk" / "manifest_cache.json",
    )

    result: dict = {
        "items":     {},
        "equipment": {},
        "materia":   {},
        "units":     {},
    }

    files   = get_latest_files(work_dir)
    scanned = 0
    for tok, (_, f) in sorted(files.items()):
        _, plains = read_dat_as_plains(f, manifest.get(tok))
        if not plains:
            continue
        text = "\n".join(plains)
        if "^" not in text:
            continue
        scanned += 1

        for line in text.splitlines():
            if "^" not in line:
                continue
            parts = line.split("^")
            if len(parts) < 2:
                continue
            key, val = parts[0].strip(), parts[1].strip()
            if not key or not val:
                continue

            for prefix, bucket in PREFIX_MAP:
                if key.startswith(prefix):
                    id_str = key[len(prefix):]
                    if id_str.isdigit():
                        result[bucket][id_str] = val
                    break

    # ── 添加元数据，方便存档修改器判断词典版本 ───────────────────────────────
    result["_meta"] = {
        "generated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "script":    "export_names_json.py",
        "entries":   sum(len(v) for k, v in result.items() if k != "_meta"),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 使用紧凑格式减小体积，加快网页端加载速度
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    total   = result["_meta"]["entries"]
    size_kb = out_path.stat().st_size / 1024
    print(f"扫描明文表: {scanned} 张")
    print(f"  道具 (items)     : {len(result['items']):,}")
    print(f"  装备 (equipment) : {len(result['equipment']):,}")
    print(f"  能力 (materia)   : {len(result['materia']):,}")
    print(f"  兵员 (units)     : {len(result['units']):,}")
    print(f"合计词条: {total:,} | 文件大小: {size_kb:.1f} KB")
    print(f"已生成: {out_path.resolve()}")
    print(f"生成时间: {result['_meta']['generated']}")


if __name__ == "__main__":
    main()
