import os
import json
import time
import requests
from datetime import datetime, timedelta, timezone

# ── 설정 ────────────────────────────────────────────────────────────────────
ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
MONTH_TAG     = os.environ.get("MONTH_TAG", "")
DATE_START    = os.environ.get("DATE_START", "")
DATE_STOP     = os.environ.get("DATE_STOP", "")
MEDIA_MODE    = os.environ.get("MEDIA_MODE", "all")  # image / video / all
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")  # 새 불씨 슬랙 알림 (없으면 알림 생략)
SITE_URL      = "https://kimsj1-web.github.io/ad-archive/"

API_VERSION  = "v19.0"
BASE_URL     = f"https://graph.facebook.com/{API_VERSION}"
ARCHIVE_FILE = "archive.json"
IMAGES_DIR   = "images"  # 이미지/영상 저장 폴더

# ── 성과 등급 기준 (총 광고비 기준) ──────────────────────────────────────────
def get_grade(total_spend, media_type="image"):
    if media_type == "쩜오건":
        # 쩜오건(F_V): 총 광고비 50만원 이상만 수집
        if total_spend >= 500_000:
            return "쩜오건"
        return None
    if media_type == "video":
        # 영상: 성공(1,000만원↑) / 불씨(400만원↑)
        if total_spend >= 10_000_000:
            return "성공"
        elif total_spend >= 4_000_000:
            return "불씨"
        return None
    else:
        # 이미지: SS/S/A/B급
        if total_spend >= 10_000_000:
            return "SS급"
        elif total_spend >= 5_000_000:
            return "S급"
        elif total_spend >= 3_000_000:
            return "A급"
        elif total_spend >= 1_000_000:
            return "B급"
        return None

def get_grade_color(grade):
    return {
        "SS급": "#BF5AF2", "S급": "#FF4B4B", "A급": "#FF9500", "B급": "#34C759",
        "성공": "#007AFF", "불씨": "#FF6B00",
        "쩜오건": "#00C4B4",
    }.get(grade, "#8E8E93")

# ── 일광고비(불씨) 등급 기준 (하루 최고 지출 기준) ────────────────────────────
def get_daily_grade(peak_daily_spend):
    if peak_daily_spend >= 1_500_000:
        return "SS"
    elif peak_daily_spend >= 1_000_000:
        return "S"
    elif peak_daily_spend >= 500_000:
        return "A"
    elif peak_daily_spend >= 300_000:
        return "B"
    return None

def get_daily_grade_color(grade):
    return {"SS": "#BF5AF2", "S": "#FF4B4B", "A": "#FF9500", "B": "#34C759"}.get(grade, "#8E8E93")

DAILY_GRADE_RANK = {"SS": 0, "S": 1, "A": 2, "B": 3}  # 정렬용 (낮을수록 상위 등급)

# ── API 헬퍼 ─────────────────────────────────────────────────────────────────
RATE_LIMIT_CODES    = {4, 17, 32, 613}          # 앱/유저/페이지 레이트 리밋
RATE_LIMIT_SUBCODES = {2446079, 1487742, 1015}  # "User request limit reached" 등

