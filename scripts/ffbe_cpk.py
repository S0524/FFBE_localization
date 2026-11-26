"""
FFBE_localization CPK 格式解析与修补库

提供 CRI CPK 档案的 TOC 解析、CRILAYLA 解压、_sg.txt 文本提取与回写功能。
将原先散落在 patch_event_cpk.py 中的解析逻辑集中为独立模块，消除重复代码。
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# 语言判断工具
# ──────────────────────────────────────────────────────────────────────────────

_CJK_RE   = re.compile(r"[\u4e00-\u9fff]")
_KANA_RE  = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_VER_RE   = re.compile(r"^Ver\d+_(.+)$")
_TAG_RE   = re.compile(r"<[^>]+>")
_ALPHA_RE = re.compile(r"[A-Za-z]")


def is_zh(text: str) -> bool:
    """判断字符串是否包含中文字符（但不含日文假名）。"""
    return bool(text.strip()) and bool(_CJK_RE.search(text)) and not bool(_KANA_RE.search(text))


def is_valid_en_key(text: str) -> bool:
    """判断英文原文是否适合作为全局跨包词库键（过滤纯符号/无字母/纯标签）。"""
    return bool(_ALPHA_RE.search(_TAG_RE.sub("", text)))


def strip_ver(name: str) -> str:
    """去掉文件名的 Ver<n>_ 前缀，返回基础名称；若无前缀则原样返回。"""
    m = _VER_RE.match(name)
    return m.group(1) if m else name


# ──────────────────────────────────────────────────────────────────────────────
# CRI 底层编解码
# ──────────────────────────────────────────────────────────────────────────────

_XOR_MASK = bytearray()


def _get_xor_mask(length: int) -> bytes:
    """按需生成并缓存 CRI UTF 表头 XOR 掩码（线程/进程内复用）。"""
    cur = len(_XOR_MASK)
    if cur < length:
        m = 0x655F
        for _ in range(cur):
            m = (m * 0x4115) & 0xFFFF
        for _ in range(length - cur):
            _XOR_MASK.append(m & 0xFF)
            m = (m * 0x4115) & 0xFFFF
    return bytes(_XOR_MASK[:length])


def xor_utf(data: bytes) -> bytes:
    """CRI UTF 表头 XOR 混淆/解混淆（原地可逆，使用整块异或加速）。"""
    n = len(data)
    if n == 0:
        return b""
    mask = _get_xor_mask(n)
    return (int.from_bytes(data, "little") ^ int.from_bytes(mask, "little")).to_bytes(n, "little")


def decomp_crilayla(data: bytes) -> bytes:
    """
    解压 CRILAYLA 格式压缩数据。
    若数据不以 'CRILAYLA' 魔数开头则原样返回（非压缩数据）。
    """
    if data[:8] != b"CRILAYLA":
        return data
    uncomp_size, comp_size = struct.unpack("<II", data[8:16])
    out = bytearray(0x100 + uncomp_size)
    out[:0x100] = data[16 + comp_size : 16 + comp_size + 0x100]
    in_pos   = 16 + comp_size - 1
    out_pos  = 0x100 + uncomp_size - 1
    bit_pool = bits_left = 0

    def get_bits(n: int) -> int:
        nonlocal in_pos, bit_pool, bits_left
        res = 0
        while n > 0:
            if bits_left == 0:
                bit_pool = data[in_pos]
                in_pos  -= 1
                bits_left = 8
            take  = min(n, bits_left)
            res   = (res << take) | ((bit_pool >> (bits_left - take)) & ((1 << take) - 1))
            bits_left -= take
            n    -= take
        return res

    while out_pos >= 0x100:
        if get_bits(1) == 0:
            out[out_pos] = get_bits(8)
            out_pos -= 1
        else:
            back_off = get_bits(13) + 3
            length   = 3
            for bits in (2, 3, 5, 8, 8, 8):
                v       = get_bits(bits)
                length += v
                if v != (1 << bits) - 1:
                    break
            else:
                while True:
                    v       = get_bits(8)
                    length += v
                    if v != 255:
                        break
            for _ in range(length):
                out[out_pos] = out[out_pos + back_off]
                out_pos -= 1
    return bytes(out)


# ──────────────────────────────────────────────────────────────────────────────
# CPK TOC 解析器
# ──────────────────────────────────────────────────────────────────────────────

_TYPE_SIZE: dict[int, int] = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 8, 7: 8, 8: 4, 10: 4, 11: 8}
_INT_FMT:   dict[int, str] = {0: ">B", 1: ">b", 2: ">H", 3: ">h", 4: ">I", 5: ">i", 6: ">Q", 7: ">q"}


class _TocParser:
    """
    CRI CPK TOC 区段解析器（内部使用）。
    初始化时解析整个 TOC 表头；后续可通过 iter_rows() 遍历所有行。
    """

    def __init__(self, raw: bytes) -> None:
        self.raw = raw
        toc_idx = raw.find(b"TOC ")
        if toc_idx == -1:
            raise ValueError("CPK 中未找到 TOC 区段")
        toc_sz  = struct.unpack("<Q", raw[toc_idx + 8 : toc_idx + 16])[0]
        toc_raw = raw[toc_idx + 16 : toc_idx + 16 + toc_sz]
        was_enc = toc_raw[:4] != b"@UTF"
        toc     = bytearray(xor_utf(toc_raw) if was_enc else toc_raw)
        if toc[:4] != b"@UTF":
            raise ValueError("UTF 表签名无效")

        rows_off, str_off, _, _ = struct.unpack(">IIII", toc[8:24])
        num_cols, row_len, num_rows = struct.unpack(">HHI", toc[24:32])
        rows_off += 8
        str_off  += 8

        self.toc_idx  = toc_idx
        self.toc_sz   = toc_sz
        self.was_enc  = was_enc
        self.toc      = toc
        self.str_off  = str_off
        self.rows_off = rows_off
        self.row_len  = row_len
        self.num_rows = num_rows
        self.cols     = self._parse_cols(num_cols)

    # ── 底层读写 ──────────────────────────────────────────────────────────────

    def _get_str(self, off: int) -> str:
        toc, str_off = self.toc, self.str_off
        end = toc.index(b"\x00", str_off + off)
        return toc[str_off + off : end].decode("utf-8", "replace")

    @staticmethod
    def _read_int(dtype: int, data: bytes | bytearray, pos: int) -> int:
        fmt = _INT_FMT[dtype]
        sz  = _TYPE_SIZE[dtype]
        return struct.unpack(fmt, data[pos : pos + sz])[0]

    @staticmethod
    def write_int(dtype: int, data: bytearray, pos: int, val: int) -> None:
        """原地写入整数字段（供外部修补调用）。"""
        fmt = _INT_FMT[dtype]
        sz  = _TYPE_SIZE[dtype]
        data[pos : pos + sz] = struct.pack(fmt, val)

    # ── 列定义解析 ────────────────────────────────────────────────────────────

    def _parse_cols(self, num_cols: int) -> list[tuple[str, int, int, object]]:
        cols, pos = [], 32
        for _ in range(num_cols):
            flag    = self.toc[pos]
            name    = self._get_str(struct.unpack(">I", self.toc[pos + 1 : pos + 5])[0])
            pos    += 5
            storage = flag & 0xF0
            dtype   = flag & 0x0F
            const_val = None
            if storage == 0x30:
                sz = _TYPE_SIZE[dtype]
                if dtype in (0, 1, 2, 3, 4, 5, 6, 7):
                    const_val = self._read_int(dtype, self.toc, pos)
                elif dtype == 10:
                    const_val = self._get_str(struct.unpack(">I", self.toc[pos : pos + 4])[0])
                pos += sz
            cols.append((name, storage, dtype, const_val))
        return cols

    # ── 行迭代 ────────────────────────────────────────────────────────────────

    def iter_rows(self):
        """
        逐行迭代 TOC，每行返回:
          dict[name -> (value, toc_byte_pos_or_None, dtype)]
        toc_byte_pos 为 None 表示该字段为常量列（不在行数据中）。
        """
        for r in range(self.num_rows):
            rpos: int = self.rows_off + r * self.row_len
            fields: dict[str, tuple] = {}
            for name, storage, dtype, const_val in self.cols:
                if storage == 0x50:
                    sz = _TYPE_SIZE[dtype]
                    if dtype == 10:
                        val = self._get_str(struct.unpack(">I", self.toc[rpos : rpos + 4])[0])
                    elif dtype in (0, 1, 2, 3, 4, 5, 6, 7):
                        val = self._read_int(dtype, self.toc, rpos)
                    else:
                        val = None
                    fields[name] = (val, rpos, dtype)
                    rpos += sz
                elif storage == 0x30:
                    fields[name] = (const_val, None, dtype)
            yield fields

    # ── _sg.txt 条目提取 ──────────────────────────────────────────────────────

    def sg_entries(self) -> list[tuple]:
        """
        提取所有 _sg.txt 条目，返回列表，每项为：
          (filename, plain_bytes,
           fsize_pos, exsize_pos, foff_pos,
           fsize_dtype, exsize_dtype, foff_dtype)
        """
        result = []
        for fields in self.iter_rows():
            fn = fields.get("FileName", ("", None, 0))[0]
            if not (isinstance(fn, str) and fn.endswith("_sg.txt")):
                continue
            fsize,  fsize_pos,  fsize_dt  = fields.get("FileSize",    (0, None, 4))
            exsize, exsize_pos, exsize_dt = fields.get("ExtractSize", (0, None, 4))
            foff,   foff_pos,   foff_dt   = fields.get("FileOffset",  (0, None, 4))
            if not fsize or fsize_pos is None or exsize_pos is None or foff_pos is None:
                continue
            abs_off = self.toc_idx + foff
            plain   = decomp_crilayla(self.raw[abs_off : abs_off + fsize])
            result.append((
                fn, plain,
                fsize_pos, exsize_pos, foff_pos,
                fsize_dt, exsize_dt, foff_dt,
            ))
        return result


# ──────────────────────────────────────────────────────────────────────────────
# 公共接口
# ──────────────────────────────────────────────────────────────────────────────

def _decode_utf_header(utf: bytes | bytearray) -> tuple[int, int, int, int, list[tuple[str, int, int, object]]]:
    """解析 @UTF 表头与列定义，返回 (rows_off, str_off, row_len, num_rows, cols)。"""
    rows_off, str_off, _, _ = struct.unpack(">IIII", utf[8:24])
    num_cols, row_len, num_rows = struct.unpack(">HHI", utf[24:32])
    rows_off += 8
    str_off  += 8
    cols: list[tuple[str, int, int, object]] = []
    pos = 32
    for _ in range(num_cols):
        flag = utf[pos]
        noff = struct.unpack(">I", utf[pos + 1 : pos + 5])[0]
        end  = utf.index(b"\x00", str_off + noff)
        name = utf[str_off + noff : end].decode("utf-8", "replace")
        pos += 5
        storage = flag & 0xF0
        dtype   = flag & 0x0F
        const_val = None
        if storage == 0x30:
            sz = _TYPE_SIZE[dtype]
            if dtype in _INT_FMT:
                const_val = struct.unpack(_INT_FMT[dtype], utf[pos : pos + sz])[0]
            elif dtype == 10:
                soff = struct.unpack(">I", utf[pos : pos + 4])[0]
                send = utf.index(b"\x00", str_off + soff)
                const_val = utf[str_off + soff : send].decode("utf-8", "replace")
            pos += sz
        cols.append((name, storage, dtype, const_val))
    return rows_off, str_off, row_len, num_rows, cols


def _extract_sg_rows(fn: str, plain: bytes) -> list[tuple[str, str, str]]:
    """从单个 _sg.txt 明文字节中提取含繁体中文的行。"""
    rows = []
    for line in plain.decode("utf-8", "replace").splitlines():
        if "^" not in line:
            continue
        parts = line.split("^")
        if len(parts) >= 3 and is_zh(parts[2]):
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def extract_sg_from_cpk(path: Path) -> dict[str, list[tuple[str, str, str]]]:
    """
    从 CPK 文件提取所有 _sg.txt 中含繁体中文的行。
    优先通过 CPK 文件头按需 seek 读取 TOC 与 _sg.txt，避免整包读盘；
    若文件格式非标准则回退至整包扫描。

    返回: { sg文件名 -> [(行ID, 英文列, 繁中列), ...] }
    若文件无法解析则返回空字典（不抛出异常）。
    """
    result: dict[str, list[tuple[str, str, str]]] = {}
    try:
        with path.open("rb") as f:
            head = f.read(16)
            if len(head) >= 16 and head[:4] == b"CPK ":
                utf_sz = struct.unpack("<Q", head[8:16])[0]
                if 32 <= utf_sz <= 1024 * 1024:
                    utf_raw = f.read(utf_sz)
                    utf = xor_utf(utf_raw) if utf_raw[:4] != b"@UTF" else utf_raw
                    if utf[:4] == b"@UTF":
                        rows_off, _, _, num_rows, cols = _decode_utf_header(utf)
                        toc_off = toc_sz = None
                        if num_rows >= 1:
                            rpos = rows_off
                            for cname, storage, dtype, cval in cols:
                                val = cval
                                if storage == 0x50:
                                    sz = _TYPE_SIZE[dtype]
                                    if dtype in _INT_FMT:
                                        val = struct.unpack(_INT_FMT[dtype], utf[rpos : rpos + sz])[0]
                                    rpos += sz
                                if cname == "TocOffset":
                                    toc_off = val
                                elif cname == "TocSize":
                                    toc_sz = val

                        if not toc_off or not toc_sz:
                            return {}
                        f.seek(toc_off)
                        toc_hdr = f.read(16)
                        if len(toc_hdr) >= 16 and toc_hdr[:4] == b"TOC ":
                            real_toc_sz = struct.unpack("<Q", toc_hdr[8:16])[0]
                            toc_raw = f.read(real_toc_sz)
                            toc = xor_utf(toc_raw) if toc_raw[:4] != b"@UTF" else toc_raw
                            if toc[:4] != b"@UTF" or b"_sg.txt" not in toc:
                                return {}
                            t_rows_off, t_str_off, t_row_len, t_num_rows, t_cols = _decode_utf_header(toc)
                            for r in range(t_num_rows):
                                rp = t_rows_off + r * t_row_len
                                fn, fsize, foff = "", 0, 0
                                for cname, storage, dtype, cval in t_cols:
                                    if storage == 0x50:
                                        sz = _TYPE_SIZE[dtype]
                                        if cname == "FileName" and dtype == 10:
                                            soff = struct.unpack(">I", toc[rp : rp + 4])[0]
                                            send = toc.index(b"\x00", t_str_off + soff)
                                            fn = toc[t_str_off + soff : send].decode("utf-8", "replace")
                                        elif cname == "FileSize" and dtype in _INT_FMT:
                                            fsize = struct.unpack(_INT_FMT[dtype], toc[rp : rp + sz])[0]
                                        elif cname == "FileOffset" and dtype in _INT_FMT:
                                            foff = struct.unpack(_INT_FMT[dtype], toc[rp : rp + sz])[0]
                                        rp += sz
                                    elif storage == 0x30:
                                        if cname == "FileName":
                                            fn = cval
                                        elif cname == "FileSize":
                                            fsize = cval
                                        elif cname == "FileOffset":
                                            foff = cval
                                if isinstance(fn, str) and fn.endswith("_sg.txt") and fsize:
                                    f.seek(toc_off + foff)
                                    plain = decomp_crilayla(f.read(fsize))
                                    rows = _extract_sg_rows(fn, plain)
                                    if rows:
                                        result[fn] = rows
                            return result

        # 非标准 CPK 结构回退到全量扫描
        raw    = path.read_bytes()
        parser = _TocParser(raw)
        for fn, plain, *_ in parser.sg_entries():
            rows = _extract_sg_rows(fn, plain)
            if rows:
                result[fn] = rows
    except Exception:
        pass
    return result


def _swap_sg_text(
    raw_txt: bytes,
    ext_id_map: dict[str, str],
    global_en2zh: dict[str, str] | None = None,
) -> tuple[bytes, int, int, int]:
    """
    将繁中文本注入单个 _sg.txt 的字节内容（内部函数）。

    优先级：col2 已是繁中 > 同包同文件行号精确匹配（可选 global_en2zh 仅作向后兼容）。
    返回 (新文本字节, 直接繁中行数, 行号匹配数, 全局词库匹配数)。
    """
    text   = raw_txt.decode("utf-8", "replace")
    zh_ok  = trans_id = trans_global = 0
    out_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        eol  = ""
        if line.endswith("\r\n"):
            body, eol = line[:-2], "\r\n"
        elif line.endswith("\n"):
            body, eol = line[:-1], "\n"
        else:
            body = line

        if "^" not in body:
            out_lines.append(line)
            continue

        parts = body.split("^")
        if len(parts) < 3:
            out_lines.append(line)
            continue

        line_id, col1, col2 = parts[0], parts[1], parts[2]
        if is_zh(col2) or col2.strip() == "？？？":
            parts[1] = col2
            out_lines.append("^".join(parts) + eol)
            zh_ok += 1
        elif line_id in ext_id_map:
            parts[1] = ext_id_map[line_id]
            out_lines.append("^".join(parts) + eol)
            trans_id += 1
        elif global_en2zh and col1 in global_en2zh:
            parts[1] = global_en2zh[col1]
            out_lines.append("^".join(parts) + eol)
            trans_global += 1
        else:
            out_lines.append(line)

    return "".join(out_lines).encode("utf-8"), zh_ok, trans_id, trans_global


def patch_cpk_sg_text(
    raw: bytes,
    ext_zh: dict[str, dict[str, str]],
    global_en2zh: dict[str, str] | None = None,
) -> tuple[bytes, int, int, int]:
    """
    将繁中文本注入 CPK 中的全部 _sg.txt 条目（原地修补字节流）。

    Args:
        raw          : CPK 原始字节。
        ext_zh       : { sg文件名 -> { 行ID -> 繁中文本 } }（同包行号精确匹配）。
        global_en2zh : { 英文原文 -> 繁中译文 }（全局词库跨包匹配）。

    Returns:
        (新CPK字节, 直接繁中行数, 行号匹配数, 全局词库匹配数)
        若无任何修改则返回原始 raw。
    """
    try:
        parser = _TocParser(raw)
    except Exception:
        return raw, 0, 0, 0

    out_cpk     = bytearray(raw)
    t_zh = t_id = t_glob = 0
    patched_any = False

    for fn, plain, fsize_pos, exsize_pos, foff_pos, fsize_dt, exsize_dt, foff_dt in parser.sg_entries():
        new_plain, zh_ok, tid, tglob = _swap_sg_text(plain, ext_zh.get(fn, {}), global_en2zh)
        t_zh  += zh_ok
        t_id  += tid
        t_glob += tglob
        if zh_ok + tid + tglob == 0:
            continue

        # 16 字节对齐后追加新文本
        pad = (16 - (len(out_cpk) % 16)) % 16
        if pad:
            out_cpk.extend(b"\x00" * pad)
        new_rel_off = len(out_cpk) - parser.toc_idx
        out_cpk.extend(new_plain)

        # 更新 TOC 中该条目的 FileSize / ExtractSize / FileOffset
        _TocParser.write_int(fsize_dt,  parser.toc, fsize_pos,  len(new_plain))
        _TocParser.write_int(exsize_dt, parser.toc, exsize_pos, len(new_plain))
        _TocParser.write_int(foff_dt,   parser.toc, foff_pos,   new_rel_off)
        patched_any = True

    if not patched_any:
        return raw, 0, 0, 0

    # 将修改后的 TOC 写回（若原来是加密的则重新加密）
    new_toc = xor_utf(bytes(parser.toc)) if parser.was_enc else bytes(parser.toc)
    if len(new_toc) != parser.toc_sz:
        # TOC 长度变化说明解析有误，放弃修补
        return raw, 0, 0, 0

    out_cpk[parser.toc_idx + 16 : parser.toc_idx + 16 + parser.toc_sz] = new_toc
    return bytes(out_cpk), t_zh, t_id, t_glob


def _extract_cpk_entries(
    path: Path,
    resolve_key,
    target_keys: set[str],
) -> dict[str, bytes]:
    """按自定义键提取 CPK 内指定条目（自动处理 CRILAYLA 解压）。"""
    if not target_keys:
        return {}
    result: dict[str, bytes] = {}
    try:
        parser = _TocParser(path.read_bytes())
        for fields in parser.iter_rows():
            key = resolve_key(fields)
            if not key or key not in target_keys:
                continue
            fsize = fields.get("FileSize", (0, None, 4))[0]
            foff  = fields.get("FileOffset", (0, None, 4))[0]
            if not fsize or foff is None:
                continue
            abs_off = parser.toc_idx + foff
            result[key] = decomp_crilayla(parser.raw[abs_off : abs_off + fsize])
            if len(result) == len(target_keys):
                break
    except Exception:
        pass
    return result


def extract_files_from_cpk(path: Path, target_names: set[str]) -> dict[str, bytes]:
    """
    从 CPK 文件中提取指定文件名的条目（自动处理 CRILAYLA 解压）。

    Args:
        path         : CPK 文件路径。
        target_names : 需要提取的 FileName 集合。

    Returns:
        { 文件名 -> 解压后的文件字节 }；若无法解析或无命中则返回空字典。
    """
    return _extract_cpk_entries(
        path,
        lambda f: f.get("FileName", ("", None, 0))[0],
        target_names,
    )


def extract_relpaths_from_cpk(path: Path, target_relpaths: set[str]) -> dict[str, bytes]:
    """
    从 CPK 文件中按内部相对路径（DirName/FileName 或 FileName）提取条目。

    Args:
        path            : CPK 文件路径。
        target_relpaths : 需要提取的内部相对路径集合（POSIX 风格）。

    Returns:
        { 相对路径 -> 解压后的文件字节 }；若无法解析或无命中则返回空字典。
    """
    def _rel_key(fields: dict) -> str:
        fn = fields.get("FileName", ("", None, 0))[0]
        dn = fields.get("DirName",  ("", None, 0))[0]
        if not isinstance(fn, str) or not fn:
            return ""
        if isinstance(dn, str) and dn:
            return f"{dn.strip('/')}/{fn}"
        return fn

    return _extract_cpk_entries(path, _rel_key, target_relpaths)
