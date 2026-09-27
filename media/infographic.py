"""글 전체를 한 장으로 요약한 인포그래픽을 그린다.

FAQ 를 글로 늘어놓으면 눈에 들어오지 않아서, 그 자리를 이 그림 한 장으로 대신한다.
네이버 스마트에디터는 HTML 을 받지 않으므로 '한눈에 보이는 것' 을 본문에 넣으려면
결국 이미지여야 한다. 그래서 HTML 로 꾸미지 않고 Pillow 로 직접 그린다.

레이아웃은 세 덩어리다. 핵심 특징 / 이런 분께 / 3줄 요약. 내용 길이에 따라 세로로
늘어나므로 높이는 그릴 때 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from core.models import KIND_CALLOUT, Article, ImageAsset, Product, ProductBrief, RoundupBrief

#: 글자가 뭉개지지 않게 2배로 그린 뒤 그대로 저장한다. 네이버가 알아서 줄여 보여준다.
SCALE = 2
WIDTH = 860

#: article.html 의 색과 맞춘다. accent 는 네이버 초록.
INK = "#1f2937"
MUTED = "#6b7280"
LINE = "#e5e7eb"
ACCENT = "#03c75a"
GOOD = "#166534"
GOOD_BG = "#f0fdf4"
WARN = "#92400e"
WARN_BG = "#fffbeb"
SUMMARY_BG = "#eff6ff"
SUMMARY_INK = "#1e40af"
CARD_BG = "#ffffff"
PAGE_BG = "#f8fafc"

#: 윈도우 기본 한글 글꼴. 없으면 순서대로 다음 후보를 찾는다.
FONT_CANDIDATES = {
    "regular": ["malgun.ttf", "NanumGothic.ttf", "gulim.ttc", "arial.ttf"],
    "bold": ["malgunbd.ttf", "NanumGothicBold.ttf", "gulim.ttc", "arialbd.ttf"],
}
FONT_DIRS = [Path(r"C:\Windows\Fonts"), Path("/usr/share/fonts"), Path("/Library/Fonts")]

PAD = 32  # 카드 바깥 여백
GAP = 20  # 덩어리 사이 간격


#: 한 장에 담을 수 있는 한계. 넘기면 그림이 세로로 늘어져 한눈에 안 들어온다.
MAX_FEATURES = 5
MAX_AUDIENCE = 3


@dataclass
class InfographicData:
    """인포그래픽에 들어갈 내용. 한 상품이든 묶음이든 이 형태로 맞춰 넘긴다."""

    title: str = ""
    #: (항목, 그래서 좋은 점). 뒤쪽은 비어도 된다.
    features: list[tuple[str, str]] = field(default_factory=list)
    features_label: str = "핵심 특징"
    recommended: list[str] = field(default_factory=list)
    not_recommended: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.features or self.recommended or self.not_recommended or self.summary)


def from_brief(product: Product, brief: ProductBrief, article: Article) -> InfographicData:
    """한 상품 리뷰용. 분석 설계도와 완성된 원고에서 요약거리를 모은다."""
    return InfographicData(
        title=product.title or brief.category,
        features=[(f.name, f.benefit) for f in brief.features[:MAX_FEATURES] if f.name],
        recommended=brief.recommended_for[:MAX_AUDIENCE],
        not_recommended=brief.not_recommended_for[:MAX_AUDIENCE],
        summary=summary_lines(article),
    )


def from_roundup(combo: RoundupBrief, article: Article) -> InfographicData:
    """묶음 글용. 이 글의 주인공은 개별 상품이 아니라 조합의 공통점이다."""
    return InfographicData(
        title=combo.theme or combo.one_liner,
        features=[(point, "") for point in combo.commonalities[:MAX_FEATURES]],
        features_label="이 조합의 공통점",
        recommended=combo.recommended_for[:MAX_AUDIENCE],
        not_recommended=combo.not_recommended_for[:MAX_AUDIENCE],
        summary=summary_lines(article),
    )


def summary_lines(article: Article) -> list[str]:
    """원고의 3줄 요약 박스를 그대로 가져온다. 없으면 빈 목록."""
    for block in article.blocks:
        if block.kind == KIND_CALLOUT and block.style == "summary":
            return [line.strip() for line in block.text.split("\n") if line.strip()]
    return []


def render(data: InfographicData, dest_dir: Path, *, name: str = "infographic.png") -> ImageAsset | None:
    """인포그래픽 PNG 를 만들고 ImageAsset 으로 돌려준다. 내용이 없으면 None."""
    if data.is_empty:
        return None

    fonts = _Fonts()
    blocks = _layout(data, fonts)
    if not blocks:
        return None

    height = PAD + sum(b.height + GAP for b in blocks) - GAP + PAD
    canvas = Image.new("RGB", (WIDTH * SCALE, height * SCALE), PAGE_BG)
    draw = ImageDraw.Draw(canvas)

    y = PAD
    for block in blocks:
        block.paint(draw, y)
        y += block.height + GAP

    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / name
    canvas.save(path, "PNG", optimize=True)

    return ImageAsset(
        source="infographic",
        path=path,
        width=canvas.width,
        height=canvas.height,
        score=100.0,
        caption=data.title or "한눈에 보기",
    )


# ------------------------------------------------------------------ 글꼴


class _Fonts:
    """제목/본문용 굵기와 크기를 미리 잡아 둔다."""

    def __init__(self) -> None:
        regular = _find_font("regular")
        bold = _find_font("bold")
        self.head = _load(bold, 26)
        self.section = _load(bold, 20)
        self.item = _load(bold, 17)
        self.body = _load(regular, 15)
        self.badge = _load(bold, 14)


def _find_font(weight: str) -> Path | None:
    for directory in FONT_DIRS:
        if not directory.exists():
            continue
        for name in FONT_CANDIDATES[weight]:
            candidate = directory / name
            if candidate.exists():
                return candidate
    return None


def _load(path: Path | None, size: int) -> ImageFont.FreeTypeFont:
    if path:
        try:
            return ImageFont.truetype(str(path), size * SCALE)
        except OSError:
            pass
    return ImageFont.load_default(size * SCALE)


# ------------------------------------------------------------------ 배치

#: 그릴 준비가 끝난 한 덩어리. height 를 먼저 알아야 전체 높이를 잡을 수 있어서,
#: 배치 계산과 그리기를 나눠 둔다.
@dataclass
class _Block:
    height: int
    paint_fn: object

    def paint(self, draw: ImageDraw.ImageDraw, top: int) -> None:
        self.paint_fn(draw, top)


def _layout(data: InfographicData, fonts: _Fonts) -> list[_Block]:
    inner = WIDTH - PAD * 2
    blocks: list[_Block] = []

    if data.title:
        blocks.append(_header_block(data.title, fonts, inner))
    if data.features:
        blocks.append(_features_block(data.features, data.features_label, fonts, inner))
    if data.recommended or data.not_recommended:
        blocks.append(_audience_block(data.recommended, data.not_recommended, fonts, inner))
    if data.summary:
        blocks.append(_summary_block(data.summary, fonts, inner))
    return blocks


def _header_block(title: str, fonts: _Fonts, inner: int) -> _Block:
    lines = _wrap(title, fonts.head, inner - 40)
    height = 24 + len(lines) * _line_height(fonts.head) + 24

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        _rect(draw, PAD, top, inner, height, CARD_BG)
        # 왼쪽에 초록 띠를 둬서 본문 h2 와 같은 인상을 준다.
        _rect(draw, PAD, top, 6, height, ACCENT)
        y = top + 24
        for line in lines:
            _text(draw, PAD + 26, y, line, fonts.head, INK)
            y += _line_height(fonts.head)

    return _Block(height, paint)


def _features_block(features: list[tuple[str, str]], label: str, fonts: _Fonts, inner: int) -> _Block:
    text_left = 26 + 34  # 번호 배지 자리를 비운다
    rows = []
    for name, benefit in features:
        name_lines = _wrap(name, fonts.item, inner - text_left - 26)
        benefit_lines = _wrap(benefit, fonts.body, inner - text_left - 26) if benefit else []
        rows.append((name_lines, benefit_lines))

    head_h = 24 + _line_height(fonts.section) + 16
    row_heights = [
        len(n) * _line_height(fonts.item) + (6 + len(b) * _line_height(fonts.body) if b else 0) + 18
        for n, b in rows
    ]
    height = head_h + sum(row_heights) + 10

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        _rect(draw, PAD, top, inner, height, CARD_BG)
        _text(draw, PAD + 26, top + 24, label, fonts.section, INK)
        y = top + head_h
        for index, ((name_lines, benefit_lines), row_h) in enumerate(zip(rows, row_heights), 1):
            _badge(draw, PAD + 26, y + 1, str(index), fonts.badge)
            ty = y
            for line in name_lines:
                _text(draw, PAD + text_left, ty, line, fonts.item, INK)
                ty += _line_height(fonts.item)
            if benefit_lines:
                ty += 6
                for line in benefit_lines:
                    _text(draw, PAD + text_left, ty, line, fonts.body, MUTED)
                    ty += _line_height(fonts.body)
            y += row_h
            if index < len(rows):
                _rect(draw, PAD + text_left, y - 9, inner - text_left - 26, 1, LINE)

    return _Block(height, paint)


def _audience_block(good: list[str], bad: list[str], fonts: _Fonts, inner: int) -> _Block:
    """추천과 비추천을 좌우로 나란히 놓아 대비가 바로 보이게 한다."""
    gutter = 16
    col_w = (inner - gutter) // 2
    # 체크/엑스 기호는 맑은 고딕에 글리프가 없어 두부(□)로 나온다. 직접 그린다.
    columns = [
        ("이런 분께 추천", good, GOOD, GOOD_BG, _check),
        ("이런 분껜 비추천", bad, WARN, WARN_BG, _cross),
    ]

    wrapped = [[_wrap(item, fonts.body, col_w - 56) for item in items] for _, items, *_ in columns]
    head_h = 22 + _line_height(fonts.item) + 14
    col_heights = [
        head_h + sum(len(lines) * _line_height(fonts.body) + 12 for lines in col) + 10
        for col in wrapped
    ]
    height = max(col_heights)

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        for i, (label, _, ink, bg, mark) in enumerate(columns):
            x = PAD + i * (col_w + gutter)
            _rect(draw, x, top, col_w, height, bg)
            _text(draw, x + 22, top + 22, label, fonts.item, ink)
            y = top + head_h
            for lines in wrapped[i]:
                mark(draw, x + 22, y + 5, ink)
                for line in lines:
                    _text(draw, x + 44, y, line, fonts.body, INK)
                    y += _line_height(fonts.body)
                y += 12

    return _Block(height, paint)


def _summary_block(summary: list[str], fonts: _Fonts, inner: int) -> _Block:
    wrapped = [_wrap(line, fonts.item, inner - 74) for line in summary]
    head_h = 24 + _line_height(fonts.section) + 16
    heights = [len(lines) * _line_height(fonts.item) + 14 for lines in wrapped]
    height = head_h + sum(heights) + 10

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        _rect(draw, PAD, top, inner, height, SUMMARY_BG)
        _text(draw, PAD + 26, top + 24, "3줄 요약", fonts.section, SUMMARY_INK)
        y = top + head_h
        for index, lines in enumerate(wrapped):
            _badge(draw, PAD + 26, y + 1, str(index + 1), fonts.badge, fill=SUMMARY_INK)
            ty = y
            for line in lines:
                _text(draw, PAD + 60, ty, line, fonts.item, INK)
                ty += _line_height(fonts.item)
            y += heights[index]

    return _Block(height, paint)


# ------------------------------------------------------------------ 그리기 도구


def _rect(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, color: str) -> None:
    draw.rectangle(
        [x * SCALE, y * SCALE, (x + w) * SCALE - 1, (y + h) * SCALE - 1],
        fill=color,
    )


def _text(draw: ImageDraw.ImageDraw, x: int, y: int, value: str, font, color: str) -> None:
    draw.text((x * SCALE, y * SCALE), value, font=font, fill=color)


def _badge(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, font, fill: str = ACCENT) -> None:
    size = 22
    draw.ellipse(
        [x * SCALE, y * SCALE, (x + size) * SCALE, (y + size) * SCALE],
        fill=fill,
    )
    width = draw.textlength(label, font=font)
    draw.text(
        (x * SCALE + (size * SCALE - width) / 2, y * SCALE + 3 * SCALE),
        label,
        font=font,
        fill="#ffffff",
    )


def _check(draw: ImageDraw.ImageDraw, x: int, y: int, color: str) -> None:
    draw.line(
        [(x * SCALE, (y + 5) * SCALE), ((x + 4) * SCALE, (y + 9) * SCALE), ((x + 11) * SCALE, y * SCALE)],
        fill=color,
        width=2 * SCALE,
        joint="curve",
    )


def _cross(draw: ImageDraw.ImageDraw, x: int, y: int, color: str) -> None:
    for start, end in (((0, 0), (10, 10)), ((10, 0), (0, 10))):
        draw.line(
            [((x + start[0]) * SCALE, (y + start[1]) * SCALE), ((x + end[0]) * SCALE, (y + end[1]) * SCALE)],
            fill=color,
            width=2 * SCALE,
        )


def _line_height(font) -> int:
    return int(font.size / SCALE * 1.5)


def _wrap(text: str, font, max_width: int) -> list[str]:
    """픽셀 너비로 줄을 나눈다.

    한국어는 띄어쓰기가 드물어 단어 단위로만 자르면 한 줄이 넘쳐 잘린다.
    그래서 띄어쓰기를 먼저 시도하고, 한 덩어리가 너무 길면 글자 단위로 쪼갠다.
    """
    text = " ".join((text or "").split())
    if not text:
        return []

    limit = max_width * SCALE
    lines: list[str] = []
    current = ""

    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        # 단어 하나가 한 줄보다 길면 글자 단위로 끊는다.
        for char in word:
            if font.getlength(current + char) <= limit:
                current += char
            else:
                lines.append(current)
                current = char
    if current:
        lines.append(current)
    return lines
