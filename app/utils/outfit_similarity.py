# Copyright (c) 2026 Alex Wang
# @author Alex Wang <https://github.com/wanglongxiao>
# @contact https://www.linkedin.com/in/alexwanglx/
# Open Source Usage: attribution required; preserve this notice in redistributions.
import re
from difflib import SequenceMatcher
from typing import FrozenSet, Iterable, List, Tuple


_CANONICAL_REPLACEMENTS = (
    (r"被?雨水(?:完全|彻底)?打湿|湿漉漉|浸湿|湿透", "湿"),
    (r"略显凌乱|有些凌乱|稍显凌乱|微乱|散乱", "凌乱"),
    (r"血迹斑斑|沾满(?:了)?血迹?|染(?:满)?鲜血|满身血污", "血污"),
    (r"撕裂|裂开|破了一道口子|出现裂口", "破损"),
    (r"干净整洁|洁净整齐", "整洁"),
)

_MINOR_WORDS = (
    "略微", "微微", "稍微", "略有", "有些", "些许", "轻微", "略显",
    "较为", "比较", "非常", "十分", "依然", "仍然", "继续", "保持",
    "当前", "此时", "看起来", "显得",
)

_STABLE_VISUAL_GROUPS = (
    ("color:black", ("黑色", "玄色", "墨色", "乌黑")),
    ("color:white", ("白色", "雪白", "乳白")),
    ("color:gray", ("灰色", "银灰")),
    ("color:red", ("红色", "暗红", "深红", "朱红")),
    ("color:pink", ("粉色",)),
    ("color:orange", ("橙色",)),
    ("color:yellow", ("黄色",)),
    ("color:green", ("绿色",)),
    ("color:blue", ("蓝色", "藏蓝", "深蓝")),
    ("color:purple", ("紫色",)),
    ("color:brown", ("棕色", "褐色")),
    ("color:gold", ("金色",)),
    ("color:silver", ("银色",)),
    ("color:beige", ("米色", "卡其色")),
    ("garment:close_fitting", ("劲装",)),
    ("garment:cape", ("披风", "斗篷")),
    ("garment:trench_coat", ("风衣",)),
    ("garment:suit", ("西装",)),
    ("garment:shirt", ("衬衫",)),
    ("garment:tshirt", ("t恤", "T恤")),
    ("garment:dress", ("连衣裙",)),
    ("garment:short_skirt", ("短裙",)),
    ("garment:long_skirt", ("长裙",)),
    ("garment:gown", ("礼服",)),
    ("garment:uniform", ("制服", "校服")),
    ("garment:armor", ("盔甲", "甲胄", "铠甲")),
    ("garment:pajamas", ("睡衣",)),
    ("garment:underwear", ("内衣", "内裤")),
    ("garment:swimwear", ("泳装", "泳衣")),
    ("garment:coat", ("外套",)),
    ("garment:jacket", ("夹克",)),
    ("garment:sweater", ("毛衣", "针织衫")),
    ("garment:jeans", ("牛仔裤",)),
    ("garment:trousers", ("西裤", "长裤")),
    ("garment:shorts", ("短裤",)),
    ("garment:robe", ("长袍", "短袍", "道袍")),
    ("garment:boots", ("长靴", "短靴", "皮靴")),
    ("cut:narrow_sleeve", ("窄袖",)),
    ("cut:long_sleeve", ("长袖",)),
    ("cut:short_sleeve", ("短袖",)),
    ("cut:sleeveless", ("无袖",)),
    ("cut:short_cape", ("短披风",)),
    ("cut:long_cape", ("长披风",)),
    ("hair:high_ponytail", ("高马尾", "高束成马尾", "高束马尾")),
    ("hair:low_ponytail", ("低马尾",)),
    ("hair:ponytail", ("马尾",)),
    ("hair:short", ("短发",)),
    ("hair:long", ("长发",)),
    ("hair:medium", ("中长发",)),
    ("hair:bun", ("丸子头", "发髻")),
    ("hair:braid", ("麻花辫", "双辫", "脏辫", "小辫")),
    ("hair:afro", ("爆炸头",)),
    ("hair:buzz", ("寸头",)),
    ("hair:slicked", ("背头",)),
    ("hair:bald", ("光头",)),
    ("hair:bangs", ("齐刘海",)),
    ("hair:middle_part", ("中分",)),
    ("hair:side_part", ("三七分",)),
    ("hair:wavy", ("波浪烫发", "短烫发")),
    ("exposure:full_nude", ("全裸", "一丝不挂", "赤身裸体", "全身赤裸")),
    ("exposure:half_nude", ("半裸",)),
    ("exposure:topless", ("上身全裸", "上身赤裸", "赤裸上身")),
    ("exposure:bottomless", ("下身赤裸", "赤裸下身")),
    ("exposure:front_nude", ("正面裸体",)),
    ("exposure:transparent", ("透视装",)),
    ("exposure:disheveled", ("衣衫不整",)),
    # 身体伤痕——持久、固定的疤痕/烙印需要作为独立造型变体
    ("injury:scar", ("疤痕", "伤疤", "烙印", "刻印")),
    ("injury:eye_patch", ("眼罩",)),
    ("injury:bandage", ("绷带缠绕", "绷带包扎")),
    ("injury:missing_limb", ("独臂", "断臂", "独腿", "断腿")),
)

