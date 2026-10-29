"""네이버 최신 뉴스 요약과 연관 쇼핑 상품 검색.

뉴스는 NAVER API HUB(네이버 클라우드)로 호출한다. API HUB 에는 쇼핑 상품 검색이
없어서, 추천 상품은 NAVER Developers 쇼핑 검색 키가 있을 때만 가져온다.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import requests

import config

NEWS_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
SHOPPING_API_URL = "https://openapi.naver.com/v1/search/shop.json"
NO_SHOP_KEYS_NOTE = (
    "NAVER API HUB 에는 쇼핑 상품 검색이 없어 추천 상품을 건너뜁니다. "
    "developers.naver.com 에서 검색 API 를 등록하고 NAVER_SHOP_CLIENT_ID / "
    "NAVER_SHOP_CLIENT_SECRET 을 .env 에 넣으면 추천 상품도 가져옵니다."
)
DEFAULT_NEWS_COUNT = 10
#: 영문 기사를 걸러낸 뒤에도 10건이 남도록 넉넉히 받는다.
NEWS_FETCH_COUNT = 30
MAX_ISSUES = 5
MAX_SHOPPING_KEYWORDS = 5

_WORD_RE = re.compile(r"[가-힣A-Za-z0-9]+")
_HANGUL_RE = re.compile(r"[가-힣]")
#: "27일", "2026년", "610만명" 처럼 숫자로 시작하는 날짜·수량 표현.
_NUMBERISH_RE = re.compile(r"^\d+[가-힣]{0,2}$")
_STOPWORDS = {
    "그리고", "그러나", "관련", "대한", "통해", "위해", "오늘", "이번", "최근",
    "지난", "새로운", "기자", "뉴스", "사진", "영상", "속보", "단독", "종합",
    "밝혔다", "전했다", "말했다", "나섰다", "한다", "했다", "있는", "있다",
    "에서", "으로", "까지", "것으로", "한다는", "hd포토", "포토", "포토월",
    "것을", "있도록", "위한", "따라", "대해", "함께", "이후", "가운데", "예정",
    "진행", "이라고", "있다고", "했다고", "밝혀", "이날", "올해", "지난해",
    "등을", "등이", "등의", "등과",
    # 한글 제목에 영문 번역 본문이 붙은 기사가 있어 영어 기능어도 뺀다.
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "that", "this", "there", "these", "those", "has", "have", "had",
    "will", "would", "may", "can", "could", "said", "says", "also", "not",
}


class NewsApiError(RuntimeError):
    """네이버 뉴스·쇼핑 검색 요청이 실패했을 때 발생."""


@dataclass(frozen=True)
class NewsArticle:
    title: str
    description: str
    published_at: str
    source_url: str
    naver_url: str


@dataclass(frozen=True)
class NewsIssue:
    keyword: str
    summary: str
    article_titles: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductRecommendation:
    title: str
    purchase_url: str
    price: str
    mall_name: str
    matched_keywords: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NewsDigest:
    category: str
    articles: list[NewsArticle]
    keywords: list[str]
    issues: list[NewsIssue]
    recommendations: list[ProductRecommendation]
    #: 추천 상품을 못 가져온 이유. 가져왔으면 빈 문자열.
    recommendation_note: str = ""

    @property
    def summary(self) -> str:
        """기사 원문에 근거한 키워드·이슈 요약."""
        keyword_text = ", ".join(self.keywords) or "추출된 키워드 없음"
        issue_text = " / ".join(issue.summary for issue in self.issues) or "뚜렷한 이슈 없음"
        return f"주요 키워드: {keyword_text}\n주요 이슈: {issue_text}"


def fetch_news_digest(
    category: str,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    shop_client_id: str | None = None,
    shop_client_secret: str | None = None,
    session: Any | None = None,
) -> NewsDigest:
    """분야별 최신 뉴스 10건을 요약하고 관련 네이버 쇼핑 상품 TOP 3을 반환한다.

    키워드와 이슈는 기사 제목·요약을 이용해 규칙 기반으로 추출한다. AI 생성 요약이
    아니므로 원문에 없는 사실을 추가하지 않는다.

    뉴스 키는 NAVER API HUB 의 ``NAVER_CLIENT_ID`` / ``NAVER_CLIENT_SECRET``,
    쇼핑 키는 NAVER Developers 의 ``NAVER_SHOP_CLIENT_ID`` /
    ``NAVER_SHOP_CLIENT_SECRET`` 이다. 쇼핑 키가 없거나 쇼핑 호출이 실패해도
    뉴스 요약은 돌려주고, 이유를 ``recommendation_note`` 에 남긴다.
    """
    query = category.strip()
    if not query:
        raise ValueError("검색할 분야(category)를 입력하세요.")

    settings = config.load_naver_search_config()
    api_client_id = (client_id or settings.client_id).strip()
    api_client_secret = (client_secret or settings.client_secret).strip()
    if not api_client_id or not api_client_secret:
        raise config.ConfigError(
            "NAVER_CLIENT_ID 와 NAVER_CLIENT_SECRET 에 NAVER API HUB 키를 .env 에 설정하세요."
        )

    http = session if session is not None else requests.Session()
    news_data = _get_json(
        http,
        NEWS_API_URL,
        _api_hub_headers(api_client_id, api_client_secret),
        {"query": query, "display": NEWS_FETCH_COUNT, "sort": "date"},
    )
    articles = [
        NewsArticle(
            title=_clean_text(item.get("title", "")),
            description=_clean_text(item.get("description", "")),
            published_at=str(item.get("pubDate", "")).strip(),
            source_url=str(item.get("originallink") or item.get("link") or "").strip(),
            naver_url=str(item.get("link") or "").strip(),
        )
        for item in news_data.get("items", [])
        if isinstance(item, dict)
    ]
    # "IT" 같은 영문 검색어는 영어 단어 "it" 으로도 걸려 해외 영문 기사가 섞인다.
    korean = [article for article in articles if _HANGUL_RE.search(article.title)]
    articles = _dedupe((korean or articles))[:DEFAULT_NEWS_COUNT]
    keywords = extract_keywords(articles, exclude=[_strip_josa(t) for t in _tokens(query)])
    issues = summarize_issues(articles, keywords)

    shop_id = (shop_client_id or settings.shop_client_id).strip()
    shop_secret = (shop_client_secret or settings.shop_client_secret).strip()
    recommendations: list[ProductRecommendation] = []
    note = ""
    if not (shop_id and shop_secret):
        note = NO_SHOP_KEYS_NOTE
    elif keywords:
        try:
            recommendations = fetch_recommendations(
                keywords, client_id=shop_id, client_secret=shop_secret, session=http
            )
        except NewsApiError as exc:
            note = f"추천 상품을 가져오지 못했습니다: {exc}"
        if not recommendations and not note:
            note = "이슈 키워드와 맞는 쇼핑 상품을 찾지 못했습니다."
    return NewsDigest(query, articles, keywords, issues, recommendations, note)


def extract_keywords(
    articles: list[NewsArticle],
    *,
    limit: int = 10,
    exclude: list[str] | None = None,
) -> list[str]:
    """기사 제목·요약에서 여러 기사에 걸쳐 나온 말을 주요 키워드로 뽑는다.

    한 기사가 같은 말을 여러 번 써도 한 번만 센다(문서 빈도). 그래야 본문이 긴
    기사 하나가 키워드를 독차지하지 않는다. 제목에 나온 말은 두 배로 친다.
    exclude 는 검색어처럼 뺄 말이다.
    """
    skip = _STOPWORDS | set(exclude or [])
    score: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for article in articles:
        title_words = _keyword_tokens(article.title, skip)
        body_words = _keyword_tokens(_body_text(article), skip)
        for word in dict.fromkeys(title_words + body_words):
            score[word] += 2 if word in title_words else 1
        total.update(title_words + body_words)
    ranked = sorted(score, key=lambda word: (-score[word], -total[word]))
    return ranked[:limit]


def summarize_issues(
    articles: list[NewsArticle],
    keywords: list[str],
    *,
    limit: int = MAX_ISSUES,
) -> list[NewsIssue]:
    """키워드별로 그 말이 나온 기사 제목을 묶어 이슈를 만든다.

    이미 만든 이슈와 같은 기사들만 가리키는 키워드는 같은 이슈로 보고 건너뛴다.
    """
    issues: list[NewsIssue] = []
    seen: list[set[int]] = []
    for keyword in keywords:
        matching = [
            index for index, article in enumerate(articles)
            if keyword in _norm_tokens(_article_text(article))
        ]
        if not matching or any(set(matching) <= covered for covered in seen):
            continue
        seen.append(set(matching))
        titles = [articles[index].title for index in matching]
        issues.append(
            NewsIssue(
                keyword=keyword,
                summary=f"{keyword} ({len(titles)}건): {' / '.join(titles[:2])}",
                article_titles=titles,
            )
        )
        if len(issues) >= limit:
            break
    return issues


def fetch_recommendations(
    keywords: list[str],
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    session: Any | None = None,
    limit: int = 3,
) -> list[ProductRecommendation]:
    """주요 키워드별 쇼핑 검색 결과를 결합해 관련도 높은 상품을 반환한다.

    NAVER Developers 쇼핑 검색 키(``NAVER_SHOP_CLIENT_ID`` /
    ``NAVER_SHOP_CLIENT_SECRET``)를 쓴다. NAVER API HUB 키로는 호출되지 않는다.
    """
    if limit < 1:
        raise ValueError("상품 결과 개수(limit)는 1 이상이어야 합니다.")
    settings = config.load_naver_search_config()
    api_client_id = (client_id or settings.shop_client_id).strip()
    api_client_secret = (client_secret or settings.shop_client_secret).strip()
    if not api_client_id or not api_client_secret:
        raise config.ConfigError(NO_SHOP_KEYS_NOTE)
    headers = {
        "X-Naver-Client-Id": api_client_id,
        "X-Naver-Client-Secret": api_client_secret,
    }
    http = session if session is not None else requests.Session()
    candidates: dict[str, tuple[float, dict[str, Any], list[str]]] = {}
    for keyword in keywords[:MAX_SHOPPING_KEYWORDS]:
        data = _get_json(
            http,
            SHOPPING_API_URL,
            headers,
            {"query": keyword, "display": 50, "sort": "sim"},
        )
        for position, item in enumerate(data.get("items", []), 1):
            if not isinstance(item, dict):
                continue
            product_id = str(item.get("productId") or item.get("link") or "")
            if not product_id:
                continue
            title = _clean_text(item.get("title", ""))
            item_tokens = set(_norm_tokens(title))
            matched = [term for term in keywords if term in item_tokens]
            # API의 유사도 정렬 순위를 반영하면서 이슈 키워드와의 일치도를 우선한다.
            score = 2.0 * len(matched) + 1.0 / position
            previous = candidates.get(product_id)
            if previous:
                matched = list(dict.fromkeys(previous[2] + matched))
                score += previous[0]
            candidates[product_id] = (score, item, matched)

    ranked = sorted(candidates.values(), key=lambda candidate: candidate[0], reverse=True)
    return [
        ProductRecommendation(
            title=_clean_text(item.get("title", "")),
            purchase_url=str(item.get("link") or "").strip(),
            price=str(item.get("lprice") or "").strip(),
            mall_name=_clean_text(item.get("mallName", "")),
            matched_keywords=matched,
        )
        for _, item, matched in ranked[:limit]
        if item.get("link")
    ]


def _api_hub_headers(client_id: str, client_secret: str) -> dict[str, str]:
    return {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
    }


def _get_json(
    session: Any,
    url: str,
    headers: dict[str, str],
    params: dict[str, str | int],
) -> dict[str, Any]:
    try:
        response = session.get(url, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as exc:
        body = (exc.response.text if exc.response is not None else "")[:200]
        raise NewsApiError(f"네이버 검색 API 요청 실패 ({url}): {exc} {body}".strip()) from exc
    except requests.RequestException as exc:
        raise NewsApiError(f"네이버 검색 API 요청 실패 ({url}): {exc}") from exc
    except ValueError as exc:
        raise NewsApiError("네이버 검색 API가 올바른 JSON을 반환하지 않았습니다.") from exc
    if not isinstance(payload, dict):
        raise NewsApiError("네이버 검색 API 응답 형식이 올바르지 않습니다.")
    return payload


def _clean_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", text)).strip()


def _tokens(text: str) -> list[str]:
    return [word.casefold() for word in _WORD_RE.findall(_clean_text(text))]


#: 떼어도 명사가 망가질 일이 거의 없는 조사만 둔다. "이·가·의·로·도" 는
#: "어린이·전문가·민주주의·레트로·반도체" 처럼 명사 끝과 겹쳐서 넣지 않았다.
_JOSA = (
    "이라는", "에서는", "으로는", "에서", "에게", "으로", "까지", "부터", "처럼",
    "보다", "라는", "은", "는", "을", "를", "과", "와", "에",
)


def _strip_josa(word: str) -> str:
    if not _HANGUL_RE.search(word[-1:]):
        return word
    for josa in _JOSA:
        if word.endswith(josa) and len(word) - len(josa) >= 2:
            return word[: -len(josa)]
    return word


def _norm_tokens(text: str) -> list[str]:
    return [_strip_josa(word) for word in _tokens(text)]


def _keyword_tokens(text: str, skip: set[str]) -> list[str]:
    return [
        word for word in _norm_tokens(text)
        if len(word) > 1 and word not in skip and not _NUMBERISH_RE.match(word)
    ]


def _body_text(article: NewsArticle) -> str:
    # 한글 제목에 영문 번역 본문이 붙은 기사는 본문의 영어 상투구가 키워드로 잡힌다.
    return article.description if _HANGUL_RE.search(article.description) else ""


def _article_text(article: NewsArticle) -> str:
    return f"{article.title} {_body_text(article)}"


def _dedupe(articles: list[NewsArticle]) -> list[NewsArticle]:
    """같은 본문을 제목만 바꿔 여러 번 낸 기사(포토 기사 등)는 하나만 남긴다."""
    seen: set[str] = set()
    unique = []
    for article in articles:
        key = re.sub(r"\s+", "", article.description)[:60] or article.title
        if key in seen:
            continue
        seen.add(key)
        unique.append(article)
    return unique
