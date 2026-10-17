"""유료 API 대신 쓰는 가짜 AI 클라이언트와 시험용 상품.

프롬프트 안의 표식으로 어느 단계의 호출인지 알아내 미리 정해 둔 응답을 돌려준다.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from ai.client import AIError, _parse_json
from core.models import Product

MAIN = "진공 쌀통 추천"
SECRET = "sk-TEST-SECRET-0123456789"

BODY = (
    "진공 쌀통 라이스킵 10kg. 버튼 한 번으로 내부 공기를 빼 진공 상태를 유지합니다. "
    "투명 창으로 남은 쌀의 양을 확인할 수 있습니다. 계량컵 1개가 함께 들어 있습니다. "
    "본체는 BPA free PP 소재입니다. 뚜껑 패킹은 분리해서 물로 씻을 수 있습니다. "
    "USB-C 충전식이며 완충 시 약 30회 사용할 수 있습니다. "
) * 2


def make_product(**overrides) -> Product:
    data = dict(
        url="https://naver.me/test-buy",
        page_url="https://smartstore.naver.com/test/products/1",
        title="라이스킵 진공 쌀통 10kg",
        price="29,900원",
        brand="라이스킵",
        body_text=BODY,
        specs={"용량": "10kg", "재질": "PP"},
    )
    data.update(overrides)
    return Product(**data)


# ------------------------------------------------------------------ 응답

BRIEF = {
    "category": "쌀통",
    "one_liner": "버튼 한 번으로 진공을 만드는 10kg 쌀통",
    "features": [
        {"name": "진공 버튼", "detail": "버튼 한 번으로 공기를 뺀다", "benefit": "쌀 보관이 편하다"},
        {"name": "투명 창", "detail": "남은 양이 보인다", "benefit": "장 볼 때를 알기 쉽다"},
    ],
    "personas": [{"who": "여름마다 쌀벌레가 걱정인 맞벌이 부부", "pain": "쌀이 금방 상한다", "why_fits": "밀폐 보관"}],
    "scenarios": [{"when": "장마철", "situation": "습한 주방", "outcome": "쌀을 밀폐해 둔다"}],
    "concerns": [{"worry": "충전이 번거로울까", "answer": "USB-C 충전식"}],
    "pros": ["진공 보관", "투명 창"],
    "cons": ["충전이 필요하다"],
    "recommended_for": ["쌀을 오래 두고 먹는 집"],
    "not_recommended_for": ["쌀을 소량만 사는 1인 가구"],
}

FOCUS = {"focus_points": [{"title": "진공 버튼", "angle": "버튼 한 번", "evidence": "버튼 한 번으로 내부 공기를 빼",
                           "target": "맞벌이", "why_now": "여름", "keywords": ["진공 쌀통"], "risk": "충전"}]}

SEO = {
    "main_keyword": MAIN,
    "sub_keywords": ["쌀벌레 방지", "10kg 쌀통", "진공 버튼"],
    "lsi_keywords": ["쌀 보관", "밀폐"],
    "search_intent": "여름 쌀 보관 방법",
    "search_type": "구매형",
    "competition": "중간",
    "h1": "진공 쌀통 추천",
    "h2s": ["쌀벌레 방지가 필요한 이유", "진공 버튼 특징"],
    "h3s": {},
    "title_candidates": ["진공 쌀통 추천"],
    "meta_description": "SEO 기본 요약",
}

FACTS = {
    "name": "라이스킵 진공 쌀통",
    "category": "쌀통",
    "summary": "버튼으로 진공을 만드는 10kg 쌀통",
    "facts": [
        {"text": "버튼 한 번으로 내부 공기를 빼 진공 상태를 유지한다", "quote": "버튼 한 번으로 내부 공기를 빼 진공 상태를 유지합니다", "source": "text"},
        {"text": "투명 창으로 남은 양을 확인할 수 있다", "quote": "투명 창으로 남은 쌀의 양을 확인할 수 있습니다", "source": "text"},
        {"text": "뚜껑 패킹은 분리 세척 가능", "quote": "뚜껑 패킹은 분리해서 물로 씻을 수 있습니다", "source": "text"},
        {"text": "식약처 인증을 받은 세계 최초 진공 쌀통", "quote": "세계 최초 식약처 인증 획득", "source": "text"},
    ],
    "unknowns": ["소음 크기"],
    "cautions": ["쌀벌레를 100% 막는다는 주장"],
}

MESSAGE = {
    "customer": "여름마다 쌀벌레가 걱정인 맞벌이 부부",
    "situation": "습한 주방에 쌀을 오래 둔다",
    "search_intent": "여름에 쌀을 어떻게 보관할지",
    "message": "버튼 한 번 진공으로 여름 쌀 보관 걱정을 덜어 준다",
    "reasons": ["진공 버튼", "투명 창"],
    "fact_ids": ["F1", "F99"],
    "tone": "차분한 존댓말",
}

OUTLINE = {
    "sections": [
        {"id": "need", "heading": "쌀벌레 방지가 어려운 이유", "goal": "원인", "points": ["습기"], "fact_ids": ["F1"]},
        {"id": "features", "heading": "진공 버튼 핵심 특징", "goal": "특징", "points": ["버튼"], "fact_ids": ["F1", "F2"]},
        {"id": "bogus", "heading": "모르는 섹션"},
    ],
    "meta_description": "진공 쌀통 추천을 찾는 분께 버튼 한 번 진공 보관의 장점과 구매 전 확인할 점을 정리했습니다.",
    "hashtags": ["#진공 쌀통", "쌀통추천", "쌀통추천"],
}

SECTION_BODIES = {
    "intro": {"paragraphs": [f"여름만 되면 쌀 보관이 신경 쓰이시죠. {MAIN} 글을 찾고 계셨다면 끝까지 읽어 보세요.",
                             "습한 계절에는 작은 차이가 쌓여 큰 차이를 만들어요."]},
    "need": {"heading": "쌀벌레 방지가 어려운 이유", "paragraphs": ["뚜껑을 열고 닫을 때마다 공기가 들어가요.", "그래서 밀폐가 중요합니다."]},
    "features": {"heading": "진공 버튼 핵심 특징", "paragraphs": ["눈에 띄는 특징을 정리했어요.", "가장 손이 가는 건 진공 버튼이에요."],
                 "rows": [{"feature": "진공 버튼", "detail": "버튼 한 번으로 공기를 뺍니다", "benefit": "매번 꾹 눌러 닫지 않아도 돼요"},
                          {"feature": "투명 창", "detail": "남은 쌀이 보입니다", "benefit": "장 볼 때를 놓치지 않아요"}]},
    "scenes": {"heading": "이럴 때 쓰기 좋아요", "scenes": [{"heading": "장마철 저녁", "body": "비가 이어지는 주에 쌀을 넉넉히 사 두었다면 든든해요."},
                                                        {"heading": "주말 장보기", "body": "투명 창을 보고 다음 장보기 목록을 정하기 좋아요."}]},
    "tips": {"heading": "사용 방법과 활용 팁", "paragraphs": ["처음에는 이렇게 써 보세요."],
             "items": ["10kg 쌀을 붓고 버튼을 눌러요", "패킹은 분리해 물로 씻어요"]},
    "audience": {"heading": "이런 분께 추천해요", "paragraphs": ["이런 분께 잘 맞아요."],
                 "items": ["쌀을 한 번에 많이 사는 집", "주방이 습한 집"], "extra_items": ["쌀을 소량만 사는 분"]},
    "checks": {"heading": "구매 전에 확인하세요", "paragraphs": ["사기 전에 이것만 확인하세요."],
               "items": ["충전식이라 주기적으로 충전해야 해요", "놓을 자리의 높이를 재 보세요"]},
    "summary": {"heading": "한눈에 보기", "paragraphs": ["한 장으로 정리했어요."],
                "closing": "여름철 쌀 보관이 걱정이라면 진공 버튼 하나로 관리가 쉬워집니다. 다만 충전이 필요해요."},
    "cta": {"heading": "마무리", "paragraphs": ["구성과 옵션은 판매 페이지에서 확인해 보세요."],
            "closing": "자세한 구성은 https://example.com 판매 페이지에서 확인해 보세요", "items": ["가격 확인하기", "옵션 살펴보기"]},
}

TITLES = {"titles": [
    {"title": f"{MAIN}, 여름 쌀 보관 걱정 덜어 주는 진공 버튼", "reason": "고객 문제"},
    {"title": f"{MAIN} 장마철에 쓰기 좋은 이유", "reason": "장면"},
    {"title": f"{MAIN} 고르기 전 확인할 점", "reason": "기준"},
]}

CRITIC = {"readability": 17, "information": 16, "humanness": 16, "conversion": 15, "feedback": ["사례를 하나 더"]}

LEGACY_FRONT = {
    "title": f"{MAIN} 써본 후기", "hook": f"{MAIN}를 찾다가 만난 제품입니다.", "intro": "한 달 동안 직접 써보니 확실히 달랐어요.",
    "problem": "문제 문단", "problem_quote": "인용", "product_intro": "소개 문단", "feature_lead": "특징 도입",
    "feature_rows": [{"item": "진공", "detail": "버튼", "point": "편함"}], "feature_body": "특징 본문",
}
LEGACY_BACK = {
    "scenarios": [{"heading": "장마철", "body": "장마철 본문"}], "pros_lead": "장점", "pros_items": ["장점1"],
    "cons_lead": "단점", "cons_items": ["단점1"], "cons_balance": "그래도", "summary_lead": "요약",
    "verdict": "총평 문단", "mid_ctas": ["가격 보기", "옵션 보기"], "cta": "확인해 보세요", "tags": ["진공 쌀통", "쌀통"],
}


def sections_response(user: str) -> dict:
    ids = re.findall(r"^### (\w+) \(", user, re.M)
    return {"sections": {sid: SECTION_BODIES[sid] for sid in ids}}


def fix_response(user: str) -> dict:
    keys = re.findall(r"^\[([\w]+/[\w.]+)\]", user.split("[고칠 곳]", 1)[-1], re.M)
    return {"changes": {k: "확인된 사실 안에서 다시 쓴 문장이에요." for k in keys}}


DEFAULTS = {
    "analyst": BRIEF,
    "focus": FOCUS,
    "seo": SEO,
    "facts": FACTS,
    "message": MESSAGE,
    "outline": OUTLINE,
    "sections": sections_response,
    "unify": {"changes": {"need/p1": "그래서 무엇보다 밀폐가 중요합니다.", "intro/p0": "키워드를 지운 문장", "bogus/p0": "x"}},
    "review": {"issues": [{"key": "checks/i1", "type": "flow", "reason": "앞 문장과 이어지지 않음", "suggestion": "연결"},
                          {"key": "nope/p0", "type": "fact", "reason": "없는 키"}]},
    "fix": fix_response,
    "titles": TITLES,
    "critic": CRITIC,
    "legacy_front": LEGACY_FRONT,
    "legacy_back": LEGACY_BACK,
    "humanize": {},
    "revise": {},
}

_LEGACY_MARKERS = (
    ("지금은 글의 앞부분만", "legacy_front"),
    ("지금은 글의 뒷부분만", "legacy_back"),
    ("냉정하게 평가", "critic"),
    ("기계적으로 읽히는", "humanize"),
    ("품질 기준에 미달", "revise"),
    ("집필용 설계도", "analyst"),
    ("집중해서 밀 만한", "focus"),
    ("키워드 전략", "seo"),
)


class Script(list):
    """차례대로 하나씩 꺼내 쓰는 응답 목록. 마지막 것은 계속 쓴다."""


class FakeClient:
    def __init__(self, responses: dict | None = None, *, fail: dict[str, int] | None = None):
        self.cfg = SimpleNamespace(provider="gemini", endpoint="agent", supports_json_mode=True, chat_model="fake")
        self.responses = {**DEFAULTS, **(responses or {})}
        self.fail = dict(fail or {})
        self.calls = 0
        self.log: list[tuple[str, str]] = []

    def chat(self, system: str, user: str, **kwargs) -> str:
        self.calls += 1
        stage = detect(user)
        self.log.append((stage, user))
        if self.fail.get(stage):
            self.fail[stage] -= 1
            raise AIError(f"가짜 실패: {stage}")
        value = self.responses[stage]
        if isinstance(value, Script):
            value = value.pop(0) if len(value) > 1 else value[0]
        if callable(value):
            value = value(user)
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

    def chat_json(self, system: str, user: str, **kwargs):
        return _parse_json(self.chat(system, user, **kwargs))

    def stages(self) -> list[str]:
        return [stage for stage, _ in self.log]


def detect(user: str) -> str:
    match = re.match(r"\[단계\] (\w+)", user)
    if match:
        return match.group(1)
    for marker, stage in _LEGACY_MARKERS:
        if marker in user:
            return stage
    raise AssertionError(f"어느 단계인지 모르는 프롬프트: {user[:80]}")