_TRANSIENT_STATE_KEYWORDS = (
    "血", "妖血", "血污", "血迹", "渗血", "伤口", "受伤", "抓痕",
    "沾满", "沾上", "染上", "淋满", "凝结", "被血",
    "撕开", "撕裂", "裂口", "破损", "战损", "湿", "雨水", "汗水",
    "泥污", "污渍", "尘土", "灰尘", "烧焦", "褶皱", "皱褶",
    "凌乱", "散乱", "微乱", "稍乱", "蓬乱", "黏成", "凝结",
    "紧贴身体", "血管状纹路",
)


def normalize_outfit_for_similarity(value: object) -> str:
    """Normalize wording while preserving visually meaningful outfit differences."""
    text = str(value or "").strip().lower()
    for word in _MINOR_WORDS:
        text = text.replace(word, "")
    for pattern, replacement in _CANONICAL_REPLACEMENTS:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?:衣角|衣领|袖口|裙摆)?(?:有)?(?:少量)?(?:褶皱|皱褶)", "", text)
    text = re.sub(r"[\s,，、/|；;:：。.!！?？（）()【】\[\]\"'“”‘’\-]+", "", text)
    return text


def _matching_groups(text: str, groups: Iterable[Tuple[str, Tuple[str, ...]]]) -> FrozenSet[str]:
    return frozenset(
        label
        for label, variants in groups
        if any(variant.lower() in text for variant in variants)
    )


def _stable_description_parts(value: object) -> List[str]:
    stable_parts: List[str] = []
    for part in re.split(r"[,，、；;。]+", str(value or "")):
        original_part = part.strip()
        normalized_part = original_part.lower()
        if not original_part:
            continue
        transient_positions = [
            normalized_part.find(keyword)
            for keyword in _TRANSIENT_STATE_KEYWORDS
            if keyword in normalized_part
        ]
        if transient_positions:
            original_part = original_part[:min(transient_positions)].strip()
        original_part = re.sub(r"(?:被|已|正|又|仍|依然|开始|变得)$", "", original_part).strip()
        if transient_positions and original_part:
            prefix_signature = _matching_groups(original_part.lower(), _STABLE_VISUAL_GROUPS)
            strong_prefix_markers = {
                marker
                for marker in prefix_signature
                if marker.startswith(("garment:", "cut:", "exposure:"))
                or (marker.startswith("hair:") and marker != "hair:ponytail")
            }
            if not strong_prefix_markers:
                original_part = ""
        if original_part:
            stable_parts.append(original_part)
    return stable_parts


def stable_outfit_description(value: object) -> str:
    """Return only reusable wardrobe, hairstyle, and exposure information."""
    original = re.sub(r"\s+", " ", str(value or "").strip())
    if not original:
        return ""
    stable_parts = _stable_description_parts(original)
    return "，".join(stable_parts) or original


def outfit_identity_signature(value: object) -> FrozenSet[str]:
    """Extract stable wardrobe, haircut, and exposure traits from an outfit description."""
    stable_parts = _stable_description_parts(value)
    text = "，".join(stable_parts) or str(value or "").strip().lower()
    return _matching_groups(text, _STABLE_VISUAL_GROUPS)


def _stable_description_core(value: object) -> str:
    return normalize_outfit_for_similarity("，".join(_stable_description_parts(value)))


_IDENTITY_PREFIXES = ("color:", "garment:", "cut:", "exposure:", "hair:", "injury:")


def _identity_markers(signature: FrozenSet[str]) -> FrozenSet[str]:
    return frozenset(marker for marker in signature if marker.startswith(_IDENTITY_PREFIXES))


def are_outfits_visually_equivalent(first: object, second: object) -> bool:
    """两个造型只有在服饰/裸露/发型/身体伤痕 的识别标记完全一致时视为同一变体。

    其他细微文字差异（褶皱、湿、血污、情绪等）不会触发新造型。
    """
    first_signature = _identity_markers(outfit_identity_signature(first))
    second_signature = _identity_markers(outfit_identity_signature(second))

    # 两边都没有抓取到任何稳定识别标记时，退化为文本等价判断
    if not first_signature and not second_signature:
        first_text = normalize_outfit_for_similarity(first)
        second_text = normalize_outfit_for_similarity(second)
        if not first_text or not second_text:
            return False
        return first_text == second_text

    return first_signature == second_signature
