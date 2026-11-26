"""
自动修补并重签名 FFBE 离线版 APK（纯离线模式 + 全盘更新目录重定向）。

主要修补内容：
1. AndroidManifest.xml：注入 MANAGE_EXTERNAL_STORAGE 权限，并在 DataDownloadActivity 启动时申请授权。
2. DataUpdates：全部更新读写重定向到 /sdcard/Download/updates，保留本地 latest.json 与 .zip 分包不被自动删除，
   禁用在线联网检查与 GitHub 回退校验，并在 prepareStartup 中检测到底包更新后立即安装。
3. 使用固定签名密钥库（默认 scripts/ffbe_zh.keystore，支持 FFBE_KS_PASS 环境变量）重打包签名。
"""
import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

SCRIPTS_DIR     = Path(__file__).resolve().parent
ROOT_DIR        = SCRIPTS_DIR.parent
TOOLS_DIR       = SCRIPTS_DIR / "tools"
DEFAULT_KEYSTORE = SCRIPTS_DIR / "ffbe_zh.keystore"
DEFAULT_KS_ALIAS = "ffbe_zh"
# 密码优先从环境变量读取，未设置则回退到默认值（向后兼容）
DEFAULT_KS_PASS  = os.environ.get("FFBE_KS_PASS", "ffbe_zh_123456")

CHECK_ALL_FILES_METHOD = """
.method private static checkAllFilesPermission(Landroid/app/Activity;)Z
    .registers 4

    sget v0, Landroid/os/Build$VERSION;->SDK_INT:I

    const/16 v1, 0x1e

    if-lt v0, v1, :cond_35

    invoke-static {}, Landroid/os/Environment;->isExternalStorageManager()Z

    move-result v0

    if-nez v0, :cond_35

    new-instance v0, Landroid/content/Intent;

    const-string v1, "android.settings.MANAGE_APP_ALL_FILES_ACCESS_PERMISSION"

    invoke-direct {v0, v1}, Landroid/content/Intent;-><init>(Ljava/lang/String;)V

    new-instance v1, Ljava/lang/StringBuilder;

    invoke-direct {v1}, Ljava/lang/StringBuilder;-><init>()V

    const-string v2, "package:"

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object v1

    invoke-virtual {p0}, Landroid/app/Activity;->getPackageName()Ljava/lang/String;

    move-result-object v2

    invoke-virtual {v1, v2}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object v1

    invoke-virtual {v1}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v1

    invoke-static {v1}, Landroid/net/Uri;->parse(Ljava/lang/String;)Landroid/net/Uri;

    move-result-object v1

    invoke-virtual {v0, v1}, Landroid/content/Intent;->setData(Landroid/net/Uri;)Landroid/content/Intent;

    invoke-virtual {p0, v0}, Landroid/app/Activity;->startActivity(Landroid/content/Intent;)V

    invoke-virtual {p0}, Landroid/app/Activity;->finish()V

    const/4 v0, 0x0

    return v0

    :cond_35
    const/4 v0, 0x1

    return v0
.end method
""".strip("\n")


