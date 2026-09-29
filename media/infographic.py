"""글 전체를 한 장으로 요약한 인포그래픽을 그린다.

FAQ 를 글로 늘어놓으면 눈에 들어오지 않아서, 그 자리를 이 그림 한 장으로 대신한다.
네이버 스마트에디터는 HTML 을 받지 않으므로 '한눈에 보이는 것' 을 본문에 넣으려면
결국 이미지여야 한다. 그래서 HTML 로 꾸미지 않고 Pillow 로 직접 그린다.

레이아웃은 네 덩어리다. 제목 배너 / 핵심 특징 아이콘 타일 / 이런 분께 / 총평.
글은 줄 수를 정해 두고 넘치면 말줄임표로 자른다. 길어지면 한눈에 안 들어온다.
"""

from __future__ import annotations

import math
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
ACCENT_DARK = "#02a84b"
ACCENT_SOFT = "#e7f9ef"
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
    "regular": [
        "AppleSDGothicNeo.ttc", "AppleGothic.ttf", "malgun.ttf",
        "NanumGothic.ttf", "gulim.ttc", "arial.ttf",
    ],
    "bold": [
        "AppleSDGothicNeo.ttc", "AppleGothic.ttf", "malgunbd.ttf",
        "NanumGothicBold.ttf", "gulim.ttc", "arialbd.ttf",
    ],
}
FONT_DIRS = [
    Path("/System/Library/Fonts"),
    Path("/System/Library/Fonts/Supplemental"),
    Path.home() / "Library/Fonts",
    Path(r"C:\Windows\Fonts"),
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
]

PAD = 32  # 카드 바깥 여백
GAP = 16  # 덩어리 사이 간격
RADIUS = 14


#: 한 장에 담을 수 있는 한계. 넘기면 그림이 세로로 늘어져 한눈에 안 들어온다.
MAX_FEATURES = 4
MAX_AUDIENCE = 2
#: 총평 글자 수 상한. 넘치면 마지막 문장 끝에서 자른다.
MAX_VERDICT_CHARS = 1000


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
    """원고의 총평 박스를 문단 목록으로 가져온다. 없으면 빈 목록."""
    for block in article.blocks:
        if block.kind == KIND_CALLOUT and block.style == "summary":
            return _limit_chars(
                [line.strip() for line in block.text.split("\n") if line.strip()], MAX_VERDICT_CHARS
            )
    return []