def api_get(url, params, _retries=5):
    """메타 Graph API GET. 레이트 리밋(code 17 등)·일시 오류는 잠시 쉬었다가 자동 재시도."""
    params["access_token"] = ACCESS_TOKEN
    delay_rate = 60   # 레이트 리밋 대기(초), 재시도마다 증가
    delay_tmp  = 5    # 일시 오류 대기(초)
    resp = None
    for attempt in range(_retries + 1):
        try:
            resp = requests.get(url, params=params, timeout=60)
        except requests.exceptions.RequestException as e:
            if attempt < _retries:
                print(f"  ⏳ 네트워크 오류 → {delay_tmp}s 후 재시도 ({attempt+1}/{_retries}): {e}")
                time.sleep(delay_tmp); delay_tmp = min(delay_tmp * 2, 60); continue
            raise
        if resp.status_code == 200:
            return resp.json()

        err = {}
        try:
            err = resp.json().get("error", {})
        except Exception:
            pass
        code, subcode = err.get("code"), err.get("error_subcode")
        rate_limited = (code in RATE_LIMIT_CODES) or (subcode in RATE_LIMIT_SUBCODES)
        transient    = err.get("is_transient", False) or resp.status_code in (500, 502, 503, 504)

        if (rate_limited or transient) and attempt < _retries:
            wait = delay_rate if rate_limited else delay_tmp
            kind = "레이트 리밋" if rate_limited else "일시 오류"
            print(f"  ⏳ {kind}(code {code}) → {wait}s 대기 후 재시도 ({attempt+1}/{_retries})")
            time.sleep(wait)
            if rate_limited:
                delay_rate = min(delay_rate + 60, 300)
            else:
                delay_tmp = min(delay_tmp * 2, 60)
            continue

        print(f"  ⚠️  API 오류: {resp.status_code} | 응답: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json()

    # 재시도 모두 소진
    print(f"  ⚠️  재시도 소진 → 마지막 응답: {resp.text[:300] if resp is not None else 'N/A'}")
    resp.raise_for_status()

# ── 조회 기간 계산 ────────────────────────────────────────────────────────────
def get_time_range():
    if DATE_START and DATE_STOP:
        return DATE_START, DATE_STOP
    if MONTH_TAG:
        yy, mm = MONTH_TAG.split(".")
        year, month = int("20" + yy), int(mm)
        start = datetime(year, month, 1)
        today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        # 항상 해당 월 1일 ~ 오늘까지 (월을 넘겨 집행된 광고도 포함)
        return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")
    today = datetime.today()
    return (today - timedelta(days=7)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

# ── 광고 목록 조회 ────────────────────────────────────────────────────────────
def fetch_ads(month_tag, media_tag, extra_keyword=""):
    """media_tag: 'F_I'(이미지) 또는 'F_V'(영상)
    extra_keyword: 광고명에 추가로 포함되어야 하는 키워드(예: '쩜오건')"""
    url = f"{BASE_URL}/{AD_ACCOUNT_ID}/ads"
    filtering = [
        {"field": "name", "operator": "CONTAIN", "value": media_tag},
        {"field": "name", "operator": "CONTAIN", "value": month_tag},
    ]
    if extra_keyword:
        filtering.append({"field": "name", "operator": "CONTAIN", "value": extra_keyword})
    params = {
        "fields": "id,name,creative{id}",
        "filtering": json.dumps(filtering),
        "limit": 500,
    }
    all_ads = []
    while True:
        data = api_get(url, params)
        all_ads.extend(data.get("data", []))
        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        url, params = next_url, {}
    return all_ads

# ── 크리에이티브 조회 (이미지 URL + 영상 썸네일/소스) ─────────────────────────
def fetch_creative_media(creative_id, media_type):
    """
    반환: {"image_url": 썸네일 또는 이미지 URL, "video_url": 영상 소스 URL(영상만)}
    media_type: 'image' 또는 'video'
    """
    result = {"image_url": "", "video_url": "", "video_permalink": ""}
    if not creative_id:
        return result
    try:
        url = f"{BASE_URL}/{creative_id}"
        data = api_get(url, {"fields": "thumbnail_url,image_url,object_story_spec"})
        spec = data.get("object_story_spec", {})

        if media_type == "video":
            # 영상: 썸네일은 thumbnail_url 우선, 소스는 video_id로 별도 조회
            result["image_url"] = data.get("thumbnail_url", "") or \
                                  spec.get("video_data", {}).get("image_url", "")
            video_id = spec.get("video_data", {}).get("video_id", "")
            if video_id:
                video_info = fetch_video_source(video_id)
                result["video_url"] = video_info["source"]
                result["video_permalink"] = video_info["permalink"]
                # 썸네일이 아직 없으면 영상 객체에서 가져오기
                if not result["image_url"]:
                    result["image_url"] = fetch_video_thumbnail(video_id)
        else:
            # 이미지
            if data.get("image_url"):
                result["image_url"] = data["image_url"]
            elif data.get("thumbnail_url"):
                result["image_url"] = data["thumbnail_url"]
            elif "link_data" in spec:
                result["image_url"] = spec["link_data"].get("image_url", "")
    except Exception as e:
        print(f"    크리에이티브 조회 실패 (creative {creative_id}): {e}")
    return result

# ── 영상 소스 URL 조회 ────────────────────────────────────────────────────────
def fetch_video_source(video_id):
    """영상 다운로드 URL + 메타 영상 페이지 URL 반환"""
    try:
        data = api_get(f"{BASE_URL}/{video_id}", {"fields": "source,permalink_url"})
        return {
            "source": data.get("source", ""),
            "permalink": f"https://www.facebook.com{data['permalink_url']}" if data.get("permalink_url") else ""
        }
    except Exception as e:
        print(f"    영상 소스 조회 실패 (video {video_id}): {e}")
        return {"source": "", "permalink": ""}

# ── 영상 썸네일 조회 ──────────────────────────────────────────────────────────
def fetch_video_thumbnail(video_id):
    try:
        data = api_get(f"{BASE_URL}/{video_id}/thumbnails", {})
        thumbs = data.get("data", [])
        # is_preferred 우선, 없으면 첫 번째
        for t in thumbs:
            if t.get("is_preferred"):
                return t.get("uri", "")
        return thumbs[0].get("uri", "") if thumbs else ""
    except Exception as e:
        print(f"    영상 썸네일 조회 실패 (video {video_id}): {e}")
        return ""

# ── 미디어 파일 다운로드 & 저장 ──────────────────────────────────────────────
def download_media(url, filename, skip_if_exists=False):
    """URL에서 파일 다운로드 후 images/ 폴더에 저장. 로컬 경로 반환.
    skip_if_exists=True면 같은 파일이 이미 있을 때 재다운로드하지 않음(영상 등)."""
    if not url:
        return ""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    filepath = os.path.join(IMAGES_DIR, filename)
    if skip_if_exists and os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        return filepath
    try:
        resp = requests.get(url, timeout=30, stream=True)
        resp.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"    💾 저장: {filename}")
        return filepath
    except Exception as e:
        print(f"    ⚠️  다운로드 실패 ({filename}): {e}")
        return ""

def safe_filename(ad_name, suffix, ext):
    """광고명 기반 안전한 파일명 생성"""
    import re
    clean = re.sub(r'[\/:*?"<>|]', '_', ad_name)[:80]
    return f"{clean}_{suffix}.{ext}"

# ── 인사이트 조회 (날짜별 — 집행일수 계산용) ──────────────────────────────────
def fetch_insights(ad_id, date_start, date_stop):
    url = f"{BASE_URL}/{ad_id}/insights"
    params = {
        "fields": "spend,cpc,actions,cost_per_action_type,clicks,inline_link_clicks",
        "time_range": json.dumps({"since": date_start, "until": date_stop}),
        "time_increment": 1,   # 날짜별로 분리해서 받기
    }
    try:
        rows = api_get(url, params).get("data", [])
        if not rows:
            return None

        # spend > 0인 날만 집행일로 카운트
        active_rows = [r for r in rows if float(r.get("spend", 0)) > 0]
        if not active_rows:
            return None

        active_days = len(active_rows)
        total_spend = sum(float(r.get("spend", 0)) for r in active_rows)
        daily_spend = total_spend / active_days

        # CPC, clicks, 전환은 전체 기간 합산
        total_clicks = sum(float(r.get("clicks", 0)) for r in rows)          # 전체 클릭 (CPC용)
        total_link_clicks = sum(float(r.get("inline_link_clicks", 0)) for r in rows)  # 링크 클릭 (전환률용)
        total_cpc    = total_spend / total_clicks if total_clicks else 0

        # 전환 수: purchase 액션만 집계
        conversions = 0
        for r in rows:
            for a in r.get("actions", []):
                if a["action_type"] == "purchase":
                    conversions += float(a["value"])

        # 전환당 비용
        cost_per_conv = total_spend / conversions if conversions else 0

        # 전환률 = 구매 ÷ 링크 클릭 (메타 '전환율' 기준)
        conversion_rate = (conversions / total_link_clicks * 100) if total_link_clicks else 0

        return {
            "daily_spend":        daily_spend,
            "active_days":        active_days,
            "active_dates":       [r.get("date_start", "") for r in active_rows],  # 집행 날짜(합집합용)
            "total_spend":        total_spend,
            "total_clicks":       total_clicks,        # 전체 클릭(CPC용, 합산용)
            "total_link_clicks":  total_link_clicks,   # 링크 클릭(전환률용, 합산용)
            "cpc":                total_cpc,
            "conversions":        conversions,
            "cost_per_conversion": cost_per_conv,
            "conversion_rate":    conversion_rate,
        }
    except Exception as e:
        print(f"    인사이트 조회 실패: {e}")
        return None

# ── 일광고비(불씨) 수집 ───────────────────────────────────────────────────────
def fetch_account_active_ads(date_start, date_stop):
    """최근 기간에 '지출이 있었던' 광고 목록만 가볍게 조회한다.
    일자 분리(time_increment)와 actions 없이 집계만 받아서 서버 부하를 낮춘다.
    → level=ad + time_increment=1 + actions 조합은 계정이 크면 500(내부오류)이 나므로 피함."""
    url = f"{BASE_URL}/{AD_ACCOUNT_ID}/insights"
    params = {
        "level": "ad",
        "fields": "ad_id,ad_name,spend",
        "time_range": json.dumps({"since": date_start, "until": date_stop}),
        "limit": 500,
    }
    rows = []
    while True:
        data = api_get(url, params)
        rows.extend(data.get("data", []))
        next_url = data.get("paging", {}).get("next")
        if not next_url:
            break
        url, params = next_url, {}
    return rows

def fetch_ad_daily_rows(ad_id, date_start, date_stop):
    """단일 광고의 '일자별' 인사이트 rows 반환 (검증된 /{ad_id}/insights 경로)."""
    url = f"{BASE_URL}/{ad_id}/insights"
    params = {
        "fields": "spend,clicks,inline_link_clicks,actions",
        "time_range": json.dumps({"since": date_start, "until": date_stop}),
        "time_increment": 1,
    }
    try:
        return api_get(url, params).get("data", [])
    except Exception as e:
        print(f"    일자별 인사이트 실패 (ad {ad_id}): {e}")
        return []

def fetch_ad_creative_id(ad_id):
    try:
        data = api_get(f"{BASE_URL}/{ad_id}", {"fields": "creative{id}"})
        return data.get("creative", {}).get("id", "")
    except Exception as e:
        print(f"    크리에이티브 ID 조회 실패 (ad {ad_id}): {e}")
        return ""

def collect_daily_spikes(date_start, date_stop):
    """최근 7일 집행된 이미지·릴스 배너(F_I) 중
    하루 최고 지출이 등급 기준(30만↑)에 걸리는 광고만 반환.
    2단계: ① 지출 있었던 광고 목록만 가볍게 조회 → ② F_I만 광고별 일자 조회."""
    print(f"🔥 일광고비 스캔: {date_start} ~ {date_stop} (F_I 이미지·릴스 / 최근 7일)")

    # 불씨 최초 발굴일 기록 로드 + 오늘(KST) 날짜
    fire_seen = load_fire_seen()
    today_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")

    # ── 1) 지출 있었던 광고 목록 (가벼운 호출) ──
    try:
        active = fetch_account_active_ads(date_start, date_stop)
    except Exception as e:
        print(f"  ⚠️  광고 목록 조회 실패 → 일광고비 탭은 이번엔 건너뜀: {e}")
        return []

    # F_I 배너만, 지출>0. 같은 이름이 여러 광고에 있으면 ad_id들을 모아둠
    name_to_ids = {}
    for r in active:
        name = r.get("ad_name", "")
        if "F_I" not in name:                 # F_I 배너만 (이미지 + 릴스)
            continue
        if float(r.get("spend", 0)) <= 0:      # 이번주 집행분만
            continue
        name_to_ids.setdefault(name, []).append(r.get("ad_id", ""))

    print(f"  → F_I 집행 광고 {len(name_to_ids)}개 → 광고별 일자 조회")

    # ── 2) 광고별 일자 조회 후 등급 판정 ──
    results = []
    for name, ad_ids in name_to_ids.items():
        date_spend = {}
        tot_spend = tot_clicks = tot_link = tot_purch = 0.0
        for ad_id in ad_ids:
            for d in fetch_ad_daily_rows(ad_id, date_start, date_stop):
                dt = d.get("date_start", "")
                sp = float(d.get("spend", 0))
                date_spend[dt] = date_spend.get(dt, 0.0) + sp   # 같은 날 여러 광고면 합산
                tot_spend  += sp
                tot_clicks += float(d.get("clicks", 0))
                tot_link   += float(d.get("inline_link_clicks", 0))
                for a in d.get("actions", []):
                    if a.get("action_type") == "purchase":
                        tot_purch += float(a.get("value", 0))

        if not date_spend:
            continue
        peak_daily = max(date_spend.values())
        grade = get_daily_grade(peak_daily)
        if grade is None:                      # 30만원 미만이면 불씨 아님
            continue
        peak_date = max(date_spend, key=date_spend.get)
        cpc = tot_spend / tot_clicks if tot_clicks else 0
        cvr = (tot_purch / tot_link * 100) if tot_link else 0

        print(f"  🔥 {grade} | {name[:45]} | 일최고 {peak_daily:,.0f}원 ({peak_date})")

        # 릴스는 영상까지 받아서 팝업 재생, 나머지는 이미지 썸네일만
        creative_id = fetch_ad_creative_id(ad_ids[0])
        is_reels = "릴스" in name
        fetch_type = "video" if is_reels else "image"
        media = fetch_creative_media(creative_id, fetch_type)
        img_filename = safe_filename(name, "daily", "jpg")
        local_img = download_media(media["image_url"], img_filename)

        local_video = ""
        if is_reels and media.get("video_url"):
            vid_filename = safe_filename(name, "daily", "mp4")
            local_video = download_media(media["video_url"], vid_filename)

        # 최초 발굴일 기록 → 오늘 처음 잡혔으면 NEW
        first_seen = fire_seen.get(name)
        if not first_seen:
            first_seen = today_kst
            fire_seen[name] = today_kst
        is_new = (first_seen == today_kst)

        results.append({
            "name":             name,
            "peak_daily_spend": peak_daily,
            "peak_date":        peak_date,
            "total_spend":      tot_spend,
            "conversions":      tot_purch,
            "cpc":              cpc,
            "cvr":              cvr,
            "grade":            grade,
            "image_url":        local_img if local_img else media["image_url"],
            "is_video":         is_reels,
            "video_url":        local_video if local_video else media.get("video_url", ""),
            "video_permalink":  media.get("video_permalink", ""),
            "first_seen":       first_seen,
            "is_new":           is_new,
        })

    save_fire_seen(fire_seen)

    # 정렬: ① 오늘 새로 발굴(NEW) 먼저, ② 등급 높은 순, ③ 일 최고 지출 큰 순
    results.sort(key=lambda x: (not x["is_new"], DAILY_GRADE_RANK.get(x["grade"], 9), -x["peak_daily_spend"]))
    new_cnt = sum(1 for r in results if r["is_new"])
    print(f"  → 이번주 불씨 {len(results)}개 (오늘 NEW {new_cnt}개)")
    return results

# ── 순위(월별/주별) 집계 ──────────────────────────────────────────────────────
def _rank_entry(name, acc):
    spend = acc["spend"]
    return {
        "name":        name,
        "spend":       round(spend),
        "cpc":         round(spend / acc["clicks"]) if acc["clicks"] else 0,
        "cvr":         round(acc["purch"] / acc["link"] * 100, 2) if acc["link"] else 0,
        "conversions": round(acc["purch"]),
        "media":       "video" if "F_V" in name else "image",  # F_I 릴스는 이미지로 분류(등급체계 기준)
        "product":     get_product(name),
    }

def _rank_visible_names(ads):
    """정렬(총광고비/CPC/전환률) × 유형(전체/이미지/영상) × 제품군(전체/빙과/제과)
    모든 조합의 TOP 20에 한 번이라도 등장하는 광고명 집합. 필터로 새로 올라오는 광고까지 포함."""
    DESC = {"spend": True, "cvr": True, "cpc": False}   # cpc는 낮을수록 위
    names = set()
    for metric, desc in DESC.items():
        for mf in ("all", "image", "video"):
            for pf in ("all", "빙과", "제과"):
                pool = [a for a in ads
                        if (mf == "all" or a["media"] == mf)
                        and (pf == "all" or a["product"] == pf)]
                pool.sort(key=lambda a: a[metric], reverse=desc)
                for a in pool[:20]:
                    names.add(a["name"])
    return names

def collect_rankings():
    """이번 달(1일~오늘)·이번 주(월~오늘) F_I/F_V 광고 성과를 광고명 단위로 집계.
    월간 일자 데이터를 광고별로 1회만 받아, 주간은 그 안에서 날짜로 걸러 재사용."""
    today = datetime.today()
    m_start = today.replace(day=1).strftime("%Y-%m-%d")
    m_stop  = today.strftime("%Y-%m-%d")
    monday  = today - timedelta(days=today.weekday())          # 이번 주 월요일
    w_start = monday.strftime("%Y-%m-%d")
    w_stop  = today.strftime("%Y-%m-%d")
    print(f"🏆 순위 집계: 월간 {m_start}~{m_stop} / 주간 {w_start}~{w_stop}")

    try:
        active = fetch_account_active_ads(m_start, m_stop)
    except Exception as e:
        print(f"  ⚠️  순위용 광고 목록 조회 실패 → 순위 탭 건너뜀: {e}")
        return {"month": {"label": f"{m_start} ~ {m_stop}", "ads": []},
                "week":  {"label": f"{w_start} ~ {w_stop}", "ads": []}}

    name_to_ids = {}
    for r in active:
        name = r.get("ad_name", "")
        if ("F_I" not in name) and ("F_V" not in name):
            continue
        if float(r.get("spend", 0)) <= 0:
            continue
        name_to_ids.setdefault(name, []).append(r.get("ad_id", ""))

    print(f"  → 대상 광고 {len(name_to_ids)}개 (월간 일자 조회 중)")
    month_ads, week_ads = [], []
    for name, ad_ids in name_to_ids.items():
        m = {"spend": 0.0, "clicks": 0.0, "link": 0.0, "purch": 0.0}
        w = {"spend": 0.0, "clicks": 0.0, "link": 0.0, "purch": 0.0}
        for ad_id in ad_ids:
            for d in fetch_ad_daily_rows(ad_id, m_start, m_stop):
                dt = d.get("date_start", "")
                sp = float(d.get("spend", 0))
                cl = float(d.get("clicks", 0))
                lk = float(d.get("inline_link_clicks", 0))
                pu = 0.0
                for a in d.get("actions", []):
                    if a.get("action_type") == "purchase":
                        pu += float(a.get("value", 0))
                m["spend"] += sp; m["clicks"] += cl; m["link"] += lk; m["purch"] += pu
                if w_start <= dt <= w_stop:
                    w["spend"] += sp; w["clicks"] += cl; w["link"] += lk; w["purch"] += pu
        if m["spend"] > 0:
            month_ads.append(_rank_entry(name, m))
        if w["spend"] > 0:
            week_ads.append(_rank_entry(name, w))

    print(f"  → 월간 {len(month_ads)}개 / 주간 {len(week_ads)}개 집계 완료")

    # ── 순위표에 실제 등장하는 광고만 썸네일/영상 다운로드 ──
    visible = _rank_visible_names(month_ads) | _rank_visible_names(week_ads)
    print(f"  → 순위 등장 광고 {len(visible)}개 썸네일 수집")
    media_map = {}
    for name in visible:
        ad_ids = name_to_ids.get(name, [])
        if not ad_ids:
            continue
        is_video = ("F_V" in name) or ("릴스" in name)   # F_V + F_I릴스 = 영상(재생 대상)
        creative_id = fetch_ad_creative_id(ad_ids[0])
        media = fetch_creative_media(creative_id, "video" if is_video else "image")
        local_img = download_media(media["image_url"], safe_filename(name, "rank", "jpg"))
        local_video = ""
        if is_video and media.get("video_url"):
            # 영상은 용량이 커서 이미 받은 파일이 있으면 재다운로드 생략
            local_video = download_media(media["video_url"], safe_filename(name, "rank", "mp4"), skip_if_exists=True)
        media_map[name] = {
            "image_url":       local_img if local_img else media["image_url"],
            "is_video":        is_video,
            "video_url":       local_video if local_video else media.get("video_url", ""),
            "video_permalink": media.get("video_permalink", ""),
        }

    for lst in (month_ads, week_ads):
        for a in lst:
            mm = media_map.get(a["name"], {})
            a["image_url"]       = mm.get("image_url", "")
            a["is_video"]        = mm.get("is_video", False)
            a["video_url"]       = mm.get("video_url", "")
            a["video_permalink"] = mm.get("video_permalink", "")

    return {
        "month": {"label": f"{m_start} ~ {m_stop}", "ads": month_ads},
        "week":  {"label": f"{w_start} ~ {w_stop}", "ads": week_ads},
    }

# ── 누적 아카이브 로드/저장 ───────────────────────────────────────────────────
def load_archive():
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_archive(archive):
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)

