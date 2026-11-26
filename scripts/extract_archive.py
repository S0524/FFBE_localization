"""
从 FFBE_GL.zip 档案馆精确提取汉化所需目录（localized_texts/zh、zh/sd、common_lang）。

支持基于源压缩包 size + mtime 的指纹缓存（.archive_cache.json），
压缩包未变且上次完整提取成功时直接跳过，传入 --force 可强制重新提取。
"""
import argparse
import json
import shutil
import zipfile
from pathlib import Path

ANCHOR  = "dlc_assets_prod/"
TARGETS = ("localized_texts/zh/", "zh/sd/", "common_lang/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip",   required=True, help="FFBE_GL.zip 完整封存包路径")
    ap.add_argument("--out",   required=True, help="解压目标目录（如 D:\\下载\\FFBE_localization\\source\\archive）")
    ap.add_argument("--force", action="store_true", help="强制重新解压")
    args = ap.parse_args()

    zip_path = Path(args.zip)
    out_dir  = Path(args.out)

    if not zip_path.exists():
        raise FileNotFoundError(f"找不到档案馆压缩包: {zip_path}")

    z_stat      = zip_path.stat()
    cache_key   = f"{z_stat.st_size}:{int(z_stat.st_mtime * 1000)}"
    cache_file  = out_dir / ".archive_cache.json"
    dirs_ready  = all((out_dir / t.rstrip("/")).is_dir() for t in TARGETS)

    if not args.force and dirs_ready and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key:
                print(
                    f"[缓存命中] 档案馆素材已完整提取于 {out_dir}"
                    f"（共 {cached.get('files', '?'):,} 个文件，如需重提请加 --force）"
                )
                return
        except Exception:
            pass

    print(f"正在从 {zip_path.name} 精准提取 {ANCHOR} 下的汉化必需目录...")
    extracted = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            norm = info.filename.replace("\\", "/")

            # 精准锚定 dlc_assets_prod/，避开 ffbe-gl-archived 等其他镜像目录
            idx = norm.find(ANCHOR)
            if idx == -1:
                continue
            rel_path = norm[idx + len(ANCHOR) :]

            if not rel_path.startswith(TARGETS):
                continue

            target = out_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, 4 * 1024 * 1024)
            extracted += 1

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(
            json.dumps({"cache_key": cache_key, "files": extracted}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    print(f"=== 档案馆提取完成！共提取 {extracted:,} 个文件到 {out_dir} ===")


if __name__ == "__main__":
    main()
