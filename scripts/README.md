# FFBE 离线版本土化重构脚本指南

**中文** | [English](README.en.md)

本目录存放 FFBE 离线版本土化重构（默认配置为繁体中文）、额外资源增强、纯离线 APK 修补以及存档修改器词典导出的全套自动化脚本与工具。

---

## 一、脚本与工具清单

### 1. 统一调度入口

- **[build_all.ps1](build_all.ps1)**：PowerShell 交互菜单与命令行批处理入口。
  - 支持交互运行：`.\build_all.ps1`
  - 支持参数化非交互运行：
    ```powershell
    .\build_all.ps1 -Action 7 -DropRateMult 10 -DropCountMult 10 -LapisMult 10
    ```
  - 菜单功能一览：
    - `0`：清理旧 `work` 与旧分包数据（保留已修补 APK），连续执行步骤 `1 ~ 9`
    - `1` ~ `9`：分步执行素材准备、本土化、额外资源/倍率、整合打包与词典导出
    - `a`：自动反编译、注入纯离线补丁并重签名 APK（每版官方 APK 只需执行一次）
    - `s`：统计系统文本本土化覆盖率
    - `c`：清空 `work/` 与 `release_repack/`
    - `q`：退出

### 2. Python 构建脚本

| 执行顺序 | 脚本 | 核心作用 |
| :--- | :--- | :--- |
| 前置基础 | [ffbe_utils.py](ffbe_utils.py) | `FFBE_AES` / AES-128 加解密、`manifest_cache.json` 加载、`.dat` 明文/密文统一读写 |
| 前置基础 | [ffbe_cpk.py](ffbe_cpk.py) | CRI CPK（UTF 表）解包与重构打包库 |
| 步骤 1 | [sync_release.py](sync_release.py) | 读取 `release/latest.json`，清理废包并校验/下载缺失的官方 `.zip` 与 APK |
| 步骤 2 | [extract_archive.py](extract_archive.py) | 从 `source/FFBE_GL.zip` 解压本土化文本、UI 图片与剧情资源至 `source/archive` |
| 步骤 3 | [extract_clean_offline.py](extract_clean_offline.py) | 从 `release/` 提取纯净原版离线底包至 `source/offline` |
| 步骤 4 | [make_zh_patch.py](make_zh_patch.py) | 解析 APK 中 `libgame.so` 清单，注入本土化文本至 `work/localized_texts/en` 与 `work/text_decoded/*.ndjson` |
| 步骤 5 | [sync_sd_cpk.py](sync_sd_cpk.py) | 同步 `zh/sd` 本土化 UI 图片及从对应 CPK 提取替换 `cpk_extract/en` 预解包素材 |
| 步骤 6 | [patch_event_cpk.py](patch_event_cpk.py) | 遍历 `common_lang`（SD/HD）全目录，对剧情与城镇对话 CPK 进行精准替换重打包 |
| 步骤 7 | [patch_extras.py](patch_extras.py) | 额外资源本土化（`game-assets.apk`、54 个铁城 HTML、礼包名、怪兽名、敌人技能）与战斗/任务倍率调整 |
| 步骤 8 | [repack_all_clean.py](repack_all_clean.py) | 合并底包与 `work/` 生成去重分包 `data_*.zip` 与 `latest.json` |
| 步骤 9 | [export_names_json.py](export_names_json.py) | 导出兵员/装备/道具/能力本土化名称至 `save_editor_web/names.json` |
| 选项 `a` | [patch_apk.py](patch_apk.py) | 调用 `apktool` + `uber-apk-signer` 注入纯离线与存储权限补丁并用固定密钥库签名 |
| 统计 | [stat_zh_coverage.py](stat_zh_coverage.py) | 映射官方表名并按模块分类统计 `work/localized_texts/en` 的本土化覆盖率 |

### 3. 工具与密钥文件

- [tools/apktool.jar](tools/apktool.jar)：APK 反编译与回编工具
- [tools/uber-apk-signer.jar](tools/uber-apk-signer.jar)：APK 4 字节对齐与 v1/v2/v3 签名工具
- [ffbe_zh.keystore](ffbe_zh.keystore)：固定签名密钥库（保证每次修补后的 APK 均可覆盖安装而不丢失本地存档；可通过环境变量 `FFBE_KS_PASS` 覆盖默认密码）

---

## 二、核心模块参数与机制说明

### 1. 额外资源本土化与倍率调整（[patch_extras.py](patch_extras.py)）