# 일광고비·순위 탭 데이터 캐시 (수동 월 실행 시 재계산 생략하고 재사용)
TABS_CACHE = "tabs_cache.json"

def load_tabs_cache():
    if os.path.exists(TABS_CACHE):
        try:
            with open(TABS_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_tabs_cache(daily_ads, d_start, d_stop, rankings):
    try:
        with open(TABS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"daily_ads": daily_ads, "daily_start": d_start,
                       "daily_stop": d_stop, "rankings": rankings}, f, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️  탭 캐시 저장 실패: {e}")

# 불씨 최초 발굴일 기록 (광고명 → 처음 불씨로 잡힌 날짜). 오늘 처음이면 NEW! 배지.
FIRE_SEEN_FILE = "fire_seen.json"

def load_fire_seen():
    if os.path.exists(FIRE_SEEN_FILE):
        try:
            with open(FIRE_SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_fire_seen(seen):
    try:
        with open(FIRE_SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(seen, f, ensure_ascii=False)
    except Exception as e:
        print(f"  ⚠️  불씨 발굴이력 저장 실패: {e}")

# ── 병합 (같은 광고명은 최신 수집값으로 교체 = upsert) ───────────────────────
def merge_archive(existing, new_results, period_label):
    """같은 광고명을 다시 수집하면 합산하지 않고 '최신 값으로 교체'한다.
    - 같은 달을 다시 돌려도 금액/전환/집행일수가 2배가 되지 않음 (idempotent)
    - 이번에 수집되지 않은 기존 광고는 그대로 보존
    (광고명에 [26.0X] 월이 박혀 있어 한 광고는 한 달에만 잡히므로 누적합산이 불필요)"""
    grade_order = {"SS급": 0, "S급": 1, "A급": 2, "B급": 3}
    existing_map = {ad["name"]: ad for ad in existing}

    for new_ad in new_results:
        name = new_ad["name"]
        old = existing_map.get(name)

        # 기간 태그: 기존 + 이번 실행 합집합 (중복 없이 보존)
        periods = list(old.get("periods", [])) if old else []
        if period_label not in periods:
            periods.append(period_label)
        new_ad["periods"] = periods

        # 미디어 URL: 이번에 비어 있으면 기존 값 유지 (재다운로드 실패 대비)
        if old:
            for k in ("image_url", "video_url", "video_permalink"):
                if not new_ad.get(k) and old.get(k):
                    new_ad[k] = old[k]

        # 최신 수집값으로 통째 교체 (등급/금액/전환/집행일수/게재상태 모두 갱신)
        existing_map[name] = new_ad

    merged = list(existing_map.values())
    merged.sort(key=lambda x: (grade_order.get(x.get("grade", "B급"), 99), -x.get("total_spend", 0)))
    return merged

# ── HTML 생성 ────────────────────────────────────────────────────────────────
def get_product(name):
    if "빙과" in name:
        return "빙과"
    if "제과" in name:
        return "제과"
    return "기타"

def build_html(ads_data, daily_ads=None, daily_start="", daily_stop="", rankings=None):
    KST = timezone(timedelta(hours=9))
    updated = datetime.now(KST).strftime("%Y-%m-%d %H:%M (KST)")
    all_periods = sorted({p for ad in ads_data for p in ad.get("periods", [])})
    periods_str = " · ".join(all_periods) if all_periods else "전체"

    cards_html = ""
    for ad in ads_data:
        grade_color = get_grade_color(ad["grade"])
        img = ad.get("image_url", "")
        video = ad.get("video_url", "")
        video_permalink = ad.get("video_permalink", "")
        media_type = ad.get("media_type", "image")
        is_video = media_type == "video"

        if img:
            img_tag = f'<img src="{img}" alt="광고 미디어" onerror="this.style.display=\'none\'">'
        else:
            img_tag = '<div class="no-img">미리보기 없음</div>'

        # 영상이면 재생 오버레이 추가 (다운로드 영상 → 모달, 없으면 메타 페이지로)
        play_overlay = ""
        card_click_attr = ""
        card_classes = "card"
        if is_video and video:
            play_overlay = '<div class="play-icon">▶</div>'
            card_click_attr = f' data-video="{video}"'
            card_classes += " has-video"
        elif is_video and video_permalink:
            play_overlay = '<div class="play-icon">▶</div>'
            card_click_attr = f' data-permalink="{video_permalink}"'
            card_classes += " has-permalink"

        # 유형 필터값: category > grade_type > media_type 순으로 사용
        media_filter = ad.get("category", ad.get("grade_type", media_type))

        periods_tag = " ".join(f'<span class="period-tag">{p}</span>' for p in ad.get("periods", []))
        product = get_product(ad["name"])

        cards_html += f"""
        <div class="{card_classes}" data-grade="{ad['grade']}" data-product="{product}" data-media="{media_filter}"{card_click_attr}>
            <div class="card-img">
                {img_tag}
                {play_overlay}
                <span class="grade-badge" style="background:{grade_color}">{ad['grade']}</span>
            </div>
            <div class="card-body">
                <p class="ad-name">{ad['name']}</p>
                <div class="metrics">
                    <div class="metric"><span class="label">총 광고비</span><span class="value">{ad.get('total_spend', 0):,.0f}원</span></div>
                    <div class="metric"><span class="label">일 평균 광고비</span><span class="value">{ad['daily_spend']:,.0f}원</span></div>
                    <div class="metric"><span class="label">CPC</span><span class="value">{ad['cpc']:,.0f}원</span></div>
                    <div class="metric"><span class="label">전환 수</span><span class="value">{ad['conversions']:,.0f}</span></div>
                    <div class="metric"><span class="label">전환당 비용</span><span class="value">{ad['cost_per_conversion']:,.0f}원</span></div>
                    <div class="metric"><span class="label">전환률</span><span class="value">{ad['conversion_rate']:.2f}%</span></div>
                    <div class="metric"><span class="label">집행일수</span><span class="value">{int(ad.get('active_days', 0))}일</span></div>
                </div>
                <div class="periods">{periods_tag}</div>
            </div>
        </div>"""

    # ── 일광고비(불씨) 카드 ──
    daily_ads = daily_ads or []
    daily_cards_html = ""
    for ad in daily_ads:
        gcolor = get_daily_grade_color(ad["grade"])
        img = ad.get("image_url", "")
        is_video = ad.get("is_video", False)
        video = ad.get("video_url", "")
        video_permalink = ad.get("video_permalink", "")
        if img:
            img_tag = f'<img src="{img}" alt="광고 미디어" onerror="this.style.display=\'none\'">'
        else:
            img_tag = '<div class="no-img">미리보기 없음</div>'

        play_overlay = ""
        card_click_attr = ""
        card_classes = "fire-card"
        if is_video and video:
            play_overlay = '<div class="play-icon">▶</div>'
            card_click_attr = f' data-video="{video}"'
            card_classes += " has-video"
        elif is_video and video_permalink:
            play_overlay = '<div class="play-icon">▶</div>'
            card_click_attr = f' data-permalink="{video_permalink}"'
            card_classes += " has-permalink"

        product = get_product(ad["name"])
        new_badge = '<span class="new-badge">NEW!</span>' if ad.get("is_new") else ''
        daily_cards_html += f"""
        <div class="{card_classes}" data-grade="{ad['grade']}" data-product="{product}"{card_click_attr}>
            <div class="card-img">
                {img_tag}
                {play_overlay}
                <span class="grade-badge" style="background:{gcolor}">{ad['grade']}</span>
                {new_badge}
            </div>
            <div class="card-body">
                <p class="ad-name">{ad['name']}</p>
                <div class="metrics">
                    <div class="metric"><span class="label">일광고비 (최고)</span><span class="value peak">{ad['peak_daily_spend']:,.0f}원</span></div>
                    <div class="metric"><span class="label">최고 지출일</span><span class="value">{ad.get('peak_date','')}</span></div>
                    <div class="metric"><span class="label">지출금액 (7일)</span><span class="value">{ad['total_spend']:,.0f}원</span></div>
                    <div class="metric"><span class="label">구매 수</span><span class="value">{ad['conversions']:,.0f}</span></div>
                    <div class="metric"><span class="label">CPC</span><span class="value">{ad['cpc']:,.0f}원</span></div>
                    <div class="metric"><span class="label">CVR</span><span class="value">{ad['cvr']:.2f}%</span></div>
                </div>
            </div>
        </div>"""

    # ── 순위 데이터 (JS에서 정렬/필터/TOP20) ──
    rankings = rankings or {"month": {"label": "", "ads": []}, "week": {"label": "", "ads": []}}
    rank_json = json.dumps(rankings, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>전환배너 고효율 광고 아카이브</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700&display=swap');
  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
  :root {{ --bg:#0F0F12; --surface:#1A1A20; --border:#2A2A35; --text:#F0F0F5; --muted:#8A8A9A; }}
  body {{ background:var(--bg); color:var(--text); font-family:'Pretendard',-apple-system,sans-serif; min-height:100vh; }}
  header {{ padding:40px 32px 24px; border-bottom:1px solid var(--border); display:flex; align-items:flex-end; justify-content:space-between; flex-wrap:wrap; gap:16px; }}
  .header-left h1 {{ font-size:22px; font-weight:700; letter-spacing:-0.5px; }}
  .header-left p {{ margin-top:4px; font-size:13px; color:var(--muted); }}
  .updated {{ font-size:12px; color:var(--muted); }}
  .controls {{ padding:16px 32px 0; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  .filter-label {{ font-size:11px; color:var(--muted); margin-right:2px; }}
  .filter-btn {{ padding:6px 16px; border-radius:20px; border:1px solid var(--border); background:transparent; color:var(--muted); font-size:13px; cursor:pointer; transition:all .15s; font-family:inherit; }}
  .filter-btn:hover {{ border-color:#555; color:var(--text); }}
  .filter-btn.active {{ background:var(--text); color:var(--bg); border-color:var(--text); font-weight:600; }}
  .divider {{ width:1px; height:20px; background:var(--border); margin:0 4px; }}
  .count {{ margin-left:auto; font-size:13px; color:var(--muted); }}
  .product-filters {{ padding:10px 32px 16px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  .gallery {{ padding:8px 32px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:20px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; transition:transform .2s,box-shadow .2s; }}
  .card:hover {{ transform:translateY(-4px); box-shadow:0 12px 32px rgba(0,0,0,.4); }}
  .card-img {{ position:relative; background:#111; overflow:hidden; display:flex; align-items:center; justify-content:center; min-height:200px; }}
  .card-img img {{ width:100%; height:auto; display:block; object-fit:contain; }}
  .no-img {{ width:100%; height:200px; display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:12px; }}
  .grade-badge {{ position:absolute; top:10px; right:10px; padding:3px 10px; border-radius:20px; font-size:12px; font-weight:700; color:#fff; }}
  .new-badge {{ position:absolute; top:10px; right:54px; padding:3px 9px; border-radius:20px; font-size:11px; font-weight:800; color:#fff; background:#FF2D55; box-shadow:0 2px 6px rgba(255,45,85,.5); letter-spacing:.3px; }}
  .play-icon {{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); width:54px; height:54px; border-radius:50%; background:rgba(0,0,0,.55); border:2px solid rgba(255,255,255,.9); color:#fff; font-size:20px; display:flex; align-items:center; justify-content:center; padding-left:4px; pointer-events:none; transition:background .15s; }}
  .card.has-video {{ cursor:pointer; }}
  .card.has-video:hover .play-icon {{ background:rgba(0,0,0,.8); }}
  .modal {{ position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center; justify-content:center; z-index:1000; padding:24px; }}
  .modal.open {{ display:flex; }}
  .modal video {{ max-width:90vw; max-height:85vh; border-radius:8px; background:#000; }}
  .modal img {{ max-width:90vw; max-height:85vh; border-radius:8px; object-fit:contain; }}
  #modalVideo, #modalImage {{ display:none; }}
  .modal-close {{ position:absolute; top:20px; right:28px; font-size:32px; color:#fff; cursor:pointer; line-height:1; background:none; border:none; }}
  .card-body {{ padding:16px; }}
  .ad-name {{ font-size:11px; color:var(--muted); margin-bottom:12px; line-height:1.5; word-break:break-all; }}
  .metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .metric {{ display:flex; flex-direction:column; gap:2px; }}
  .metric .label {{ font-size:10px; color:var(--muted); letter-spacing:0.5px; }}
  .metric .value {{ font-size:13px; font-weight:600; display:flex; align-items:center; gap:4px; }}
  .periods {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:4px; }}
  .period-tag {{ font-size:10px; color:var(--muted); border:1px solid var(--border); border-radius:4px; padding:1px 6px; }}
  .tabs {{ display:flex; gap:4px; padding:16px 32px 0; border-bottom:1px solid var(--border); flex-wrap:wrap; }}
  .tab-btn {{ padding:10px 20px; border:none; background:transparent; color:var(--muted); font-size:14px; font-weight:600; cursor:pointer; border-bottom:2px solid transparent; font-family:inherit; transition:all .15s; }}
  .tab-btn:hover {{ color:var(--text); }}
  .tab-btn.active {{ color:var(--text); border-bottom-color:var(--text); }}
  .tab-panel {{ display:none; }}
  .tab-panel.active {{ display:block; }}
  .panel-pad {{ padding:60px 32px; }}
  .section-head {{ padding:24px 32px 8px; display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }}
  .section-title {{ font-size:18px; font-weight:700; letter-spacing:-0.3px; }}
  .section-sub {{ font-size:12px; color:var(--muted); }}
  .fire-card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; transition:transform .2s,box-shadow .2s; }}
  .fire-card:hover {{ transform:translateY(-4px); box-shadow:0 12px 32px rgba(0,0,0,.4); }}
  .value.peak {{ color:#FF6B00; }}
  .fire-card.has-video {{ cursor:pointer; }}
  .fire-card.has-video:hover .play-icon {{ background:rgba(0,0,0,.8); }}
  /* 순위표 */
  .rank-table-wrap {{ padding:8px 32px 40px; overflow-x:auto; }}
  .rank-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  .rank-table th, .rank-table td {{ padding:12px 14px; text-align:right; white-space:nowrap; border-bottom:1px solid var(--border); }}
  .rank-table th {{ color:var(--muted); font-weight:600; font-size:12px; position:sticky; top:0; background:var(--bg); }}
  .rank-table th.rank-col, .rank-table td.rank-col {{ text-align:center; width:48px; }}
  .rank-table th.name-col, .rank-table td.name-col {{ text-align:left; white-space:normal; min-width:220px; }}
  .rank-table td.rank-col {{ font-weight:700; font-size:15px; }}
  .rank-table th.active-col {{ color:var(--text); }}
  .rank-table td.active-col {{ color:#FF6B00; font-weight:700; }}
  .rank-table tbody tr:hover {{ background:var(--surface); }}
  .rank-table td.thumb {{ width:56px; padding-right:0; }}
  .rank-table tr.clickable {{ cursor:pointer; }}
  .rtw {{ position:relative; width:44px; height:44px; border-radius:8px; overflow:hidden; background:#23232B; display:flex; align-items:center; justify-content:center; }}
  .rtw img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .rtw .noimg {{ color:var(--muted); font-size:10px; }}
  .rtw .pl {{ position:absolute; right:2px; bottom:2px; width:16px; height:16px; border-radius:50%; background:rgba(0,0,0,.7); color:#fff; font-size:8px; display:flex; align-items:center; justify-content:center; }}
  .mchip {{ display:inline-block; margin-left:6px; padding:1px 6px; border-radius:5px; font-size:10px; font-weight:600; vertical-align:middle; }}
  .mchip.image {{ background:rgba(52,199,89,.15); color:#34C759; }}
  .mchip.video {{ background:rgba(191,90,242,.15); color:#BF5AF2; }}
  .mtag {{ display:inline-block; padding:2px 8px; border-radius:6px; font-size:11px; font-weight:600; }}
  .mtag.image {{ background:rgba(52,199,89,.15); color:#34C759; }}
  .mtag.video {{ background:rgba(191,90,242,.15); color:#BF5AF2; }}
  .empty {{ grid-column:1/-1; text-align:center; padding:80px 0; color:var(--muted); }}
</style>
</head>
<body>
<header>
  <div class="header-left">
    <h1>전환배너 고효율 광고 아카이브</h1>
    <p>누적 조회: {periods_str}</p>
  </div>
  <span class="updated">마지막 업데이트: {updated}</span>
</header>
<div class="tabs">
  <button class="tab-btn" data-tab="rankPanel">순위</button>
  <button class="tab-btn active" data-tab="dailyPanel">일광고비</button>
  <button class="tab-btn" data-tab="archivePanel">고효율 아카이브</button>
</div>

<section class="tab-panel" id="rankPanel">
  <div class="section-head">
    <h2 class="section-title">🏆 광고 성과 순위 TOP 20</h2>
    <span class="section-sub" id="rankSub"></span>
  </div>
  <div class="product-filters">
    <span class="filter-label">기간</span>
    <button class="filter-btn rperiod-btn active" data-period="month">월별</button>
    <button class="filter-btn rperiod-btn" data-period="week">주별</button>
    <div class="divider"></div>
    <span class="filter-label">정렬</span>
    <button class="filter-btn rmetric-btn active" data-metric="spend">총 광고비</button>
    <button class="filter-btn rmetric-btn" data-metric="cpc">CPC</button>
    <button class="filter-btn rmetric-btn" data-metric="cvr">전환률</button>
    <div class="divider"></div>
    <span class="filter-label">유형</span>
    <button class="filter-btn rmedia-btn active" data-media="all">전체</button>
    <button class="filter-btn rmedia-btn" data-media="image">이미지</button>
    <button class="filter-btn rmedia-btn" data-media="video">영상</button>
    <div class="divider"></div>
    <span class="filter-label">제품군</span>
    <button class="filter-btn rproduct-btn active" data-product="all">전체</button>
    <button class="filter-btn rproduct-btn" data-product="빙과">빙과</button>
    <button class="filter-btn rproduct-btn" data-product="제과">제과</button>
  </div>
  <div class="rank-table-wrap">
    <table class="rank-table">
      <thead>
        <tr>
          <th class="rank-col">#</th>
          <th class="name-col" colspan="2">광고명</th>
          <th data-col="spend">총 광고비</th>
          <th data-col="cpc">CPC</th>
          <th data-col="cvr">전환률</th>
          <th>구매 수</th>
        </tr>
      </thead>
      <tbody id="rankBody"></tbody>
    </table>
    <div class="empty" id="rankEmpty" style="display:none;">해당 조건의 광고가 없습니다.</div>
  </div>
</section>

<section class="tab-panel active" id="dailyPanel">
  <div class="section-head">
    <h2 class="section-title">🔥 이번주 불씨</h2>
    <span class="section-sub">최근 7일 · {daily_start} ~ {daily_stop} · 이미지·릴스 배너 · 하루 최고 지출 기준</span>
  </div>
  <div class="product-filters">
    <span class="filter-label">제품군</span>
    <button class="filter-btn dproduct-btn active" data-product="all">전체</button>
    <button class="filter-btn dproduct-btn" data-product="빙과">빙과</button>
    <button class="filter-btn dproduct-btn" data-product="제과">제과</button>
    <div class="divider"></div>
    <span class="filter-label">등급</span>
    <button class="filter-btn dgrade-btn active" data-grade="all">전체</button>
    <button class="filter-btn dgrade-btn" data-grade="SS">SS</button>
    <button class="filter-btn dgrade-btn" data-grade="S">S</button>
    <button class="filter-btn dgrade-btn" data-grade="A">A</button>
    <button class="filter-btn dgrade-btn" data-grade="B">B</button>
    <span class="count" id="dailyCount"></span>
  </div>
  <div class="gallery" id="dailyGallery">
    {daily_cards_html}
  </div>
  <div class="empty" id="dailyEmpty" style="display:none;">불씨 발굴중...</div>
</section>

<section class="tab-panel" id="archivePanel">
<div class="controls">
  <span class="filter-label">유형</span>
  <button class="filter-btn media-btn active" data-media="all">전체</button>
  <button class="filter-btn media-btn" data-media="image">이미지</button>
  <button class="filter-btn media-btn" data-media="video">영상</button>
  <button class="filter-btn media-btn" data-media="쩜오건">쩜오건</button>
  <div class="divider" id="gradeDivider"></div>
  <span class="filter-label" id="gradeLabel">등급</span>
  <button class="filter-btn grade-btn active" data-grade="all">전체</button>
  <button class="filter-btn grade-btn" data-grade="SS급">SS급</button>
  <button class="filter-btn grade-btn" data-grade="S급">S급</button>
  <button class="filter-btn grade-btn" data-grade="A급">A급</button>
  <button class="filter-btn grade-btn" data-grade="B급">B급</button>
  <button class="filter-btn grade-btn" data-grade="성공">성공</button>
  <button class="filter-btn grade-btn" data-grade="불씨">불씨</button>
  <span class="count" id="count"></span>
</div>
<div class="product-filters">
  <span class="filter-label">제품군</span>
  <button class="filter-btn product-btn active" data-product="all">전체</button>
  <button class="filter-btn product-btn" data-product="빙과">빙과</button>
  <button class="filter-btn product-btn" data-product="제과">제과</button>
</div>
<div class="gallery" id="gallery">
  {cards_html or '<div class="empty">고효율 기준(총 광고비 100만원 이상)을 충족하는 광고가 없습니다.</div>'}
</div>
</section>

<!-- 영상 팝업: 탭 밖 최상단에 두어 어느 탭에서 열든 현재 탭 위에 표시 -->
<div class="modal" id="videoModal">
  <button class="modal-close" id="modalClose">&times;</button>
  <video id="modalVideo" controls></video>
  <img id="modalImage" alt="광고 이미지">
</div>
<script>
  const cards = [...document.querySelectorAll('#archivePanel .card')];
  const countEl = document.getElementById('count');
  const gradeLabel = document.getElementById('gradeLabel');
  const gradeDivider = document.getElementById('gradeDivider');
  let activeGrade = 'all';
  let activeMedia = 'all';
  let activeProduct = 'all';

  // 유형별로 노출할 등급 버튼
  const gradeGroups = {{
    all:    ['SS급','S급','A급','B급','성공','불씨'],
    image:  ['SS급','S급','A급','B급'],
    video:  ['성공','불씨'],
    '쩜오건': []
  }};

  function updateGradeButtons() {{
    const allowed = gradeGroups[activeMedia] || [];
    const showGroup = allowed.length > 0;   // 쩜오건이면 등급 필터 자체를 숨김
    gradeLabel.style.display   = showGroup ? '' : 'none';
    gradeDivider.style.display = showGroup ? '' : 'none';
    document.querySelectorAll('.grade-btn').forEach(b => {{
      const g = b.dataset.grade;
      const visible = showGroup && (g === 'all' || allowed.includes(g));
      b.style.display = visible ? '' : 'none';
    }});
    // 탭을 바꾸면 등급 선택은 '전체'로 초기화
    activeGrade = 'all';
    document.querySelectorAll('.grade-btn').forEach(b => b.classList.toggle('active', b.dataset.grade === 'all'));
  }}

  function applyFilters() {{
    let v = 0;
    cards.forEach(c => {{
      const gradeOk = activeGrade === 'all' || c.dataset.grade === activeGrade;
      const mediaOk = activeMedia === 'all' || c.dataset.media === activeMedia;
      const productOk = activeProduct === 'all' || c.dataset.product === activeProduct;
      const show = gradeOk && mediaOk && productOk;
      c.style.display = show ? '' : 'none';
      if (show) v++;
    }});
    countEl.textContent = v + '개';
  }}

  updateGradeButtons();
  applyFilters();

  document.querySelectorAll('.media-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.media-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeMedia = btn.dataset.media;
      updateGradeButtons();
      applyFilters();
    }});
  }});

  document.querySelectorAll('.grade-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.grade-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeGrade = btn.dataset.grade;
      applyFilters();
    }});
  }});

  document.querySelectorAll('.product-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.product-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeProduct = btn.dataset.product;
      applyFilters();
    }});
  }});

  // ── 영상·이미지 모달 ──
  const modal = document.getElementById('videoModal');
  const modalVideo = document.getElementById('modalVideo');
  const modalImage = document.getElementById('modalImage');
  const modalClose = document.getElementById('modalClose');

  function openVideoModal(src) {{
    if (!src) return;
    modalImage.style.display = 'none';
    modalVideo.style.display = 'block';
    modalVideo.src = src;
    modal.classList.add('open');
    modalVideo.play().catch(() => {{}});
  }}
  function openImageModal(src) {{
    if (!src) return;
    modalVideo.pause();
    modalVideo.src = '';
    modalVideo.style.display = 'none';
    modalImage.style.display = 'block';
    modalImage.src = src;
    modal.classList.add('open');
  }}

  // 저장된 영상 → 모달 재생 (아카이브 + 일광고비 릴스)
  document.querySelectorAll('#archivePanel .card.has-video, #dailyPanel .fire-card.has-video').forEach(card => {{
    card.addEventListener('click', () => openVideoModal(card.dataset.video));
  }});

  // 다운로드 불가 영상 → 메타 페이지로 이동 (아카이브 + 일광고비 릴스)
  document.querySelectorAll('#archivePanel .card.has-permalink, #dailyPanel .fire-card.has-permalink').forEach(card => {{
    card.addEventListener('click', () => {{
      const url = card.dataset.permalink;
      if (!url) return;
      window.open(url, '_blank');
    }});
  }});

  function closeModal() {{
    modal.classList.remove('open');
    modalVideo.pause();
    modalVideo.src = '';
    modalImage.src = '';
  }}
  modalClose.addEventListener('click', closeModal);
  modal.addEventListener('click', e => {{ if (e.target === modal) closeModal(); }});
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});

  // ── 탭 전환 ──
  document.querySelectorAll('.tab-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById(btn.dataset.tab).classList.add('active');
    }});
  }});

  // ── 일광고비(불씨) 필터 ──
  const fireCards  = [...document.querySelectorAll('#dailyPanel .fire-card')];
  const dailyCount = document.getElementById('dailyCount');
  const dailyEmpty = document.getElementById('dailyEmpty');
  let dProduct = 'all';
  let dGrade = 'all';
  function applyDaily() {{
    let v = 0;
    fireCards.forEach(c => {{
      const pOk = dProduct === 'all' || c.dataset.product === dProduct;
      const gOk = dGrade === 'all' || c.dataset.grade === dGrade;
      const show = pOk && gOk;
      c.style.display = show ? '' : 'none';
      if (show) v++;
    }});
    dailyCount.textContent = v + '개';
    dailyEmpty.style.display = v === 0 ? '' : 'none';
  }}
  document.querySelectorAll('.dproduct-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.dproduct-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      dProduct = btn.dataset.product;
      applyDaily();
    }});
  }});
  document.querySelectorAll('.dgrade-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.dgrade-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      dGrade = btn.dataset.grade;
      applyDaily();
    }});
  }});
  applyDaily();

  // ── 순위 탭 ──
  const RANK_DATA = {rank_json};
  const rankBody  = document.getElementById('rankBody');
  const rankSub   = document.getElementById('rankSub');
  const rankEmpty = document.getElementById('rankEmpty');
  let rPeriod = 'month', rMetric = 'spend', rMedia = 'all', rProduct = 'all';
  const METRIC_DESC = {{ spend: true, cvr: true, cpc: false }};  // true=내림차순, cpc는 낮을수록 위
  const fmt = n => (n || 0).toLocaleString('ko-KR');
  function rankThumb(a) {{
    const play = a.is_video ? '<span class="pl">▶</span>' : '';
    const inner = a.image_url
      ? `<img src="${{a.image_url}}" onerror="this.style.display='none'">`
      : `<span class="noimg">${{a.product || ''}}</span>`;
    return `<div class="rtw">${{inner}}${{play}}</div>`;
  }}
  function renderRank() {{
    const src = RANK_DATA[rPeriod] || {{ label: '', ads: [] }};
    rankSub.textContent = src.label;
    let ads = (src.ads || []).filter(a =>
      (rMedia === 'all' || a.media === rMedia) &&
      (rProduct === 'all' || a.product === rProduct)
    );
    const desc = METRIC_DESC[rMetric];
    ads = ads.slice().sort((a, b) => desc ? b[rMetric] - a[rMetric] : a[rMetric] - b[rMetric]).slice(0, 20);
    document.querySelectorAll('.rank-table th[data-col]').forEach(th =>
      th.classList.toggle('active-col', th.dataset.col === rMetric)
    );
    if (!ads.length) {{ rankBody.innerHTML = ''; rankEmpty.style.display = ''; return; }}
    rankEmpty.style.display = 'none';
    rankBody.innerHTML = ads.map((a, i) => {{
      const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : (i + 1);
      const mlabel = a.media === 'video' ? '영상' : '이미지';
      const cls = m => rMetric === m ? ' class="active-col"' : '';
      let attr = '', klass = '';
      if (a.is_video && a.video_url) {{ attr = ` data-video="${{a.video_url}}"`; klass = ' class="clickable"'; }}
      else if (a.is_video && a.video_permalink) {{ attr = ` data-permalink="${{a.video_permalink}}"`; klass = ' class="clickable"'; }}
      else if (a.image_url) {{ attr = ` data-image="${{a.image_url}}"`; klass = ' class="clickable"'; }}
      return `<tr${{klass}}${{attr}}>
        <td class="rank-col">${{medal}}</td>
        <td class="thumb">${{rankThumb(a)}}</td>
        <td class="name-col">${{a.name}}<span class="mchip ${{a.media}}">${{mlabel}}</span></td>
        <td${{cls('spend')}}>${{fmt(a.spend)}}원</td>
        <td${{cls('cpc')}}>${{fmt(a.cpc)}}원</td>
        <td${{cls('cvr')}}>${{a.cvr}}%</td>
        <td>${{fmt(a.conversions)}}</td>
      </tr>`;
    }}).join('');
  }}
  // 순위표 클릭 → 영상은 재생, 이미지는 확대 팝업 (행이 동적 생성이라 위임 방식)
  rankBody.addEventListener('click', e => {{
    const tr = e.target.closest('tr[data-video], tr[data-permalink], tr[data-image]');
    if (!tr) return;
    if (tr.dataset.video) {{
      openVideoModal(tr.dataset.video);
    }} else if (tr.dataset.permalink) {{
      window.open(tr.dataset.permalink, '_blank');
    }} else if (tr.dataset.image) {{
      openImageModal(tr.dataset.image);
    }}
  }});
  function wireRank(cls, setter) {{
    document.querySelectorAll(cls).forEach(btn => {{
      btn.addEventListener('click', () => {{
        document.querySelectorAll(cls).forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        setter(btn);
        renderRank();
      }});
    }});
  }}
  wireRank('.rperiod-btn',  b => rPeriod  = b.dataset.period);
  wireRank('.rmetric-btn',  b => rMetric  = b.dataset.metric);
  wireRank('.rmedia-btn',   b => rMedia   = b.dataset.media);
  wireRank('.rproduct-btn', b => rProduct = b.dataset.product);
  renderRank();
</script>
</body>
</html>"""

# ── 메인 ─────────────────────────────────────────────────────────────────────
def collect_media(month_tag, date_start, date_stop, media_tag, media_type, category=None, extra_keyword=""):
    """media_tag: 'F_I'/'F_V', media_type: 'image'/'video'
    category: None(일반)/'쩜오건', extra_keyword: 광고명 추가 필터 → new_results 리스트 반환"""
    ads = fetch_ads(month_tag, media_tag, extra_keyword)
    label = category or media_tag
    print(f"  → [{month_tag}] {label} 광고 {len(ads)}개 발견")

    is_jjamo = (category == "쩜오건")

    raw = {}
    for ad in ads:
        ad_id, ad_name = ad["id"], ad["name"]
        # 일반 영상 수집에서는 쩜오건 광고 제외 (전용 탭에서만 다룸)
        if not is_jjamo and "쩜오건" in ad_name:
            continue
        creative_id = ad.get("creative", {}).get("id", "")
        print(f"  처리 중: {ad_name[:50]}...")

        # 1. 인사이트 먼저 조회
        metrics = fetch_insights(ad_id, date_start, date_stop)
        if not metrics:
            continue

        # F_I + 릴스: 영상으로 가져오되 등급/필터는 이미지 기준
        is_reels = (media_tag == "F_I" and "릴스" in ad_name)
        actual_media_type = "video" if (is_reels or media_type == "video") else media_type  # 미디어 fetch용

        # 등급 타입(grade_type)과 유형 탭(category) 결정
        if is_jjamo:
            grade_type = "쩜오건"
            cat = "쩜오건"
        elif media_type == "image" or is_reels:
            grade_type = "image"
            cat = "image"
        else:
            grade_type = "video"
            cat = "video"

        candidate = {
            "name":                ad_name,
            "creative_id":         creative_id,
            "media_type":          actual_media_type,
            "grade_type":          grade_type,
            "category":            cat,
            "total_spend":         metrics["total_spend"],
            "active_dates":        metrics["active_dates"],
            "total_clicks":        metrics["total_clicks"],
            "total_link_clicks":   metrics["total_link_clicks"],
            "conversions":         metrics["conversions"],
        }
        raw.setdefault(ad_name, []).append(candidate)

    new_results = []
    for ad_name, candidates in raw.items():
        # ── 같은 이름의 광고는 성과를 '합산'해서 1개 카드로 ──
        total_spend       = sum(c["total_spend"] for c in candidates)
        conversions       = sum(c["conversions"] for c in candidates)
        total_clicks      = sum(c["total_clicks"] for c in candidates)
        total_link_clicks = sum(c["total_link_clicks"] for c in candidates)

        # 집행일수: 날짜 합집합 (같은 날 병렬 집행해도 중복 카운트 안 함)
        active_date_set = set()
        for c in candidates:
            active_date_set.update(d for d in c["active_dates"] if d)
        active_days = len(active_date_set)

        # 파생 지표 재계산
        daily_spend     = total_spend / active_days if active_days else 0
        cpc             = total_spend / total_clicks if total_clicks else 0
        cost_per_conv   = total_spend / conversions if conversions else 0
        conversion_rate = (conversions / total_link_clicks * 100) if total_link_clicks else 0

        base = candidates[0]
        actual_type = base["media_type"]
        grade_type  = base.get("grade_type", actual_type)

        # 등급 판정 (합산된 총 광고비 기준)
        grade = get_grade(total_spend, grade_type)
        if grade is None:
            continue

        if len(candidates) > 1:
            print(f"  중복 {len(candidates)}개 합산: {ad_name[:40]} (총 {total_spend:,.0f}원 / 구매 {conversions:.0f}건 / {active_days}일)")

        # 미디어 다운로드 (같은 이름=같은 소재, 구매 최다 광고의 크리에이티브 사용)
        is_reels_type = (actual_type == "video" and media_tag == "F_I")
        type_label = "릴스(영상/이미지등급)" if is_reels_type else actual_type
        print(f"    → {grade} | 총 {total_spend:,.0f}원 / 구매 {conversions:.0f}건 ({active_days}일 집행) [{type_label}]")
        creative_id = max(candidates, key=lambda x: x["conversions"])["creative_id"]
        media = fetch_creative_media(creative_id, actual_type)

        img_filename = safe_filename(ad_name, "thumb", "jpg")
        local_img = download_media(media["image_url"], img_filename)

        local_video = ""
        if actual_type == "video" and media["video_url"]:
            vid_filename = safe_filename(ad_name, "video", "mp4")
            # 영상은 용량이 커서 이미 받은 파일이 있으면 재다운로드 생략 (매일 갱신 대비)
            local_video = download_media(media["video_url"], vid_filename, skip_if_exists=True)

        new_results.append({
            "name":                ad_name,
            "media_type":          actual_type,
            "grade_type":          grade_type,
            "category":            base["category"],
            "total_spend":         total_spend,
            "daily_spend":         daily_spend,
            "active_days":         active_days,
            "cpc":                 cpc,
            "conversions":         conversions,
            "cost_per_conversion": cost_per_conv,
            "conversion_rate":     conversion_rate,
            "image_url":           local_img if local_img else media["image_url"],
            "video_url":           local_video if local_video else media["video_url"],
            "video_permalink":     media.get("video_permalink", ""),
            "grade":               grade,
        })

    print(f"  → [{month_tag}] {label} 고효율 광고 {len(new_results)}개")
    return new_results

def expand_month_tags(spec):
    """월 태그 입력을 월 목록으로 변환.
    - "26.06"        → ["26.06"]
    - "26.01~26.05"  → ["26.05","26.04","26.03","26.02","26.01"]  (최신 달부터)
    - "-" 구분자도 허용 ("26.01-26.05")"""
    spec = (spec or "").strip()
    if not spec:
        return []
    sep = "~" if "~" in spec else ("-" if "-" in spec else None)
    if sep is None:
        return [spec]
    a, b = [s.strip() for s in spec.split(sep, 1)]
    def to_n(t):
        yy, mm = t.split(".")
        return int("20" + yy) * 12 + (int(mm) - 1)
    def to_tag(n):
        y, m = divmod(n, 12)
        return f"{y % 100:02d}.{m + 1:02d}"
    lo, hi = sorted([to_n(a), to_n(b)])
    return list(reversed([to_tag(n) for n in range(lo, hi + 1)]))  # 최신 달부터

def run_archive_for_month(merged, month_tag, media_mode="all", date_start=None, date_stop=None):
    """지정한 월 태그의 고효율 아카이브를 수집해 병합(교체) 후 반환."""
    if not (date_start and date_stop):
        yy, mm = month_tag.split(".")
        start = datetime(int("20" + yy), int(mm), 1)
        stop = datetime.today()
        date_start, date_stop = start.strftime("%Y-%m-%d"), stop.strftime("%Y-%m-%d")
    print(f"📡 [{month_tag}] 아카이브 수집 (성과 기간: {date_start} ~ {date_stop})")

    all_modes = [
        ("F_I", "image", None, ""),
        ("F_V", "video", None, ""),
        ("F_V", "video", "쩜오건", "쩜오건"),  # 쩜오건 전용 (50만원↑)
    ]
    if media_mode == "image":
        modes = [("F_I", "image", None, "")]
    elif media_mode == "video":
        modes = [("F_V", "video", None, ""), ("F_V", "video", "쩜오건", "쩜오건")]
    else:
        modes = all_modes
    for media_tag, media_type, category, extra_keyword in modes:
        print(f"🎯 {category or media_tag} ({media_type}) 수집")
        try:
            results = collect_media(month_tag, date_start, date_stop, media_tag, media_type, category, extra_keyword)
            merged = merge_archive(merged, results, month_tag)
        except Exception as e:
            print(f"  ⚠️  [{month_tag}] {category or media_tag} 수집 실패(건너뜀): {e}")
    return merged

def notify_slack_new_fires(daily_ads):
    """오늘 새로 발견된 불씨(is_new)가 있으면 슬랙으로 알림. 없으면 아무것도 안 보냄."""
    if not SLACK_WEBHOOK_URL:
        return
    new_fires = [a for a in (daily_ads or []) if a.get("is_new")]
    if not new_fires:
        print("  ℹ️  새 불씨 없음 → 슬랙 알림 생략")
        return

    # 등급 높은 순 → 일 최고 지출 큰 순
    new_fires.sort(key=lambda x: (DAILY_GRADE_RANK.get(x["grade"], 9), -x["peak_daily_spend"]))

    lines = [f"🔥 *새 불씨 발견!* ({len(new_fires)}건)", ""]
    for a in new_fires:
        lines.append(f"*[{a['grade']}]* {a['name']}")
        lines.append(f"　일광고비 {a['peak_daily_spend']:,}원 · CPC {a['cpc']:,}원 · CVR {a['cvr']:.2f}%")
    lines.append("")
    lines.append(f"👉 자세히 보기: {SITE_URL}")
    payload = {"text": "\n".join(lines)}

    try:
        r = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=15)
        if r.status_code == 200:
            print(f"  📨 슬랙 알림 전송: 새 불씨 {len(new_fires)}건")
        else:
            print(f"  ⚠️  슬랙 알림 실패: HTTP {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"  ⚠️  슬랙 알림 오류: {e}")

def main():
    existing = load_archive()
    print(f"기존 아카이브 {len(existing)}개")
    merged = existing

    auto_archive = os.environ.get("AUTO_ARCHIVE", "").strip().lower() in ("1", "yes", "true")

    # ── 1) 고효율 아카이브 수집 ──
    try:
        if MONTH_TAG:
            # (수동) 지정한 월/범위 수집 — 범위면 최신 달부터 한 달씩
            tags = expand_month_tags(MONTH_TAG)
            print(f"수집 유형: {MEDIA_MODE} | 대상 월(최신순): {', '.join(tags)}")
            if len(tags) == 1 and DATE_START and DATE_STOP:
                # 단일 월 + 직접 지정 기간
                merged = run_archive_for_month(merged, tags[0], MEDIA_MODE, DATE_START, DATE_STOP)
                save_archive(merged)
            else:
                for i, tag in enumerate(tags):
                    merged = run_archive_for_month(merged, tag, MEDIA_MODE)
                    save_archive(merged)   # 각 달 끝날 때마다 저장 (중간 중단돼도 여기까지 보존)
                    print(f"  → [{tag}] 저장 완료 (누적 {len(merged)}개)")
                    if i < len(tags) - 1:
                        time.sleep(30)     # 달 사이 텀 (레이트 리밋 완화)
            print(f"  → 최종 총 {len(merged)}개")
        elif auto_archive:
            # (매일 자동) 최근 3개월(지지난 달·지난 달·이번 달)
            today = datetime.today()
            tags, first = [], today.replace(day=1)
            for _ in range(3):
                tags.append(first.strftime("%y.%m"))
                first = (first - timedelta(days=1)).replace(day=1)
            tags = list(reversed(tags))
            print(f"🗓️  자동 아카이브 갱신(최근 3개월): {', '.join(tags)}")
            for tag in tags:
                merged = run_archive_for_month(merged, tag, "all")
            save_archive(merged)
            print(f"  → 병합 후 총 {len(merged)}개")
        else:
            print("ℹ️  아카이브 갱신 안 함 → 일광고비/순위만 갱신")
    except Exception as e:
        print(f"  ⚠️  아카이브 단계 오류(기존 데이터 유지하고 계속): {e}")
        save_archive(merged)

    # ── 2) 일광고비·순위 ──
    today = datetime.today()
    d_start = (today - timedelta(days=6)).strftime("%Y-%m-%d")
    d_stop  = today.strftime("%Y-%m-%d")

    if MONTH_TAG:
        # (수동 월 실행) 일광고비·순위는 재계산 생략 → 마지막 계산값을 그대로 유지 (호출량↓)
        print("ℹ️  수동 월 실행 → 일광고비·순위는 이전 계산값 유지(재계산 생략)")
        cache = load_tabs_cache()
        daily_ads = cache.get("daily_ads", [])
        d_start   = cache.get("daily_start", d_start)
        d_stop    = cache.get("daily_stop", d_stop)
        rankings  = cache.get("rankings")
        if not cache:
            print("  ⚠️  캐시 없음 → 일광고비·순위 탭은 다음 정기 실행(8·14·20·2시) 때 채워집니다")
    else:
        try:
            daily_ads = collect_daily_spikes(d_start, d_stop)
        except Exception as e:
            print(f"  ⚠️  일광고비 단계 오류(건너뜀): {e}")
            daily_ads = []
        # 오늘 새로 발견된 불씨가 있으면 슬랙 알림 (정기 실행에서만)
        notify_slack_new_fires(daily_ads)
        try:
            rankings = collect_rankings()
        except Exception as e:
            print(f"  ⚠️  순위 단계 오류(건너뜀): {e}")
            rankings = None
        # 다음 수동 실행에서 재사용할 수 있게 저장
        save_tabs_cache(daily_ads, d_start, d_stop, rankings)

    # ── 3) HTML 생성 ──
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(merged, daily_ads, d_start, d_stop, rankings))
    print("✅ 완료")

if __name__ == "__main__":
    main()
