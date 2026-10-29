# 🎬 Shorts Generator (`shorts/app.py`) 사용 설명서

`shorts/app.py`는 상품 판매 URL(네이버 스마트스토어, 브랜드커넥트 제휴 링크, 알리익스프레스 등)을 입력받아 **유튜브 쇼츠용 세로 영상(1080x1920, 9:16, 20초)**과 **업로드용 문구(제목, 설명, 태그, 고정 댓글)**를 자동으로 생성하고, **네이버 블로그(동영상 글) 및 티스토리**에 자동 포스팅할 수 있는 도구입니다.

---

## 📌 주요 특징

1. **실제 상품 미디어 활용**
   - AI로 가짜 이미지를 생성하지 않고, 상품 상세 페이지의 실제 사진 및 동영상을 수집하여 편집합니다.
   - 부족한 이미지는 로컬 폴더에서 직접 추가할 수 있습니다.
2. **AI 트렌드 키워드 & 대본 기획**
   - 웹 검색을 통해 사용자들이 실제로 검색하는 메인 키워드와 연관 검색어, 해시태그를 발굴합니다.
   - 제품 이미지/영상을 분석하여 시선을 사로잡는 2줄 훅(Hook), 장면 구성, 내레이션 대본을 작성합니다.
3. **완성도 높은 20초 세로 쇼츠 렌더링**
   - 1080x1920 9:16 세로 해상도, 30 FPS, 정확히 20초 분량 맞춤.
   - 사진 서서히 줌인(Push 효과, 93% → 100%) 및 여백 블러(배경 흐림) 처리로 잘림 현상 방지.
   - 상단 `(광고)` 표기, 중앙 2줄 훅(노란색/흰색), 하단 싱크 맞춤 테두리 자막, 엔딩 구매 안내 CTA 자동 삽입.
4. **자연스러운 고품질 음성 (TTS)**
   - `edge-tts`(`ko-KR-SunHiNeural`)를 적용하여 빠르고 또렷한 쇼츠 호흡(+15% 배속) 구현.
5. **멀티 플랫폼 자동 발행 연동**
   - 네이버 블로그: 비디오로그 카테고리에 동영상 본문 첨부, 네이버 쇼핑 커넥트 카드 링크, 검색 태그 10개 등록.
   - 티스토리: 대표 이미지, 소개글, 네이버 동영상 글 링크, 구매 유도 버튼 등록.

---

## 🛠 1. 환경 설정 및 설치

프로젝트 루트 폴더에서 필요한 패키지와 브라우저 드라이버를 설치합니다.

```bash
# 필수 의존성 패키지 설치
pip install "moviepy>=2" edge-tts numpy pillow requests beautifulsoup4 python-dotenv truststore playwright

# Playwright Chromium 브라우저 설치 (크롤링 및 업로드용)
python -m playwright install chromium
```

> ⚠️ **주의**: `moviepy`는 반드시 **2.x 버전 이상**이어야 합니다.

---

## ⚙️ 2. 환경 변수 설정 (`.env`)

블로그 생성기에서 사용하는 기본 `.env` 설정(AI API 키, 네이버/티스토리 로그인 쿠키 등)과 공유하며, 아래와 같은 쇼츠 전용 옵션을 추가할 수 있습니다.

```env
# --- 네이버 & 티스토리 카테고리 ---
NAVER_VIDEO_CATEGORY=비디오로그       # 네이버 블로그 동영상 글을 등록할 카테고리명
TISTORY_VIDEO_CATEGORY=              # 티스토리 카테고리명 (비워둘 시 TISTORY_CATEGORY 기본값 사용)

# --- 공정위 제휴 공표 문구 ---
SHORTS_DISCLOSURE=[광고] 이 영상에는 제휴 링크가 포함되어 있으며, 링크로 구매하면 수수료를 받습니다.

# --- 자막 폰트 및 출력 경로 ---
SHORTS_FONT=C:/Windows/Fonts/malgunbd.ttf   # 자막에 사용할 폰트 (.ttf 또는 .ttc)
SHORTS_OUTPUT_DIR=shorts/output            # 결과물 저장 폴더 (기본: shorts/output)

# --- TTS 설정 ---
SHORTS_TTS_VOICE=ko-KR-SunHiNeural         # TTS 음성 모델
SHORTS_TTS_RATE=+15%                       # 음성 속도 (+15% 권장)
SHORTS_TTS_PITCH=-4Hz                      # 음성 피치

# --- 기본 배경음악 ---
SHORTS_BGM=assets/bgm.mp3                  # 기본 사용할 배경음악 파일 경로
```

