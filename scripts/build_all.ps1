<#
.SYNOPSIS
    FFBE 离线版繁体中文重构工具统一入口脚本（交互菜单 / 命令行批处理）。
.DESCRIPTION
    串联官方底包同步、档案馆解压、系统文本/UI/剧情/额外资源汉化、去重打包、
    存档修改器词典导出以及 APK 自动修补签名等全部步骤。
#>
param(
    [string]$Action = "",
    [double]$DropRateMult = -1,
    [double]$DropCountMult = -1,
    [double]$LapisMult = -1
)

$Scripts = $PSScriptRoot
$Root = Split-Path -Path $Scripts -Parent
$ArchiveZip = "$Root\source\FFBE_GL.zip"
$Archive = "$Root\source\archive"
$OfflineRoot = "$Root\source\offline"
$Offline = "$OfflineRoot\native"
$Work = "$Root\work"
$Release = "$Root\release"
$Repack = "$Root\release_repack"

Set-Location $Scripts

# 获取 source/apk 目录下最新修改的官方 APK 文件路径
function Get-LatestApk {
    $ApkObj = Get-ChildItem "$Root\source\apk\*.apk" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
    if (-not $ApkObj) { throw "在 $Root\source\apk 中找不到任何 APK 文件" }
    Write-Host "自动选用 APK: $($ApkObj.Name)" -ForegroundColor Yellow
    return $ApkObj.FullName
}

$script:NextRevision = 0

# 清空 release_repack 中旧的数据分包与 latest.json（保留已修补 APK，并记录递增后的 revision）
function Clear-RepackData {
    if (-not (Test-Path $Repack)) { return }
    $oldManifest = Join-Path $Repack "latest.json"
    if (Test-Path $oldManifest) {
        try {
            $oldObj = Get-Content $oldManifest -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($oldObj.revision) {
                $cand = [int]$oldObj.revision + 1
                if ($cand -gt $script:NextRevision) { $script:NextRevision = $cand }
            }
        } catch {}
    }
    Get-ChildItem $Repack -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "data_*.zip" -or $_.Name -like "exvius-repack-*.zip" -or $_.Name -eq "latest.json" } |
    Remove-Item -Force -ErrorAction SilentlyContinue
}

# [c] 删除中转目录 work 与成品目录 release_repack
function Invoke-Clean {
    Write-Host "`n=== [c] 清理旧中转与 release_repack ===" -ForegroundColor Cyan
    Remove-Item $Work   -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $Repack -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "清理完成 (已清空 work 与 release_repack)" -ForegroundColor Green
}

# [1] 按 latest.json 校验并清理/下载官方 release 分包与 APK
function Invoke-Step1 {
    Write-Host "`n=== [1] 同步 latest.json 内容 ===" -ForegroundColor Cyan
    python sync_release.py --release $Release --apk-dir "$Root\source\apk" --download
    if ($LASTEXITCODE -ne 0) { throw "步骤 [1] sync_release.py 执行失败" }
}

# [2] 从 FFBE_GL.zip 解压繁中本地化文本与资源目录
function Invoke-Step2 {
    Write-Host "`n=== [2] 检查/提取档案馆繁中素材 ===" -ForegroundColor Cyan
    python extract_archive.py --zip $ArchiveZip --out $Archive
    if ($LASTEXITCODE -ne 0) { throw "步骤 [2] extract_archive.py 执行失败" }
}

# [3] 从官方 release 提取纯净离线底包至 source/offline
function Invoke-Step3 {
    Write-Host "`n=== [3] 提取纯净官方底包 ===" -ForegroundColor Cyan
    python extract_clean_offline.py --release $Release --out $OfflineRoot
    if ($LASTEXITCODE -ne 0) { throw "步骤 [3] extract_clean_offline.py 执行失败" }
}

# [4] 解析 APK 加密清单并生成汉化系统文本表 (.dat / .ndjson)
function Invoke-Step4([string]$Apk) {
    Write-Host "`n=== [4] 生成系统文本表 ===" -ForegroundColor Cyan
    python make_zh_patch.py `
        --apk $Apk `
        --en "$Offline\localized_texts\en" `
        --zh "$Archive\localized_texts\zh" `
        --out $Work
    if ($LASTEXITCODE -ne 0) { throw "步骤 [4] make_zh_patch.py 执行失败" }
}

