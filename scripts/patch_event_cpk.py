"""
汉化全目录剧情与城镇对话（common_lang CPK）。

仅执行严格同包精确匹配，不做跨包全局语料库替换：
1. 若离线版 CPK 的 _sg.txt 自带 col2 官方繁中，直接对调启用；
2. 否则仅按 (相对目录, 去版本 CPK 名, _sg.txt 文件名, 行ID) 从历史存档对应包补回官方繁中；
3. 无匹配行保持英文原版。
"""
import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from ffbe_cpk import extract_sg_from_cpk, patch_cpk_sg_text, strip_ver


def _process_one_cpk(
    p:      Path,
    src:    Path,
    out:    Path,
    ext_zh: dict[str, dict[str, str]],
) -> tuple[int, int, int]:
    """处理单个 CPK 文件（供多进程池调用）。返回 (patched_count, builtin_zh_count, id_match_count)。"""
    data, zh_ok, tid, _ = patch_cpk_sg_text(p.read_bytes(), ext_zh, None)
    if zh_ok + tid > 0:
        dst = out / p.relative_to(src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return 1, zh_ok, tid
    return 0, 0, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src",    required=True, help="离线版 common_lang 目录（同时包含 sd 和 hd）")
    ap.add_argument("--zh-src", required=True, help="历史存档 common_lang 目录")
    ap.add_argument("--out",    required=True, help="输出 common_lang 目录")
    ap.add_argument(
        "--workers",
        type=int,
        default=min(8, (os.cpu_count() or 4)),
        help="并行进程数（默认 min(8, CPU核心数)）",
    )
    args = ap.parse_args()

    t_start = time.perf_counter()
    src    = Path(args.src)
    zh_src = Path(args.zh_src)
    out    = Path(args.out)

    # ── 第 1 遍：多进程按需 seek 扫描历史存档 CPK，构建同包精确行号索引 ────────
    print(f"1/2 正在扫描历史存档，构建同包行号精确索引（{args.workers} 进程并行）...")

    zh_files  = sorted(zh_src.rglob("*.cpk"))
    src_files = sorted(src.rglob("*.cpk"))

    # zh_by_cpk: (相对目录, 去版本后文件名) -> { sg文件名 -> { 行ID -> 繁中 } }
    zh_by_cpk: dict[tuple[str, str], dict[str, dict[str, str]]] = {}

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for p, sg in zip(zh_files, pool.map(extract_sg_from_cpk, zh_files, chunksize=64)):
            if not sg:
                continue
            rel_parent = p.parent.relative_to(zh_src).as_posix()
            cpk_key    = (rel_parent, strip_ver(p.name))
            id_maps    = zh_by_cpk.setdefault(cpk_key, {})
            for fn, rows in sg.items():
                fmap = id_maps.setdefault(fn, {})
                for lid, _, zh in rows:
                    fmap[lid] = zh

    t_pass1 = time.perf_counter() - t_start
    print(f"    索引构建完成（耗时 {t_pass1:.1f}s）：共收录 {len(zh_by_cpk)} 个同包对照 CPK（已关闭跨包全局替换）")

    # ── 第 2 遍：多进程并行修补所有 CPK ────────────────────────────────────
    print(f"2/2 正在以离线版为底包执行严格同包汉化（{args.workers} 进程并行）...")

    total   = len(src_files)
    patched = total_builtin = total_id = 0
    errors: list[str] = []

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for p in src_files:
            rel_parent = p.parent.relative_to(src).as_posix()
            ext_zh     = zh_by_cpk.get((rel_parent, strip_ver(p.name)), {})
            futures[pool.submit(_process_one_cpk, p, src, out, ext_zh)] = p.name

        for future in as_completed(futures):
            cpk_name = futures[future]
            try:
                p_count, tzh, tid = future.result()
                patched       += p_count
                total_builtin += tzh
                total_id      += tid
            except Exception as exc:
                errors.append(f"  {cpk_name}: {exc}")

    if errors:
        print(f"\n[警告] {len(errors)} 个 CPK 处理异常：")
        for e in errors:
            print(e)

    t_total = time.perf_counter() - t_start
    print(f"\n=== 全目录汉化完成（总耗时 {t_total:.1f}s） ===")
    print(f"共扫描离线版 CPK: {total} | 成功输出: {patched}")
    print(f"内置官方繁中对调: {total_builtin} 行 | 同包行号补回: {total_id} 行")


if __name__ == "__main__":
    main()
