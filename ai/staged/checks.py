"""코드로 하는 검수. AI 판단에 맡기면 매번 결과가 달라지는 항목은 여기서 결정론적으로 잡는다.

- 과장·의료 표현: 상품 정보 원문에 그 말이 있을 때만 허용한다
- 구매 압박, 체험 단정: 항상 잡는다
- 근거 없는 수치: 본문의 '숫자+단위' 가 상품 정보에 없는 숫자면 잡는다
- 검색어: 한 문단 반복, 전체 밀도 과다, 첫 문단 누락
- 반복 표현, 모바일에서 읽기 힘든 긴 문단·문장
"""

from __future__ import annotations

import re

from ai.humanizer import _repeated_ngrams, _split_sentences
from ai.seo import DENSITY_MAX, density
from ai.staged.units import iter_units
from core.models import ReviewIssue, SectionDraft, SeoPlan

EXAGGERATION = (
    "최고의", "최고급", "완벽", "1위", "넘버원", "역대급", "무조건", "100%", "세계 최초", "국내 최초",
    "유일한", "기적 같은", "기적의", "획기적", "압도적", "끝판왕", "인생템", "혁신적",
)
MEDICAL = ("치료", "완치", "부작용 없", "부작용이 없", "질병", "예방 효과")
PRESSURE = (
    "지금 바로", "서두르", "놓치면", "놓치지 마", "마감 임박", "품절 임박", "오늘만", "한정 수량",
    "후회하지", "후회할", "당장", "망설이지 마", "고민하지 마", "더 늦기 전에", "지금 아니면",
)
EXPERIENCE = (
    "직접 써", "직접 사용해", "써 보니", "써보니", "써 봤", "써봤", "사용해 보니", "사용해보니",
    "사용해 봤", "사용해봤", "구매해 봤", "구매해봤", "실제로 써", "제가 산", "후기를 보면",
    "리뷰를 보면", "별점", "내돈내산", "솔직후기", "솔직 후기",
)

_SPEC_UNITS = r"mAh|kWh|Wh|kW|kg|mg|ml|mL|cm|mm|GB|TB|MB|Hz|dB|℃|°C|%|퍼센트|리터|인치|개입|g|L|W|V|m"
_ALL_UNITS = _SPEC_UNITS + r"|개월|시간|만원|분|초|년|일|배|원|평|매|회"
_NUMBER = r"(?<![\d.,])(\d+(?:[.,]\d+)*)\s*"
_SPEC_RE = re.compile(_NUMBER + rf"(?:{_SPEC_UNITS})(?![A-Za-z])")
_ALL_RE = re.compile(_NUMBER + rf"(?:{_ALL_UNITS})(?![A-Za-z])")
_ANY_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

MAX_PARAGRAPH = 250
MAX_SENTENCE = 110
MAX_SUB_KEYWORD = 3
TITLE_MAX = 40

#: 사용 장면은 상상해서 그리는 자리라 '아침 10분' 같은 시간 표현은 허용하고 사양 수치만 본다.
_IMAGINED = {"scenes"}
#: 총평은 요약 그림에 들어가는 긴 글이라 문단 길이를 재지 않는다.
_LONG_OK = {"summary/c"}