# [5] 同步繁中 UI 图片及预解包横幅素材
function Invoke-Step5 {
    Write-Host "`n=== [5] 同步繁中 UI 图片 ===" -ForegroundColor Cyan
    python sync_sd_cpk.py `
        --en-sd "$Offline\en\sd" `
        --zh-sd "$Archive\zh\sd" `
        --out "$Work\en\sd" `
        --en-cpk-extract "$OfflineRoot\static\cpk_extract\en" `
        --out-cpk-extract "$Work\cpk_extract\en"
    if ($LASTEXITCODE -ne 0) { throw "步骤 [5] sync_sd_cpk.py 执行失败" }
}

# 解析战斗掉率、掉落数量/Gil/EXP、关卡挑战宝石倍率（CLI 参数优先，否则交互提示且默认 10 倍）
function Resolve-DropMultipliers {
    if ($DropRateMult -gt 0 -and $DropCountMult -gt 0 -and $LapisMult -gt 0) {
        return @($DropRateMult, $DropCountMult, $LapisMult)
    }
    if ($Action -ne "") {
        $r = if ($DropRateMult -gt 0) { $DropRateMult } else { 10 }
        $c = if ($DropCountMult -gt 0) { $DropCountMult } else { 10 }
        $l = if ($LapisMult -gt 0) { $LapisMult } else { 10 }
        return @($r, $c, $l)
    }
    $inRate = Read-Host "请输入战斗掉落率倍率 (直接回车默认 10 倍，原版填 1)"
    $r = 10.0
    if ($inRate.Trim() -ne "") {
        [double]$parsedR = 0
        if ([double]::TryParse($inRate.Trim(), [ref]$parsedR) -and $parsedR -gt 0) { $r = $parsedR }
    }
    $inCnt = Read-Host "请输入战斗掉落量与Gil/EXP倍率 (直接回车默认 10 倍，原版填 1)"
    $c = 10.0
    if ($inCnt.Trim() -ne "") {
        [double]$parsedC = 0
        if ([double]::TryParse($inCnt.Trim(), [ref]$parsedC) -and $parsedC -gt 0) { $c = $parsedC }
    }
    $inLapis = Read-Host "请输入关卡挑战宝石倍率 (直接回车默认 10 倍，原版填 1)"
    $l = 10.0
    if ($inLapis.Trim() -ne "") {
        [double]$parsedL = 0
        if ([double]::TryParse($inLapis.Trim(), [ref]$parsedL) -and $parsedL -gt 0) { $l = $parsedL }
    }
    return @($r, $c, $l)
}

# [6] 汉化全目录剧情与城镇对话 CPK
function Invoke-Step6 {
    Write-Host "`n=== [6] 汉化全目录剧情与城镇对话 ===" -ForegroundColor Cyan
    python patch_event_cpk.py `
        --src    "$Offline\common_lang" `
        --zh-src "$Archive\common_lang" `
        --out    "$Work\common_lang"
    if ($LASTEXITCODE -ne 0) { throw "步骤 [6] patch_event_cpk.py 执行失败" }
}

# [7] 汉化额外文本资源、敌人技能、54个装备网页及战斗/任务倍率调整
function Invoke-Step7 {
    Write-Host "`n=== [7] 汉化额外资源与倍率调整 ===" -ForegroundColor Cyan
    $mults  = Resolve-DropMultipliers
    $rateM  = $mults[0]
    $cntM   = $mults[1]
    $lapisM = $mults[2]
    Write-Host "当前倍率设定: 掉率 ${rateM}x | 掉落量/Gil/EXP ${cntM}x | 挑战宝石 ${lapisM}x" -ForegroundColor DarkGray
    python patch_extras.py `
        --offline-root    $OfflineRoot `
        --work            $Work `
        --drop-rate-mult  $rateM `
        --drop-count-mult $cntM `
        --lapis-mult      $lapisM
    if ($LASTEXITCODE -ne 0) { throw "步骤 [7] patch_extras.py 执行失败" }
}

# [8] 合并官方底包与 work/ 汉化产物并生成纯净离线分包（data_*.zip + latest.json）
function Invoke-Step8 {
    Write-Host "`n=== [8] 全量去重重构纯净整合包 ===" -ForegroundColor Cyan
    Clear-RepackData
    python repack_all_clean.py `
        --src          $Release `
        --work-out     $Work `
        --out          $Repack `
        --min-revision $script:NextRevision
    if ($LASTEXITCODE -ne 0) { throw "步骤 [8] repack_all_clean.py 执行失败" }
    Write-Host "`n纯净整合包已输出至：$Repack" -ForegroundColor Green
}

# [9] 从 work/text_decoded 提取兵员/装备/道具中文词典供网页存档修改器使用
function Invoke-Step9 {
    Write-Host "`n=== [9] 同步存档修改器中文词典 ===" -ForegroundColor Cyan
    python export_names_json.py
    if ($LASTEXITCODE -ne 0) { throw "步骤 [9] export_names_json.py 执行失败" }
}