### 폰트 기본 탐색 순서
`--font` 옵션을 주지 않거나 환경변수가 없을 경우, OS별 기본 한글 폰트를 자동 탐색합니다:
- **Windows**: `malgunbd.ttf`, `NanumGothicBold.ttf`, `malgun.ttf` 등
- **macOS (Darwin)**: `AppleSDGothicNeo.ttc`, `NanumGothicBold.ttf`, `AppleGothic.ttf` 등
- **Linux**: `NanumGothicBold.ttf`, `NotoSansCJK-Bold.ttc` 등

---

## 🚀 3. 기본 사용법

### 1) 단일 상품 링크로 쇼츠 생성
네이버 스마트스토어, 브랜드커넥트 단축 URL, 알리익스프레스 링크 등을 직접 입력합니다.

```bash
python shorts/app.py "https://naver.me/구매링크"
```

### 2) 단축 링크 크롤링이 안 될 때 (`--page-url`)
구매 제휴 링크가 리다이렉트되거나 크롤링이 원활하지 않은 경우, 실제 상세 페이지 URL을 함께 제공합니다.

```bash
python shorts/app.py "https://naver.me/제휴링크" --page-url "https://smartstore.naver.com/스토어/products/123456"
```

### 3) 알리익스프레스 상품 링크
알리익스프레스 링크는 전용 파서를 통해 제목 및 이미지를 자동 수집합니다.

```bash
python shorts/app.py "https://ko.aliexpress.com/item/1005001234567890.html"
```

---

## 💡 4. 주요 CLI 옵션 및 활용 예시

### 1) 상품명 직접 지정 및 폰트 지정 (`--title`, `--font`)
크롤링된 복잡한 판매자 제목 대신 간결하고 명확한 상품명을 지정하고 글꼴을 변경할 때 사용합니다.

```bash
python shorts/app.py "https://naver.me/구매링크" --title "무선 블루투스 이어폰" --font "/System/Library/Fonts/AppleSDGothicNeo.ttc"
```

### 2) 캡차 대응 브라우저 모드 (`--browser`)
네이버나 알리익스프레스에서 캡차(CAPTCHA)가 발생할 경우, 브라우저 창을 띄워 직접 인증을 풀 수 있습니다.

```bash
python shorts/app.py "https://naver.me/구매링크" --browser
```

### 3) 로컬 사진/영상 직접 활용 (`--images-dir`, `--videos-dir`)
크롤링이 막혔거나 직접 촬영한 제품 사진 및 영상(mp4/mov)을 함께 사용하고자 할 때 사용합니다.

```bash
# 로컬 사진으로만 제작
python shorts/app.py --images-dir ./my_product_photos --title "초경량 등산 배낭"

# 크롤링한 사진 + 로컬 소장 영상 결합
python shorts/app.py "https://naver.me/구매링크" --videos-dir ./my_product_videos
```

### 4) 배경음악(BGM) 추가 (`--bgm`)
내레이션 배경에 깔릴 음악 파일을 지정합니다 (볼륨 12% 자동 조절 및 페이드아웃 적용).

```bash
python shorts/app.py "https://naver.me/구매링크" --bgm ./bgm/upbeat.mp3
```

### 5) AI 비용 절약 및 무음 옵션 (`--no-ai`, `--no-tts`)
- `--no-ai`: AI 키워드 조사와 대본 기획을 건너뛰고, 원본 상품명과 사진 순서 그대로 단순 슬라이드쇼를 제작합니다.
- `--no-tts`: 음성 내레이션 없이 자막만 들어간 영상을 제작합니다.

```bash
python shorts/app.py "https://naver.me/구매링크" --no-ai --no-tts
```

---

## 📤 5. 블로그 자동 업로드 (`--upload`)

쇼츠 영상을 만든 즉시 네이버 블로그 및 티스토리에 동영상 리뷰 글로 발행합니다.