`patch_extras.py` 对应交互菜单步骤 `7`，支持以下命令行参数：

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `--offline-root` | 必填 | 纯净底包根目录（通常为 `source/offline`） |
| `--work` | 必填 | 本土化输出目录（通常为 `work`） |
| `--drop-rate-mult` | `10.0` | 战斗掉落概率倍率（单条掉率上限封顶 `100%`，填 `1` 保留原版） |
| `--drop-count-mult` | `10.0` | 战斗掉落数量倍率（作用于可堆叠素材与碎片，含主线/活动/探索等战斗掉落表，填 `1` 保留原版） |
| `--reward-mult` | 跟随 `--drop-count-mult` | 战斗结算 Gil / 兵员 EXP / Rank EXP 倍率（填 `1` 保留原版） |
| `--lapis-mult` | `10.0` | 关卡挑战任务宝石（Lapis）倍率（填 `1` 保留原版） |
| `--release-dir` | 自动推断 | 官方 `release` 目录（未传时取 `--offline-root` 同级 `release`） |

**双数据源同步设计要点**：
- **战斗结算与掉落**：同时覆盖服务端/事件表与客户端解码 `.ndjson` / 静态数据（确保 Rank EXP、Unit EXP、Gil 与道具数量和概率生效）。
- **关卡挑战宝石**：同步改写 `F_MISSION_MST` 与 `F_CHALLENGE_MST` 相关表及静态配置，使结算实际入账与挑战列表 UI 显示一致。
- **敌人技能与怪兽名**：
  - 敌人技能通过提取多版本与技能主表映射补全 `F_MONSTER_SKILL_MST`；
  - 怪兽名由 `make_zh_patch.py`（低版本 + 同英文名唯一目标译名回退）与 `patch_extras.py`（`F_TEXT_MONSTER_NAME` 补充）共同保障。

### 2. 纯离线 APK 修补原理（[patch_apk.py](patch_apk.py)）

`patch_apk.py` 会定位 `AndroidManifest.xml`、`com/exvius/local/DataUpdates.smali` 与 `com/exvius/local/DataDownloadActivity.smali`，完成以下改造：

1. **存储权限**：注入 `MANAGE_EXTERNAL_STORAGE` 权限声明，并在 `DataDownloadActivity.onCreate` 开头插入 `checkAllFilesPermission` 引导用户开启权限。
2. **本地路径重定向**：将 `DataUpdates.downloadFile` 与 `fetchLatest` 中原有的 `getExternalFilesDir("updates")` 重定向到 `/sdcard/Download/updates`。
3. **纯离线模式保护**：
   - 修改 `DataUpdates.validate` 移除对 `https://github.com/Elvlin/...` 的前缀硬编码检查；
   - 修改 `DataUpdates.fetchLatest` 移除 `raw.githubusercontent.com` 在线回退，仅读取本地 `/sdcard/Download/updates/latest.json`；
   - 将 `DataUpdates.online` 设为恒返回 `false`，禁止 `downloadApp` / `resumableDownload` 触发联网下载；
   - 移除 `fetchLatest` 删除 `latest.json`、移除 `clearDownload` 删除 `.zip`/`.verified` 的逻辑，避免更新完成后本地底包丢失。
4. **即时生效更新**：增强 `DataUpdates.prepareStartup`，发现本地高 `revision` 时当次启动直接解压应用更新。

---

## 三、常用工作流

### 仅微调倍率或手工微调文本后重新打包

```powershell
cd d:\下载\FFBE_localization\scripts
# 示例：只调整为 2 倍掉率、5 倍掉落与经验、1 倍原版宝石，并重新重构整合包
.\build_all.ps1 -Action 7 -DropRateMult 2 -DropCountMult 5 -LapisMult 1
.\build_all.ps1 -Action 8
```

然后将 `release_repack/` 新生成的 `latest.json` 和对应 `data_*.zip` 覆盖到手机 `/sdcard/Download/updates/` 即可。

### 单独调试静态参数查看

```powershell
# 查看某脚本全部可选参数
python repack_all_clean.py --help
python patch_extras.py --help
python patch_apk.py --help

# 查看当前 work/ 系统文本本土化覆盖率
.\build_all.ps1 s
```

---

## 四、注意事项

- **`source/FFBE_GL.zip` 与 `release/`** 请视为只读底包资产，不要手动删改其中的原版分包。
- **`ffbe_zh.keystore`** 请勿丢失或随意更换，否则新打包出来的 APK 与旧版本签名不一致，手机上将无法直接覆盖安装（必须先卸载，容易丢失游戏进度）。
- 若需将密码从脚本默认值改掉，可在执行 `patch_apk.py` 前设置环境变量 `FFBE_KS_PASS` 或传 `--ks-pass`。
- 当前 `source/FFBE_JP.zip` 仅作可选原始资料保存，**构建脚本均不会读取该文件**；如本地磁盘空间紧张，可按需自行删除以释放空间。
