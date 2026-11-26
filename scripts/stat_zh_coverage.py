"""
统计系统文本汉化覆盖率（支持官方表名映射与按游戏模块分类汇总）。

明文表（Beta 版）：直接对比 en 源与 work 输出，统计已注入比例。
加密表（正式版）：若提供 --manifest-cache（由步骤4生成），则解密后统计；否则跳过。
"""
import argparse
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

from ffbe_utils import decrypt_dat_lines, get_latest_files, is_unencrypted_dat, key16, load_manifest_cache_from_paths

ROOT_DIR = Path(__file__).resolve().parent.parent

# 模块分类显示顺序
CATEGORY_ORDER = [
    "兵员 / 幻兵卡 / 幻兽",
    "能力 / 魔法 / 战斗",
    "怪物 / 图鉴 / 斗技场",
    "装备 / 道具 / 商店召唤",
    "剧情 / 关卡 / 活动任务",
    "世界 / 城镇 / 地图NPC",
    "系统 UI / 成就 / 杂项",
]

# 官方表名 -> (所属模块分类, 中文功能说明)
TABLE_META: dict[str, tuple[str, str]] = {
    # ── 兵员 / 幻兵卡 / 幻兽 ────────────────────────────────────────────────
    "F_TEXT_UNITS_NAME":                       ("兵员 / 幻兵卡 / 幻兽", "兵员名称"),
    "F_TEXT_UNIT_DESCRIPTION":                 ("兵员 / 幻兵卡 / 幻兽", "兵员背景描述"),
    "F_TEXT_UNIT_SUB_CATEGORY_NAME":           ("兵员 / 幻兵卡 / 幻兽", "兵员子分类名称"),
    "F_TEXT_UNIT_EVO":                         ("兵员 / 幻兵卡 / 幻兽", "兵员觉醒台词与说明"),
    "F_TEXT_UNIT_FUSION":                      ("兵员 / 幻兵卡 / 幻兽", "兵员强化台词与说明"),
    "F_TEXT_UNIT_SUMMON":                      ("兵员 / 幻兵卡 / 幻兽", "兵员召唤台词"),
    "F_TEXT_UNIT_AFFINITY":                    ("兵员 / 幻兵卡 / 幻兽", "兵员信赖度相关文本"),
    "F_TEXT_UNIT_EXPLAIN_SHOP":                ("兵员 / 幻兵卡 / 幻兽", "商店兵员说明"),
    "F_TEXT_UNIT_SERIES_POWER_UP":             ("兵员 / 幻兵卡 / 幻兽", "兵员系列强化说明"),
    "F_TEXT_NV_UNIT_CLASS_UP":                 ("兵员 / 幻兵卡 / 幻兽", "NV兵员EX觉醒说明"),
    "F_TEXT_CHARACTER_NAME":                   ("兵员 / 幻兵卡 / 幻兽", "角色名称"),
    "F_TEXT_JOB_NAME":                         ("兵员 / 幻兵卡 / 幻兽", "兵员职业名称"),
    "F_TEXT_LIMIT_BURST_NAME":                 ("兵员 / 幻兵卡 / 幻兽", "极限技(LB)名称"),
    "F_TEXT_LIMIT_BURST_DES":                  ("兵员 / 幻兵卡 / 幻兽", "极限技(LB)描述"),
    "F_TEXT_VISION_CARD_NAME":                 ("兵员 / 幻兵卡 / 幻兽", "幻兵卡名称"),
    "F_TEXT_VISION_CARD_EXPLAIN_SHORT":        ("兵员 / 幻兵卡 / 幻兽", "幻兵卡简短说明"),
    "F_TEXT_VISION_CARD_EXPLAIN_LONG":         ("兵员 / 幻兵卡 / 幻兽", "幻兵卡详细说明"),
    "F_TEXT_VISION_CARD_ABILITY_ACT_COND":     ("兵员 / 幻兵卡 / 幻兽", "幻兵卡能力发动条件"),
    "F_TEXT_VISION_CARD_SUMMON":               ("兵员 / 幻兵卡 / 幻兽", "幻兵卡召唤文本"),
    "F_TEXT_VISION_CARD_MIX":                  ("兵员 / 幻兵卡 / 幻兽", "幻兵卡强化合成文本"),
    "F_TEXT_VISION_CARD_SHOP":                 ("兵员 / 幻兵卡 / 幻兽", "幻兵卡商店文本"),
    "F_TEXT_VISION_CARD_BGM":                  ("兵员 / 幻兵卡 / 幻兽", "幻兵卡BGM名称"),
    "F_TEXT_VISION_CARD_DICTIONARY":           ("兵员 / 幻兵卡 / 幻兽", "幻兵卡图鉴说明"),
    "F_TEXT_BEAST_NAME":                       ("兵员 / 幻兵卡 / 幻兽", "幻兽名称"),
    "F_TEXT_BEAST_SKILL_NAME":                 ("兵员 / 幻兵卡 / 幻兽", "幻兽技能名称"),
    "F_TEXT_BEAST_SKILL_DES":                  ("兵员 / 幻兵卡 / 幻兽", "幻兽技能描述"),
    "F_TEXT_SUBLIMATION_EXPLAIN":              ("兵员 / 幻兵卡 / 幻兽", "能力/兵员升华觉醒说明"),

    # ── 能力 / 魔法 / 战斗 ──────────────────────────────────────────────────
    "F_TEXT_ABILITY_NAME":                         ("能力 / 魔法 / 战斗", "能力名称"),
    "F_TEXT_ABILITY_EXPLAIN_SHORT":                ("能力 / 魔法 / 战斗", "能力简短说明"),
    "F_TEXT_ABILITY_PARAM_MSG":                    ("能力 / 魔法 / 战斗", "能力参数提示文本"),
    "F_TEXT_HIDDEN_SKILL_UPGRADE":                 ("能力 / 魔法 / 战斗", "潜在/隐藏技能强化"),
    "F_TEXT_MAGIC_NAME":                           ("能力 / 魔法 / 战斗", "魔法名称"),
    "F_TEXT_MAGIC_EXPLAIN_SHORT":                  ("能力 / 魔法 / 战斗", "魔法简短说明"),
    "F_TEXT_CHAIN_FAMILY_NAME":                    ("能力 / 魔法 / 战斗", "连锁系列(Chain)名称"),
    "F_TEXT_BATTLE_SCRIPT":                        ("能力 / 魔法 / 战斗", "战斗对话与AI脚本台词"),
    "F_TEXT_SCENARIO_BATTLE":                      ("能力 / 魔法 / 战斗", "剧情战斗文本"),
    "F_TEXT_GAMBIT_NAME":                          ("能力 / 魔法 / 战斗", "预设指令(Gambit)名称"),
    "F_TEXT_GAMBIT_EXPLAIN":                       ("能力 / 魔法 / 战斗", "预设指令(Gambit)说明"),
    "F_TEXT_MORALE_IN_BATTLE_RULE_EXPLAIN":        ("能力 / 魔法 / 战斗", "CoW战中士气规则说明"),
    "F_TEXT_MORALE_BEFORE_BATTLE_RULE_EXPLAIN":    ("能力 / 魔法 / 战斗", "CoW战前士气规则说明"),
    "F_TEXT_PROCESS_TYPE_EXPLAIN_LONG":            ("能力 / 魔法 / 战斗", "主动效果类型详述"),
    "F_TEXT_PROCESS_PASSIVE_TYPE_EXPLAIN_LONG":    ("能力 / 魔法 / 战斗", "被动效果类型详述"),
    "F_TEXT_AREA_PROCESS_PASSIVE_TYPE_EXPLAIN_LONG": ("能力 / 魔法 / 战斗", "区域效果详细说明"),
    "F_TEXT_FIELD_EFFECT_EXPLAIN_SHORT":           ("能力 / 魔法 / 战斗", "场地效果简短说明"),
    "F_TEXT_FIELD_EFFECT_EXPLAIN_LONG":            ("能力 / 魔法 / 战斗", "场地效果详细说明"),
    "F_TEXT_DESCRIPTION_FORMAT_1":                 ("能力 / 魔法 / 战斗", "效果描述模板1"),
    "F_TEXT_DESCRIPTION_FORMAT_2":                 ("能力 / 魔法 / 战斗", "效果描述模板2"),
    "F_TEXT_DESCRIPTION_FORMAT_3":                 ("能力 / 魔法 / 战斗", "效果描述模板3"),
    "F_TEXT_DESCRIPTION_FORMAT_4":                 ("能力 / 魔法 / 战斗", "效果描述模板4"),
    "F_TEXT_DESCRIPTION_FORMAT_5":                 ("能力 / 魔法 / 战斗", "效果描述模板5"),

    # ── 怪物 / 图鉴 / 斗技场 ────────────────────────────────────────────────
    "F_TEXT_MONSTER_NAME":                     ("怪物 / 图鉴 / 斗技场", "怪物名称"),
    "F_TEXT_MONSTER_SKILL_NAME":               ("怪物 / 图鉴 / 斗技场", "怪物/敌人技能名称"),
    "F_TEXT_MONSTER_DICTIONARY_NAME":          ("怪物 / 图鉴 / 斗技场", "怪物图鉴名称"),
    "F_TEXT_MONSTER_DIC_EXPLAIN_SHORT":        ("怪物 / 图鉴 / 斗技场", "怪物图鉴简短说明"),
    "F_TEXT_MONSTER_DIC_EXPLAIN_LONG":         ("怪物 / 图鉴 / 斗技场", "怪物图鉴详细说明"),
    "F_TEXT_MONSTER_PART_DIC_NAME":            ("怪物 / 图鉴 / 斗技场", "怪物部位图鉴名称"),
    "F_TEXT_MONSTER_PARTS_BREAK_DES":          ("怪物 / 图鉴 / 斗技场", "怪物部位破坏提示"),
    "F_TEXT_TRIBE":                            ("怪物 / 图鉴 / 斗技场", "怪物种族名称"),
    "F_TEXT_COLOSSEUM_GRADE":                  ("怪物 / 图鉴 / 斗技场", "斗技场阶级名称"),
    "F_TEXT_COLOSSEUM_MONSTER_GROUP_NAME":     ("怪物 / 图鉴 / 斗技场", "斗技场怪物组合名"),

    # ── 装备 / 道具 / 商店召唤 ──────────────────────────────────────────────
    "F_TEXT_ITEM_NAME":                        ("装备 / 道具 / 商店召唤", "消耗道具名称"),
    "F_TEXT_ITEM_EXPLAIN_SHORT":               ("装备 / 道具 / 商店召唤", "消耗道具简短说明"),
    "F_TEXT_ITEM_EXPLAIN_LONG":                ("装备 / 道具 / 商店召唤", "消耗道具详细说明"),
    "F_TEXT_ITEM_EQUIP_NAME":                  ("装备 / 道具 / 商店召唤", "武器防具饰品名称"),
    "F_TEXT_ITEM_EQUIP_SHORT":                 ("装备 / 道具 / 商店召唤", "装备简短说明"),
    "F_TEXT_ITEM_EQUIP_LONG":                  ("装备 / 道具 / 商店召唤", "装备详细说明"),
    "F_TEXT_MATERIA_NAME":                     ("装备 / 道具 / 商店召唤", "能力晶石名称"),
    "F_TEXT_MATERIA_EXPLAIN_SHORT":            ("装备 / 道具 / 商店召唤", "能力晶石简短说明"),
    "F_TEXT_MATERIA_EXPLAIN_LONG":             ("装备 / 道具 / 商店召唤", "能力晶石详细说明"),
    "F_TEXT_IMPORTANT_ITEM_NAME":              ("装备 / 道具 / 商店召唤", "重要道具名称"),
    "F_TEXT_IMPORTANT_ITEM_EXPLAIN_SHORT":     ("装备 / 道具 / 商店召唤", "重要道具简短说明"),
    "F_TEXT_IMPORTANT_ITEM_EXPLAIN_LONG":      ("装备 / 道具 / 商店召唤", "重要道具详细说明"),
    "F_TEXT_RECIPE_BOOK_NAME":                 ("装备 / 道具 / 商店召唤", "配方书名称"),
    "F_TEXT_RECIPE_EXPLAIN_LONG":              ("装备 / 道具 / 商店召唤", "制作配方详细说明"),
    "F_TEXT_SHOP":                             ("装备 / 道具 / 商店召唤", "商店文本"),
    "F_TEXT_BUNDLE":                           ("装备 / 道具 / 商店召唤", "礼包名称与说明"),
    "F_TEXT_EXCHANGE_SHOP_ITEM":               ("装备 / 道具 / 商店召唤", "交换所兑换物品文本"),
    "F_TEXT_IMPORTANT_ITEM_SHOP":              ("装备 / 道具 / 商店召唤", "重要道具商店文本"),
    "F_TEXT_NV_SHOP_PRODUCT_NAME":             ("装备 / 道具 / 商店召唤", "NV商店商品名称"),
    "F_TEXT_NV_SHOP_PRODUCT_SHORT_DES":        ("装备 / 道具 / 商店召唤", "NV商店商品简述"),
    "F_TEXT_NV_SHOP_PRODUCT_LONG_DES":         ("装备 / 道具 / 商店召唤", "NV商店商品详述"),
    "F_TEXT_DIAMOND_NAME":                     ("装备 / 道具 / 商店召唤", "矿石/宝石商店名称"),
    "F_TEXT_GACHA":                            ("装备 / 道具 / 商店召唤", "召唤卡池标题与说明"),
    "F_TEXT_GACHA_BOX_DETAIL_NAME":            ("装备 / 道具 / 商店召唤", "箱池召唤详情名称"),

    # ── 剧情 / 关卡 / 活动任务 ──────────────────────────────────────────────
    "F_TEXT_STORY_NAME":                       ("剧情 / 关卡 / 活动任务", "主线剧情章节标题"),
    "F_TEXT_STORY_SUB":                        ("剧情 / 关卡 / 活动任务", "剧情副标题"),
    "F_TEXT_EXPN_STORY":                       ("剧情 / 关卡 / 活动任务", "探索/外传剧情文本"),
    "F_TEXT_PLAYBACK":                         ("剧情 / 关卡 / 活动任务", "剧情回放文本"),
    "F_TEXT_PICTURE_STORY_NAME":               ("剧情 / 关卡 / 活动任务", "故事绘本名称"),
    "F_TEXT_MISSION":                          ("剧情 / 关卡 / 活动任务", "关卡战斗与任务目标"),
    "F_TEXT_CHALLENGE_NAME":                   ("剧情 / 关卡 / 活动任务", "关卡挑战条件名称"),
    "F_TEXT_EXTRA_CHALLENGE_NAME":             ("剧情 / 关卡 / 活动任务", "额外挑战条件名称"),
    "F_TEXT_SPCHALLENGE":                      ("剧情 / 关卡 / 活动任务", "特殊挑战任务文本"),
    "F_TEXT_QUEST":                            ("剧情 / 关卡 / 活动任务", "任务名称与文本"),
    "F_TEXT_QUEST_DISP_NAME":                  ("剧情 / 关卡 / 活动任务", "任务显示名称"),
    "F_TEXT_QUEST_SUB_NAME":                   ("剧情 / 关卡 / 活动任务", "支线任务名称"),
    "F_TEXT_QUEST_SUB_DISP_NAME":              ("剧情 / 关卡 / 活动任务", "支线任务显示名称"),
    "F_TEXT_QUEST_SUB_DETAIL":                 ("剧情 / 关卡 / 活动任务", "支线任务详情"),
    "F_TEXT_QUEST_SUB_STORY":                  ("剧情 / 关卡 / 活动任务", "支线任务剧情文本"),
    "F_TEXT_QUEST_SUB_TARGET_PARAM":           ("剧情 / 关卡 / 活动任务", "支线任务目标参数"),
    "F_TEXT_DAILY_QUEST_NAME":                 ("剧情 / 关卡 / 活动任务", "每日任务名称"),
    "F_TEXT_DAILY_QUEST_DES":                  ("剧情 / 关卡 / 活动任务", "每日任务描述"),
    "F_TEXT_DAILY_QUEST_DETAIL":               ("剧情 / 关卡 / 活动任务", "每日任务详情"),
    "F_TEXT_PANEL_QUEST_NAME":                 ("剧情 / 关卡 / 活动任务", "拼图面板任务名称"),
    "F_TEXT_SEASON_EVENT_NAME":                ("剧情 / 关卡 / 活动任务", "季节活动名称"),
    "F_TEXT_SEASON_EVENT_DESCRIPTION":         ("剧情 / 关卡 / 活动任务", "季节活动描述"),
    "F_TEXT_SEASON_EVENT_ABILITY_NAME":        ("剧情 / 关卡 / 活动任务", "季节活动能力名称"),
    "F_TEXT_SEASON_EVENT_ABILITY_TYPE_NAME":   ("剧情 / 关卡 / 活动任务", "季节活动能力类型名"),
    "F_TEXT_SEASON_EVENT_ABILITY_TYPE_DESCRIPTION": ("剧情 / 关卡 / 活动任务", "季节活动能力类型描述"),
    "F_TEXT_SG_COMEBACK_QUEST_NAME":           ("剧情 / 关卡 / 活动任务", "回归活动任务名称"),
    "F_TEXT_RB_BONUS_RULE_NAME":               ("剧情 / 关卡 / 活动任务", "Rank排名战加成规则"),
    "F_TEXT_RB_BONUS_RULE_DESCRIPTION":        ("剧情 / 关卡 / 活动任务", "Rank排名战加成描述"),
    "F_TEXT_RB_ABILITY_GROUP_NAME":            ("剧情 / 关卡 / 活动任务", "Rank排名战能力组名"),
    "F_TEXT_RB_ABILITY_GROUP_DESCRIPTION":     ("剧情 / 关卡 / 活动任务", "Rank排名战能力组描述"),
    "F_TEXT_RB_FORBIDDEN_INFO_NAME":           ("剧情 / 关卡 / 活动任务", "Rank排名战禁用规则"),
    "F_TEXT_RB_FORBIDDEN_INFO_DESCRIPTION":    ("剧情 / 关卡 / 活动任务", "Rank排名战禁用描述"),

    # ── 世界 / 城镇 / 地图NPC ───────────────────────────────────────────────
    "F_TEXT_WORLD_NAME":                       ("世界 / 城镇 / 地图NPC", "世界/次元名称"),
    "F_TEXT_LAND_NAME":                        ("世界 / 城镇 / 地图NPC", "大陆/大地图名称"),
    "F_TEXT_AREA_NAME":                        ("世界 / 城镇 / 地图NPC", "地区/章节地图名"),
    "F_TEXT_DUNGEON_NAME":                     ("世界 / 城镇 / 地图NPC", "迷宫/副本名称"),
    "F_TEXT_TELEPO_NAME":                      ("世界 / 城镇 / 地图NPC", "传送点名称"),
    "F_TEXT_TOWN_NAME":                        ("世界 / 城镇 / 地图NPC", "城镇名称"),
    "F_TEXT_TOWN_EXPLAIN":                     ("世界 / 城镇 / 地图NPC", "城镇介绍文本"),
    "F_TEXT_TOWN_STORE":                       ("世界 / 城镇 / 地图NPC", "城镇商店名称"),
    "F_TEXT_TOWN_STORE_COMMENT":               ("世界 / 城镇 / 地图NPC", "城镇店主台词"),
    "F_TEXT_TOWN_STORE_OWNER_NAME":            ("世界 / 城镇 / 地图NPC", "城镇店主名称"),
    "F_TEXT_NPC_NAME":                         ("世界 / 城镇 / 地图NPC", "NPC名称"),
    "F_TEXT_MAP_OBJECT":                       ("世界 / 城镇 / 地图NPC", "探索地图物件名称"),

    # ── 系统 UI / 成就 / 杂项 ───────────────────────────────────────────────
    "F_TEXT_TEXT_EN":                          ("系统 UI / 成就 / 杂项", "全局通用系统UI文本"),
    "F_TEXT_DEFINE":                           ("系统 UI / 成就 / 杂项", "系统常量定义文本"),
    "F_TEXT_GAME_TITLE_NAME":                  ("系统 UI / 成就 / 杂项", "历代FF作品标题名"),
    "F_TEXT_ARCHIVE_NAME":                     ("系统 UI / 成就 / 杂项", "图鉴/档案分类名称"),
    "F_TEXT_EMBLEM":                           ("系统 UI / 成就 / 杂项", "徽章名称与说明"),
    "F_TEXT_AWARD_NAME":                       ("系统 UI / 成就 / 杂项", "称号/成就名称"),
    "F_TEXT_AWARD_TYPE":                       ("系统 UI / 成就 / 杂项", "称号分类名"),
    "F_TEXT_AWARD_EXPLAIN":                    ("系统 UI / 成就 / 杂项", "称号获取条件说明"),
    "F_TEXT_TROPHY_EXPLAIN":                   ("系统 UI / 成就 / 杂项", "奖杯/成就说明"),
    "F_TEXT_TROPHY_METER_SERIF":               ("系统 UI / 成就 / 杂项", "奖杯进度提示台词"),
    "F_TEXT_GLORY":                            ("系统 UI / 成就 / 杂项", "荣耀/功绩系统文本"),
    "F_TEXT_TICKER":                           ("系统 UI / 成就 / 杂项", "公告跑马灯文本"),
    "F_TEXT_CAPTURE_INFO":                     ("系统 UI / 成就 / 杂项", "图片/教程引导说明"),
    "F_TEXT_RULE_DESCRIPTION":                 ("系统 UI / 成就 / 杂项", "通用规则说明文本"),
    "F_TEXT_WEB_EVENT":                        ("系统 UI / 成就 / 杂项", "网页活动文本"),
    "F_TEXT_ANALYTICS_ITEMS":                  ("系统 UI / 成就 / 杂项", "统计埋点道具名"),
    "F_TEXT_ANALYTICS_LOCALIZE":               ("系统 UI / 成就 / 杂项", "统计埋点本地化项"),
}


