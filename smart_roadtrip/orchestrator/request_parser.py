"""行程需求解析。

优先使用 OpenAI 兼容 LLM（配置了 ``LLM_API_KEY`` 与 ``LLM_MODEL`` 时）；
未配置或调用失败时，回退到确定性规则解析，保证无密钥也能使用。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from smart_roadtrip import config
from smart_roadtrip.schemas.trip import (
    EnergyType,
    TravelPace,
    TravelPreferences,
    Traveler,
    TripRequest,
    VehicleInfo,
)

logger = logging.getLogger(__name__)

_CN_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_ORIGIN_PATTERNS = (
    re.compile(r"从\s*([^\s，,。；;、:：]{1,15}?)\s*(?:出发|启程|到|去|前往)"),
    re.compile(r"出发地\s*[:：]?\s*([^\s，,。；;、]{1,15})"),
    re.compile(r"(?:^|[，,。；;、\s])([^\s，,。；;、]{2,15}?)\s*出发"),
)

_DESTINATION_PATTERNS = (
    re.compile(r"目的地\s*[:：]?\s*([^\s，,。；;、]{1,20})"),
    re.compile(
        r"(?:到|去|前往|抵达)\s*(?!\d)([^\s，,。；;、]{2,20}?)"
        r"(?=玩|游玩|旅游|旅行|自驾|住|呆|待|吃|看|，|,|。|；|;|\s"
        r"|(?:[0-9]+|[一二两三四五六七八九十]+)\s*(?:天|日)|$)"
    ),
)

_INTEREST_KEYWORDS = {
    "雪山": ("雪山", "雪景", "冰川"),
    "摄影": ("摄影", "拍照", "出片"),
    "美食": ("美食", "小吃"),
    "亲子": ("亲子", "孩子", "带娃"),
    "露营": ("露营",),
    "徒步": ("徒步", "爬山"),
    "古镇": ("古镇", "古城"),
    "海边": ("海边", "海岛", "看海"),
    "温泉": ("温泉",),
    "博物馆": ("博物馆", "展览"),
    "历史人文": ("历史", "人文", "古迹"),
    "自然风光": ("自然", "风光", "风景"),
    "滑雪": ("滑雪",),
    "星空": ("星空", "观星"),
}

_MISSING_PLACES = {"", "未提及", "未知", "未指定", "无", "待定"}


class RequestParseError(ValueError):
    """用户需求无法解析成可执行的 TripRequest。"""


def is_llm_configured() -> bool:
    """读取运行时配置，便于测试通过 monkeypatch 切换。"""

    return bool(config.LLM_API_KEY and config.LLM_MODEL)


def parse_request_text(text: str, *, today: date | None = None) -> TripRequest:
    """把自然语言解析为 TripRequest。

    LLM 路径失败不会阻断流程：记录警告后使用规则解析；规则解析仍失败时
    抛出带中文提示的 :class:`RequestParseError`。
    """

    text = (text or "").strip()
    if not text:
        raise RequestParseError("请输入行程需求。")

    today = today or date.today()
    if is_llm_configured():
        try:
            return _parse_with_llm(text, today)
        except Exception as exc:  # noqa: BLE001 - LLM 是可选增强，失败必须降级
            logger.warning(
                "LLM 需求解析失败（%s），改用规则解析。", type(exc).__name__
            )

    try:
        return _parse_with_rules(text, today)
    except RequestParseError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析边界统一转成用户可读错误
        raise RequestParseError(f"需求解析失败：{exc}") from exc


# ---------------------------------------------------------------------------
# LLM 解析
# ---------------------------------------------------------------------------


def _parse_with_llm(text: str, today: date) -> TripRequest:
    kwargs: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "api_key": config.LLM_API_KEY,
        "temperature": 0,
        "timeout": 20,
        "max_retries": 0,
    }
    if config.LLM_BASE_URL:
        kwargs["base_url"] = config.LLM_BASE_URL

    client = ChatOpenAI(**kwargs)
    schema = json.dumps(TripRequest.model_json_schema(), ensure_ascii=False)
    system = (
        "你是自驾游行程需求解析器。把用户的中文需求转换成 TripRequest JSON。\n"
        "只输出一个 JSON 对象，不要 Markdown 代码块，不要解释。\n"
        f"今天是 {today.isoformat()}。\n"
        "要求：\n"
        "- origin / destination 必须是用户明确提到的地名；没有提到就留空字符串。\n"
        "- days 为 1-30 的整数；start_date 使用 YYYY-MM-DD；未提到日期时用今天+7 天。\n"
        "- travelers 至少 1 人，每项包含 name、is_driver、preferences。\n"
        "- vehicle.energy_type 只能是 ev / phev / ice；current_soc 为 0-1。\n"
        "- preferences.pace 只能是 relaxed / balanced / intensive。\n"
        "- 不要输出 schema 之外的字段。\n"
        f"JSON Schema：{schema}"
    )
    response = client.invoke(
        [SystemMessage(content=system), HumanMessage(content=f"用户需求：{text}")]
    )

    payload = _extract_json(_content_to_text(response.content))
    payload = {key: value for key, value in payload.items() if key in TripRequest.model_fields}
    payload = _normalize_llm_payload(payload, today)

    if _is_missing_place(payload.get("origin")) or _is_missing_place(payload.get("destination")):
        raise RequestParseError("请补充出发地和目的地。")

    try:
        return TripRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestParseError(f"LLM 返回的行程格式不正确：{exc}") from exc


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("LLM 响应中没有 JSON 对象")

    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM 响应的 JSON 顶层不是对象")
    return value


def _is_missing_place(value: Any) -> bool:
    return not isinstance(value, str) or value.strip() in _MISSING_PLACES


def _normalize_llm_payload(payload: dict[str, Any], today: date) -> dict[str, Any]:
    """容忍 LLM 常见的字段形态偏差，减少无谓降级。"""

    for key in ("origin", "destination"):
        value = payload.get(key)
        if isinstance(value, dict):
            payload[key] = value.get("name") or value.get("city") or ""

    days = payload.get("days")
    if isinstance(days, str):
        match = re.search(r"\d+", days)
        if match:
            payload["days"] = int(match.group(0))
        else:
            match = re.search(r"[一二两三四五六七八九十]+", days)
            if match:
                value = _cn_to_int(match.group(0))
                if value:
                    payload["days"] = value
    payload.setdefault("days", 3)

    start_date = payload.get("start_date")
    if isinstance(start_date, str) and not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", start_date.strip()
    ):
        payload["start_date"] = _extract_start_date(start_date, today).isoformat()
    payload.setdefault("start_date", (today + timedelta(days=7)).isoformat())

    travelers = payload.get("travelers")
    if isinstance(travelers, int) and travelers > 0:
        payload["travelers"] = [
            {"name": f"同行人{index + 1}", "is_driver": index == 0}
            for index in range(travelers)
        ]
    elif isinstance(travelers, dict):
        payload["travelers"] = [
            {
                **travelers,
                "name": travelers.get("name", "同行人1"),
                "is_driver": travelers.get("is_driver", True),
            }
        ]
    elif isinstance(travelers, list):
        normalized: list[dict[str, Any]] = []
        for index, item in enumerate(travelers):
            if isinstance(item, str):
                normalized.append({"name": item, "is_driver": index == 0})
            elif isinstance(item, dict):
                item = {**item}
                item.setdefault("name", f"同行人{index + 1}")
                item.setdefault("is_driver", index == 0)
                preferences = item.get("preferences")
                if isinstance(preferences, str):
                    item["preferences"] = [
                        value.strip()
                        for value in re.split(r"[，,、]", preferences)
                        if value.strip()
                    ]
                normalized.append(item)
        payload["travelers"] = normalized
    if not payload.get("travelers"):
        payload["travelers"] = [{"name": "我", "is_driver": True}]

    preferences = payload.get("preferences")
    if isinstance(preferences, dict):
        interests = preferences.get("interests")
        if isinstance(interests, str):
            preferences["interests"] = [
                value.strip()
                for value in re.split(r"[，,、]", interests)
                if value.strip()
            ]
        avoid = preferences.get("avoid")
        if isinstance(avoid, str):
            preferences["avoid"] = [
                value.strip()
                for value in re.split(r"[，,、]", avoid)
                if value.strip()
            ]
        pace = preferences.get("pace")
        if isinstance(pace, str):
            preferences["pace"] = {
                "轻松": "relaxed",
                "休闲": "relaxed",
                "relaxed": "relaxed",
                "平衡": "balanced",
                "balanced": "balanced",
                "紧凑": "intensive",
                "赶": "intensive",
                "intensive": "intensive",
            }.get(pace.strip().lower(), pace)

    vehicle = payload.get("vehicle")
    energy_map = {
        "电车": "ev",
        "纯电": "ev",
        "电动车": "ev",
        "新能源": "ev",
        "ev": "ev",
        "插混": "phev",
        "插电混动": "phev",
        "混动": "phev",
        "phev": "phev",
        "油车": "ice",
        "燃油": "ice",
        "汽油": "ice",
        "ice": "ice",
    }
    if isinstance(vehicle, str):
        payload["vehicle"] = {
            "energy_type": energy_map.get(vehicle.strip().lower(), "ice")
        }
    elif isinstance(vehicle, dict):
        energy = vehicle.get("energy_type")
        if isinstance(energy, str):
            vehicle["energy_type"] = energy_map.get(energy.strip().lower(), energy)
        for key in ("current_soc", "rated_range_km", "seats"):
            value = vehicle.get(key)
            if isinstance(value, str):
                match = re.search(r"\d+(?:\.\d+)?", value)
                if match:
                    number = float(match.group(0))
                    vehicle[key] = int(number) if key == "seats" else number

    budget = payload.get("budget_cny")
    if isinstance(budget, str):
        match = re.search(r"\d+(?:\.\d+)?", budget)
        if match:
            payload["budget_cny"] = float(match.group(0))

    return payload


# ---------------------------------------------------------------------------
# 规则解析
# ---------------------------------------------------------------------------


def _parse_with_rules(text: str, today: date) -> TripRequest:
    origin = _first_match(text, _ORIGIN_PATTERNS)
    destination = _first_match(text, _DESTINATION_PATTERNS)

    if not origin:
        raise RequestParseError(
            "未识别到出发地，请说明从哪里出发，例如：从成都到稻城亚丁玩4天。"
        )
    if not destination:
        raise RequestParseError(
            "未识别到目的地，请说明想去哪里，例如：从成都到稻城亚丁玩4天。"
        )

    origin = _clean_place(origin)
    destination = _clean_place(destination)
    interests = _extract_interests(text)
    traveler_count = _extract_travelers(text)

    travelers = [
        Traveler(
            name="我" if traveler_count == 1 and "我" in text else f"同行人{index + 1}",
            is_driver=(index == 0),
            preferences=interests,
        )
        for index in range(traveler_count)
    ]

    try:
        return TripRequest(
            origin=origin,
            destination=destination,
            days=_extract_days(text),
            start_date=_extract_start_date(text, today),
            travelers=travelers,
            vehicle=_extract_vehicle(text),
            preferences=TravelPreferences(
                pace=_extract_pace(text),
                interests=interests,
                avoid=_extract_avoid(text),
            ),
            budget_cny=_extract_budget(text),
            notes=text,
        )
    except ValidationError as exc:
        logger.warning("规则解析结果校验失败：%s", exc)
        raise RequestParseError(
            "解析出的行程信息不完整或不合法，请补充出发地、目的地与天数。"
        ) from exc


def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def _clean_place(value: str) -> str:
    value = value.strip(" ，,。；;、:：")
    for suffix in ("出发", "启程"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    return value.strip()


def _cn_to_int(value: str) -> int | None:
    if not value:
        return None
    if "十" in value:
        left, _, right = value.partition("十")
        tens = _CN_DIGITS.get(left, 1) if left else 1
        units = _CN_DIGITS.get(right, 0) if right else 0
        return tens * 10 + units

    total = 0
    for char in value:
        digit = _CN_DIGITS.get(char)
        if digit is None:
            return None
        total = total * 10 + digit
    return total


def _extract_days(text: str) -> int:
    match = re.search(r"(\d+)\s*(?:天|日)", text)
    if match:
        return int(match.group(1))

    match = re.search(r"([一二两三四五六七八九十]+)\s*(?:天|日)", text)
    if match:
        value = _cn_to_int(match.group(1))
        if value:
            return value

    if re.search(r"(?:一|1)\s*(?:周|星期)", text):
        return 7
    if re.search(r"(?:两|二|2)\s*(?:周|星期)", text):
        return 14
    return 3


def _extract_start_date(text: str, today: date) -> date:
    match = re.search(
        r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?", text
    )
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            pass

    match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日?", text)
    if match:
        try:
            candidate = date(today.year, int(match.group(1)), int(match.group(2)))
            if candidate < today:
                candidate = date(
                    today.year + 1, int(match.group(1)), int(match.group(2))
                )
            return candidate
        except ValueError:
            pass

    for keyword, offset in (("今天", 0), ("明天", 1), ("后天", 2), ("大后天", 3)):
        if keyword in text:
            return today + timedelta(days=offset)

    return today + timedelta(days=7)


def _extract_travelers(text: str) -> int:
    match = re.search(r"(\d+)\s*(?:人|个人|位)", text)
    if match:
        return max(1, int(match.group(1)))

    match = re.search(r"([一二两三四五六七八九十]+)\s*(?:人|个人|位)", text)
    if match:
        value = _cn_to_int(match.group(1))
        if value:
            return max(1, value)

    for pattern, count in (
        (r"一家三口", 3),
        (r"一家四口", 4),
        (r"一家五口", 5),
    ):
        if re.search(pattern, text):
            return count

    count = 1
    if any(keyword in text for keyword in ("我们", "我俩", "夫妻", "情侣", "俩人", "两人")):
        count = 2
    if any(keyword in text for keyword in ("孩子", "娃", "亲子")):
        count = max(count + 1, 2)
    if any(keyword in text for keyword in ("老人", "父母", "爸妈")):
        count += 1
    return count


def _extract_budget(text: str) -> float | None:
    match = re.search(r"预算\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(万|元|块)?", text)
    if match is None:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(万|元|块)", text)
    if match is None:
        return None

    value = float(match.group(1))
    if (match.group(2) or "") == "万":
        value *= 10000
    return value


def _extract_vehicle(text: str) -> VehicleInfo:
    lowered = text.lower()
    if any(keyword in text for keyword in ("电车", "纯电", "电动车", "新能源")) or "ev" in lowered:
        energy_type = EnergyType.EV
    elif any(keyword in text for keyword in ("插混", "插电混动", "混动")) or "phev" in lowered:
        energy_type = EnergyType.PHEV
    else:
        energy_type = EnergyType.ICE

    range_km = 500.0
    match = re.search(r"续航\s*(\d+)\s*(?:公里|km|KM)", text)
    if match:
        range_km = float(match.group(1))

    current_soc = 0.8 if energy_type in (EnergyType.EV, EnergyType.PHEV) else 1.0
    match = re.search(r"(?:电量|soc)\s*(\d+(?:\.\d+)?)\s*%?", text, flags=re.IGNORECASE)
    if match:
        value = float(match.group(1))
        current_soc = value / 100 if value > 1 else value

    seats = 5
    match = re.search(r"(\d+)\s*座", text)
    if match:
        seats = max(1, int(match.group(1)))

    return VehicleInfo(
        energy_type=energy_type,
        rated_range_km=range_km,
        current_soc=current_soc,
        seats=seats,
    )


def _extract_pace(text: str) -> TravelPace:
    if any(keyword in text for keyword in ("轻松", "休闲", "慢", "不赶", "养老")):
        return TravelPace.RELAXED
    if any(keyword in text for keyword in ("紧凑", "赶", "特种兵", "排满")):
        return TravelPace.INTENSIVE
    return TravelPace.BALANCED


def _extract_interests(text: str) -> list[str]:
    interests: list[str] = []
    for label, keywords in _INTEREST_KEYWORDS.items():
        if any(keyword in text for keyword in keywords) and label not in interests:
            interests.append(label)
    return interests


def _extract_avoid(text: str) -> list[str]:
    avoid: list[str] = []
    for match in re.finditer(
        r"(?:避开|不去|不要|不想去)\s*([^\s，,。；;、]{1,10})", text
    ):
        value = match.group(1)
        if value not in avoid:
            avoid.append(value)
    return avoid
