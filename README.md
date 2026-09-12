# 네이버 블로그 자동 포스팅

상품 페이지 URL 을 넣으면 Bayer myGenAssist AI 가 트렌드를 조사해 홍보 컨셉을 제안하고,
고른 컨셉으로 블로그 원고와 이미지를 만들어 네이버 블로그에 올려주는 도구입니다.

## 동작 흐름

1. 상품 페이지를 읽어 상품명, 가격, 설명, 이미지를 수집한다.
2. AI 가 웹 검색으로 현재 트렌드를 조사한 뒤 홍보 컨셉을 5개 제안한다.
3. 사용자가 컨셉을 하나 고른다.
4. AI 가 본문을 쓰고, 문단별 분위기에 맞는 무료 스톡 사진을 자동으로 내려받는다.
5. 원고를 확인한 뒤 네이버 블로그에 임시저장하거나 발행한다.

## 설치

```powershell
cd c:\Temp\blog
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

`.env` 를 열어 아래 값을 채웁니다.

| 항목 | 발급처 |
| --- | --- |
| `AI_API_KEY` | myGenAssist 웹 > 설정 > API Key |
| `PEXELS_API_KEY` | https://www.pexels.com/api/ (무료) |
| `NAVER_ID` / `NAVER_PW` / `NAVER_BLOG_ID` | 본인 네이버 계정 |

설정이 끝나면 점검합니다.

```powershell
python main.py doctor
```

모든 항목이 초록색이면 준비 완료입니다.

## 사용

```powershell
# 원고와 이미지만 만들어 보기 (네이버에 올리지 않음)
python main.py post --url "https://상품페이지주소" --dry-run

# 실제 포스팅
python main.py post --url "https://상품페이지주소"

# 쿠팡처럼 크롤링이 막히는 사이트는 설명을 직접 넘긴다
python main.py post --url "https://..." --desc "상품 설명 내용"

# 에디터 조작이 실패할 때 스크린샷을 남긴다
python main.py post --url "https://..." --debug
```

생성된 원고와 이미지는 `output/날짜_제목/` 에 저장되므로, 발행에 실패해도 내용은 남습니다.

## 알아둘 점

**처음에는 `POST_MODE=draft` 로 쓰세요.** 임시저장만 하고 실제 발행은 네이버에서 직접
눈으로 확인한 뒤 누르는 방식입니다. 몇 번 돌려보고 결과가 만족스러우면 `publish` 로 바꾸세요.

**대가성 문구는 지우지 마세요.** 브랜드커넥트처럼 대가를 받고 쓰는 글에 이 문구가 없으면
표시광고법 위반입니다. `.env` 의 `DISCLOSURE_TEXT` 가 본문 맨 위에 자동으로 들어갑니다.

**첫 로그인 때는 브라우저 창을 지켜보세요.** 캡차나 2단계 인증이 뜨면 프로그램이 멈추고
직접 로그인하라고 안내합니다. 한 번 로그인하면 세션이 `storage/naver_session.json` 에
저장되어 다음부터는 자동으로 넘어갑니다.

**주 1회 정도가 적당합니다.** 매일 자동 포스팅을 하면 네이버 어뷰징 필터에 걸려 저품질
블로그로 분류될 수 있습니다.

## 사내망에서 생길 수 있는 문제

**이미지 다운로드가 SSL 오류로 실패할 때.** 사내 SSL 검사 장비가 외부 HTTPS 통신을
자체 서명 인증서로 가로채기 때문입니다. `truststore` 패키지가 파이썬이 윈도우 인증서
저장소를 쓰도록 바꿔서 해결합니다. `requirements.txt` 에 포함되어 있으니 설치만 하면
됩니다. 검증을 끄는 방식(`AI_VERIFY_SSL=false`)은 쓰지 마세요.

**AI 응답이 비어서 온다면** 토큰 한도가 부족한 것입니다. myGenAssist 게이트웨이는
`gpt-4o` 를 요청해도 `gpt-5-mini` 같은 추론 모델로 라우팅할 수 있는데, 이런 모델은
주어진 토큰을 추론에 먼저 씁니다. 한도가 작으면 추론만 하다 끝나 본문이 빈 채로
돌아옵니다. 이 경우 프로그램이 몇 개 토큰이 추론에 쓰였는지 알려주므로,
`ai_client.py` 의 `max_tokens` 기본값을 올리면 됩니다.

## 에디터 선택자가 바뀌었을 때

네이버 스마트에디터는 CSS 클래스명을 수시로 바꿉니다. "제목 입력란을 찾지 못했습니다"
같은 오류가 나면 `--debug` 로 실행해 `storage/screenshots/` 의 화면을 확인하고,
`naver_blog.py` 위쪽의 `TITLE_SELECTORS`, `BODY_SELECTORS` 등 목록에 새 선택자를
추가하면 됩니다.

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `main.py` | CLI 진입점, 전체 워크플로우 |
| `config.py` | `.env` 설정 로딩 |
| `ai_client.py` | myGenAssist API 호출 (`/chat/agent`, `/responses`) |
| `product.py` | 상품 페이지 크롤링 |
| `copywriter.py` | 컨셉 제안 및 본문 작성 프롬프트 |
| `images.py` | Pexels / Unsplash 스톡 이미지 |
| `naver_blog.py` | Playwright 로 네이버 블로그 조작 |
| `docs/v3-api.yaml` | myGenAssist API 스펙 원본 |