def resolve_table_meta(manifest: dict | None, token: str) -> tuple[str, str, str]:
    """根据 token 从清单缓存反查 (官方表名, 所属模块分类, 中文功能说明)。"""
    entry = manifest.get(token) if manifest else None
    table = entry.get("table", "") if isinstance(entry, dict) else ""
    if not table:
        return "-", "其他 / 未识别", "未匹配表名"
    if table in TABLE_META:
        cat, desc = TABLE_META[table]
        return table, cat, desc
    # 新版未知表按关键字自动兜底归类
    up = table.upper()
    if any(k in up for k in ("UNIT", "VISION_CARD", "BEAST", "LIMIT_BURST", "CHARACTER", "JOB")):
        return table, "兵员 / 幻兵卡 / 幻兽", table.removeprefix("F_TEXT_")
    if any(k in up for k in ("ABILITY", "MAGIC", "BATTLE", "GAMBIT", "MORALE", "CHAIN", "PROCESS", "FIELD")):
        return table, "能力 / 魔法 / 战斗", table.removeprefix("F_TEXT_")
    if any(k in up for k in ("MONSTER", "TRIBE", "COLOSSEUM")):
        return table, "怪物 / 图鉴 / 斗技场", table.removeprefix("F_TEXT_")
    if any(k in up for k in ("ITEM", "MATERIA", "RECIPE", "SHOP", "BUNDLE", "GACHA", "DIAMOND")):
        return table, "装备 / 道具 / 商店召唤", table.removeprefix("F_TEXT_")
    if any(k in up for k in ("STORY", "QUEST", "MISSION", "CHALLENGE", "EVENT", "RB_", "PLAYBACK")):
        return table, "剧情 / 关卡 / 活动任务", table.removeprefix("F_TEXT_")
    if any(k in up for k in ("WORLD", "LAND", "AREA", "DUNGEON", "TOWN", "TELEPO", "NPC", "MAP")):
        return table, "世界 / 城镇 / 地图NPC", table.removeprefix("F_TEXT_")
    return table, "系统 UI / 成就 / 杂项", table.removeprefix("F_TEXT_")