def _limit_chars(paragraphs: list[str], limit: int) -> list[str]:
    kept: list[str] = []
    used = 0
    for paragraph in paragraphs:
        if used + len(paragraph) <= limit:
            kept.append(paragraph)
            used += len(paragraph)
            continue
        room = paragraph[: limit - used]
        cut = max(room.rfind(". "), room.rfind("다."), room.rfind("? "), room.rfind("! "))
        if cut > 0:
            kept.append(room[: cut + 2].strip())
        break
    return kept


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
        self.section = _load(bold, 19)
        self.item = _load(bold, 17)
        self.body = _load(regular, 15)
        self.badge = _load(bold, 14)
        self.pill = _load(bold, 13)
        self.verdict = _load(regular, 16)


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
    try:
        return ImageFont.truetype("AppleSDGothicNeo.ttc", size * SCALE)
    except OSError:
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
    """초록 배너. '한눈에 보기' 딱지 아래에 상품명을 두 줄까지만 둔다."""
    lines = _wrap(title, fonts.head, inner - 200, max_lines=2)
    pill_h = 26
    height = 26 + pill_h + 12 + len(lines) * _line_height(fonts.head) + 22

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        _round(draw, PAD, top, inner, height, ACCENT)
        # 오른쪽에 겹친 원을 깔아 배너가 밋밋하지 않게 한다.
        right = PAD + inner
        _circle(draw, right - 70, top + height // 2, 58, ACCENT_DARK)
        _circle(draw, right - 150, top + 30, 18, ACCENT_DARK)
        _circle(draw, right - 70, top + height // 2, 30, "#ffffff")
        _check(draw, right - 82, top + height // 2 - 10, ACCENT, size=2.2)

        label = "한눈에 보기"
        pill_w = int(draw.textlength(label, font=fonts.pill) / SCALE) + 28
        _round(draw, PAD + 26, top + 26, pill_w, pill_h, "#ffffff", radius=13)
        _text(draw, PAD + 40, top + 30, label, fonts.pill, ACCENT_DARK)

        y = top + 26 + pill_h + 12
        for line in lines:
            _text(draw, PAD + 26, y, line, fonts.head, "#ffffff")
            y += _line_height(fonts.head)

    return _Block(height, paint)


def _features_block(features: list[tuple[str, str]], label: str, fonts: _Fonts, inner: int) -> _Block:
    """특징을 아이콘 타일 격자로 놓는다. 세 개면 한 줄, 그 외엔 두 칸이다."""
    cols = 3 if len(features) == 3 else min(2, len(features))
    gutter = 12
    tile_w = (inner - gutter * (cols - 1)) // cols
    icon = 44
    name_w = tile_w - 18 - icon - 12 - 18
    text_w = tile_w - 36

    tiles = [
        (
            _wrap(name, fonts.item, name_w, max_lines=2),
            _wrap(benefit, fonts.body, text_w, max_lines=2) if benefit else [],
        )
        for name, benefit in features
    ]
    tile_h = max(
        18 + max(icon, len(n) * _line_height(fonts.item)) + (10 + len(b) * _line_height(fonts.body) if b else 0) + 18
        for n, b in tiles
    )
    rows = math.ceil(len(tiles) / cols)
    head_h = _section_head_height(fonts)
    height = head_h + rows * tile_h + (rows - 1) * gutter

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        _section_head(draw, PAD, top, label, fonts, INK, ACCENT)
        for index, (name_lines, benefit_lines) in enumerate(tiles):
            row, col = divmod(index, cols)
            x = PAD + col * (tile_w + gutter)
            y = top + head_h + row * (tile_h + gutter)
            _round(draw, x, y, tile_w, tile_h, CARD_BG, outline=LINE)
            _circle(draw, x + 18 + icon // 2, y + 18 + icon // 2, icon // 2, ACCENT_SOFT)
            ICONS[index % len(ICONS)](draw, x + 18 + icon // 2, y + 18 + icon // 2, ACCENT)
            name_h = len(name_lines) * _line_height(fonts.item)
            ty = y + 18 + max(0, (icon - name_h) // 2)
            for line in name_lines:
                _text(draw, x + 18 + icon + 12, ty, line, fonts.item, INK)
                ty += _line_height(fonts.item)
            if benefit_lines:
                ty = y + 18 + max(icon, name_h) + 10
                for line in benefit_lines:
                    _text(draw, x + 18, ty, line, fonts.body, MUTED)
                    ty += _line_height(fonts.body)

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

    wrapped = [[_wrap(item, fonts.body, col_w - 62, max_lines=2) for item in items] for _, items, *_ in columns]
    badge = 34
    head_h = 20 + badge + 14
    col_heights = [
        head_h + sum(len(lines) * _line_height(fonts.body) + 10 for lines in col) + 12
        for col in wrapped
    ]
    height = max(col_heights)

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        for i, (label, _, ink, bg, mark) in enumerate(columns):
            x = PAD + i * (col_w + gutter)
            _round(draw, x, top, col_w, height, bg)
            # 진한 원 안에 흰 체크/엑스를 넣어 좌우 대비가 멀리서도 보이게 한다.
            cx, cy = x + 22 + badge // 2, top + 20 + badge // 2
            _circle(draw, cx, cy, badge // 2, ink)
            mark(draw, cx - 8, cy - 7, "#ffffff", size=1.4)
            _text(draw, x + 22 + badge + 12, top + 20 + (badge - _line_height(fonts.item)) // 2 + 2, label, fonts.item, ink)
            y = top + head_h
            for lines in wrapped[i]:
                mark(draw, x + 26, y + 5, ink)
                for line in lines:
                    _text(draw, x + 48, y, line, fonts.body, INK)
                    y += _line_height(fonts.body)
                y += 10

    return _Block(height, paint)


def _summary_block(summary: list[str], fonts: _Fonts, inner: int) -> _Block:
    """총평. 번호 없이 문단 그대로 넣는다."""
    side = 26
    wrapped = [_wrap(paragraph, fonts.verdict, inner - side * 2) for paragraph in summary]
    head_h = 22 + _line_height(fonts.section) + 12
    para_gap = 10
    body_h = sum(len(lines) * _line_height(fonts.verdict) for lines in wrapped) + para_gap * (len(wrapped) - 1)
    height = head_h + body_h + 22

    def paint(draw: ImageDraw.ImageDraw, top: int) -> None:
        _round(draw, PAD, top, inner, height, SUMMARY_BG)
        _pin(draw, PAD + side, top + 24, SUMMARY_INK)
        _text(draw, PAD + side + 24, top + 22, "총평", fonts.section, SUMMARY_INK)
        y = top + head_h
        for lines in wrapped:
            for line in lines:
                _text(draw, PAD + side, y, line, fonts.verdict, INK)
                y += _line_height(fonts.verdict)
            y += para_gap

    return _Block(height, paint)


def _section_head_height(fonts: _Fonts) -> int:
    return _line_height(fonts.section) + 12


def _section_head(draw: ImageDraw.ImageDraw, x: int, top: int, label: str, fonts: _Fonts, ink: str, bar: str) -> None:
    """카드 밖에 놓는 소제목. 왼쪽 세로 띠는 본문 h2 와 같은 인상을 준다."""
    _round(draw, x, top + 3, 5, _line_height(fonts.section) - 8, bar, radius=2)
    _text(draw, x + 14, top, label, fonts.section, ink)


# ------------------------------------------------------------------ 그리기 도구


def _round(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    w: int,
    h: int,
    color: str,
    *,
    radius: int = RADIUS,
    outline: str | None = None,
) -> None:
    draw.rounded_rectangle(
        [x * SCALE, y * SCALE, (x + w) * SCALE - 1, (y + h) * SCALE - 1],
        radius=radius * SCALE,
        fill=color,
        outline=outline,
        width=SCALE if outline else 0,
    )


def _circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, color: str) -> None:
    draw.ellipse([(cx - r) * SCALE, (cy - r) * SCALE, (cx + r) * SCALE, (cy + r) * SCALE], fill=color)


def _poly(draw: ImageDraw.ImageDraw, points: list[tuple[float, float]], color: str) -> None:
    draw.polygon([(px * SCALE, py * SCALE) for px, py in points], fill=color)


# 아이콘은 글꼴 글리프가 없어도 나오도록 도형으로 직접 그린다. (cx, cy) 가 중심이다.


def _icon_star(draw: ImageDraw.ImageDraw, cx: float, cy: float, color: str) -> None:
    points = []
    for i in range(10):
        r = 12 if i % 2 == 0 else 5
        angle = math.pi / 2 + i * math.pi / 5
        points.append((cx + r * math.cos(angle), cy - r * math.sin(angle)))
    _poly(draw, points, color)


def _icon_bolt(draw: ImageDraw.ImageDraw, cx: float, cy: float, color: str) -> None:
    _poly(draw, [(cx + 3, cy - 13), (cx - 8, cy + 2), (cx - 1, cy + 2), (cx - 3, cy + 13), (cx + 8, cy - 2), (cx + 1, cy - 2)], color)


def _icon_diamond(draw: ImageDraw.ImageDraw, cx: float, cy: float, color: str) -> None:
    _poly(draw, [(cx - 12, cy - 4), (cx - 6, cy - 10), (cx + 6, cy - 10), (cx + 12, cy - 4), (cx, cy + 12)], color)
    draw.line([((cx - 12) * SCALE, (cy - 4) * SCALE), ((cx + 12) * SCALE, (cy - 4) * SCALE)], fill="#ffffff", width=SCALE)


def _icon_heart(draw: ImageDraw.ImageDraw, cx: float, cy: float, color: str) -> None:
    _circle(draw, cx - 5.5, cy - 4, 6.5, color)
    _circle(draw, cx + 5.5, cy - 4, 6.5, color)
    _poly(draw, [(cx - 11.8, cy - 1.5), (cx + 11.8, cy - 1.5), (cx, cy + 11)], color)


ICONS = [_icon_star, _icon_bolt, _icon_diamond, _icon_heart]


def _pin(draw: ImageDraw.ImageDraw, x: int, y: int, color: str) -> None:
    """총평 제목 옆 핀 모양."""
    _circle(draw, x + 8, y + 7, 7, color)
    _poly(draw, [(x + 2, y + 10), (x + 14, y + 10), (x + 8, y + 20)], color)
    _circle(draw, x + 8, y + 7, 2.5, "#ffffff")


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


def _check(draw: ImageDraw.ImageDraw, x: int, y: int, color: str, *, size: float = 1.0) -> None:
    draw.line(
        [
            (x * SCALE, (y + 5 * size) * SCALE),
            ((x + 4 * size) * SCALE, (y + 9 * size) * SCALE),
            ((x + 11 * size) * SCALE, y * SCALE),
        ],
        fill=color,
        width=round(2 * size * SCALE),
        joint="curve",
    )


def _cross(draw: ImageDraw.ImageDraw, x: int, y: int, color: str, *, size: float = 1.0) -> None:
    for start, end in (((0, 0), (10, 10)), ((10, 0), (0, 10))):
        draw.line(
            [
                ((x + start[0] * size) * SCALE, (y + start[1] * size) * SCALE),
                ((x + end[0] * size) * SCALE, (y + end[1] * size) * SCALE),
            ],
            fill=color,
            width=round(2 * size * SCALE),
        )


def _line_height(font) -> int:
    return int(font.size / SCALE * 1.5)


def _wrap(text: str, font, max_width: int, *, max_lines: int = 0) -> list[str]:
    """픽셀 너비로 줄을 나눈다. max_lines 를 넘기면 마지막 줄 끝을 말줄임표로 자른다.

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
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > limit:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines
