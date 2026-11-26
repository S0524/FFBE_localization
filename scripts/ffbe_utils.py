"""
FFBE_localization 公共工具函数库
提供跨脚本共用的工具函数，消除重复定义。
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

FILE_PAT = re.compile(r"^Ver(\d+)_([A-Za-z0-9]{8})\.dat$")
FFBE_IV  = b"dZMjkk8gFDzKHlsx"


# ──────────────────────────────────────────────────────────────────────────────
# .dat 文件扫描
# ──────────────────────────────────────────────────────────────────────────────

def get_latest_files(folder: Path) -> dict[str, tuple[int, Path]]:
    """
    扫描目录，返回 token -> (最大版本号, 文件路径) 的映射。
    仅处理文件名形如 Ver<n>_<token>.dat 的文件（取版本号最大者）。
    """
    latest: dict[str, tuple[int, Path]] = {}
    for p in folder.glob("Ver*_*.dat"):
        m = FILE_PAT.match(p.name)
        if not m:
            continue
        ver, tok = int(m.group(1)), m.group(2)
        if tok not in latest or ver > latest[tok][0]:
            latest[tok] = (ver, p)
    return latest


# ──────────────────────────────────────────────────────────────────────────────
# .dat 格式判断
# ──────────────────────────────────────────────────────────────────────────────

def is_unencrypted_dat(raw_bytes: bytes) -> bool:
    """
    判断 .dat 文件是否为明文（Beta 版）格式。

    明文 .dat 的每行为 KEY^TEXT 格式或 JSON 对象；
    加密 .dat 的每行为纯 Base64（无 ^ 字符，不以 { 开头）。

    返回 True  → 明文格式（可直接解析文本）。
    返回 False → 加密格式（需 AES 解密）。
    """
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if "^" in s or s.startswith("{"):
            return True
    return False


# 向后兼容别名：旧脚本中使用 is_plaintext_dat，含义与 is_unencrypted_dat 相同
is_plaintext_dat = is_unencrypted_dat


# ──────────────────────────────────────────────────────────────────────────────
# 哈希与 I/O
# ──────────────────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256 十六进制摘要，内存占用恒定（每次读 1 MB）。"""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """计算 bytes 的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(data).hexdigest()


def fmt_size(num_bytes: int) -> str:
    """将字节数格式化为人类可读字符串（B / KB / MB / GB）。"""
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / (1024 ** 3):.2f} GB"
    if num_bytes >= 1024 ** 2:
        return f"{num_bytes / (1024 ** 2):.1f} MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes} B"


# ──────────────────────────────────────────────────────────────────────────────
# FFBE AES 加解密与 .dat 逐行解密
# ──────────────────────────────────────────────────────────────────────────────

def key16(token: str) -> bytes:
    """将 8 位密钥扩展为 16 字节 NUL-padded AES-128 密钥。"""
    raw = token.encode("utf-8")[:16]
    return raw + b"\x00" * (16 - len(raw))


def unpad_pkcs7(raw: bytes) -> bytes:
    """移除最多两层 PKCS7 填充（兼容客户端双层填充）。"""
    for _ in range(2):
        pad = raw[-1] if raw else 0
        if not 1 <= pad <= 16 or raw[-pad:] != bytes([pad]) * pad:
            break
        raw = raw[:-pad]
    return raw


def aes_ecb_decrypt(key: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    if len(key) != 16:
        raise ValueError("key must be 16 bytes")
    if len(data) % 16:
        raise ValueError("ciphertext not 16-byte aligned")
    if not data:
        return b""
    op = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return op.update(data) + op.finalize()


def aes_ecb_encrypt(key: bytes, plain: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad    = 16 - (len(plain) % 16)
    padded = plain + bytes([pad]) * pad
    op     = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return base64.b64encode(op.update(padded) + op.finalize()).decode("ascii")


def cbc_decrypt(key: bytes, b64: str, iv: bytes = FFBE_IV) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    data = base64.b64decode(b64, validate=True)
    if len(data) % 16:
        raise ValueError(f"ciphertext not 16-byte aligned (len={len(data)})")
    op  = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    out = op.update(data) + op.finalize()
    if not out or not 1 <= out[-1] <= 16 or out[-out[-1] :] != bytes([out[-1]]) * out[-1]:
        raise ValueError(f"bad PKCS5 padding (len={len(out)})")
    return out[: -out[-1]]


def cbc_encrypt(key: bytes, plain: bytes, iv: bytes = FFBE_IV) -> str:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    pad  = 16 - (len(plain) % 16)
    data = plain + bytes([pad]) * pad
    op   = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return base64.b64encode(op.update(data) + op.finalize()).decode("ascii")


def decrypt_dat_lines(raw_bytes: bytes, key: bytes) -> tuple[str | None, list[str] | None]:
    """尝试以 CBC / ECB 模式解密加密 .dat 行，成功返回 (mode, plains)，否则返回 (None, None)。"""
    try:
        text = raw_bytes.decode("ascii")
    except UnicodeDecodeError:
        return None, None
    lines = [l for l in text.splitlines() if l.strip()]
    for mode in ("cbc", "ecb"):
        try:
            plains = []
            for line in lines:
                p = (
                    cbc_decrypt(key, line)
                    if mode == "cbc"
                    else aes_ecb_decrypt(key, base64.b64decode(line, validate=True))
                )
                plains.append(unpad_pkcs7(p).decode("utf-8"))
            return mode, plains
        except (ValueError, UnicodeError):
            continue
    return None, None


def load_manifest_cache_from_paths(*candidates: Path) -> dict:
    """按优先级查找并加载 manifest_cache.json，跳过 _cache_key 元数据。"""
    import json

    for path in candidates:
        if not path or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {k: v for k, v in raw.items() if k != "_cache_key"}
        except Exception:
            continue
    return {}


def read_dat_as_plains(dat_path: Path, entry: dict | None) -> tuple[str | None, list[str] | None]:
    """自动识别明文/加密 .dat 并返回 (mode, plain_blocks)；无法解密返回 (None, None)。"""
    raw = dat_path.read_bytes()
    if is_unencrypted_dat(raw):
        return "plain", [raw.decode("utf-8")]
    if entry:
        return decrypt_dat_lines(raw, key16(entry["key"]))
    return None, None
