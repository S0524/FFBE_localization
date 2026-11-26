# FFBE Offline Localization & Build Scripts Guide

[中文说明](README.md) | **English**

This directory contains the full suite of automation scripts and tools for FFBE Offline localization (configured for Traditional Chinese by default), extra asset enhancements, pure offline APK patching, and save-editor dictionary export.

---

## 1. Script & Tool Inventory

### 1.1 Unified Entry Point

- **[build_all.ps1](build_all.ps1)**: PowerShell interactive menu and CLI batch entry point.
  - Interactive mode: `.\build_all.ps1`
  - Non-interactive parameterized mode:
    ```powershell
    .\build_all.ps1 -Action 7 -DropRateMult 10 -DropCountMult 10 -LapisMult 10
    ```
  - Menu options:
    - `0`: Clean old `work/` and repacked data chunks (keeps already-patched APK), then run Steps `1`–`9` sequentially
    - `1`–`9`: Run individual stages (asset prep, localization, extra assets/multipliers, clean repackaging, dictionary export)
    - `a`: Decompile, inject offline & storage-permission patches, and re-sign the APK (only needed once per base APK update)
    - `s`: Report system text localization coverage
    - `c`: Clear `work/` and `release_repack/`
    - `q`: Quit

### 1.2 Python Build Scripts

| Order | Script | Role |
| :--- | :--- | :--- |
| Core Helper | [ffbe_utils.py](ffbe_utils.py) | `FFBE_AES` / AES-128 encryption & decryption, `manifest_cache.json` loading, unified plaintext/encrypted `.dat` I/O |
| Core Helper | [ffbe_cpk.py](ffbe_cpk.py) | CRI CPK (`@UTF` table) unpacking, `_sg.txt` extraction, and in-place repacking |
| Step 1 | [sync_release.py](sync_release.py) | Reads `release/latest.json`, removes obsolete packs, and verifies/downloads missing official `.zip` packs and APK |
| Step 2 | [extract_archive.py](extract_archive.py) | Extracts localized texts, UI images, and story resources from `source/FFBE_GL.zip` to `source/archive` |
| Step 3 | [extract_clean_offline.py](extract_clean_offline.py) | Extracts the clean vanilla offline baseline from `release/` to `source/offline` |
| Step 4 | [make_zh_patch.py](make_zh_patch.py) | Parses the `libgame.so` manifest from the APK and injects localized text into `work/localized_texts/en` and `work/text_decoded/*.ndjson` |
| Step 5 | [sync_sd_cpk.py](sync_sd_cpk.py) | Syncs `zh/sd` localized UI assets and extracts matching pre-unpacked assets into `cpk_extract/en` |
| Step 6 | [patch_event_cpk.py](patch_event_cpk.py) | Scans all `common_lang` (`sd`/`hd`) CPKs and performs strict same-pack `_sg.txt` localization |
| Step 7 | [patch_extras.py](patch_extras.py) | Localizes extra assets (`game-assets.apk`, 54 Item World HTML pages, bundle names, monster names, enemy skills) and scales battle/mission multipliers |
| Step 8 | [repack_all_clean.py](repack_all_clean.py) | Merges base packs with `work/` overrides into deduplicated `data_*.zip` packs and `latest.json` |
| Step 9 | [export_names_json.py](export_names_json.py) | Exports localized unit/equipment/item/materia names to `save_editor_web/names.json` |
| Option `a` | [patch_apk.py](patch_apk.py) | Invokes `apktool` + `uber-apk-signer` to inject offline & storage patches and sign with a fixed keystore |
| Stats | [stat_zh_coverage.py](stat_zh_coverage.py) | Maps official table names and reports localization coverage across `work/localized_texts/en` by module |

### 1.3 Tools & Keystore

- [tools/apktool.jar](tools/apktool.jar): APK decompilation and rebuilding tool
- [tools/uber-apk-signer.jar](tools/uber-apk-signer.jar): 4-byte alignment and v1/v2/v3 APK signing tool
- [ffbe_zh.keystore](ffbe_zh.keystore): Fixed signing keystore (ensures updated APK builds can overwrite existing installs without losing local save data; override the default password via `FFBE_KS_PASS` if needed)

---

## 2. Core Module Parameters & Mechanics

### 2.1 Extra Localization & Reward Multipliers ([patch_extras.py](patch_extras.py))

`patch_extras.py` corresponds to menu Step `7` and accepts the following CLI flags:

