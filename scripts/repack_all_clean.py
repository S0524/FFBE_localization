"""
全量去重重构纯净离线整合包（合并官方分包与 work/ 汉化产物）。

主要优化：
1. 流式 ZipInfo 拷贝（消除内存峰值：不再整块加载大文件）
2. 自适应压缩策略：文本类 level-6（保留时间戳），媒体类 ZIP_STORED
3. allowZip64=False（配合 1.8 GB 上限彻底规避 ZIP64 扩展头兼容性风险）
4. ProcessPoolExecutor 多进程并行打包与算 SHA-256（默认 2~3 进程）
"""
import argparse
import json
import os
import re
import shutil
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from zipfile import ZipFile, ZipInfo

from ffbe_utils import sha256_file

MAX_UNPACKED_BYTES = 1_800_000_000  # 每包解包上限 1.8 GB

DAT_ARC_PAT = re.compile(r"^native/localized_texts/en/(Ver\d+_(.+\.dat))$")

# 已高度压缩的媒体/资源格式：直接 ZIP_STORED，跳过无效二次压缩
STORED_EXTS = {
    ".cpk", ".mp4", ".usm", ".acb", ".awb",
    ".png", ".jpg", ".jpeg", ".webp", ".ogg", ".m4a", ".zip", ".gz", ".apk",
}

# 文本/JSON/数据类格式：使用较高压缩级别
TEXT_EXTS = {".dat", ".ndjson", ".json", ".txt", ".xml", ".csv", ".html"}


def _compress_type(arcname: str) -> int:
    ext = Path(arcname).suffix.lower()
    return zipfile.ZIP_STORED if ext in STORED_EXTS else zipfile.ZIP_DEFLATED


def _compress_level(arcname: str) -> int:
    """文本类用 level-6（压缩率更好），其余用 level-3（速度优先）。"""
    ext = Path(arcname).suffix.lower()
    return 6 if ext in TEXT_EXTS else 3


def _stream_copy_entry(
    zin: ZipFile,
    zout: ZipFile,
    arcname: str,
    comp_type: int,
) -> None:
    """
    流式将 zin 中的 arcname 条目拷贝到 zout，
    全程以 4 MB 块读取，不在内存中缓存整个条目。
    """
    src_info = zin.getinfo(arcname)
    out_info = ZipInfo(filename=arcname, date_time=src_info.date_time)
    out_info.compress_type = comp_type
    out_info.file_size     = src_info.file_size
    out_info.external_attr = src_info.external_attr
    with zin.open(src_info) as src_f:
        with zout.open(out_info, "w") as dst_f:
            shutil.copyfileobj(src_f, dst_f, 4 * 1024 * 1024)


def _collect_work_entries(
    work_dir: Path,
    official_dats: dict[str, set[str]],
) -> tuple[dict[str, tuple[str, Path, int]], int]:
    """收集 work 目录下的所有汉化产物，并同步展开到官方 .dat 的全部版本号。"""
    entries: dict[str, tuple[str, Path, int]] = {}
    zh_applied = 0
    mappings = [
        (work_dir / "localized_texts", "native/localized_texts"),
        (work_dir / "mst",             "native/mst"),
        (work_dir / "en",              "native/en"),
        (work_dir / "common_lang",     "native/common_lang"),
        (work_dir / "exvius",          "native/exvius"),
        (work_dir / "mst_decoded",     "static/mst_decoded"),
        (work_dir / "text_decoded",    "static/text_decoded"),
        (work_dir / "cpk_extract",     "static/cpk_extract"),
        (work_dir / "gacha_community", "static/gacha_community"),
        (work_dir / "client",          "static/client"),
    ]
    for sub_dir, arc_prefix in mappings:
        if not sub_dir.exists():
            print(f"[跳过] 目录不存在: {sub_dir}")
            continue
        for p in sorted(sub_dir.rglob("*")):
            if not p.is_file():
                continue
            rel     = p.relative_to(sub_dir).as_posix()
            arcname = f"{arc_prefix}/{rel}"
            fsz     = p.stat().st_size

            if (
                arcname.startswith("native/localized_texts/en/")
                and "_" in p.name
                and p.name.endswith(".dat")
            ):
                token   = p.name.split("_", 1)[1]
                targets = set(official_dats.get(token, set()))
                targets.add(p.name)
                for tname in sorted(targets):
                    t_arc = f"native/localized_texts/en/{tname}"
                    entries[t_arc] = ("file", p, fsz)
                    zh_applied    += 1
            else:
                entries[arcname] = ("file", p, fsz)
                zh_applied      += 1
    return entries, zh_applied