### 1) 생성과 동시에 업로드
```bash
python shorts/app.py "https://naver.me/구매링크" --upload
```
- 네이버 블로그: 지정된 비디오로그 카테고리에 동영상 업로드 + 쇼핑 커넥트 링크 카드 + 검색어 태그 10개 삽입
- 티스토리: 대표 이미지 + 동영상 소개글 + 네이버 글 링크 + 구매 버튼 삽입

### 2) 이미 생성된 쇼츠 폴더만 재업로드 (`--upload-from`)
영상을 다시 인코딩하지 않고, 이전에 완성된 결과 폴더의 내용만 블로그로 발행합니다.

```bash
python shorts/app.py --upload-from shorts/output/20261020_120000_상품폴더
```

### 3) 티스토리 제어 옵션 (`--no-tistory`, `--tistory-only`)
- `--no-tistory`: 네이버 블로그에만 올리고 티스토리는 건너뜁니다.
- `--tistory-only`: (`--upload-from`과 함께 사용) 네이버는 건너뛰고 티스토리에만 올립니다.

```bash
# 네이버 블로그에만 발행
python shorts/app.py "https://naver.me/구매링크" --upload --no-tistory

# 기존 폴더에서 티스토리에만 발행
python shorts/app.py --upload-from shorts/output/20261020_120000_상품폴더 --tistory-only
```

---

## 📦 6. 여러 상품 일괄 순차 처리 (`--each`)

여러 상품 링크를 전달하여 한 번에 하나씩 연속해서 쇼츠를 만듭니다. 중간에 한 상품이 실패해도 다음 상품 작업을 계속 진행합니다.

```bash
python shorts/app.py --each \
  "https://naver.me/링크1" \
  "https://naver.me/링크2" \
  "https://naver.me/링크3" \
  --upload
```

> 💡 **안내**: `--each` 모드에서는 상품마다 고유 속성이 들어가야 하므로 `--title`, `--images-dir`, `--videos-dir` 옵션은 자동으로 비활성화됩니다.

---

## 📁 7. 결과물 폴더 구조

쇼츠 생성이 완료되면 `shorts/output/{YYYYMMDD_HHMMSS}_{상품명}/` 폴더에 다음과 같은 파일들이 생성됩니다:

```text
shorts/output/20261020_210000_무선_블루투스_이어폰/
├── shorts.mp4          # 완성된 세로 쇼츠 영상 (1080x1920, 20초)
├── youtube.txt         # 유튜브 업로드용 메타데이터 (제목, 설명, 태그, 고정 댓글)
├── meta.json           # 전체 파이프라인 메타데이터 및 AI API 비용 내역
├── audio/              # 생성된 장면별 TTS 음성 파일 (*.mp3)
├── images/             # 수집/다운로드된 상품 이미지 파일
└── videos/             # 수집된 상품 영상 및 AI 분석용 콘택트 시트
```

### `youtube.txt` 구성 예시
- **제목**: AI가 추천한 클릭률 높은 유튜브 영상 제목
- **설명란**: 제품 요약 문구, 필수 제휴 공표 문구, 구매 링크, 핵심 해시태그
- **태그**: 유튜브 업로드용 콤마 구분 태그 목록
- **고정 댓글**: 시청자가 바로 클릭할 수 있는 구매 링크/프로모션 코드 안내 문구

---

## ❓ 8. 문제 해결 (Troubleshooting)

| 현상 | 원인 | 해결 방법 |
|---|---|---|
| `moviepy 2.x 가 없습니다` | moviepy 구버전 설치됨 | `pip install "moviepy>=2"` 재설치 |
| 한글 글꼴을 찾지 못함 | 시스템 한글 폰트 미인식 | `--font "/경로/글꼴.ttf"` 지정 또는 `.env`에 `SHORTS_FONT` 설정 |
| 상품 페이지 수집 실패 | 접속 차단 또는 캡차 | `--browser` 옵션으로 수동 인증하거나, `--images-dir`로 사진 직접 지정 |
| 티스토리 하루 발행 한도 | 티스토리 공개 글 15개 제한 | 자동으로 네이버만 업로드되며, 다음 날 `--upload-from ... --tistory-only`로 잔여분 발행 가능 |
| 로그인 세션 만료 | 네이버/티스토리 쿠키 만료 | `storage/naver_state.json` 또는 티스토리 쿠키 재갱신 |
