"""
同步繁体中文 UI 图片、界面 CPK 素材（native/en/sd）以及预解包目录（static/cpk_extract/en）。

1. 按去版本号后的相对路径与基础文件名匹配 native/en/sd -> work/en/sd。
2. 若提供 --en-cpk-extract 与 --out-cpk-extract，则从 zh/sd 对应 CPK 中提取繁中条目
   覆盖 static/cpk_extract/en 下的同名文件（如 area / banner / gacha 解包图）。
"""
import argparse
import re
import shutil
from collections import Counter
from pathlib import Path

from ffbe_cpk import extract_files_from_cpk

VER_PAT = re.compile(r"^Ver(\d+)_(.+)$")


def strip_ver(name: str) -> str:
    """去除文件名前缀 Ver123_，提取原始基础文件名。"""
    m = VER_PAT.match(name)
    return m.group(2) if m else name


def index_zh(zh_root: Path):
    """扫描繁中素材目录，分别建立带版本号与无版本号的文件索引。"""
    ver_map:   dict[tuple[Path, str], tuple[int, Path]] = {}
    plain_map: dict[tuple[Path, str], Path]             = {}

    for p in zh_root.rglob("*"):
        if not p.is_file():
            continue
        rel_dir = p.parent.relative_to(zh_root)
        m = VER_PAT.match(p.name)
        ver  = int(m.group(1)) if m else -1
        base = m.group(2) if m else p.name
        if m:
            key = (rel_dir, base)
            if key not in ver_map or ver > ver_map[key][0]:
                ver_map[key] = (ver, p)
        else:
            plain_map[(rel_dir, p.name)] = p

    return ver_map, plain_map


def find_zh_file(rel_dir: Path, filename: str, ver_map: dict, plain_map: dict) -> Path | None:
    """按相对目录与基础文件名查找对应的繁中文件（优先匹配带版本号的高版本条目）。"""
    m = VER_PAT.match(filename)
    base = m.group(2) if m else filename
    if (rel_dir, base) in ver_map:
        return ver_map[(rel_dir, base)][1]
    if (rel_dir, filename) in plain_map:
        return plain_map[(rel_dir, filename)]
    return None


def _png_dim(p: Path) -> tuple[int, int] | None:
    """读取 PNG 文件头中的宽高尺寸（像素），非 PNG 或读取失败返回 None。"""
    try:
        with p.open("rb") as f:
            hdr = f.read(24)
        if len(hdr) == 24 and hdr[:8] == b"\x89PNG\r\n\x1a\n":
            w, h = hdr[16:20], hdr[20:24]
            return int.from_bytes(w, "big"), int.from_bytes(h, "big")
    except OSError:
        pass
    return None


def sync_cpk_extract(en_extract: Path, out_extract: Path, ver_map: dict, plain_map: dict) -> tuple[int, int]:
    """根据 static/cpk_extract/en 的目录结构，从 zh/sd 对应 CPK 中提取繁中素材。"""
    if not en_extract.is_dir():
        print(f"[跳过] cpk_extract 源目录不存在: {en_extract}")
        return 0, 0

    # 按 CPK 分组：<rel_dir>/<cpk_dir>/<file>
    grouped: dict[tuple[Path, str], list[Path]] = {}
    for p in en_extract.rglob("*"):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(en_extract).parts
        if len(rel_parts) < 2:
            continue
        cpk_dir_rel = Path(*rel_parts[:-1])
        grouped.setdefault((cpk_dir_rel.parent, cpk_dir_rel.name), []).append(p)

    extracted = missed = 0
    for (cat_dir, cpk_dir_name), files in sorted(grouped.items()):
        cpk_filename = strip_ver(cpk_dir_name) + ".cpk"
        zh_cpk = find_zh_file(cat_dir, cpk_filename, ver_map, plain_map)
        if zh_cpk is None:
            # 再尝试直接按去掉 Ver 前缀后的文件名查找
            entry = ver_map.get((cat_dir, cpk_filename))
            zh_cpk = entry[1] if entry else plain_map.get((cat_dir, cpk_filename))
        if zh_cpk is None:
            missed += len(files)
            continue

        target_names = {f.name for f in files}
        extracted_map = extract_files_from_cpk(zh_cpk, target_names)
        for f in files:
            data = extracted_map.get(f.name)
            if data is not None:
                dst = out_extract / f.relative_to(en_extract)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
                extracted += 1
            else:
                missed += 1

    return extracted, missed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--en-sd",           required=True, help="离线版原版 en/sd 目录")
    ap.add_argument("--zh-sd",           required=True, help="CDN 档案里的 zh/sd 目录")
    ap.add_argument("--out",             required=True, help="输出目录（例如 work/en/sd）")
    ap.add_argument("--en-cpk-extract",  default="",    help="离线版 static/cpk_extract/en 目录")
    ap.add_argument("--out-cpk-extract", default="",    help="输出 work/cpk_extract/en 目录")
    args = ap.parse_args()

    en_root  = Path(args.en_sd)
    zh_root  = Path(args.zh_sd)
    out_root = Path(args.out)

    ver_map, plain_map = index_zh(zh_root)
    copied = missed = skipped_dim = 0
    copied_ext: Counter[str] = Counter()
    missed_ext: Counter[str] = Counter()

    for p in en_root.rglob("*"):
        if not p.is_file():
            continue
        rel_dir = p.parent.relative_to(en_root)
        ext     = p.suffix.lower() or "(无后缀)"
        src     = find_zh_file(rel_dir, p.name, ver_map, plain_map)
        if src and p.suffix.lower() == ".png":
            sd, dd = _png_dim(p), _png_dim(src)
            if sd is not None and dd is not None and sd != dd:
                skipped_dim += 1
                src = None

        if src:
            dst = out_root / rel_dir / p.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1
            copied_ext[ext] += 1
        else:
            missed += 1
            missed_ext[ext] += 1

    cpk_hit   = copied_ext.get(".cpk", 0)
    cpk_total = cpk_hit + missed_ext.get(".cpk", 0)
    png_hit   = copied_ext.get(".png", 0)
    png_total = png_hit + missed_ext.get(".png", 0)

    dim_note = f"（跳过尺寸不符 {skipped_dim} 个）" if skipped_dim else ""
    print(f"完成！成功匹配并生成中文素材: {copied} 个，未匹配保留原版: {missed} 个{dim_note}")
    print(f"  - UI 界面包 (.cpk): 匹配 {cpk_hit} / {cpk_total}")
    print(f"  - 活动/召唤横幅 (.png): 匹配 {png_hit} / {png_total}（其余多为离线版复原的独立英文横幅，无繁中档案）")
    print(f"输出目录: {out_root}")

    if args.en_cpk_extract and args.out_cpk_extract:
        ext_ok, ext_miss = sync_cpk_extract(
            Path(args.en_cpk_extract),
            Path(args.out_cpk_extract),
            ver_map,
            plain_map,
        )
        print(f"CPK 预解包图集同步: 提取繁中 {ext_ok:,} 个，未匹配保留原版 {ext_miss:,} 个 -> {args.out_cpk_extract}")


if __name__ == "__main__":
    main()