| Flag | Default | Description |
| :--- | :--- | :--- |
| `--offline-root` | Required | Clean offline baseline root directory (typically `source/offline`) |
| `--work` | Required | Localization output directory (typically `work`) |
| `--drop-rate-mult` | `10.0` | Battle drop rate multiplier (capped at `100%` per entry; pass `1` for vanilla) |
| `--drop-count-mult` | `10.0` | Battle drop quantity multiplier (applies to stackable materials & unit fragments across story/event/exploration tables; pass `1` for vanilla) |
| `--reward-mult` | Follows `--drop-count-mult` | Battle Gil / Unit EXP / Rank EXP multiplier (pass `1` for vanilla) |
| `--lapis-mult` | `10.0` | Mission challenge Lapis multiplier (pass `1` for vanilla) |
| `--release-dir` | Auto-inferred | Official `release` directory (defaults to `release` sibling of `--offline-root`) |

**Dual Data-Source Sync Design**:
- **Battle Rewards & Drops**: Patches both server/event JSON/TSV tables and client decoded `.ndjson` / static tables so Rank EXP, Unit EXP, Gil, drop rates, and item counts apply consistently.
- **Mission Challenge Lapis**: Updates both `F_MISSION_MST` and `F_CHALLENGE_MST` client `.dat`/`.i` files and server `.ndjson` tables so awarded Lapis matches UI display.
- **Enemy Skills & Monster Names**:
  - Enemy skills are filled into `F_MONSTER_SKILL_MST` via ID and English-name matching across skill master tables;
  - Monster names are handled by both `make_zh_patch.py` (older-version + unique English-name fallback) and `patch_extras.py` (`F_TEXT_MONSTER_NAME`).

### 2.2 Pure Offline APK Patching ([patch_apk.py](patch_apk.py))

`patch_apk.py` locates `AndroidManifest.xml`, `com/exvius/local/DataUpdates.smali`, and `com/exvius/local/DataDownloadActivity.smali` and applies the following changes:

1. **Storage Permission**: Adds `MANAGE_EXTERNAL_STORAGE` and injects `checkAllFilesPermission` at the start of `DataDownloadActivity.onCreate` to guide users to grant access.
2. **Local Path Redirection**: Redirects `getExternalFilesDir("updates")` calls in `DataUpdates.downloadFile` and `fetchLatest` to `/sdcard/Download/updates`.
3. **Pure Offline Protection**:
   - Modifies `DataUpdates.validate` to remove the hardcoded `https://github.com/Elvlin/...` URL prefix check;
   - Modifies `DataUpdates.fetchLatest` to remove the `raw.githubusercontent.com` fallback, reading only local `/sdcard/Download/updates/latest.json`;
   - Forces `DataUpdates.online` to always return `false`, preventing `downloadApp` / `resumableDownload` from attempting network downloads;
   - Removes deletion of `latest.json` in `fetchLatest` and `.zip`/`.verified` cleanup in `clearDownload` so local update packs remain intact.
4. **Immediate Update Application**: Enhances `DataUpdates.prepareStartup` so a higher local `revision` is unpacked and applied immediately on that launch.

---

## 3. Common Workflows

### Adjust Multipliers or Tweak Text & Repackage

```powershell
cd scripts
# Example: 2x drop rate, 5x drop count & EXP/Gil, 1x vanilla Lapis, then repackage
.\build_all.ps1 -Action 7 -DropRateMult 2 -DropCountMult 5 -LapisMult 1
.\build_all.ps1 -Action 8
```

Then copy the newly generated `latest.json` and `data_*.zip` files from `release_repack/` to `/sdcard/Download/updates/` on your phone.

### Inspect CLI Flags & Coverage

```powershell
# Inspect all CLI flags for a script
python repack_all_clean.py --help
python patch_extras.py --help
python patch_apk.py --help

# Check current localization coverage under work/
.\build_all.ps1 s
```

---

## 4. Notes

- Treat **`source/FFBE_GL.zip` and `release/`** as read-only base assets; do not manually delete or alter official packs inside them.
- Keep **`ffbe_zh.keystore`** safe once generated. Changing keystores produces a different APK signature, which prevents over-the-top upgrades on Android (forcing an uninstall that can wipe local save data).
- To override the default keystore password, set the `FFBE_KS_PASS` environment variable or pass `--ks-pass` to `patch_apk.py`.
- `source/FFBE_JP.zip` (if present) is strictly an optional reference archive and is **never read by any build script**; you may delete it to save disk space.
