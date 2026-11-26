# FFBE Offline (`exvius-offline`) Localization & Enhanced Build Project

[中文说明](README.md) | **English**

This project localizes the **FFBE Offline (`exvius-offline`)** English text, system UI, story and town dialogues, enemy skills, Equipment Enhancement (Item World) HTML guides, and optional gameplay reward multipliers using the archived Global CDN assets (`ffbe_gl_contentserver_archive.rar`), producing a **100% offline, zero-network** APK and deduplicated data packs (configured for Traditional Chinese `zh` by default; see [Porting to Other Languages](#porting-to-other-languages) for other languages).

---
<p align="center">
  <img width="750" alt="预览" src="https://github.com/user-attachments/assets/0d9cba34-3fc5-4028-8039-da6fc9521fd5" />
</p>

<p align="center">
  <img width="150" alt="截图1" src="https://github.com/user-attachments/assets/93684a86-fde2-401f-91d7-088a519b36b9" />
  <img width="150" alt="截图2" src="https://github.com/user-attachments/assets/4ce88992-5636-4ac6-9b01-34d090f8b3a4" />
  <img width="150" alt="截图3" src="https://github.com/user-attachments/assets/fce1c74d-930a-4bfc-92be-914f3e240076" />
  <img width="150" alt="截图4" src="https://github.com/user-attachments/assets/596fe12d-8c98-42df-bb9f-24c8205b0a5e" />
  <img width="150" alt="截图5" src="https://github.com/user-attachments/assets/ecf80079-76f9-4e21-a0f4-cf6d54303b97" />
</p>
## Directory Structure

```text
FFBE_localization/
├── README.md                         # Chinese project overview & quick-start guide
├── README.en.md                      # English project overview & localization guide (this file)
├── release/                          # Official offline release packs (place latest.json here; Step 1 auto-downloads packs & APK)
├── release_repack/                   # Final build output (patched & signed APK + latest.json + deduplicated data_*.zip packs)
├── save_editor_web/                  # Local browser save editor (index.html + names.json localized name dictionary)
├── source/
│   ├── apk/                          # Official APK to patch (e.g., exvius-offline-v0.1.90-beta.apk)
│   ├── archive/                      # Extracted GL localization assets from FFBE_GL.zip (auto-extracted, defaults to zh)
│   ├── offline/                      # Clean base extracted from release/ (native / static reference baseline)
│   └── FFBE_GL.zip                   # Archived GL multi-language CDN package (repacked to .zip from the Internet Archive .rar)
├── work/                             # Intermediate localized assets (texts, images, CPKs, HTML, etc.)
└── scripts/                          # All build, patching, and helper scripts
    ├── README.md                     # Detailed script parameters & maintenance guide (Chinese)
    ├── README.en.md                  # Detailed script parameters & maintenance guide (English)
    ├── build_all.ps1                 # Unified PowerShell interactive menu / CLI entry point
    ├── ffbe_zh.keystore              # Fixed APK signing keystore (ensures upgrade installs keep save data)
    ├── tools/                        # apktool.jar, uber-apk-signer.jar
    └── *.py                          # Per-stage Python build scripts
```

---

## Key Features (Current State)

1. **Pure Offline APK Patching ([patch_apk.py](scripts/patch_apk.py))**
   - Injects `MANAGE_EXTERNAL_STORAGE` into `AndroidManifest.xml` and prompts for all-files access on startup (`DataDownloadActivity`) for Android 11+ compatibility.
   - Redirects `DataUpdates` storage reads/writes to `/sdcard/Download/updates`.
   - **Fully offline**: removes hardcoded GitHub URL prefix validation in `DataUpdates.validate`, disables online checks (`DataUpdates.online` returns `false`) and `raw.githubusercontent.com` fallbacks, and prevents automatic deletion of local `latest.json` and `data_*.zip` files.
   - **Instant startup apply**: `DataUpdates.prepareStartup` applies local updates immediately when a newer `revision` is detected without requiring a second restart.

2. **High-Coverage Localization Injection ([make_zh_patch.py](scripts/make_zh_patch.py), [patch_event_cpk.py](scripts/patch_event_cpk.py), [sync_sd_cpk.py](scripts/sync_sd_cpk.py))**
   - Automatically parses the encrypted table manifest from `lib/arm64-v8a/libgame.so` inside the APK (cached as `manifest_cache.json`).
   - Promotes the target language column to the primary display slot in system text tables, with same-key fallback from older archive versions and unique English-to-target fallback within the same table (fixing untranslated monsters/entries).
   - Replaces `en/sd` UI images, `cpk_extract/en` pre-extracted textures, and story/town dialogue CPKs across all `common_lang` (`sd` & `hd`) directories.

3. **Extra Localization & Configurable Gameplay Multipliers ([patch_extras.py](scripts/patch_extras.py))**
   - Patches `0ThfQQWd.csv` and `resources_pretutorial.cpk` inside `static/client/game-assets.apk`.
   - Localizes 54 Equipment Enhancement HTML guides and reward pages under `native/exvius/equipment-enhancement/*.html`.
   - Translates bundle names, monster names (`F_TEXT_MONSTER_NAME`), and enemy skill names (`F_MONSTER_SKILL_MST`).
   - Supports configurable multipliers (**both `build_all.ps1` and `patch_extras.py` default to `10x`; pass `1` for vanilla rates**):
     - Battle drop rate multiplier (capped at `100%`)
     - Battle drop quantity (stackable items/fragments) and Gil / Unit EXP / Rank EXP multipliers
     - Mission challenge Lapis multiplier (patches both client and server-side data sources so actual rewards match UI display)

4. **Clean Deduplicated Repackaging ([repack_all_clean.py](scripts/repack_all_clean.py))**
   - Step `8` in `build_all.ps1` merges official packs with `work/` overrides, deduplicates entries, and outputs new `data_*.zip` chunks (1.8 GB unpacked limit, multiprocess) along with `latest.json`.

5. **Web Save Editor Dictionary Sync ([export_names_json.py](scripts/export_names_json.py))**
   - Exports unit, equipment, materia, and item names from localized tables to [save_editor_web/names.json](save_editor_web/names.json) for use with [save_editor_web/index.html](save_editor_web/index.html).

---

## Requirements

- **OS**: Windows (PowerShell 5.1+ / 7+) for `build_all.ps1` (individual `.py` scripts are cross-platform)
- **Python**: 3.10+ with `cryptography` (`pip install cryptography`) for AES-128 encryption/decryption
- **Java (JDK 11+)**: `java` and `keytool` in `PATH` for `apktool.jar`, `uber-apk-signer.jar`, and keystore generation

---

## Quick Start

### 0. Prepare Base Assets (`latest.json` and `FFBE_GL.zip`)

#### (1) Obtain the Official Offline Manifest (`release/latest.json`)

Download the latest `latest.json` from either official `exvius-offline` repository and place it at **`release/latest.json`**:
- Stable: [Elvlin/exvius-offline](https://github.com/Elvlin/exvius-offline)
- Beta: [Elvlin/exvius-offline-beta](https://github.com/Elvlin/exvius-offline-beta)

> [!NOTE]
> You do not need to manually download the individual `.zip` data packs or the base APK. Once `release/latest.json` is in place, running the full build (or Step `1` via [sync_release.py](scripts/sync_release.py)) will automatically verify, download (multi-threaded with resume support) all missing `.zip` packs into `release/` and the matching APK into `source/apk/`, and clean up obsolete packs.

#### (2) Prepare the Archived GL Assets (`source/FFBE_GL.zip`)

The historical Global localization assets come from the Internet Archive preservation dump:  
<https://archive.org/download/ffbe-eos-contentserver-archive/ffbe_gl_contentserver_archive.rar>

Because the downloaded file is a **`.rar` archive with nested wrapper directories**, and [extract_archive.py](scripts/extract_archive.py) uses Python's built-in `zipfile` module (which cannot read `.rar` directly), prepare `source/FFBE_GL.zip` as follows:

1. Extract `ffbe_gl_contentserver_archive.rar` to a temporary folder and verify it contains a `dlc_assets_prod/` directory (any number of outer domain/mirror wrapper folders is fine — the script anchors directly on `dlc_assets_prod/`).
2. Re-compress the folder containing `dlc_assets_prod/` into **`source/FFBE_GL.zip`**.
   - **Time & Space Tip**: If you do not want to re-zip every language in the archive, you only need to keep `localized_texts/zh/`, `zh/sd/`, and `common_lang/` under `dlc_assets_prod/` when creating `FFBE_GL.zip` (using ZIP **Store / no compression** is much faster since `.cpk` and `.png` files are already compressed).

### 1. Build Data Packs & Dictionary

Run the unified build script from `scripts/`:

```powershell
cd scripts
.\build_all.ps1
```

Enter **`0`** in the interactive menu (cleans old `work/`, runs Steps `1`–`9`), and enter your desired multipliers when prompted (press Enter for **`10x`** defaults, or **`1`** for vanilla).  
For your first build or whenever the official base APK updates, also run **`a`** once to patch and sign the APK:

```powershell
.\build_all.ps1 a
```

Final outputs are placed in `release_repack/`:
- `exvius-offline-*-zh-patched.apk`: Patched & signed offline APK
- `latest.json` and `data_*.zip`: Clean, deduplicated local update packs

### 2. Device Installation & Update

1. Create or open `/sdcard/Download/updates/` on your Android device's internal storage.
2. Copy **`latest.json` and all `data_*.zip` files** from `release_repack/` into `/sdcard/Download/updates/`.
3. Install `release_repack/exvius-offline-*-zh-patched.apk`.
4. On first launch, grant **"All files access"** (`MANAGE_EXTERNAL_STORAGE`) when prompted; the app will automatically load the local `latest.json` and data packs.

> [!TIP]
> If you later tweak only translations or multipliers, run `.\build_all.ps1 7` (or edit `work/` manually) followed by `.\build_all.ps1 8`, then overwrite `latest.json` and `data_*.zip` in `/sdcard/Download/updates/`. **You do not need to reinstall the APK.**

---

## Porting to Other Languages

The underlying infrastructure (manifest extraction, AES encryption/decryption, CRI CPK parsing/rebuilding, reward scaling, 1.8 GB deduplicated repacking, and offline APK Smali patching) is **language-agnostic**. Depending on your target language, refer to the guide below:

### 1. Porting Other Official GL Languages (`ko` / `fr` / `de` / `es` / `th` / `id`)

Besides `en` and `zh`, both `ffbe_gl_contentserver_archive.rar` and `game-assets.apk` already include complete official assets for **Korean (`ko`), French (`fr`), German (`de`), Spanish (`es`), Thai (`th`), and Indonesian (`id`)**. Porting to any of them requires adjustments in 4 places:

1. **Archive Extraction & Paths ([extract_archive.py](scripts/extract_archive.py), [build_all.ps1](scripts/build_all.ps1))**
   - Update `TARGETS` (`localized_texts/zh/`, `zh/sd/`) to your target language folder (e.g. `localized_texts/ko/`, `ko/sd/`), and update the corresponding arguments in Steps `4` and `5` of `build_all.ps1`.
2. **Remove the CJK Regex Filter ([make_zh_patch.py](scripts/make_zh_patch.py))**
   - `_CJK_RE` and `_has_cjk_in_line()` currently match Han characters (`[\u4e00-\u9fff]`) to skip untranslated English placeholders.
   - For Korean/Thai, replace this regex with the target script's Unicode range; for Latin-script languages (`fr`/`de`/`es`/`id`), check that the target string is non-empty and differs from the English column.
3. **Update `_sg.txt` Column Index ([ffbe_cpk.py](scripts/ffbe_cpk.py))**
   - Story and town dialogue `_sg.txt` files inside `common_lang` CPKs store all 8 GL languages per line in fixed column order:
     `ID(0) ^ EN(1) ^ ZH(2) ^ KO(3) ^ FR(4) ^ DE(5) ^ ES(6) ^ TH(7) ^ ID(8)`
   - Update `_extract_sg_rows()` and `_swap_sg_text()` to read the column index matching your target language instead of `parts[2]`, and replace `is_zh()` accordingly.
4. **Extra Assets & HTML Templates ([patch_extras.py](scripts/patch_extras.py))**
   - In `patch_game_assets_apk()`, change `assets/localized_texts/zh/0ThfQQWd.csv` and `assets/cpk/zh/sd/resources/resources_pretutorial.cpk` to your target language paths, and replace `_CJK_RE.search(...)` checks in the helper functions.
   - In `patch_equipment_enhancement()`, translate or skip the hardcoded Traditional Chinese templates (`_EXACT_HTML_PHRASES`, `_STATIC_REWARDS`, `_MONTH_ZH`, `_STAT_WORDS`, and `<html lang="zh-Hant">`) used for the 54 Item World HTML pages.
5. **Optional**: Update `TABLE_META` descriptions in [stat_zh_coverage.py](scripts/stat_zh_coverage.py), the UI text in [save_editor_web/index.html](save_editor_web/index.html), and the `-zh-patched.apk` filename suffix in [patch_apk.py](scripts/patch_apk.py).

### 2. Custom Fan Translations & Japanese (`ffbe_jp_contentserver_archive.rar`)

- **New / Non-GL Languages (e.g. Simplified Chinese, Russian, Portuguese)**: You can reuse the decoded `work/text_decoded/*.ndjson`, `KEY^TEXT` `.dat` tables, and `_sg.txt` pipeline by injecting your own translation dictionary. For non-Latin / non-CJK scripts, test on-device to confirm the client's bundled fonts include the required glyphs.
- **Japanese Archive (`ffbe_jp_contentserver_archive.rar`)**: The JP archive uses different table tokens, single-column layouts, and asset naming compared to the GL-based `exvius-offline` client, so direct column swapping does not apply and cross-server ID mapping is required.

---

## Further Details

For detailed CLI flags, data table internals, and troubleshooting, see [scripts/README.en.md](scripts/README.en.md) (or [scripts/README.md](scripts/README.md) in Chinese).
