"""
根据 release/latest.json 自动清理过期分包并核对/下载缺失更新。

主要优化：
1. 断点续传：利用 HTTP Range 请求，从上次中断处继续下载
2. 并行下载：使用 ThreadPoolExecutor 同时下载多个缺失分包（默认 3 并发）
3. 使用 ffbe_utils.sha256_file 避免重复定义
"""
import argparse
import shutil
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ffbe_utils import fmt_size, sha256_file

import json

ROOT_DIR    = Path(__file__).resolve().parent.parent
DEFAULT_RELEASE = ROOT_DIR / "release"
DEFAULT_APK_DIR = ROOT_DIR / "source" / "apk"

# 下载进度打印锁，防止多线程输出交错
_print_lock = threading.Lock()


def _safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


def download_file(
    url: str,
    dest: Path,
    expected_sha256: str = "",
    expected_size: int   = 0,
    resume: bool         = True,
) -> None:
    """
    支持断点续传的文件下载。

    若目标目录下已存在同名 .part 文件且服务器支持 Range 请求，
    则从上次中断处继续下载，不必从零重来。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    # 确定已下载的字节数（断点续传起点）
    start_byte = 0
    if resume and part.exists():
        start_byte = part.stat().st_size
        if expected_size and start_byte >= expected_size:
            start_byte = 0  # .part 大小异常，重新下载

    headers = {"User-Agent": "FFBE_localization-Sync/1.0"}
    if start_byte > 0:
        headers["Range"] = f"bytes={start_byte}-"
        _safe_print(f"  [续传] {dest.name} 从 {fmt_size(start_byte)} 继续下载...")

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
            # 206 = Partial Content（服务器接受 Range），其余视作从头开始
            if status == 206:
                file_mode = "ab"
            else:
                file_mode  = "wb"
                start_byte = 0  # 服务器不支持 Range，从头来

            content_len = int(resp.headers.get("Content-Length") or 0)
            total       = start_byte + content_len if content_len else expected_size
            done        = start_byte
            last_pct    = -10

            _safe_print(f"  [下载] {dest.name} <- {url}")
            with part.open(file_mode) as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if total > 0:
                        pct = int(done * 100 / total)
                        if pct >= last_pct + 10 or done == total:
                            _safe_print(f"    {dest.name}: {pct}% ({fmt_size(done)} / {fmt_size(total)})")
                            last_pct = pct

    except urllib.error.HTTPError as exc:
        if exc.code == 416:
            # Range Not Satisfiable：.part 超出文件大小，删除后重试
            _safe_print(f"  [重试] {dest.name}：Range 请求无效，从头下载")
            part.unlink(missing_ok=True)
            download_file(url, dest, expected_sha256, expected_size, resume=False)
            return
        raise

    # ── 完整性校验 ────────────────────────────────────────────────────────────
    actual_size = part.stat().st_size
    if expected_size and actual_size != expected_size:
        part.unlink(missing_ok=True)
        raise RuntimeError(
            f"下载大小不匹配: {dest.name}（实际 {actual_size} != 预期 {expected_size}）"
        )

    if expected_sha256:
        actual_sha = sha256_file(part)
        if actual_sha.lower() != expected_sha256.lower():
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 校验失败: {dest.name}\n  实际: {actual_sha}\n  预期: {expected_sha256}"
            )

    part.replace(dest)
    _safe_print(f"  [完成] {dest.name} ({fmt_size(dest.stat().st_size)})")


def _download_task(item: dict) -> str:
    """单个下载任务包装，供 ThreadPoolExecutor 调用。"""
    download_file(
        url            = item["url"],
        dest           = item["dest"],
        expected_sha256 = item.get("sha256", ""),
        expected_size   = item.get("size", 0),
    )
    return item["dest"].name


def main() -> None:
    ap = argparse.ArgumentParser(description="根据 release/latest.json 自动清理过期分包并核对缺失更新")
    ap.add_argument("--release",      default=str(DEFAULT_RELEASE), help="官方 release 目录（含 latest.json）")
    ap.add_argument("--apk-dir",      default=str(DEFAULT_APK_DIR), help="存放待处理官方 APK 的目录")
    ap.add_argument("--dry-run",      action="store_true",          help="仅预览要删除和缺失的文件，不实际操作")
    ap.add_argument("--download",     action="store_true",          help="自动下载 latest.json 中缺失的 .zip 分包与新版 APK")
    ap.add_argument("--verify-sha256", action="store_true",         help="同时校验已有文件的 SHA-256（较耗时）")
    ap.add_argument(
        "--parallel",
        type=int,
        default=3,
        metavar="N",
        help="并行下载的最大并发数（默认 3，设为 1 则串行）",
    )
    args = ap.parse_args()

    release_dir   = Path(args.release)
    apk_dir       = Path(args.apk_dir)
    manifest_path = release_dir / "latest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"找不到清单文件: {manifest_path}")

    data  = json.loads(manifest_path.read_text(encoding="utf-8"))
    packs = [p for p in data.get("packs", []) if not str(p.get("id", "")).startswith("zh-tw-")]
    if len(packs) < 5:
        raise RuntimeError(f"latest.json 中的有效 packs 数量异常（{len(packs)} 个），为安全起见中止清理")

    expected_zips: dict[str, dict] = {}
    for p in packs:
        url = str(p.get("url", "")).strip()
        if not url:
            continue
        fname = url.rsplit("/", 1)[-1]
        expected_zips[fname] = p

    app_info  = data.get("app") if isinstance(data.get("app"), dict) else {}
    app_url   = str(app_info.get("url", "")).strip()
    app_fname = app_url.rsplit("/", 1)[-1] if app_url else ""

    valid_names = set(expected_zips.keys())
    if app_fname:
        valid_names.add(app_fname)

    # 1. 查找并清理 release 目录中未被 latest.json 引用的 .zip 与 .apk
    obsolete: list[Path] = []
    for f in sorted(release_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in {".zip", ".apk"}:
            continue
        if f.name not in valid_names:
            obsolete.append(f)

    rev = data.get("revision", "?")
    ver = data.get("version",  "?")
    print(f"当前清单: version={ver} | revision={rev} | 分包={len(expected_zips)} 个")

    if obsolete:
        freed        = sum(f.stat().st_size for f in obsolete)
        action_label = "预计删除" if args.dry_run else "已删除"
        print(f"\n发现 {len(obsolete)} 个未适配/过期文件（共 {fmt_size(freed)}）：")
        for f in obsolete:
            sz = f.stat().st_size
            if not args.dry_run:
                f.unlink()
            print(f"  [{action_label}] {f.name} ({fmt_size(sz)})")
    else:
        print("\nrelease 目录干净，没有未适配的过期 .zip / .apk。")

    # 2. 核对缺失或大小/哈希异常的分包
    missing_packs: list[dict] = []
    for fname, p in expected_zips.items():
        fp       = release_dir / fname
        exp_size = int(p.get("size")   or 0)
        exp_sha  = str(p.get("sha256") or "")
        if not fp.is_file():
            missing_packs.append(p)
            continue
        if exp_size and fp.stat().st_size != exp_size:
            print(f"  [大小不符] {fname}: 本地 {fp.stat().st_size} != 清单 {exp_size}")
            missing_packs.append(p)
            continue
        if args.verify_sha256 and exp_sha:
            actual = sha256_file(fp)
            if actual.lower() != exp_sha.lower():
                print(f"  [校验失败] {fname}: {actual} != {exp_sha}")
                missing_packs.append(p)

    # 检查新版 APK
    apk_missing = False
    if app_fname:
        rel_apk = release_dir / app_fname
        src_apk = apk_dir / app_fname
        if not src_apk.is_file():
            if rel_apk.is_file():
                if not args.dry_run:
                    apk_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(rel_apk, src_apk)
                    print(f"\n[同步] 已将新版 APK 从 release 复制到 source/apk: {app_fname}")
                else:
                    print(f"\n[预计同步] 将新版 APK 从 release 复制到 source/apk: {app_fname}")
            else:
                apk_missing = True

    if missing_packs or apk_missing:
        print(f"\n缺失/需更新文件：分包 {len(missing_packs)} 个" + ("，新版 APK 1 个" if apk_missing else ""))
        for p in missing_packs:
            fname = p["url"].rsplit("/", 1)[-1]
            print(f"  - {fname} ({fmt_size(int(p.get('size') or 0))}) -> {p['url']}")
        if apk_missing:
            print(f"  - {app_fname} ({fmt_size(int(app_info.get('size') or 0))}) -> {app_url}")

        if args.download:
            # ── 构造下载任务列表 ──────────────────────────────────────────────
            tasks: list[dict] = []
            for p in missing_packs:
                fname = p["url"].rsplit("/", 1)[-1]
                tasks.append({
                    "url":    p["url"],
                    "dest":   release_dir / fname,
                    "sha256": str(p.get("sha256") or ""),
                    "size":   int(p.get("size")   or 0),
                })
            if apk_missing:
                tasks.append({
                    "url":    app_url,
                    "dest":   apk_dir / app_fname,
                    "sha256": str(app_info.get("sha256") or ""),
                    "size":   int(app_info.get("size")   or 0),
                })

            workers = min(args.parallel, len(tasks))
            if workers <= 1:
                # 串行下载（进度显示友好）
                print("\n开始下载缺失文件...")
                for task in tasks:
                    _download_task(task)
            else:
                # 并行下载（最多 N 并发）
                print(f"\n开始并行下载 {len(tasks)} 个文件（最多 {workers} 并发）...")
                errors: list[str] = []
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(_download_task, t): t for t in tasks}
                    for future in as_completed(futures):
                        task = futures[future]
                        try:
                            future.result()
                        except Exception as exc:
                            errors.append(f"  {task['dest'].name}: {exc}")
                if errors:
                    raise RuntimeError("以下文件下载失败：\n" + "\n".join(errors))
    else:
        print("\n所有清单分包与 APK 均已齐备。")

    # 3. 清理 source/apk 目录下的旧版 APK
    if app_fname and apk_dir.is_dir():
        old_apks     = [f for f in sorted(apk_dir.glob("*.apk")) if f.name != app_fname]
        current_ready = (apk_dir / app_fname).is_file()
        if old_apks:
            if args.dry_run:
                print(f"\n发现 source/apk 中有 {len(old_apks)} 个旧版 APK：")
                for f in old_apks:
                    print(f"  [预计删除] source/apk/{f.name} ({fmt_size(f.stat().st_size)})")
            elif current_ready:
                print(f"\n清理 source/apk 中的 {len(old_apks)} 个旧版 APK：")
                for f in old_apks:
                    sz = f.stat().st_size
                    f.unlink()
                    print(f"  [已删除] source/apk/{f.name} ({fmt_size(sz)})")
            else:
                print(f"\n[提示] 尚未下载新版 {app_fname}，暂保留 source/apk 中的旧版 APK。")


if __name__ == "__main__":
    main()