def run(sections: dict[str, SectionDraft], *, source_text: str, seo: SeoPlan) -> list[ReviewIssue]:
    units = list(iter_units(sections))
    source = _squeeze(source_text)
    known_numbers = {_norm_number(n) for n in _ANY_NUMBER.findall(source_text)}
    issues: dict[str, ReviewIssue] = {}

    def flag(key: str, kind: str, reason: str, suggestion: str = "") -> None:
        if key in issues:
            if reason not in issues[key].reason:
                issues[key].reason += f" / {reason}"
            return
        issues[key] = ReviewIssue(key=key, type=kind, reason=reason, suggestion=suggestion)

    for key, text in units:
        sid = key.split("/", 1)[0]
        squeezed = _squeeze(text)

        for word in EXAGGERATION + MEDICAL:
            if _squeeze(word) in squeezed and _squeeze(word) not in source:
                flag(key, "exaggeration", f"상품 정보에 없는 과장·효능 표현 '{word}'", "사실 그대로의 표현으로 바꾼다")
        for word in PRESSURE:
            if _squeeze(word) in squeezed:
                flag(key, "pressure", f"구매를 재촉하는 표현 '{word}'", "재촉하지 않는 담백한 표현으로 바꾼다")
        for word in EXPERIENCE:
            if _squeeze(word) in squeezed:
                flag(key, "experience", f"써 본 것처럼 단정한 표현 '{word}'", "'~할 때', '~라면' 처럼 상황으로 그린다")

        pattern = _SPEC_RE if sid in _IMAGINED else _ALL_RE
        for match in pattern.finditer(text):
            if _norm_number(match.group(1)) not in known_numbers:
                flag(key, "number", f"상품 정보에서 확인되지 않는 수치 '{match.group(0).strip()}'",
                     "수치를 빼거나 확인된 사실의 수치로 바꾼다")

        if seo.main_keyword and text.count(seo.main_keyword) >= 2:
            flag(key, "keyword", f"한 문장 안에 메인 키워드 '{seo.main_keyword}' 가 반복됨", "한 번만 남긴다")

        if _is_prose(key) and key not in _LONG_OK:
            if len(text) > MAX_PARAGRAPH:
                flag(key, "readability", f"문단이 {len(text)}자로 길다", "두세 문장씩 나눠 줄바꿈한다")
            long = [s for s in _split_sentences(text) if len(s) > MAX_SENTENCE]
            if long:
                flag(key, "readability", f"{len(long[0])}자짜리 긴 문장이 있다", "문장을 둘로 나눈다")

    _check_keywords(units, seo, flag)
    _check_repetition(units, seo, flag)
    return list(issues.values())


def check_title(title: str, seo: SeoPlan, source_text: str = "") -> list[str]:
    problems = []
    if not title:
        return ["제목이 비었다"]
    if len(title) > TITLE_MAX:
        problems.append(f"{len(title)}자로 길다")
    if seo.main_keyword and _squeeze(seo.main_keyword) not in _squeeze(title):
        problems.append("메인 키워드가 없다")
    source = _squeeze(source_text)
    for word in EXAGGERATION + PRESSURE + EXPERIENCE:
        if _squeeze(word) in _squeeze(title) and _squeeze(word) not in source:
            problems.append(f"'{word}' 표현")
    return problems


def _check_keywords(units: list[tuple[str, str]], seo: SeoPlan, flag) -> None:
    main = seo.main_keyword
    if not main:
        return
    prose = [(k, t) for k, t in units if _is_prose(k)]
    body = "\n".join(t for _, t in prose)

    intro = " ".join(t for k, t in prose if k.startswith("intro/"))
    if intro and _squeeze(main) not in _squeeze(intro):
        flag("intro/p0", "seo", f"도입부에 메인 키워드 '{main}' 가 없다", "첫 문단에 자연스럽게 한 번 넣는다")

    if density(body, main) > DENSITY_MAX:
        candidates = [(t.count(main), k) for k, t in prose if main in t and not k.startswith("intro/")]
        if candidates:
            _, key = max(candidates)
            flag(key, "keyword", f"메인 키워드 밀도가 {density(body, main):.1f}% 로 높다", "이 문장에서는 빼거나 다른 말로 바꾼다")

    for keyword in seo.sub_keywords:
        hits = [k for k, t in prose if keyword and keyword in t]
        if len(hits) > MAX_SUB_KEYWORD:
            flag(hits[-1], "keyword", f"보조 키워드 '{keyword}' 가 {len(hits)}번 반복됨", "이 문장에서는 다른 말로 바꾼다")


def _check_repetition(units: list[tuple[str, str]], seo: SeoPlan, flag) -> None:
    prose = [(k, t) for k, t in units if _is_prose(k)]
    keywords = [_squeeze(k) for k in [seo.main_keyword, *seo.sub_keywords] if k]
    phrases = _repeated_ngrams("\n".join(t for _, t in prose))
    for phrase in phrases[:5]:
        # 검색어는 일부러 여러 섹션에 나눠 넣었다. 반복으로 보지 않는다.
        if any(k in _squeeze(phrase) or _squeeze(phrase) in k for k in keywords):
            continue
        hits = [k for k, t in prose if phrase in t]
        for key in hits[1:3]:
            flag(key, "repetition", f"'{phrase}' 표현이 여러 번 나온다", "다른 말로 바꾼다")


def _is_prose(key: str) -> bool:
    unit = key.split("/", 1)[1]
    return unit.startswith("p") or unit == "c" or (unit.startswith("s") and unit.endswith(".1"))


def _squeeze(text: str) -> str:
    return re.sub(r"\s", "", text or "").lower()


def _norm_number(raw: str) -> str:
    value = raw.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value
