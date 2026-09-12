"""AI 냄새 제거.

"사람처럼 써줘"라고 부탁하는 것만으로는 잘 고쳐지지 않는다. 그래서 파이썬이 먼저
문체를 계측해서 무엇이 기계적인지 수치로 짚어내고, 그 결함만 집어서 다시 쓰게 한다.

계측 항목
  - 문장 길이 변동계수: 길이가 균일하면 기계가 쓴 티가 난다
  - 종결 어미 다양성: "~습니다"만 반복되는지
  - 반복 표현: 같은 3어절이 여러 번 나오는지
  - AI 상투어: 한국어 생성문에서 유독 자주 나오는 표현
"""

from __future__ import annotations

import re
import statistics

from ai.client import MyGenAssistClient
from ai.prompts import WRITER_RULES
from core.models import KIND_CTA, KIND_PARAGRAPH, KIND_QUOTE, Article, HumannessReport, SeoPlan

#: 한국어 AI 생성문에서 유독 자주 튀어나오는 표현들.
CLICHES = [
    "결론적으로",
    "뿐만 아니라",
    "무엇보다도",
    "라고 할 수 있습니다",
    "할 수 있습니다",
    "알아보겠습니다",
    "살펴보겠습니다",
    "주목할 만한",
    "매우 중요한",
    "필수적인",
    "혁신적인",
    "탁월한",
    "극대화",
    "선사합니다",
    "자랑합니다",
    "만족스러운 경험",
    "다양한 기능",
    "효과적인",
    "최적화된",
    "중요한 역할을",
    "다시 한번",
    "종합적으로",
]

LONG_SENTENCE = 90  # 이보다 길면 쪼개는 편이 읽기 좋다
#: 재작성을 반복해도 이 이상 좋아지지 않으면 멈춘다.
MAX_PASSES = 2

_REWRITABLE = (KIND_PARAGRAPH, KIND_QUOTE, KIND_CTA)


def measure(text: str) -> HumannessReport:
    sentences = _split_sentences(text)
    report = HumannessReport(sentence_count=len(sentences))
    if len(sentences) < 2:
        return report

    lengths = [len(s) for s in sentences]
    report.mean_length = statistics.mean(lengths)
    report.stdev_length = statistics.pstdev(lengths)
    report.variation = report.stdev_length / report.mean_length if report.mean_length else 0.0

    endings = [s[-3:] for s in sentences if len(s) >= 3]
    report.ending_variety = len(set(endings)) / len(endings) if endings else 0.0

    report.repeated_phrases = _repeated_ngrams(text)
    report.cliches = [c for c in CLICHES if c in text]
    report.long_sentences = [s for s in sentences if len(s) > LONG_SENTENCE]

    return report


def humanize(
    client: MyGenAssistClient, article: Article, persona: str, seo: SeoPlan | None = None
) -> tuple[Article, HumannessReport]:
    """계측 -> 재작성을 반복하되, 개선이 없으면 멈춘다."""
    report = measure(article.body_text())

    for _ in range(MAX_PASSES):
        if not report.needs_work:
            break

        rewritten = _rewrite(client, article, persona, report, seo)
        new_report = measure(rewritten.body_text())

        # 재작성이 오히려 나빠졌다면 이전 원고를 지킨다.
        if _grade(new_report) <= _grade(report):
            break
        # 문체가 좋아져도 검색 키워드를 잃었다면 손해다.
        if _keyword_loss(article, rewritten, seo):
            break

        article, report = rewritten, new_report

    return article, report


def _keyword_loss(before: Article, after: Article, seo: SeoPlan | None) -> bool:
    """재작성으로 메인 키워드 노출이 줄었는지 본다. 도입부에서 사라지는 것도 손실로 본다."""
    if not seo or not seo.main_keyword:
        return False

    old, new = before.body_text(), after.body_text()
    main = seo.main_keyword
    if new.count(main) < old.count(main):
        return True
    return main in old[:300] and main not in new[:300]


def _rewrite(
    client: MyGenAssistClient,
    article: Article,
    persona: str,
    report: HumannessReport,
    seo: SeoPlan | None = None,
) -> Article:
    targets = [(i, b) for i, b in enumerate(article.blocks) if b.kind in _REWRITABLE and b.text]
    if not targets:
        return article

    # 문체만 손보다가 검색 키워드를 날려 먹는 일이 잦아, 지켜야 할 표현을 못 박는다.
    keep = ""
    if seo:
        terms = [k for k in [seo.main_keyword, *seo.sub_keywords[:5]] if k]
        if terms:
            keep = (
                "\n[반드시 글자 그대로 남겨야 하는 표현. 개수를 줄이거나 다른 말로 "
                f"바꾸지 마라]\n{', '.join(terms)}\n"
            )

    numbered = "\n".join(f"[{i}] {b.text}" for i, b in targets)
    user = f"""아래는 블로그 원고의 문단들이다. 기계적으로 읽히는 부분을 사람이 쓴 것처럼 고쳐라.

[글쓴이 페르소나]
{persona}
{keep}

[문체 진단 결과. 이 지점들을 고쳐라]
{report.as_instructions()}

[고칠 문단들]
{numbered}

규칙.
- 문단 번호와 개수를 그대로 유지한다. 내용과 정보는 바꾸지 않는다.
- 문장을 합치거나 쪼개서 길이에 리듬을 만든다. 한 문장짜리 문단이 있어도 좋다.
- 체험한 사람의 감정과 망설임을 한두 군데 섞는다. 과하면 어색하니 절제한다.
- 존댓말과 평서문 중 원문의 어조를 유지한다.

아래 JSON 형식으로만 출력한다. 키는 문단 번호다.

{{"0": "고친 문단", "3": "고친 문단"}}"""

    data = client.chat_json(WRITER_RULES, user, temperature=0.95, max_tokens=12000, websearch=False)
    if not isinstance(data, dict):
        return article

    blocks = list(article.blocks)
    for key, value in data.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        text = str(value or "").strip()
        if text and 0 <= index < len(blocks) and blocks[index].kind in _REWRITABLE:
            blocks[index] = _replace_text(blocks[index], text)

    return Article(title=article.title, blocks=blocks, tags=article.tags, disclosure=article.disclosure)


def _replace_text(block, text: str):
    from dataclasses import replace

    return replace(block, text=text)


def _grade(report: HumannessReport) -> float:
    """재작성 전후를 비교하기 위한 단일 점수. 높을수록 사람 글에 가깝다."""
    score = min(report.variation / 0.6, 1.0) * 40
    score += min(report.ending_variety / 0.5, 1.0) * 30
    score += max(0, 15 - 3 * len(report.cliches))
    score += max(0, 15 - 3 * len(report.repeated_phrases))
    return score


def _split_sentences(text: str) -> list[str]:
    chunks = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [c.strip() for c in chunks if len(c.strip()) >= 5]


def _repeated_ngrams(text: str, n: int = 3, threshold: int = 3) -> list[str]:
    words = re.findall(r"[가-힣A-Za-z0-9]+", text)
    if len(words) < n:
        return []

    counts: dict[str, int] = {}
    for i in range(len(words) - n + 1):
        phrase = " ".join(words[i : i + n])
        counts[phrase] = counts.get(phrase, 0) + 1

    return [p for p, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= threshold][:10]
