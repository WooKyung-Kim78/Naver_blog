"""단계별 집필을 순서대로 돌리고, 단계마다 결과를 저장해 실패한 지점부터 다시 시작한다.

  1 상품 정보 검증·정리      facts
  2 핵심 고객·검색 의도      (pipeline 의 analyst·focus·seo 결과를 그대로 쓴다)
  3 핵심 메시지              message
  4 개요                     outline
  5 섹션별 본문              sections   섹션 단위로 저장해 쓴 섹션은 다시 쓰지 않는다
  6 흐름·문체 통일           unify
  7 검수                     review     코드 검사 + AI 검수
  8 부분 수정                fix        지적된 곳만 고치고 코드 검사를 다시 돌린다
  9 최종 조립                titles + assemble

각 저장본에는 입력 해시가 붙는다. 앞 단계가 바뀌면 뒤 단계는 저절로 다시 돈다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ai.staged import checks, steps
from ai.staged.assemble import assemble
from ai.staged.prompts import PROMPT_VERSION, SECTION_ORDER
from config import WriterConfig
from core.checkpoint import Checkpoint, digest, from_data, to_data
from core.models import (
    Article,
    FocusPoint,
    MessagePlan,
    Outline,
    Product,
    ProductBrief,
    ProductFacts,
    ReviewIssue,
    SectionDraft,
    SeoPlan,
    TitleOption,
)

FILES = {
    "facts": "01_facts",
    "message": "03_message",
    "outline": "04_outline",
    "sections": "05_sections",
    "unify": "06_unified",
    "review": "07_review",
    "fix": "08_fixed",
    "titles": "09_titles",
}


@dataclass
class FixOutcome:
    sections: dict[str, SectionDraft] = field(default_factory=dict)
    remaining: list[ReviewIssue] = field(default_factory=list)
    rounds: int = 0


@dataclass
class TitlePick:
    options: list[TitleOption] = field(default_factory=list)
    chosen: int = 0

    @property
    def title(self) -> str:
        return self.options[self.chosen].title if self.options else ""


@dataclass
class StagedResult:
    article: Article
    facts: ProductFacts
    message: MessagePlan
    outline: Outline
    sections: dict[str, SectionDraft]
    titles: TitlePick
    issues: list[ReviewIssue]
    remaining: list[ReviewIssue]

    def report(self) -> dict[str, Any]:
        return {
            "prompt_version": PROMPT_VERSION,
            "facts": to_data(self.facts),
            "message": to_data(self.message),
            "outline": to_data(self.outline),
            "title_options": to_data(self.titles.options),
            "title_chosen": self.titles.chosen,
            "meta_description": self.outline.meta_description,
            "hashtags": self.outline.hashtags,
            "review_issues": to_data(self.issues),
            "remaining_issues": to_data(self.remaining),
        }


class StagedWriter:
    def __init__(
        self,
        client,
        checkpoint: Checkpoint,
        *,
        persona: str,
        disclosure: str,
        cfg: WriterConfig,
        report: Callable[[str], None] | None = None,
    ):
        self.client = client
        self.ckpt = checkpoint
        self.persona = persona
        self.disclosure = disclosure
        self.cfg = cfg
        self.report = report or (lambda _: None)

    def run(
        self,
        product: Product,
        brief: ProductBrief,
        focus: FocusPoint | None,
        seo: SeoPlan,
        *,
        images: list[Path] | None = None,
        rewrite_sections: list[str] | None = None,
        rewrite_title: bool = False,
    ) -> StagedResult:
        """rewrite_sections 를 주면 그 섹션만 새로 쓰고, 통일·검수·수정도 그 섹션에만 한다."""
        images = images or []
        source = steps.source_text(product, seo)
        only = set(rewrite_sections or []) or None
        if only and not only <= set(SECTION_ORDER):
            raise ValueError(f"모르는 섹션: {', '.join(sorted(only - set(SECTION_ORDER)))}. "
                             f"쓸 수 있는 섹션: {', '.join(SECTION_ORDER)}")

        facts = self._stage(
            "facts", "[1/9] 상품 정보 검증·정리",
            [product, [p.name for p in images]],
            lambda: steps.extract_facts(self.client, product, images),
            ProductFacts,
        )
        self.report(f"  확인된 사실 {len(facts.facts)}개" + (f" / 근거 없어 뺀 주장 {len(facts.dropped)}개" if facts.dropped else ""))
        self.report("[2/9] 핵심 고객·검색 의도: 상품 분석과 SEO 전략 결과를 씁니다")

        message = self._stage(
            "message", "[3/9] 핵심 메시지 선정",
            [facts, brief, focus, seo],
            lambda: steps.plan_message(self.client, facts, brief, focus, seo),
            MessagePlan,
        )
        self.report(f"  {message.message}")

        outline = self._stage(
            "outline", "[4/9] 글 구성과 개요 작성",
            [facts, message, seo],
            lambda: steps.plan_outline(self.client, facts, message, seo),
            Outline,
        )

        base = self._previous_final() if only else None
        drafts = self._write_sections(product, facts, message, outline, seo, force=only or set())
        if base:
            drafts = {sid: drafts[sid] if sid in only else base.get(sid, drafts[sid]) for sid in SECTION_ORDER}

        unified = self._stage(
            "unify", "[6/9] 흐름과 문체 통일",
            [drafts, message, seo, self.persona, sorted(only or [])],
            lambda: steps.unify(self.client, drafts, message=message, seo=seo, persona=self.persona, only=only),
            dict[str, SectionDraft],
        )

        issues = self._stage(
            "review", "[7/9] 사실성·과장·반복·가독성·SEO 검수",
            [unified, facts, seo, self.cfg.ai_review, sorted(only or [])],
            lambda: steps.review(self.client, unified, facts=facts, seo=seo, source=source,
                                 use_ai=self.cfg.ai_review, only=only),
            list[ReviewIssue],
        )
        self.report(f"  지적 {len(issues)}건" + (f" ({_kinds(issues)})" if issues else ""))

        outcome = self._stage(
            "fix", "[8/9] 지적된 문장만 수정",
            [unified, issues, facts, seo, self.persona, self.cfg.max_fix_rounds],
            lambda: self._fix(unified, issues, facts, seo, source, only),
            FixOutcome,
        )
        if outcome.remaining:
            self.report(f"  남은 지적 {len(outcome.remaining)}건 ({_kinds(outcome.remaining)})")

        final = outcome.sections
        previous = from_data(TitlePick, self.ckpt.load(FILES["titles"])) if rewrite_title else None
        titles = self._stage(
            "titles", "[9/9] 제목 3안 중 1순위 선택 후 조립",
            [final.get("intro"), [d.heading for d in final.values()], message, seo],
            lambda: self._titles(final, message, seo, source, previous),
            TitlePick,
            force=rewrite_title,
        )
        for i, option in enumerate(titles.options):
            mark = "→" if i == titles.chosen else " "
            self.report(f"  {mark} {i + 1}. {option.title}")

        article = assemble(final, outline, titles.title, disclosure=self.disclosure, product_url=product.url)
        return StagedResult(article, facts, message, outline, final, titles, issues, outcome.remaining)

    # ------------------------------------------------------------ 단계 실행

    def _stage(self, name: str, label: str, inputs: list, build: Callable[[], Any], cls: Any,
               *, force: bool = False) -> Any:
        key = digest(PROMPT_VERSION, name, *inputs)
        if not force:
            cached = self.ckpt.load(FILES[name], key)
            if cached is not None:
                self.report(f"{label} (저장본 사용)")
                return from_data(cls, cached)
        self.report(label)
        result = build()
        self.ckpt.save(FILES[name], result, key)
        return result

    def _write_sections(
        self, product, facts, message, outline, seo, *, force: set[str]
    ) -> dict[str, SectionDraft]:
        record = self.ckpt.load(FILES["sections"]) or {}
        cache = record.get("items", {}) if isinstance(record, dict) else {}
        flow = [(p.id, p.heading, p.goal) for p in outline.sections]

        hashes = {
            sid: digest(PROMPT_VERSION, "section", facts, message, outline.get(sid), flow,
                        self.persona, product.title, product.price)
            for sid in SECTION_ORDER
        }
        drafts: dict[str, SectionDraft] = {}
        for sid in SECTION_ORDER:
            entry = cache.get(sid)
            if sid not in force and isinstance(entry, dict) and entry.get("hash") == hashes[sid]:
                drafts[sid] = from_data(SectionDraft, entry.get("data"))

        pending = [sid for sid in SECTION_ORDER if sid not in drafts]
        if not pending:
            self.report("[5/9] 섹션별 본문 작성 (저장본 사용)")
            return drafts

        size = self.cfg.section_batch
        batches = [pending[i:i + size] for i in range(0, len(pending), size)]
        for n, batch in enumerate(batches, 1):
            self.report(f"[5/9] 섹션별 본문 작성 ({n}/{len(batches)}): {', '.join(batch)}")
            written = steps.write_sections(
                self.client, batch, product=product, facts=facts, message=message,
                outline=outline, persona=self.persona,
            )
            for sid, draft in written.items():
                drafts[sid] = draft
                cache[sid] = {"hash": hashes[sid], "data": to_data(draft)}
            # 묶음마다 저장해 두면 다음 묶음에서 실패해도 쓴 섹션은 남는다.
            self.ckpt.save(FILES["sections"], {"items": cache})
        return {sid: drafts[sid] for sid in SECTION_ORDER}

    def _fix(self, sections, issues, facts, seo, source, only) -> FixOutcome:
        current, pending, rounds = sections, list(issues), 0
        while pending and rounds < self.cfg.max_fix_rounds:
            rounds += 1
            self.report(f"  수정 {rounds}회차: {len(pending)}곳")
            fixed = steps.fix(self.client, current, pending, facts=facts, seo=seo, persona=self.persona)
            changed = to_data(fixed) != to_data(current)
            current = fixed
            # 다시 검사는 코드로만 한다. AI 검수를 매번 돌리면 지적이 끝없이 바뀐다.
            pending = [i for i in checks.run(current, source_text=source, seo=seo)
                       if not only or i.key.split("/", 1)[0] in only]
            if not changed:
                break
        return FixOutcome(sections=current, remaining=pending, rounds=rounds)

    def _titles(self, sections, message, seo, source, previous: TitlePick | None) -> TitlePick:
        avoid = [o.title for o in previous.options] if previous else None
        options, chosen = steps.propose_titles(
            self.client, sections, message=message, seo=seo, source=source, avoid=avoid,
        )
        return TitlePick(options=options, chosen=chosen)

    def _previous_final(self) -> dict[str, SectionDraft] | None:
        data = self.ckpt.load(FILES["fix"])
        if not isinstance(data, dict):
            return None
        sections = from_data(FixOutcome, data).sections
        return sections or None


def _kinds(issues: list[ReviewIssue]) -> str:
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.type or "기타"] = counts.get(issue.type or "기타", 0) + 1
    return ", ".join(f"{k} {v}" for k, v in counts.items())
