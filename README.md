# 전환배너 고효율 광고 아카이브

광고명에 `F_I`가 포함된 메타 광고 중 **S/A/B급 고효율 광고**를 자동으로 수집해 갤러리 형식으로 보여주는 도구입니다.  
매일 오전 9시(KST)에 GitHub Actions가 자동으로 실행되어 `index.html`을 갱신합니다.

---

## 성과 등급 기준

| 등급 | 일 평균 광고비 (D+7) | CPC |
|------|------------------|-----|
| S급  | 500만원 이상      | 600원대 목표 |
| A급  | 300만원 이상      | 600원대 목표 |
| B급  | 100만원 이상      | 600원대 목표 |

---

## 세팅 방법

### 1단계 — 저장소 생성

1. GitHub에서 **New repository** 클릭
2. 저장소 이름 입력 (예: `ad-archive`)
3. **Public** 선택 (GitHub Pages 무료 사용을 위해)
4. 이 파일들을 모두 업로드:
   - `fetch_ads.py`
   - `.github/workflows/daily.yml`
   - `README.md`

### 2단계 — Secret 등록

GitHub 저장소 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 이름          | 값 |
|---------------------|----|
| `META_ACCESS_TOKEN`  | 메타 비즈니스 액세스 토큰 |
| `META_AD_ACCOUNT_ID` | 광고 계정 ID (예: `act_123456789`) |

> **액세스 토큰 발급 방법**  
> [Meta for Developers](https://developers.facebook.com/) → My Apps → 앱 선택 → Graph API Explorer  
> → `ads_read` 권한 포함하여 토큰 생성

### 3단계 — GitHub Pages 활성화

저장소 → **Settings → Pages**  
→ Source: **Deploy from a branch**  
→ Branch: `main` / `/ (root)` 선택 후 **Save**

약 1~2분 후 `https://{username}.github.io/{저장소명}/` 에서 확인 가능

### 4단계 — 첫 실행 (수동)

저장소 → **Actions → 광고 아카이브 자동 업데이트 → Run workflow**

기간을 지정하려면 `시작일`과 `종료일`을 입력하고 실행합니다.  
비워두면 **최근 30일** 기준으로 동작합니다.

---

## 조회 기간 변경 방법

`daily.yml`의 cron 표현식을 수정하거나,  
Actions 탭에서 수동으로 `Run workflow` 실행 시 날짜를 입력하면 됩니다.

```
# 예시: 매일 오전 8시 KST 실행
- cron: "0 23 * * *"   # UTC 23:00 = KST 08:00
```

---

## 팀 공유

GitHub Pages URL을 팀원에게 공유하면 됩니다.  
매일 자동 갱신되므로 북마크만 해두면 항상 최신 데이터 확인 가능합니다.