def _build_one_chunk(task: dict) -> dict:
    """
    独立构建单个分包并计算 SHA-256（供串行或 ProcessPoolExecutor 调用）。
    """
    idx              = task["idx"]
    total_chunks     = task["total_chunks"]
    chunk            = task["chunk"]
    out_dir          = Path(task["out_dir"])
    auto_base_url    = task["auto_base_url"]
    pack_id          = task["pack_id"]
    registry_arcname = task.get("registry_arcname")
    merged_registry  = task.get("merged_registry")

    zip_name = f"exvius-repack-{idx:03d}.zip"
    zip_path = out_dir / zip_name
    unpacked = 0

    by_zip:      dict[Path, list[tuple[str, int]]] = {}
    local_files: list[tuple[str, Path, int]]       = []
    for arcname, src_type, src_path, fsz in chunk:
        if src_type == "zip":
            by_zip.setdefault(Path(src_path), []).append((arcname, fsz))
        else:
            local_files.append((arcname, Path(src_path), fsz))

    with ZipFile(
        zip_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=3,
        allowZip64=False,
    ) as zout:
        # 来自官方 source zip：小文本走 level-6（保留时间戳），大文件流式拷贝
        for zp, members in by_zip.items():
            with ZipFile(zp, "r") as zin:
                for arcname, fsz in members:
                    comp  = _compress_type(arcname)
                    level = _compress_level(arcname)
                    if comp == zipfile.ZIP_DEFLATED and Path(arcname).suffix.lower() in TEXT_EXTS and fsz <= 16 * 1024 * 1024:
                        src_info = zin.getinfo(arcname)
                        out_info = ZipInfo(filename=arcname, date_time=src_info.date_time)
                        out_info.external_attr = src_info.external_attr
                        zout.writestr(out_info, zin.read(arcname), compress_type=comp, compresslevel=level)
                    else:
                        try:
                            _stream_copy_entry(zin, zout, arcname, comp)
                        except KeyError:
                            zout.writestr(arcname, zin.read(arcname), compress_type=comp, compresslevel=level)
                    unpacked += fsz

        # 来自 work 目录的汉化文件
        for arcname, fpath, fsz in local_files:
            comp  = _compress_type(arcname)
            level = _compress_level(arcname)
            zout.write(fpath, arcname, compress_type=comp, compresslevel=level)
            unpacked += fsz

        # registry.json 合并写入最后一包
        reg_written = False
        if registry_arcname and merged_registry:
            reg_bytes = (json.dumps(merged_registry, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            zout.writestr(registry_arcname, reg_bytes, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
            unpacked   += len(reg_bytes)
            reg_written = True

    size       = zip_path.stat().st_size
    sha        = sha256_file(zip_path)
    final_name = f"data_{sha}.zip"
    final_path = out_dir / final_name
    zip_path.replace(final_path)

    return {
        "idx":          idx,
        "total_chunks": total_chunks,
        "file_count":   len(chunk),
        "reg_written":  reg_written,
        "pack": {
            "id":           pack_id,
            "url":          f"{auto_base_url}/{final_name}",
            "size":         size,
            "unpackedSize": unpacked,
            "sha256":       sha,
        },
    }


def _split_chunks(items: list[tuple[str, tuple[str, Path, int]]]) -> list[list[tuple[str, str, str, int]]]:
    chunks: list[list[tuple[str, str, str, int]]] = []
    cur_chunk: list[tuple[str, str, str, int]]    = []
    cur_size = 0
    for arcname, (src_type, src_path, fsz) in items:
        if cur_chunk and cur_size + fsz > MAX_UNPACKED_BYTES:
            chunks.append(cur_chunk)
            cur_chunk, cur_size = [], 0
        cur_chunk.append((arcname, src_type, str(src_path), fsz))
        cur_size += fsz
    if cur_chunk:
        chunks.append(cur_chunk)
    return chunks


def _resolve_next_revision(src_data: dict, out_dir: Path, min_revision: int = 0) -> int:
    """计算下一版本号：始终严格高于源清单与输出目录已有 latest.json 的 revision。"""
    src_next = int(src_data.get("revision", 0)) + 1
    out_next = 0
    out_manifest = out_dir / "latest.json"
    if out_manifest.is_file():
        try:
            old = json.loads(out_manifest.read_text(encoding="utf-8"))
            out_next = int(old.get("revision", 0)) + 1
        except Exception:
            pass
    return max(src_next, out_next, min_revision)


def _rewrite_app_url(data: dict, base_url: str) -> None:
    app = data.get("app")
    if isinstance(app, dict) and app.get("url"):
        fname = str(app["url"]).rsplit("/", 1)[-1]
        app["url"] = f"{base_url}/{fname}"


def main():
    """命令行入口：合并官方分包与汉化产物目录，去重并输出纯净离线整合包。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--src",        required=True, help="原 release 目录（只读，含官方 latest.json 和所有官方 zip）")
    ap.add_argument("--work-out",   default="",    help="汉化输出目录（如 D:\\下载\\FFBE_localization\\work），留空则仅重构官方包")
    ap.add_argument("--out",        required=True, help="重构后的输出目录（新建独立目录）")
    ap.add_argument("--base-url",     default="local://updates", help="latest.json 中的占位根地址（纯离线模式下本地直接读取分包）")
    ap.add_argument("--min-revision", type=int, default=0,       help="强制指定 latest.json 的最小 revision")
    ap.add_argument("--workers",    type=int, default=min(3, (os.cpu_count() or 2)), help="并行打包进程数（默认 min(3, CPU核心数)）")
    args = ap.parse_args()

    src_dir = Path(args.src)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = src_dir / "latest.json"
    data          = json.loads(manifest_path.read_text(encoding="utf-8"))
    orig_packs    = [p for p in data.get("packs", []) if not str(p.get("id", "")).startswith("zh-tw-")]
    auto_base_url = args.base_url.rstrip("/") or "local://updates"

    # 1. 按 latest.json 严格顺序收集官方分包（跳过旧 zh-tw- 汉化包）
    zip_list: list[Path] = []
    for p in orig_packs:
        fname = p.get("url", "").rsplit("/", 1)[-1]
        zp    = src_dir / fname
        if zp.exists():
            zip_list.append(zp)
        else:
            print(f"[警告] 缺少分包，已跳过: {fname}")

    # 2. 扫描官方包去重 + 收集官方 .dat 全版本号 + 合并 registry.json
    winners: dict[str, tuple[str, Path, int]] = {}
    official_dats: dict[str, set[str]]        = {}
    merged_registry: dict                     = {}
    registry_arcname: str | None              = None
    total_seen = 0

    print(f"正在按顺序扫描 {len(zip_list)} 个官方分包并去重...")
    for zp in zip_list:
        with ZipFile(zp, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total_seen += 1
                name = info.filename

                if name == "registry.json" or name.endswith("/registry.json"):
                    registry_arcname = name
                    try:
                        reg_data = json.loads(zf.read(name).decode("utf-8"))
                        if isinstance(reg_data, dict):
                            merged_registry.update(reg_data)
                    except Exception:
                        pass
                    continue

                m = DAT_ARC_PAT.match(name)
                if m:
                    full_name, token = m.group(1), m.group(2)
                    official_dats.setdefault(token, set()).add(full_name)

                winners[name] = ("zip", zp, info.file_size)

    # 3. 叠加汉化产物（含 .dat 多版本号同步）
    zh_applied = 0
    if args.work_out:
        work = Path(args.work_out)
        print(f"正在直接叠加汉化产物目录: {work} ...")
        work_entries, zh_applied = _collect_work_entries(work, official_dats)
        winners.update(work_entries)

    print(
        f"扫描完成：官方总文件 {total_seen:,} 个 | 叠加汉化条目 {zh_applied:,} 个"
        f" -> 最终保留 {len(winners):,} 个纯净文件"
    )

    # 4. 按 1.8 GB 解包上限切分新分包
    items  = sorted(winners.items(), key=lambda x: x[0])
    chunks = _split_chunks(items)

    # 5. 并行或串行写入各分包
    tasks = []
    for idx, chunk in enumerate(chunks, 1):
        is_last = idx == len(chunks)
        tasks.append({
            "idx":              idx,
            "total_chunks":     len(chunks),
            "chunk":            chunk,
            "out_dir":          str(out_dir),
            "auto_base_url":    auto_base_url,
            "pack_id":          f"repack-{idx:03d}",
            "registry_arcname": registry_arcname if is_last else None,
            "merged_registry":  merged_registry  if is_last else None,
        })

    workers = max(1, min(args.workers, len(tasks)))
    results_by_idx: dict[int, dict] = {}

    if workers == 1:
        for t in tasks:
            res = _build_one_chunk(t)
            results_by_idx[res["idx"]] = res
            pk = res["pack"]
            print(
                f"[{res['idx']}/{res['total_chunks']}] 已生成 {pk['url'].rsplit('/', 1)[-1]}: "
                f"{res['file_count']:,} 文件 | 压缩 {pk['size'] / 1024 / 1024:.1f} MB | 解包 {pk['unpackedSize'] / 1024 / 1024:.1f} MB"
            )
    else:
        print(f"正在使用 {workers} 进程并行重构 {len(tasks)} 个分包...")
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_build_one_chunk, t): t["idx"] for t in tasks}
            for fut in as_completed(futures):
                res = fut.result()
                results_by_idx[res["idx"]] = res
                pk = res["pack"]
                print(
                    f"[{res['idx']}/{res['total_chunks']}] 已完成 {pk['url'].rsplit('/', 1)[-1]}: "
                    f"{res['file_count']:,} 文件 | 压缩 {pk['size'] / 1024 / 1024:.1f} MB | 解包 {pk['unpackedSize'] / 1024 / 1024:.1f} MB"
                )

    ordered     = [results_by_idx[i] for i in range(1, len(chunks) + 1)]
    new_packs   = [r["pack"] for r in ordered]
    reg_written = any(r["reg_written"] for r in ordered)

    # 6. 生成全新的 latest.json
    data["packs"]          = new_packs
    data["revision"]       = _resolve_next_revision(data, out_dir, args.min_revision)
    _rewrite_app_url(data, auto_base_url)
    data["downloadBytes"]  = sum(p["size"]         for p in new_packs)
    data["installedBytes"] = sum(p["unpackedSize"] for p in new_packs)
    data["files"]          = len(winners) + (1 if reg_written else 0)

    out_manifest = out_dir / "latest.json"
    out_manifest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\n=== 重构完成！输出目录: {out_dir} ===")
    print(
        f"总压缩体积: {data['downloadBytes'] / 1024 / 1024 / 1024:.2f} GB | "
        f"总解包体积: {data['installedBytes'] / 1024 / 1024 / 1024:.2f} GB"
    )


if __name__ == "__main__":
    main()