def run_cmd(cmd: list[str], desc: str) -> None:
    """
    执行外部命令；失败时打印完整 stderr 后抛出异常。
    以字节模式捕获输出，再以系统本地编码（fallback: UTF-8 replace）解码，
    避免 uber-apk-signer 等 Java 工具在中文 Windows 下输出 GBK 字节导致 UnicodeDecodeError。
    """
    import locale
    print(f"[执行] {desc}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True)
    enc = locale.getpreferredencoding(False) or "utf-8"
    stdout = proc.stdout.decode(enc, errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode(enc, errors="replace") if proc.stderr else ""
    if stdout:
        print(stdout, end="")
    if proc.returncode != 0:
        if stderr:
            print(f"[stderr]\n{stderr}", end="")
        raise RuntimeError(f"{desc} 失败（退出码 {proc.returncode}）")


def find_tool(explicit: str, candidates: list[str]) -> str | None:
    """按显式参数 -> scripts/tools -> 系统 PATH 顺序查找外部工具（apktool / signer / zipalign）。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return str(p)
        found = shutil.which(explicit)
        if found:
            return found
        raise FileNotFoundError(f"指定的工具不存在: {explicit}")

    for name in candidates:
        local = TOOLS_DIR / name
        if local.exists():
            return str(local)
        found = shutil.which(name)
        if found:
            return found
    return None


def find_latest_apk(apk_dir: Path) -> Path:
    """返回指定目录下按修改时间排序的最新 .apk 文件。"""
    apks = sorted(apk_dir.glob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not apks:
        raise FileNotFoundError(f"在 {apk_dir} 中找不到任何 .apk 文件")
    return apks[0]


def ensure_keystore(keystore: Path, alias: str, password: str) -> Path:
    """若固定签名密钥库不存在，则调用 keytool 自动生成 100 年有效期 RSA 密钥库。"""
    if keystore.exists():
        return keystore
    keytool = shutil.which("keytool")
    if not keytool:
        raise RuntimeError(f"未找到 keytool，无法生成签名密钥库: {keystore}")
    keystore.parent.mkdir(parents=True, exist_ok=True)
    print(f"[密钥] 首次使用，正在生成固定签名密钥库（请妥善保存，以后覆盖安装需用同一把密钥）: {keystore}")
    run_cmd(
        [
            keytool,
            "-genkeypair", "-v",
            "-keystore",   str(keystore),
            "-alias",      alias,
            "-keyalg",     "RSA",
            "-keysize",    "2048",
            "-validity",   "36500",
            "-storepass",  password,
            "-keypass",    password,
            "-dname",      "CN=FFBE_localization, OU=Offline, O=FFBE_localization, L=Local, ST=Local, C=CN",
        ],
        "生成固定签名密钥库",
    )
    return keystore


def find_smali_file(decompiled_dir: Path, rel_path: str) -> Path:
    """遍历 apktool 解包后的 smali* 目录定位目标类的 Smali 文件。"""
    for smali_root in sorted(decompiled_dir.glob("smali*")):
        if not smali_root.is_dir():
            continue
        candidate = smali_root / rel_path
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"在 {decompiled_dir}\\smali* 中找不到 {rel_path}")


def patch_manifest(manifest_path: Path) -> bool:
    """向 AndroidManifest.xml 注入 MANAGE_EXTERNAL_STORAGE 权限（Android 11+ 本地更新目录读写必需）。"""
    text = manifest_path.read_text(encoding="utf-8")
    perm = '<uses-permission android:name="android.permission.MANAGE_EXTERNAL_STORAGE" />'
    if "android.permission.MANAGE_EXTERNAL_STORAGE" in text:
        print("[跳过] AndroidManifest.xml 已包含 MANAGE_EXTERNAL_STORAGE 权限")
        return False

    m = re.search(r"^([ \t]*)<application\b", text, flags=re.MULTILINE)
    if not m:
        raise RuntimeError("AndroidManifest.xml 中找不到 <application 节点")
    indent  = m.group(1)
    patched = text[: m.start()] + f"{indent}{perm}\n{indent}" + text[m.start() + len(indent) :]
    manifest_path.write_text(patched, encoding="utf-8")
    print("[完成] AndroidManifest.xml 已添加 MANAGE_EXTERNAL_STORAGE 权限")
    return True


def replace_method(text: str, method_name: str, replacer) -> str:
    """在 Smali 类文本中精确定位唯一同名方法并应用替换回调。"""
    pat = re.compile(
        rf"(^\.method\b[^\n]*\b{re.escape(method_name)}\([^\n]*\n.*?^\.end method)",
        flags=re.MULTILINE | re.DOTALL,
    )
    matches = list(pat.finditer(text))
    if not matches:
        raise RuntimeError(f"找不到方法: {method_name}")
    if len(matches) > 1:
        raise RuntimeError(f"找到多个同名方法 {method_name}，请手动确认")
    m       = matches[0]
    new_block = replacer(m.group(1))
    return text[: m.start()] + new_block + text[m.end() :]


def ensure_min_locals(body: str, min_locals: int, label: str) -> str:
    """确保方法的 .locals 至少为 min_locals（或等效的 .registers），避免局部变量覆盖 p0(this)。"""
    locals_m = re.search(r'^([ \t]*\.locals\s+)(\d+)\b', body, flags=re.MULTILINE)
    if locals_m:
        cur = int(locals_m.group(2))
        if cur < min_locals:
            print(f"[修正] {label} .locals {cur} -> {min_locals}")
            return body[: locals_m.start()] + f"{locals_m.group(1)}{min_locals}" + body[locals_m.end() :]
        return body
    regs_m = re.search(r'^([ \t]*\.registers\s+)(\d+)\b', body, flags=re.MULTILINE)
    if regs_m:
        cur = int(regs_m.group(2))
        # 保守按至少保留 2 个参数位估计；仅当明显不足时提示并提升
        min_regs = min_locals + 2
        if cur < min_regs:
            print(f"[修正] {label} .registers {cur} -> {min_regs}")
            return body[: regs_m.start()] + f"{regs_m.group(1)}{min_regs}" + body[regs_m.end() :]
    return body


def _patch_redirect_updates(method_text: str, method_label: str) -> str:
    """将 Context.getExternalFilesDir("updates") 重定向到共享目录 /sdcard/Download/updates。"""
    if '"/sdcard/Download/updates"' in method_text:
        print(f"[跳过] {method_label} 已重定向到 /sdcard/Download/updates")
        return method_text

    pat = re.compile(
        r'([ \t]*)const-string ([vp]\d+), "updates"\s+'
        r'invoke-virtual \{([vp]\d+), \2\}, Landroid/content/Context;->getExternalFilesDir\(Ljava/lang/String;\)Ljava/io/File;\s+'
        r'move-result-object ([vp]\d+)'
    )
    m = pat.search(method_text)
    if not m:
        raise RuntimeError(f"{method_label} 中未匹配到 getExternalFilesDir(\"updates\") 代码块")

    indent, str_reg, _ctx_reg, res_reg = m.group(1), m.group(2), m.group(3), m.group(4)
    updated = method_text
    if res_reg == str_reg:
        str_reg = "v1" if res_reg == "v0" else "v0"
        need    = 2 if str_reg == "v1" else 1
        updated = ensure_min_locals(updated, need, method_label)
        m       = pat.search(updated)

    replacement = (
        f"{indent}new-instance {res_reg}, Ljava/io/File;\n\n"
        f'{indent}const-string {str_reg}, "/sdcard/Download/updates"\n\n'
        f"{indent}invoke-direct {{{res_reg}, {str_reg}}}, Ljava/io/File;-><init>(Ljava/lang/String;)V"
    )
    print(f"[完成] {method_label} 已重定向为 /sdcard/Download/updates ({res_reg}, {str_reg})")
    return updated[: m.start()] + replacement + updated[m.end() :]



def _patch_validate_remove_github_url(body: str) -> str:
    """移除 DataUpdates.validate() 中硬编码的 GitHub URL 前缀校验。"""
    if "https://github.com/Elvlin/" not in body:
        print("[跳过] DataUpdates.validate 已移除 GitHub URL 前缀校验")
        return body

    # 匹配两种 Smali 编译写法：
    # 1) beta/新版：先读入寄存器 v6，再 invoke getString("url") + startsWith(v6) + if-eqz
    # 2) 旧版：在 getString("url") 和 startsWith 之间内联 const-string
    pat_v6 = re.compile(
        r'[ \t]*invoke-virtual \{([vp]\d+), ([vp]\d+)\}, Lorg/json/JSONObject;->getString\(Ljava/lang/String;\)Ljava/lang/String;\s+'
        r'move-result-object ([vp]\d+)\s+'
        r'invoke-virtual \{\3, ([vp]\d+)\}, Ljava/lang/String;->startsWith\(Ljava/lang/String;\)Z\s+'
        r'move-result \3\s+'
        r'if-eqz \3, :cond_\w+[ \t]*\n?'
    )
    pat_inline = re.compile(
        r'[ \t]*invoke-virtual \{([vp]\d+), ([vp]\d+)\}, Lorg/json/JSONObject;->getString\(Ljava/lang/String;\)Ljava/lang/String;\s+'
        r'move-result-object ([vp]\d+)\s+'
        r'const-string ([vp]\d+), "https://github\.com/Elvlin/[^"]+/releases/download/"\s+'
        r'invoke-virtual \{\3, \4\}, Ljava/lang/String;->startsWith\(Ljava/lang/String;\)Z\s+'
        r'move-result \3\s+'
        r'if-eqz \3, :cond_\w+[ \t]*\n?'
    )

    body_new, n1 = pat_v6.subn("", body)
    body_new, n2 = pat_inline.subn("", body_new)
    total = n1 + n2
    if total == 0:
        raise RuntimeError("DataUpdates.validate 中未匹配到 GitHub URL 前缀校验代码块")

    # 顺带清理残留的 GitHub URL 常量行，避免误以为仍在使用
    body_new = re.sub(
        r'[ \t]*const-string [vp]\d+, "https://github\.com/Elvlin/[^"]+/releases/download/"[ \t]*\n',
        "",
        body_new,
    )
    print(f"[完成] DataUpdates.validate 已移除 {total} 处 GitHub URL 硬编码校验")
    return body_new


def _patch_fetch_latest_offline_only(body: str) -> str:
    """移除 DataUpdates.fetchLatest() 中的在线 GitHub 拉取分支，仅保留本地读取。"""
    if "raw.githubusercontent.com" not in body:
        print("[跳过] DataUpdates.fetchLatest 已禁用在线拉取")
        return body

    # 定位到本地读取返回之后（return-object v0）的在线拉取入口
    pat_tail = re.compile(
        r'(^[ \t]*if-eqz (v\d+), (:cond_\w+)\s+'
        r'return-object \2\s+)'
        r'(?:\.line \d+\s+)?'
        r'\3\b.*\Z',
        flags=re.MULTILINE | re.DOTALL,
    )
    m = pat_tail.search(body)
    if not m:
        raise RuntimeError("DataUpdates.fetchLatest 中未匹配到在线回退代码块")

    is_two_arg = "(Landroid/content/Context;Z)" in body.splitlines()[0]
    label = m.group(3)
    if is_two_arg:
        offline_tail = (
            f"{label}\n"
            f"    if-eqz p1, :cond_offline_err\n\n"
            f"    return-object v1\n\n"
            f"    :cond_offline_err\n"
            f"    new-instance v0, Ljava/io/IOException;\n\n"
            f'    const-string v2, "Offline mode: /sdcard/Download/updates/latest.json is missing or invalid."\n\n'
            f"    invoke-direct {{v0, v2}}, Ljava/io/IOException;-><init>(Ljava/lang/String;)V\n\n"
            f"    throw v0\n"
            f".end method"
        )
    else:
        offline_tail = (
            f"{label}\n"
            f"    new-instance v0, Ljava/io/IOException;\n\n"
            f'    const-string v2, "Offline mode: /sdcard/Download/updates/latest.json is missing or invalid."\n\n'
            f"    invoke-direct {{v0, v2}}, Ljava/io/IOException;-><init>(Ljava/lang/String;)V\n\n"
            f"    throw v0\n"
            f".end method"
        )

    print("[完成] DataUpdates.fetchLatest 已移除 GitHub 在线拉取，仅保留本地 latest.json 读取")
    return body[: m.start()] + m.group(1) + offline_tail


def _patch_method_return_false(body: str, label: str) -> str:
    """将返回 Z 的方法体替换为恒返回 false（用于强制禁用联网状态检测）。"""
    header = body.splitlines()[0]
    if "const/4 v0, 0x0\n\n    return v0" in body:
        print(f"[跳过] {label} 已设为恒返回 false")
        return body
    print(f"[完成] {label} 已禁用（恒返回 false）")
    return (
        f"{header}\n"
        f"    .locals 1\n\n"
        f"    const/4 v0, 0x0\n\n"
        f"    return v0\n"
        f".end method"
    )


def _patch_method_throw_offline(body: str, label: str, msg: str) -> str:
    """将在线下载方法体替换为立即抛出本地离线提示异常。"""
    header = body.splitlines()[0]
    if msg in body:
        print(f"[跳过] {label} 已禁用在线下载")
        return body
    print(f"[完成] {label} 已禁用在线下载")
    return (
        f"{header}\n"
        f"    .locals 2\n"
        f"    .annotation system Ldalvik/annotation/Throws;\n"
        f"        value = {{\n"
        f"            Ljava/lang/Exception;\n"
        f"        }}\n"
        f"    .end annotation\n\n"
        f"    new-instance v0, Ljava/io/IOException;\n\n"
        f'    const-string v1, "{msg}"\n\n'
        f"    invoke-direct {{v0, v1}}, Ljava/io/IOException;-><init>(Ljava/lang/String;)V\n\n"
        f"    throw v0\n"
        f".end method"
    )


def _patch_prepare_startup_immediate_local(body: str) -> str:
    """
    在 prepareStartup 中，当 discoverUpdate 检测到本地 latest.json 有新 revision 并写出 available.json 后，
    立即跳转到安装流程（不再拖延到下一次重启才更新）。
    """
    if ":cond_local_apply" in body:
        print("[跳过] DataUpdates.prepareStartup 已启用同次启动自动更新")
        return body

    pat = re.compile(
        r'([ \t]*:cond_4\s+'
        r'if-nez p1, :cond_6\s+'
        r'invoke-static \{p0\}, Lcom/exvius/local/DataUpdates;->ready\(Landroid/content/Context;\)Z\s+'
        r'move-result (v\d+)\s+'
        r'if-eqz \2, :cond_6\s+'
        r'(?:\.line \d+\s+)?)'
        r'sget-boolean p1, Lcom/exvius/local/DataUpdates;->updateDiscoveredThisProcess:Z\s+'
        r'if-nez p1, :cond_5\s+'
        r'invoke-static \{p0, (v\d+), (v\d+)\}, Lcom/exvius/local/DataUpdates;->discoverUpdate\(Landroid/content/Context;Ljava/io/File;I\)V\s+'
        r'(?:\.line \d+\s+)?'
        r':cond_5\s+'
        r'return (v\d+)',
    )
    m = pat.search(body)
    if not m:
        print("[跳过] DataUpdates.prepareStartup 未匹配到延迟更新分支（保持默认行为）")
        return body

    prefix, tmp_reg, avail_reg, appver_reg, ret_reg = (
        m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    )
    # 注意保持 p1 = 0 不被覆盖，后续 :goto_0 的 installData 需要 p1 == 0
    replacement = (
        f"{prefix}"
        f"sget-boolean {tmp_reg}, Lcom/exvius/local/DataUpdates;->updateDiscoveredThisProcess:Z\n\n"
        f"    if-nez {tmp_reg}, :cond_5\n\n"
        f"    invoke-static {{p0, {avail_reg}, {appver_reg}}}, Lcom/exvius/local/DataUpdates;->discoverUpdate(Landroid/content/Context;Ljava/io/File;I)V\n\n"
        f"    invoke-virtual {{{avail_reg}}}, Ljava/io/File;->isFile()Z\n\n"
        f"    move-result {tmp_reg}\n\n"
        f"    if-eqz {tmp_reg}, :cond_5\n\n"
        f"    :cond_local_apply\n"
        f"    new-instance {tmp_reg}, Lorg/json/JSONObject;\n\n"
        f"    invoke-static {{{avail_reg}}}, Lcom/exvius/local/DataUpdates;->read(Ljava/io/File;)Ljava/lang/String;\n\n"
        f"    move-result-object v6\n\n"
        f"    invoke-direct {{{tmp_reg}, v6}}, Lorg/json/JSONObject;-><init>(Ljava/lang/String;)V\n\n"
        f"    goto :goto_0\n\n"
        f"    :cond_5\n"
        f"    return {ret_reg}"
    )
    print("[完成] DataUpdates.prepareStartup 已启用同次启动自动更新（无需重启或清数据）")
    return body[: m.start()] + replacement + body[m.end() :]


def patch_data_updates(smali_path: Path) -> None:
    """修补 DataUpdates.smali：路径重定向、禁止删包、禁用在线校验/下载并启用同次启动即更新。"""
    text = smali_path.read_text(encoding="utf-8")

    text = replace_method(
        text, "downloadFile",
        lambda body: _patch_redirect_updates(body, "DataUpdates.downloadFile"),
    )

    def patch_fetch_latest(body: str) -> str:
        body = _patch_redirect_updates(body, "DataUpdates.fetchLatest")

        if '"Cannot remove copied latest.json"' not in body:
            print("[跳过] DataUpdates.fetchLatest 已移除 latest.json 删除逻辑")
        else:
            del_pat = re.compile(
                r'([ \t]*)invoke-virtual \{([vp]\d+)\}, Ljava/io/File;->delete\(\)Z\s+'
                r'move-result \2\s+'
                r'if-eqz \2, (:cond_\w+)\s+'
                r'goto (:goto_\w+)\s+'
                r'\3\s+'
                r'new-instance ([vp]\d+), Ljava/io/IOException;\s+'
                r'const-string ([vp]\d+), "Cannot remove copied latest\.json"\s+'
                r'invoke-direct \{\5, \6\}, Ljava/io/IOException;-><init>\(Ljava/lang/String;\)V\s+'
                r'throw \5'
            )
            m = del_pat.search(body)
            if not m:
                raise RuntimeError("DataUpdates.fetchLatest 中未匹配到删除 latest.json 的代码块")
            indent, goto_label = m.group(1), m.group(4)
            print(f"[完成] DataUpdates.fetchLatest 已禁止删除 latest.json (goto {goto_label})")
            body = body[: m.start()] + f"{indent}goto {goto_label}" + body[m.end() :]

        return _patch_fetch_latest_offline_only(body)

    text = replace_method(text, "fetchLatest", patch_fetch_latest)

    def patch_clear_download(body: str) -> str:
        if "->delete()Z" not in body and '".verified"' not in body:
            print("[跳过] DataUpdates.clearDownload 已移除自动删除分包逻辑")
            return body

        clr_pat = re.compile(
            r'(?:[ \t]*\.line \d+\s+)?'
            r'[ \t]*invoke-static \{([vp]\d+), ([vp]\d+), ([vp]\d+)\}, Lcom/exvius/local/DataUpdates;->downloadFile\(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;\)Ljava/io/File;\s+'
            r'move-result-object ([vp]\d+)\s+'
            r'invoke-virtual \{\4\}, Ljava/io/File;->delete\(\)Z\s+'
            r'(?:\.line \d+\s+)?'
            r'const-string ([vp]\d+), "\.verified"\s+'
            r'invoke-static \{\1, \2, \5\}, Lcom/exvius/local/DataUpdates;->downloadFile\(Landroid/content/Context;Ljava/lang/String;Ljava/lang/String;\)Ljava/io/File;\s+'
            r'move-result-object ([vp]\d+)\s+'
            r'invoke-virtual \{\6\}, Ljava/io/File;->delete\(\)Z[ \t]*\n?'
        )
        m = clr_pat.search(body)
        if not m:
            raise RuntimeError("DataUpdates.clearDownload 中未匹配到自动删除 .zip/.verified 代码块")
        print("[完成] DataUpdates.clearDownload 已禁止自动删除 .zip 分包")
        return body[: m.start()] + body[m.end() :]

    text = replace_method(text, "clearDownload", patch_clear_download)
    text = replace_method(text, "validate", _patch_validate_remove_github_url)

    if re.search(r"^\.method\b[^\n]*\bonline\(", text, flags=re.MULTILINE):
        text = replace_method(
            text, "online",
            lambda body: _patch_method_return_false(body, "DataUpdates.online"),
        )
    if re.search(r"^\.method\b[^\n]*\bdownloadApp\(", text, flags=re.MULTILINE):
        text = replace_method(
            text, "downloadApp",
            lambda body: _patch_method_throw_offline(
                body,
                "DataUpdates.downloadApp",
                "Offline mode: online APK update is disabled.",
            ),
        )
    if re.search(r"^\.method\b[^\n]*\bresumableDownload\(", text, flags=re.MULTILINE):
        text = replace_method(
            text, "resumableDownload",
            lambda body: _patch_method_throw_offline(
                body,
                "DataUpdates.resumableDownload",
                "Offline mode: local pack zip missing or size mismatch in /sdcard/Download/updates.",
            ),
        )
    if re.search(r"^\.method\b[^\n]*\bprepareStartup\(", text, flags=re.MULTILINE):
        text = replace_method(text, "prepareStartup", _patch_prepare_startup_immediate_local)

    smali_path.write_text(text, encoding="utf-8")


def patch_data_download_activity(smali_path: Path) -> None:
    """修补 DataDownloadActivity.smali：在 onCreate 最前面检查并引导授予所有文件访问权限。"""
    text = smali_path.read_text(encoding="utf-8")

    def patch_on_create(body: str) -> str:
        if "->checkAllFilesPermission(Landroid/app/Activity;)Z" in body:
            print("[跳过] DataDownloadActivity.onCreate 已插入权限检查调用")
            return body

        super_pat = re.compile(
            r'([ \t]*invoke-super \{p0, p1\}, L[^;]+;->onCreate\(Landroid/os/Bundle;\)V[ \t]*\n)'
        )
        body_updated = ensure_min_locals(body, 1, "DataDownloadActivity.onCreate")
        m = super_pat.search(body_updated)
        if not m:
            raise RuntimeError("DataDownloadActivity.onCreate 中未找到 invoke-super onCreate")

        hook = (
            "\n"
            "    invoke-static {p0}, Lcom/exvius/local/DataDownloadActivity;->checkAllFilesPermission(Landroid/app/Activity;)Z\n\n"
            "    move-result v0\n\n"
            "    if-nez v0, :cond_perm_ok\n\n"
            "    return-void\n\n"
            "    :cond_perm_ok\n"
        )
        print("[完成] DataDownloadActivity.onCreate 已插入所有文件访问权限检查")
        return body_updated[: m.end()] + hook + body_updated[m.end() :]

    text = replace_method(text, "onCreate", patch_on_create)

    if ".method private static checkAllFilesPermission(Landroid/app/Activity;)Z" in text:
        print("[跳过] DataDownloadActivity 已包含 checkAllFilesPermission 方法")
    else:
        text = text.rstrip() + "\n\n" + CHECK_ALL_FILES_METHOD + "\n"
        print("[完成] DataDownloadActivity 已添加 checkAllFilesPermission 方法")

    smali_path.write_text(text, encoding="utf-8")


def patch_decompiled_dir(decompiled_dir: Path) -> None:
    """对 apktool 解包目录依次执行 Manifest、DataUpdates 与 DataDownloadActivity 修补。"""
    manifest = decompiled_dir / "AndroidManifest.xml"
    if not manifest.is_file():
        raise FileNotFoundError(f"找不到 {manifest}")
    patch_manifest(manifest)

    data_updates = find_smali_file(decompiled_dir, "com/exvius/local/DataUpdates.smali")
    print(f"[定位] {data_updates}")
    patch_data_updates(data_updates)

    activity = find_smali_file(decompiled_dir, "com/exvius/local/DataDownloadActivity.smali")
    print(f"[定位] {activity}")
    patch_data_download_activity(activity)


def build_apktool_cmd(apktool_path: str) -> list[str]:
    """根据扩展名构造调用 apktool（.jar 通过 java -jar，脚本/可执行文件直接调用）的命令前缀。"""
    if apktool_path.lower().endswith(".jar"):
        java = shutil.which("java")
        if not java:
            raise RuntimeError("使用 apktool.jar 需要系统已安装 java")
        return [java, "-jar", apktool_path]
    return [apktool_path]


def sign_apk(
    unsigned_apk:  Path,
    out_apk:       Path,
    signer_path:   str,
    zipalign_path: str | None,
    keystore:      Path,
    ks_alias:      str,
    ks_pass:       str,
) -> None:
    """使用 uber-apk-signer 或 Android SDK zipalign+apksigner 对回编后的 APK 对齐并签名。"""
    out_apk.parent.mkdir(parents=True, exist_ok=True)
    ensure_keystore(keystore, ks_alias, ks_pass)

    if signer_path.lower().endswith("uber-apk-signer.jar") or "uber-apk-signer" in Path(signer_path).name.lower():
        java = shutil.which("java")
        if not java:
            raise RuntimeError("运行 uber-apk-signer.jar 需要系统已安装 java")
        with tempfile.TemporaryDirectory(prefix="ffbe_sign_") as tmp_dir:
            cmd = [
                java, "-jar", signer_path,
                "--apks",    str(unsigned_apk),
                "--out",     tmp_dir,
                "--ks",      str(keystore),
                "--ksAlias", ks_alias,
                "--ksPass",  ks_pass,
                "--ksKeyPass", ks_pass,
            ]
            run_cmd(cmd, "对齐并签名 APK (uber-apk-signer)")
            signed_files = list(Path(tmp_dir).glob("*.apk"))
            if not signed_files:
                raise RuntimeError("uber-apk-signer 未生成签名后的 APK")
            shutil.move(str(signed_files[0]), str(out_apk))
        return

    # 使用 Android SDK zipalign + apksigner
    aligned_apk = unsigned_apk
    if zipalign_path:
        aligned_apk = unsigned_apk.with_name(unsigned_apk.stem + "_aligned.apk")
        run_cmd([zipalign_path, "-f", "-p", "4", str(unsigned_apk), str(aligned_apk)], "zipalign 4字节对齐")
    else:
        print("[警告] 未找到 zipalign，跳过对齐直接签名（Android 11+ 可能要求对齐）")

    sign_cmd = (
        [shutil.which("java") or "java", "-jar", signer_path]
        if signer_path.lower().endswith(".jar")
        else [signer_path]
    )
    sign_cmd += [
        "sign",
        "--ks",           str(keystore),
        "--ks-key-alias", ks_alias,
        "--ks-pass",      f"pass:{ks_pass}",
        "--key-pass",     f"pass:{ks_pass}",
        "--out",          str(out_apk),
        str(aligned_apk),
    ]
    run_cmd(sign_cmd, "签名 APK (apksigner)")


def main() -> None:
    """命令行入口：反编译官方 APK、注入纯离线补丁、回编并签名输出到 release_repack。"""
    ap = argparse.ArgumentParser(
        description="自动修补 FFBE 离线版 APK（MANAGE_EXTERNAL_STORAGE 权限 + 本地 /sdcard/Download/updates 重定向）"
    )
    ap.add_argument("--apk",           default="",                       help="输入官方 APK 路径（默认自动选用 source/apk 下最新 APK）")
    ap.add_argument("--out",           default="",                       help="输出修补后 APK 路径（默认 release_repack/<原名>-zh-patched.apk）")
    ap.add_argument("--decompiled-dir", default="",                      help="仅对已用 apktool d 解包好的目录执行 Smali/Manifest 修补（调试用）")
    ap.add_argument("--keep-temp",     action="store_true",              help="保留 apktool 临时解包目录以便排查")
    ap.add_argument("--apktool",       default="",                       help="指定 apktool.jar 或 apktool.bat 路径")
    ap.add_argument("--signer",        default="",                       help="指定 uber-apk-signer.jar 或 apksigner 路径")
    ap.add_argument("--zipalign",      default="",                       help="指定 zipalign 路径（配合 apksigner 使用）")
    ap.add_argument("--keystore",      default=str(DEFAULT_KEYSTORE),    help="固定签名密钥库路径（默认 scripts/ffbe_zh.keystore）")
    ap.add_argument("--ks-alias",      default=DEFAULT_KS_ALIAS,         help="密钥库别名")
    ap.add_argument(
        "--ks-pass",
        default=DEFAULT_KS_PASS,
        help="密钥库及私钥密码（也可通过环境变量 FFBE_KS_PASS 设置）",
    )
    args = ap.parse_args()

    if os.environ.get("FFBE_KS_PASS"):
        print("[密钥] 使用环境变量 FFBE_KS_PASS 中指定的密码")

    if args.decompiled_dir:
        patch_decompiled_dir(Path(args.decompiled_dir))
        print("\n=== 已完成对解包目录的修补 ===")
        return

    apk_path = Path(args.apk) if args.apk else find_latest_apk(ROOT_DIR / "source" / "apk")
    if not apk_path.is_file():
        raise FileNotFoundError(f"找不到输入 APK: {apk_path}")

    out_apk = (
        Path(args.out)
        if args.out
        else (ROOT_DIR / "release_repack" / f"{apk_path.stem}-zh-patched.apk")
    )

    apktool  = find_tool(args.apktool,  ["apktool.bat", "apktool", "apktool.jar"])
    signer   = find_tool(args.signer,   ["uber-apk-signer.jar", "apksigner.bat", "apksigner", "apksigner.jar"])
    zipalign = find_tool(args.zipalign, ["zipalign.exe", "zipalign"])

    missing = []
    if not apktool:
        missing.append("apktool（请将 apktool.jar 或 apktool.bat 放到 scripts\\tools\\，或加入 PATH）")
    if not signer:
        missing.append("签名工具（推荐将 uber-apk-signer.jar 放到 scripts\\tools\\，或配置 Android SDK 的 apksigner + zipalign）")
    if missing:
        raise SystemExit("缺少以下 APK 反编译/签名依赖：\n  - " + "\n  - ".join(missing))

    print(f"输入 APK: {apk_path}")
    print(f"输出 APK: {out_apk}")

    tmp_ctx = None
    if args.keep_temp:
        work_dir = Path(tempfile.mkdtemp(prefix="ffbe_apk_"))
        print(f"临时解包目录（保留）: {work_dir}")
    else:
        tmp_ctx  = tempfile.TemporaryDirectory(prefix="ffbe_apk_")
        work_dir = Path(tmp_ctx.name)

    try:
        dec_dir      = work_dir / "decompiled"
        fw_dir       = work_dir / "framework"
        fw_dir.mkdir(parents=True, exist_ok=True)
        unsigned_apk = work_dir / "unsigned.apk"
        apktool_cmd  = build_apktool_cmd(apktool)

        run_cmd(apktool_cmd + ["d", "-f", "-p", str(fw_dir), str(apk_path), "-o", str(dec_dir)], "反编译 APK (apktool d)")
        patch_decompiled_dir(dec_dir)
        run_cmd(apktool_cmd + ["b", "-p", str(fw_dir), str(dec_dir), "-o", str(unsigned_apk)],  "回编 APK (apktool b)")
        sign_apk(
            unsigned_apk  = unsigned_apk,
            out_apk       = out_apk,
            signer_path   = signer,
            zipalign_path = zipalign,
            keystore      = Path(args.keystore),
            ks_alias      = args.ks_alias,
            ks_pass       = args.ks_pass,
        )
        if not args.out:
            for old_patched in sorted(out_apk.parent.glob("*-zh-patched.apk")):
                if old_patched != out_apk:
                    old_patched.unlink()
                    print(f"[清理旧版] 已删除旧修补 APK: {old_patched.name}")
        print(f"\n=== APK 自动修补与签名完成！输出：{out_apk} ===")
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    main()
