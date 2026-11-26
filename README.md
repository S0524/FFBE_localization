# FFBE 离线版（Exvius Offline）本土化与增强构建项目

**中文** | [English](README.en.md)

本项目用于将 **FFBE 离线版（`exvius-offline`）** 的英文文本、系统界面、剧情与城镇对话、敌人技能、武具强化网页说明及部分游玩数值倍率，基于历史国际服档案（`ffbe_gl_contentserver_archive.rar`）进行本土化离线注入与去重重构（默认以繁体中文 `zh` 为配置，其他语种可参考[本土化移植指南](#其他语言移植与本土化指南)），最终输出一套**纯本地离线、免联网下载**的 APK 与纯净数据包。

---

## 目录结构说明

```text
FFBE_localization/
├── README.md                         # 项目总览与快速上手说明（本文件）
├── release/                          # 官方原版离线更新包（仅需放入 latest.json，步骤 1 会自动下载分包与 APK）
├── release_repack/                   # 最终产物输出目录（含修补签名后的 APK + latest.json + 9 个去重分包）
├── save_editor_web/                  # 本地浏览器端存档修改器（index.html + names.json 本土化名称词典）
├── source/
│   ├── apk/                          # 待修补的官方原版 APK（如 exvius-offline-v0.1.90-beta.apk）
│   ├── archive/                      # FFBE_GL.zip 解压后的本土化语料目录（由脚本自动解压，默认提取 zh）
│   ├── offline/                      # 官方 release 提取出的纯净底包（native / static / work 对照基准）
│   └── FFBE_GL.zip                   # 历史国际服多语言 CDN 档案（需将档案馆 .rar 解压后重打包为 .zip）
├── work/                             # 本土化中间产物目录（文本、图片、CPK、网页等，可直接二次打包）
└── scripts/                          # 全部构建与修补脚本及工具
    ├── README.md                     # 详细脚本参数与维护指南
    ├── build_all.ps1                 # PowerShell 统一交互菜单 / 批处理入口
    ├── ffbe_zh.keystore              # 固定 APK 签名密钥库（保证覆盖安装签名一致）
    ├── tools/                        # apktool.jar、uber-apk-signer.jar
    └── *.py                          # 各阶段 Python 构建脚本
```

---

## 核心特性（当前状态）

1. **纯离线 APK 修补（[patch_apk.py](scripts/patch_apk.py)）**
   - 注入 `MANAGE_EXTERNAL_STORAGE` 权限并在启动界面自动检查/引导授权，解决 Android 11+ 读取外置共享目录限制。
   - 将 `DataUpdates` 的更新读取路径重定向至 `/sdcard/Download/updates`。
   - **纯离线化**：移除在线 GitHub 地址前缀校验、禁用联网检查与在线回退拉取，且不再删除本地 `latest.json` 与 `data_*.zip` 分包。
   - **启动即装**：`DataUpdates.prepareStartup` 检测到新 `revision` 后当次启动直接解压更新，无需重启两遍。

2. **高覆盖本土化注入与补全（[make_zh_patch.py](scripts/make_zh_patch.py)、[patch_event_cpk.py](scripts/patch_event_cpk.py)、[sync_sd_cpk.py](scripts/sync_sd_cpk.py)）**
   - 自动从 APK 的 `lib/arm64-v8a/libgame.so` 提取加密清单（同目录生成 `manifest_cache.json` 缓存加速二次解析）。
   - 系统文本表自动将目标语言列移至首位，支持同 Key 低版本语料回退，以及基于相同英文原文的唯一译名回退（修复部分怪兽/条目未本土化问题）。
   - 全量对齐替换 `en/sd` 界面图片、`cpk_extract/en` 解包图以及全目录 `common_lang`（SD/HD）剧情与城镇对话 CPK。

3. **额外本土化与游玩倍率调整（[patch_extras.py](scripts/patch_extras.py)）**
   - 补全 `static/client/game-assets.apk` 内 `0ThfQQWd.csv` 与 `resources_pretutorial.cpk`。
   - 本土化 `native/exvius/equipment-enhancement/*.html` 共 54 个铁城装备强化说明及奖励网页。
   - 补全礼包名称、怪兽名称（`F_TEXT_MONSTER_NAME`）与敌人技能名（`F_MONSTER_SKILL_MST`）。
   - 支持自定义倍率（**`build_all.ps1` 与 `patch_extras.py` 默认均为 10 倍，原版填 `1`**）：
     - 战斗掉落概率倍率（上限 `100%`）
     - 战斗掉落数量与 Gil / 兵员 EXP / Rank EXP 倍率
     - 关卡挑战任务宝石（Lapis）倍率（同步覆盖游戏内双份数据源，确保实际入账与界面一致）

4. **纯净去重全量打包（[repack_all_clean.py](scripts/repack_all_clean.py)）**
   - `build_all.ps1` 的步骤 `8` 会将官方分包与 `work/` 本土化产物去重合并，并按 1.8 GB 解包上限多进程重构输出全新 `data_*.zip` 与 `latest.json`。

5. **配套网页存档修改器词典（[export_names_json.py](scripts/export_names_json.py)）**
   - 从 `work/text_decoded` 同步兵员、装备、能力与道具本土化名称到 [save_editor_web/names.json](save_editor_web/names.json)，搭配 [save_editor_web/index.html](save_editor_web/index.html) 直接在本地浏览器编辑存档。

---

## 环境要求

- **操作系统**：Windows（PowerShell 5.1+ / 7+）
- **Python**：3.10+（需安装 `cryptography`，即 `pip install cryptography`，供 AES-128 加解密使用）
- **Java (JDK 11+)**：提供 `java` 与 `keytool`，供 `apktool.jar`、`uber-apk-signer.jar` 与生成固定签名密钥库使用

---

## 快速开始

### 0. 准备基础资源（`latest.json` 与 `FFBE_GL.zip`）

#### (1) 获取官方离线版清单 `release/latest.json`

从官方离线版仓库下载最新的 `latest.json`（二选一，正式版或测试版均可）并放入 **`release/latest.json`**：
- 正式版：[Elvlin/exvius-offline](https://github.com/Elvlin/exvius-offline)
- 测试版：[Elvlin/exvius-offline-beta](https://github.com/Elvlin/exvius-offline-beta)

> [!NOTE]
> 无需手动逐个下载官方分包和 APK。放好 `release/latest.json` 后，后续执行一键构建（或单独执行步骤 `1`）时，[sync_release.py](scripts/sync_release.py) 会自动根据 `latest.json` 校验并多线程断点续传下载全部缺失的官方 `.zip` 分包到 `release/`、下载对应原版 APK 到 `source/apk/`，同时清理过期旧包。

#### (2) 准备历史国际服档案 `source/FFBE_GL.zip`

本项目使用的历史多语言语料来自互联网档案馆（Internet Archive）的国际服封存包：  
<https://archive.org/download/ffbe-eos-contentserver-archive/ffbe_gl_contentserver_archive.rar>

该原始压缩包为 **`.rar` 格式且带有多层外层目录嵌套**。由于构建脚本（[extract_archive.py](scripts/extract_archive.py)）通过 Python 标准库 `zipfile` 读取压缩包，无法直接解析 `.rar`，请按以下步骤准备 `source/FFBE_GL.zip`：

1. 将下载的 `ffbe_gl_contentserver_archive.rar` 解压到本地目录，确认内部存在 `dlc_assets_prod/` 目录（外层无论嵌套多少层域名或镜像文件夹均可，脚本会自动锚定 `dlc_assets_prod/` 提取）。
2. 将包含 `dlc_assets_prod/` 的目录重新打包为 **`source/FFBE_GL.zip`**。
   - **提速 / 省空间建议**：若无需在本地保留全语种档案，只需保留 `dlc_assets_prod/` 下的 `localized_texts/zh/`、`zh/sd/`、`common_lang/` 三个子目录打包为 `FFBE_GL.zip` 即可（ZIP 压缩方式选“存储 / 不压缩”更快，因为 `.cpk` 与 `.png` 本身已压缩）。

### 1. 一键构建数据包与修改器词典

进入 `scripts/` 目录运行统一入口脚本：

```powershell
cd d:\下载\FFBE_localization\scripts
.\build_all.ps1
```

在交互菜单中输入 **`0`**（清理旧 `work` 并执行步骤 `1~9`），中途按提示设置倍率（直接回车默认全部 **`10`** 倍，原版体验输入 **`1`**）。  
若官方更新了 APK 版本，或首次构建需要产出可安装 APK，再单独执行一次 **`a`**：

```powershell
.\build_all.ps1 a
```

构建完成后，最终成品位于 `release_repack/`：
- `exvius-offline-*-zh-patched.apk`：已注入权限与纯离线补丁并签名的安装包
- `latest.json` 与多个 `data_*.zip`：纯净去重后的本地离线更新数据包

### 2. 手机端安装与更新步骤

1. 在手机存储根目录创建或打开 `/sdcard/Download/updates/`（即手机内部存储的 `Download/updates` 文件夹）。
2. 将 `release_repack/` 下的 **`latest.json` 与全部 `data_*.zip`** 复制到 `/sdcard/Download/updates/`。
3. 安装 `release_repack/exvius-offline-*-zh-patched.apk`。
4. 首次打开游戏时，按提示授予“**所有文件访问权限**”，游戏即可自动读取本地 `latest.json` 与分包完成初始化并进入游戏。

> [!TIP]
> 后续若仅修改了少量翻译或调整数值倍率，只需在电脑端运行 `.\build_all.ps1 7`（或手工改 `work/`）后运行 `.\build_all.ps1 8` 重新打包，再将 `release_repack/` 下新的 `latest.json` 与 `data_*.zip` 覆盖进手机 `/sdcard/Download/updates/` 即可，**APK 无需重复重签安装**。

---

## 其他语言移植与本土化指南

本项目的底层核心模块（清单解析、AES 加解密、CRI CPK 解封包、数值倍率调整、1.8 GB 去重打包、纯离线 APK Smali 补丁）均**与语言无关**。若希望基于本底座移植到其他语种，可按目标语言类型参考以下修改点：

### 1. 移植国际服原有语种（`ko` / `fr` / `de` / `es` / `th` / `id`）

原始国际服封存包（`ffbe_gl_contentserver_archive.rar`）与 `game-assets.apk` 内除 `en` / `zh` 外，还完整保留了韩语（`ko`）、法语（`fr`）、德语（`de`）、西班牙语（`es`）、泰语（`th`）、印尼语（`id`）的同构资源，只需调整以下 4 处即可适配：

1. **提取与路径配置（[extract_archive.py](scripts/extract_archive.py)、[build_all.ps1](scripts/build_all.ps1)）**
   - 将 `TARGETS` 中的 `localized_texts/zh/`、`zh/sd/` 替换为目标语种目录（如 `localized_texts/ko/`、`ko/sd/`），并在 `build_all.ps1` 的步骤 `4`、`5` 中同步修改传参路径。
2. **去除汉字正则限制（[make_zh_patch.py](scripts/make_zh_patch.py)）**
   - 脚本中 `_CJK_RE` 与 `_has_cjk_in_line()` 当前仅匹配汉字（`[\u4e00-\u9fff]`），用于过滤未本土化的英文占位行。
   - 移植韩文/泰文可改为对应 Unicode 区间正则；移植法/德/西/印尼等拉丁语系时，需改为判断“目标列非空且与英文原文不相同”。
3. **修改剧情 `_sg.txt` 列索引（[ffbe_cpk.py](scripts/ffbe_cpk.py)）**
   - 国际服剧情与对话 CPK 内的 `_sg.txt` 按固定列顺序存放 8 种语言：
     `ID(0) ^ EN(1) ^ ZH(2) ^ KO(3) ^ FR(4) ^ DE(5) ^ ES(6) ^ TH(7) ^ ID(8)`
   - 在 `_extract_sg_rows()` 与 `_swap_sg_text()` 中，将当前写死的列号 `parts[2]` 改为目标语言对应的列索引，并替换 `is_zh()` 校验逻辑即可。
4. **额外资源与网页模板（[patch_extras.py](scripts/patch_extras.py)）**
   - 将 `patch_game_assets_apk()` 中的 `assets/localized_texts/zh/0ThfQQWd.csv` 与 `assets/cpk/zh/sd/resources/resources_pretutorial.cpk` 改为目标语言路径，并替换该文件内的 `_CJK_RE.search(...)` 校验。
   - `patch_equipment_enhancement()` 中 54 个铁城装备强化网页的静态模板（`_EXACT_HTML_PHRASES`、`_STATIC_REWARDS`、`_MONTH_ZH`、`_STAT_WORDS` 及 `<html lang="zh-Hant">`）当前为繁中硬编码，移植时需翻译为目标语言或选择跳过 HTML 替换。
5. **可选附属项**：[stat_zh_coverage.py](scripts/stat_zh_coverage.py) 的 `TABLE_META` 分类说明、[save_editor_web/index.html](save_editor_web/index.html) 网页界面，以及 [patch_apk.py](scripts/patch_apk.py) 输出文件名后缀 `-zh-patched.apk` 可按需本地化。

### 2. 移植国际服未收录语种 / 日服（`ffbe_jp_contentserver_archive.rar`）

- **民间二次翻译语言（如简体中文、俄语、葡萄牙语等）**：可直接复用 `make_zh_patch.py` 解码出的 `work/text_decoded/*.ndjson`、`KEY^TEXT` 明文表与 `_sg.txt` 文本管道进行翻译回填（例如繁中转简中或基于英文翻译）。若目标语言使用非拉丁 / 非 CJK 字符集，需实机验证客户端内置字体是否包含对应字形。
- **日服原始档案（`ffbe_jp_contentserver_archive.rar`）**：日服数据表的 token、单列结构及资源命名与国际服 `exvius-offline` 不一致，无法直接按列对调，需额外构建跨服 ID 映射关系。

---

## 详细说明

各脚本命令行参数、底层数据表机制与常见问题请参阅 [scripts/README.md](scripts/README.md)。
