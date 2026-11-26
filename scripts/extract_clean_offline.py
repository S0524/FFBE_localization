"""
从 release 目录中提取纯净官方底包（仅 localized_texts/en、en/sd、common_lang）。
只保留各逻辑文件的最高版本，剔除重复/旧版 Ver*_ 冗余。

主要优化：
1. 清空旧 native 目录前打印大小预估并支持 --no-clean 增量模式
2. 添加 --force 参数强制重新提取（即使已有缓存）
"""
import argparse
import json
import re
import shutil
import zipfile
from pathlib import Path

from ffbe_utils import sha256_bytes

VER_PAT = re.compile(r"^Ver(\d+)_(.+)$")
NEEDED_PREFIXES = (
    "native/localized_texts/en/",
    "native/en/sd/",
    "native/common_lang/",
    "native/exvius/equipment-enhancement/",
    "static/cpk_extract/en/",
    "static/gacha_community/art/",
    "static/client/game-assets.apk",
)


def _dir_size_mb(path: Path) -> float:
    """递归统计目录大小（MB）。"""
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return total / 1024 / 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release",  required=True, help="包含官方 zip 和 latest.json 的目录")
    ap.add_argument(
        "--out",
        required=True,
        help="输出纯净 offline 目录（如 D:\\下载\\FFBE_localization\\source\\offline）",
    )
    ap.add_argument(
        "--no-clean",
        action="store_true",
        help="不清空旧的 offline/native 目录，直接覆盖（增量模式，更快但可能残留旧文件）",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="即使 offline/native 已存在也强制重新提取",
    )
    args = ap.parse_args()

    release_dir   = Path(args.release)
    out_dir       = Path(args.out)
    manifest_path = release_dir / "latest.json"

    if not manifest_path.exists():
        raise FileNotFoundError(f"找不到清单文件: {manifest_path}")

    native_out    = out_dir / "native"
    static_out    = out_dir / "static"
    cache_marker  = out_dir / ".offline_cache.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha   = sha256_bytes(manifest_bytes)

    if native_out.exists() and not args.force and not args.no_clean and cache_marker.is_file():
        try:
            cached = json.loads(cache_marker.read_text(encoding="utf-8"))
            if (
                cached.get("manifest_sha256") == manifest_sha
                and cached.get("prefixes") == list(NEEDED_PREFIXES)
            ):
                print(
                    f"[缓存命中] 纯净底包已是最新（revision={cached.get('revision', '?')}，"
                    f"共 {cached.get('files', '?')} 个文件），跳过重复提取（加 --force 可强制重提）。"
                )
                return
        except Exception:
            pass

    data = json.loads(manifest_bytes.decode("utf-8"))
    official_zips: list[Path] = []
    for p in data.get("packs", []):
        if str(p.get("id", "")).startswith("zh-tw-"):
            continue
        url   = p.get("url", "")
        fname = url.rsplit("/", 1)[-1]
        zp    = release_dir / fname
        if zp.exists():
            official_zips.append(zp)
        else:
            print(f"[警告] 清单中的文件不存在，已跳过: {fname}")

    # 第一遍扫描：按顺序记录每个逻辑文件的最终胜出版本
    winners: dict[tuple[str, str], tuple[int, Path, str]] = {}
    skipped_old = 0

    for zp in official_zips:
        with zipfile.ZipFile(zp, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if not name.startswith(NEEDED_PREFIXES):
                    continue

                parent, fname_only = name.rsplit("/", 1)
                m = VER_PAT.match(fname_only)
                if m:
                    ver, base = int(m.group(1)), m.group(2)
                else:
                    ver, base = -1, fname_only

                key = (parent, base)
                if key in winners:
                    old_ver = winners[key][0]
                    if ver >= old_ver:
                        winners[key] = (ver, zp, name)
                    skipped_old += 1
                else:
                    winners[key] = (ver, zp, name)

    print(f"扫描完成：共锁定 {len(winners)} 个最新必需文件，已剔除 {skipped_old} 个旧版本/重复文件。")

    # 清空或保留旧 native / static 目录
    if native_out.exists() or static_out.exists():
        if args.no_clean:
            print(f"[增量模式] 保留现有目录，直接覆盖更新文件: {out_dir}")
        else:
            for d in (native_out, static_out):
                if d.exists():
                    size_mb = _dir_size_mb(d)
                    print(f"正在清空旧目录（约 {size_mb:.0f} MB）: {d}")
                    shutil.rmtree(d)
            print("清空完成。")

    # 按 zip 分组提取，避免反复开关压缩包
    by_zip: dict[Path, list[str]] = {}
    for _, zp, member in winners.values():
        by_zip.setdefault(zp, []).append(member)

    extracted = 0
    for zp, members in by_zip.items():
        print(f"正在提取: {zp.name} ({len(members)} 个文件)")
        with zipfile.ZipFile(zp, "r") as zf:
            for member in members:
                target = out_dir / member
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
                extracted += 1

    try:
        cache_marker.write_text(
            json.dumps(
                {
                    "manifest_sha256": manifest_sha,
                    "prefixes": list(NEEDED_PREFIXES),
                    "revision": data.get("revision"),
                    "files": extracted,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    print(f"\n=== 纯净底包构建完成！共提取 {extracted} 个文件到 {native_out} ===")


if __name__ == "__main__":
    main()
