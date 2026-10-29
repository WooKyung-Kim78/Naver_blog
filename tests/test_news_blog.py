from __future__ import annotations

import requests

from news_blog import NEWS_API_URL, NO_SHOP_KEYS_NOTE, SHOPPING_API_URL, fetch_news_digest


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, news, shops):
        self.news = news
        self.shops = shops
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        self.calls.append((url, headers, params, timeout))
        if url == NEWS_API_URL:
            return FakeResponse(self.news)
        return FakeResponse(self.shops.get(params["query"], {"items": []}))


def test_fetch_news_digest_extracts_issues_and_ranks_products():
    articles = [
        {
            "title": f"<b>AI 반도체</b> 시장 확대 {index}",
            "description": f"AI 반도체 수요와 데이터센터 투자가 {index}분기 증가했다.",
            "pubDate": "Tue, 27 Oct 2026 10:00:00 +0900",
            "originallink": f"https://news.example/{index}",
            "link": f"https://n.news.naver.com/{index}",
        }
        for index in range(12)
    ]
    shops = {
        "ai": {
            "items": [
                {
                    "productId": "p1",
                    "title": "<b>AI</b> 반도체 개발 키트",
                    "link": "https://shop.example/p1",
                    "lprice": "25000",
                    "mallName": "테스트몰",
                },
                {
                    "productId": "p2",
                    "title": "AI 입문서",
                    "link": "https://shop.example/p2",
                    "lprice": "18000",
                    "mallName": "서점",
                },
            ]
        },
        "반도체": {
            "items": [
                {
                    "productId": "p1",
                    "title": "AI 반도체 개발 키트",
                    "link": "https://shop.example/p1",
                    "lprice": "25000",
                    "mallName": "테스트몰",
                },
                {
                    "productId": "p3",
                    "title": "반도체 산업 보고서",
                    "link": "https://shop.example/p3",
                    "lprice": "12000",
                    "mallName": "서점",
                },
            ]
        },
    }
    session = FakeSession({"items": articles}, shops)

    digest = fetch_news_digest(
        "IT",
        client_id="hub-id",
        client_secret="hub-secret",
        shop_client_id="shop-id",
        shop_client_secret="shop-secret",
        session=session,
    )

    assert len(digest.articles) == 10
    assert digest.articles[0].title == "AI 반도체 시장 확대 0"
    assert digest.articles[0].source_url == "https://news.example/0"
    assert digest.keywords[:2] == ["ai", "반도체"]
    assert digest.issues[0].article_titles
    assert "주요 키워드" in digest.summary
    assert [product.purchase_url for product in digest.recommendations] == [
        "https://shop.example/p1",
        "https://shop.example/p2",
        "https://shop.example/p3",
    ]
    assert digest.recommendation_note == ""

    news_call, *shop_calls = session.calls
    assert news_call[0] == NEWS_API_URL
    assert news_call[1] == {"X-NCP-APIGW-API-KEY-ID": "hub-id", "X-NCP-APIGW-API-KEY": "hub-secret"}
    assert news_call[2]["display"] == 30 and news_call[2]["sort"] == "date"
    assert shop_calls and all(call[0] == SHOPPING_API_URL for call in shop_calls)
    assert all(call[2]["sort"] == "sim" for call in shop_calls)
    assert shop_calls[0][1] == {"X-Naver-Client-Id": "shop-id", "X-Naver-Client-Secret": "shop-secret"}


def _one_article():
    return {"items": [{"title": "AI 반도체", "description": "AI 반도체 수요", "link": "https://n.news/1"}]}


def test_without_shop_keys_returns_news_and_skips_products(monkeypatch):
    monkeypatch.delenv("NAVER_SHOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_SHOP_CLIENT_SECRET", raising=False)
    session = FakeSession(_one_article(), {})

    digest = fetch_news_digest("IT", client_id="hub-id", client_secret="hub-secret", session=session)

    assert digest.articles and digest.keywords
    assert digest.recommendations == []
    assert digest.recommendation_note == NO_SHOP_KEYS_NOTE
    assert [call[0] for call in session.calls] == [NEWS_API_URL]


def test_shop_failure_keeps_news_digest():
    class FailingShopSession(FakeSession):
        def get(self, url, *, headers, params, timeout):
            if url == SHOPPING_API_URL:
                raise requests.ConnectionError("down")
            return super().get(url, headers=headers, params=params, timeout=timeout)

    digest = fetch_news_digest(
        "IT", client_id="a", client_secret="b", shop_client_id="c", shop_client_secret="d",
        session=FailingShopSession(_one_article(), {}),
    )

    assert digest.articles
    assert digest.recommendations == []
    assert "추천 상품을 가져오지 못했습니다" in digest.recommendation_note


def _news(title, description, n=0):
    return {"title": title, "description": description, "link": f"https://n.news/{title}{n}"}


def test_keywords_skip_english_noise_query_josa_and_duplicates(monkeypatch):
    monkeypatch.delenv("NAVER_SHOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("NAVER_SHOP_CLIENT_SECRET", raising=False)
    items = [
        # 검색어 "IT" 가 영어 "it" 으로 걸린 영문 기사
        _news("It said the market is weak", "It said that the market is weak and the pound fell."),
        # 한글 제목 + 영문 번역 본문
        _news("진천군 행사 개최", "It is assumed that there may be errors in the English translation."),
        _news("로봇 시장이 커진다", "로봇과 AI가 27일 공개됐다. IT 업계는 로봇을 주목한다."),
        _news("삼성, 로봇 투자 확대", "삼성이 로봇에 투자한다. 반도체와 로봇을 함께 키운다."),
        # 같은 본문의 포토 기사가 제목만 바꿔 여러 번 나온 경우
        _news("[HD포토] 배우 A, 화이트 팬츠", "여유롭게 떨어지는 화이트 팬츠에 실버 벨트로 포인트를 줬다.", 1),
        _news("[HD포토] 배우 A, 꾸안꾸 비주얼", "여유롭게 떨어지는 화이트 팬츠에 실버 벨트로 포인트를 줬다.", 2),
    ]
    digest = fetch_news_digest(
        "IT", client_id="a", client_secret="b", session=FakeSession({"items": items}, {})
    )

    titles = [a.title for a in digest.articles]
    assert "It said the market is weak" not in titles
    assert titles.count("[HD포토] 배우 A, 화이트 팬츠") + titles.count("[HD포토] 배우 A, 꾸안꾸 비주얼") == 1
    assert digest.keywords[0] == "로봇"
    for noise in ("it", "the", "english", "translation", "27일", "로봇을", "로봇과", "hd포토"):
        assert noise not in digest.keywords
    assert digest.issues[0].keyword == "로봇"
    assert len(digest.issues[0].article_titles) == 2


def test_issue_with_same_articles_is_merged():
    from news_blog import NewsArticle, summarize_issues

    articles = [
        NewsArticle("킥플립 민제 비주얼", "", "", "", ""),
        NewsArticle("로봇 시장 확대", "", "", "", ""),
    ]
    issues = summarize_issues(articles, ["킥플립", "민제", "로봇"])
    assert [issue.keyword for issue in issues] == ["킥플립", "로봇"]