def _disp_width(s: str) -> int:
    """计算字符串在等宽终端中的显示列宽（中文全角按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def _rpad_disp(s: str, width: int) -> str:
    """按终端显示列宽左补空格右对齐（专用于中文表头与数字列对齐）。"""
    w = _disp_width(s)
    return (" " * max(0, width - w)) + s


def _pad_disp(s: str, width: int) -> str:
    """按终端显示列宽截断或右补空格，确保中英文混排表格左对齐。"""
    cur = 0
    out: list[str] = []
    for ch in s:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if cur + cw > width:
            if width >= 2:
                while out and cur > width - 2:
                    rem = out.pop()
                    cur -= 2 if unicodedata.east_asian_width(rem) in ("W", "F") else 1
                out.append("..")
                cur += 2
            break
        out.append(ch)
        cur += cw
    return "".join(out) + (" " * max(0, width - cur))


# ──────────────────────────────────────────────────────────────────────────────
# 明文表工具（与 make_zh_patch.py 保持一致）
# ──────────────────────────────────────────────────────────────────────────────

def parse_entries(text: str) -> dict[str, str]:
    """提取明文 .dat 中的所有 KEY -> TEXT（兼容 JSON 战斗脚本逐行签名与 KEY^TEXT）。"""
    entries: dict[str, str] = {}
    json_sig_counts: dict[tuple[str, str, str, str], int] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("{"):
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    if "P_COMMAND_PARAM_LOCAL" in obj and isinstance(obj["P_COMMAND_PARAM_LOCAL"], str):
                        sig = (
                            str(obj.get("P_BATTLE_SCRIPT_INFO", "")),
                            str(obj.get("P_COND", "")),
                            str(obj.get("P_COND_PARAM", "")),
                            str(obj.get("P_COMMAND", "")),
                        )
                        occ = json_sig_counts.get(sig, 0)
                        json_sig_counts[sig] = occ + 1
                        entries[f"JSON:{'|'.join(sig)}#{occ}"] = obj["P_COMMAND_PARAM_LOCAL"]
                    else:
                        for k, v in obj.items():
                            if isinstance(v, str):
                                entries[f"JSON:{len(entries)}:{k}"] = v
                    continue
            except json.JSONDecodeError:
                pass
        if "^" in line:
            parts = line.split("^")
            if len(parts) >= 2 and parts[0]:
                entries[parts[0]] = parts[1]
    return entries


# ──────────────────────────────────────────────────────────────────────────────
# 主逻辑
# ──────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="统计 Beta 明文 .dat 表与正式版加密 .dat 表的繁中注入比例")
    ap.add_argument(
        "--en",
        default=str(ROOT_DIR / "source" / "offline" / "native" / "localized_texts" / "en"),
        help="官方纯净 en 目录",
    )
    ap.add_argument(
        "--work",
        default=str(ROOT_DIR / "work" / "localized_texts" / "en"),
        help="汉化输出 work 目录",
    )
    ap.add_argument(
        "--manifest-cache",
        default=str(ROOT_DIR / "source" / "apk" / "manifest_cache.json"),
        help="manifest_cache.json 路径（由步骤4生成），用于解密加密表以统计其覆盖率",
    )
    ap.add_argument(
        "--dump-missing",
        default="",
        help="将所有未匹配 KEY 导出到指定文本文件（如 missing_keys.txt）",
    )
    ap.add_argument(
        "--summary",
        action="store_true",
        help="仅显示各模块分类汇总，不打印逐文件明细",
    )
    args = ap.parse_args()

    en_map = get_latest_files(Path(args.en))
    wk_map = get_latest_files(Path(args.work))

    # ── 加载清单缓存（若存在）以支持表名映射与加密表统计 ────────────────────
    manifest = load_manifest_cache_from_paths(
        Path(args.manifest_cache),
        ROOT_DIR / "source" / "apk" / "manifest_cache.json",
        ROOT_DIR / "work" / "manifest_cache.json",
    )
    if manifest:
        print(f"[清单缓存] 已加载 {len(manifest)} 条表项，已启用官方表名映射与模块分类")
    else:
        print("[提示] 未找到 manifest_cache.json，将跳过加密表统计及表名映射（先执行步骤 4 生成缓存）")

    # 行格式: (filename, table_name, category, desc, is_encrypted, total, injected, unmatched, ratio)
    all_rows: list[tuple[str, str, str, str, bool, int, int, int, float]] = []
    enc_no_key    = 0
    missing_dump: list[str]  = []

    for tok, (_, en_file) in sorted(en_map.items()):
        en_raw = en_file.read_bytes()
        table_name, category, desc = resolve_table_meta(manifest, tok)

        # ── 明文表 ───────────────────────────────────────────────────────────
        if is_unencrypted_dat(en_raw):
            en_entries = parse_entries(en_raw.decode("utf-8"))
            total      = len(en_entries)
            if total == 0:
                continue

            wk_entries: dict[str, str] = {}
            if tok in wk_map:
                wk_raw = wk_map[tok][1].read_bytes()
                if is_unencrypted_dat(wk_raw):
                    wk_entries = parse_entries(wk_raw.decode("utf-8"))

            injected       = 0
            unmatched_keys = []
            for k, en_val in en_entries.items():
                wk_val = wk_entries.get(k)
                if wk_val is not None and wk_val != en_val:
                    injected += 1
                else:
                    unmatched_keys.append((k, en_val))

            unmatched = total - injected
            ratio     = (injected / total) * 100
            all_rows.append((en_file.name, table_name, category, desc, False, total, injected, unmatched, ratio))

            if args.dump_missing and unmatched_keys:
                missing_dump.append(f"=== {en_file.name} [{table_name} - {desc}] (未匹配 {unmatched}/{total}) ===")
                for k, val in unmatched_keys:
                    preview = val.replace("\n", "\\n")[:80]
                    missing_dump.append(f"  {k} | {preview}")
                missing_dump.append("")
            continue

        # ── 加密表 ───────────────────────────────────────────────────────────
        if not manifest:
            continue
        entry = manifest.get(tok)
        if not entry:
            enc_no_key += 1
            continue

        key = key16(entry["key"])

        _, en_plains = decrypt_dat_lines(en_raw, key)
        if en_plains is None:
            enc_no_key += 1
            continue

        en_entries = parse_entries("\n".join(en_plains))
        total      = len(en_entries)
        if total == 0:
            continue

        wk_entries = {}
        if tok in wk_map:
            wk_raw = wk_map[tok][1].read_bytes()
            if not is_unencrypted_dat(wk_raw):
                _, wk_plains = decrypt_dat_lines(wk_raw, key)
                if wk_plains:
                    wk_entries = parse_entries("\n".join(wk_plains))
            else:
                wk_entries = parse_entries(wk_raw.decode("utf-8"))

        injected = 0
        for k, en_val in en_entries.items():
            wk_val = wk_entries.get(k)
            if wk_val is not None and wk_val != en_val:
                injected += 1

        unmatched = total - injected
        ratio     = (injected / total) * 100
        all_rows.append((en_file.name, table_name, category, desc, True, total, injected, unmatched, ratio))

    if not all_rows:
        print("\n未统计到任何有效文本表。")
        return

    # ── 按模块分类聚合 ───────────────────────────────────────────────────────
    by_cat: dict[str, list[tuple[str, str, str, str, bool, int, int, int, float]]] = defaultdict(list)
    for row in all_rows:
        by_cat[row[2]].append(row)

    ordered_cats = [c for c in CATEGORY_ORDER if c in by_cat]
    for c in sorted(by_cat):
        if c not in ordered_cats:
            ordered_cats.append(c)

    # ── 1. 模块分类汇总总览（数字列放左，避免字体全/半角比例影响对齐）────────
    sep = "─" * 90
    print(f"\n{sep}")
    print(f"  模块分类覆盖率汇总  （共 {len(all_rows)} 张表，{len(ordered_cats)} 个分类）")
    print(sep)
    print(
        f"{_pad_disp('编号', 6)} "
        f"{_rpad_disp('表数', 6)} "
        f"{_rpad_disp('总KEY', 9)} "
        f"{_rpad_disp('已注入', 9)} "
        f"{_rpad_disp('未匹配', 9)} "
        f"{_rpad_disp('注入比例', 10)}  "
        f"模块分类"
    )
    print(sep)

    grand_files = grand_total = grand_inj = grand_unm = 0
    for idx, cat in enumerate(ordered_cats, 1):
        rows = by_cat[cat]
        ct = sum(r[5] for r in rows)
        ci = sum(r[6] for r in rows)
        cu = sum(r[7] for r in rows)
        cr = (ci / ct * 100) if ct else 0.0
        grand_files += len(rows)
        grand_total += ct
        grand_inj   += ci
        grand_unm   += cu
        print(
            f"[{idx:02d}]   "
            f"{len(rows):>6} "
            f"{ct:>9,} "
            f"{ci:>9,} "
            f"{cu:>9,} "
            f"{cr:>9.2f}%  "
            f"{cat}"
        )

    print(sep)
    grand_ratio = (grand_inj / grand_total * 100) if grand_total else 0.0
    print(
        f"[ALL]  "
        f"{grand_files:>6} "
        f"{grand_total:>9,} "
        f"{grand_inj:>9,} "
        f"{grand_unm:>9,} "
        f"{grand_ratio:>9.2f}%  "
        f"全局合计"
    )

    # ── 2. 按分类输出各文件功能与覆盖率明细（文件名/数字放左，中文说明放最右）──
    if not args.summary:
        detail_w = 100
        d_sep = "─" * detail_w
        for idx, cat in enumerate(ordered_cats, 1):
            rows = sorted(by_cat[cat], key=lambda r: (r[8], -r[7], r[0]))
            ct = sum(r[5] for r in rows)
            ci = sum(r[6] for r in rows)
            cu = sum(r[7] for r in rows)
            cr = (ci / ct * 100) if ct else 0.0

            print(f"\n{d_sep}")
            print(f"  [{idx:02d}] {cat}  （{len(rows)} 张表 | 覆盖率 {cr:.2f}%）")
            print(d_sep)
            print(
                f"{_pad_disp('文件名', 20)} "
                f"{_rpad_disp('总KEY', 9)} "
                f"{_rpad_disp('已注入', 9)} "
                f"{_rpad_disp('未匹配', 9)} "
                f"{_rpad_disp('注入比例', 10)}  "
                f"功能说明 [官方表名]"
            )
            print(d_sep)
            for name, table_name, _c, desc, is_enc, total, inj, unm, ratio in rows:
                enc_tag = " (加密)" if is_enc else ""
                func_label = f"{desc} [{table_name}]{enc_tag}" if table_name != "-" else desc
                print(
                    f"{name:<20} "
                    f"{total:>9,} "
                    f"{inj:>9,} "
                    f"{unm:>9,} "
                    f"{ratio:>9.2f}%  "
                    f"{func_label}"
                )
            print(d_sep)
            print(
                f"{'[SUBTOTAL]':<20} "
                f"{ct:>9,} "
                f"{ci:>9,} "
                f"{cu:>9,} "
                f"{cr:>9.2f}%  "
                f"分类小计：共 {len(rows)} 张表"
            )

    if enc_no_key and manifest:
        print(f"\n[提示] {enc_no_key} 张加密表无法匹配清单 key，已跳过")

    if grand_total:
        print(f"\n{'═' * 90}")
        print(f"全局合计: {grand_total:,} KEY | 已注入: {grand_inj:,} | 覆盖率: {grand_ratio:.2f}%")

    if args.dump_missing:
        out_path = Path(args.dump_missing)
        out_path.write_text("\n".join(missing_dump), encoding="utf-8")
        print(f"\n已导出未匹配 KEY 明细至: {out_path.resolve()}")


if __name__ == "__main__":
    main()