# [a] 反编译、注入离线补丁与存储权限并重新签名 APK
function Invoke-PatchApk([string]$Apk) {
    Write-Host "`n=== [a] 自动修补并签名离线版 APK ===" -ForegroundColor Cyan
    python patch_apk.py --apk $Apk
    if ($LASTEXITCODE -ne 0) { throw "选项 [a] patch_apk.py 执行失败" }
}

# [s] 统计系统文本表的繁中覆盖率
function Invoke-Stats {
    Write-Host "`n=== [s] 统计系统文本汉化覆盖率 ===" -ForegroundColor Cyan
    python stat_zh_coverage.py
    if ($LASTEXITCODE -ne 0) { throw "stat_zh_coverage.py 执行失败" }
}

# 根据菜单或参数编号分发对应构建任务
function Invoke-Choice([string]$Choice) {
    try {
        switch ($Choice.Trim().ToLower()) {
            "0" {
                # ── 一键全量构建：执行步骤 1~9（选项 a 修补 APK 每版只需单独执行一次）──
                $SharedApk = Get-LatestApk
                Write-Host "全量构建提取清单使用 APK: $SharedApk" -ForegroundColor DarkGray

                Write-Host "`n=== [c] 清理旧中转 work 与旧分包数据（保留已修补 APK）===" -ForegroundColor Cyan
                Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
                Clear-RepackData
                Invoke-Step1
                Invoke-Step2
                Invoke-Step3
                Invoke-Step4 $SharedApk
                Invoke-Step5
                Invoke-Step6
                Invoke-Step7
                Invoke-Step8
                Invoke-Step9

                $ExpectedApk = Join-Path $Repack ("{0}-zh-patched.apk" -f [System.IO.Path]::GetFileNameWithoutExtension($SharedApk))
                if (-not (Test-Path $ExpectedApk)) {
                    Write-Host "[提示] $Repack 中尚未找到 $([System.IO.Path]::GetFileName($ExpectedApk))，若为新版本 APK 请单独执行一次 [a]。" -ForegroundColor Yellow
                }
                Write-Host "`n=== 全量构建完成（步骤 1~9）！ ===" -ForegroundColor Green
            }
            "1" { Invoke-Step1 }
            "2" { Invoke-Step2 }
            "3" { Invoke-Step3 }
            "4" { Invoke-Step4 (Get-LatestApk) }
            "5" { Invoke-Step5 }
            "6" { Invoke-Step6 }
            "7" { Invoke-Step7 }
            "8" { Invoke-Step8 }
            "9" { Invoke-Step9 }
            "a" { Invoke-PatchApk (Get-LatestApk) }
            "s" { Invoke-Stats }
            "c" { Invoke-Clean }
            default {
                Write-Host "无效选项: $Choice (请输入 0~9、a、s、c 或 q)" -ForegroundColor Red
            }
        }
    }
    catch {
        Write-Host "`n[错误] $_" -ForegroundColor Red
    }
}

# 若启动时直接传入参数（例如 .\build_all.ps1 9），执行完直接退出
if ($Action -ne "") {
    Invoke-Choice $Action
    exit
}

# 交互式菜单模式
while ($true) {
    Write-Host "`n================ FFBE 繁中重构工具 ================" -ForegroundColor Cyan
    Write-Host "  0. 一键全量构建              (清理 + 步骤 1~9)" -ForegroundColor Yellow
    Write-Host "  [素材准备]" -ForegroundColor DarkGray
    Write-Host "  1. 同步 latest.json 内容     (sync_release.py)"
    Write-Host "  2. 提取档案馆繁中素材        (extract_archive.py)"
    Write-Host "  3. 提取纯净官方底包          (extract_clean_offline.py)"
    Write-Host "  [汉化处理]" -ForegroundColor DarkGray
    Write-Host "  4. 生成系统文本表            (make_zh_patch.py)"
    Write-Host "  5. 同步繁中 UI 图片          (sync_sd_cpk.py)"
    Write-Host "  6. 汉化剧情与城镇对话        (patch_event_cpk.py)"
    Write-Host "  7. 汉化额外资源与倍率调整    (patch_extras.py)"
    Write-Host "  [整合输出]" -ForegroundColor DarkGray
    Write-Host "  8. 重构纯净整合包            (repack_all_clean.py)"
    Write-Host "  9. 同步存档修改器词典        (export_names_json.py)"
    Write-Host "  a. 自动修补并签名 APK        (patch_apk.py)"
    Write-Host "  [辅助维护]" -ForegroundColor DarkGray
    Write-Host "  s. 统计汉化覆盖率            (stat_zh_coverage.py)"
    Write-Host "  c. 清理缓存                  (清空 work 与 release_repack)"
    Write-Host "  q. 退出"
    Write-Host "===================================================" -ForegroundColor Cyan

    $sel = Read-Host "请输入编号"
    if ($sel.Trim().ToLower() -eq "q") { break }
    Invoke-Choice $sel
}
