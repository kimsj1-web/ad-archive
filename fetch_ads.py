import os
import json
import requests
from datetime import datetime, timedelta, timezone

# ── 설정 ────────────────────────────────────────────────────────────────────
ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
MONTH_TAG     = os.environ.get("MONTH_TAG", "")
DATE_START    = os.environ.get("DATE_START", "")
DATE_STOP     = os.environ.get("DATE_STOP", "")
MEDIA_MODE    = os.environ.get("MEDIA_MODE", "all")  # image / video / all

API_VERSION  = "v19.0"
BASE_URL     = f"https://graph.facebook.com/{API_VERSION}"
ARCHIVE_FILE = "archive.json"
IMAGES_DIR   = "images"  # 이미지/영상 저장 폴더

# ── 성과 등급 기준 (총 광고비 기준) ──────────────────────────────────────────
def get_grade(total_spend, media_type="image"):
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
    }.get(grade, "#8E8E93")

# ── API 헬퍼 ─────────────────────────────────────────────────────────────────
def api_get(url, params):
    params["access_token"] = ACCESS_TOKEN
    resp = requests.get(url, params=params)
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"  ⚠️  API 오류: {e} | 응답: {resp.text[:300]}")
        raise
    return resp.json()

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
def fetch_ads(month_tag, media_tag):
    """media_tag: 'F_I'(이미지) 또는 'F_V'(영상)"""
    url = f"{BASE_URL}/{AD_ACCOUNT_ID}/ads"
    params = {
        "fields": "id,name,creative{id}",
        "filtering": json.dumps([
            {"field": "name", "operator": "CONTAIN", "value": media_tag},
            {"field": "name", "operator": "CONTAIN", "value": month_tag},
        ]),
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
def download_media(url, filename):
    """URL에서 파일 다운로드 후 images/ 폴더에 저장. 로컬 경로 반환"""
    if not url:
        return ""
    os.makedirs(IMAGES_DIR, exist_ok=True)
    filepath = os.path.join(IMAGES_DIR, filename)
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
        "fields": "spend,cpc,actions,cost_per_action_type,clicks",
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
        total_clicks = sum(float(r.get("clicks", 0)) for r in rows)
        total_cpc    = total_spend / total_clicks if total_clicks else 0

        # 전환 수: purchase 액션만 집계
        conversions = 0
        for r in rows:
            for a in r.get("actions", []):
                if a["action_type"] == "purchase":
                    conversions += float(a["value"])

        # 전환당 비용
        cost_per_conv = total_spend / conversions if conversions else 0

        # 전환률
        conversion_rate = (conversions / total_clicks * 100) if total_clicks else 0

        return {
            "daily_spend":        daily_spend,
            "active_days":        active_days,
            "total_spend":        total_spend,
            "cpc":                total_cpc,
            "conversions":        conversions,
            "cost_per_conversion": cost_per_conv,
            "conversion_rate":    conversion_rate,
        }
    except Exception as e:
        print(f"    인사이트 조회 실패: {e}")
        return None

# ── 누적 아카이브 로드/저장 ───────────────────────────────────────────────────
def load_archive():
    if os.path.exists(ARCHIVE_FILE):
        with open(ARCHIVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_archive(archive):
    with open(ARCHIVE_FILE, "w", encoding="utf-8") as f:
        json.dump(archive, f, ensure_ascii=False, indent=2)

# ── 누적 병합 (총 광고비 합산 기준) ──────────────────────────────────────────
def merge_archive(existing, new_results, period_label):
    grade_order = {"SS급": 0, "S급": 1, "A급": 2, "B급": 3}
    existing_map = {ad["name"]: ad for ad in existing}

    for new_ad in new_results:
        name = new_ad["name"]
        new_ad["periods"] = [period_label]

        if name in existing_map:
            rec = existing_map[name]

            # 기간 태그 누적
            if period_label not in rec.get("periods", []):
                rec.setdefault("periods", []).append(period_label)

            # 총 광고비 합산
            rec["total_spend"] = rec.get("total_spend", 0) + new_ad.get("total_spend", 0)

            # 집행일수 합산
            rec["active_days"] = rec.get("active_days", 0) + new_ad.get("active_days", 0)

            # 전환 수 합산
            rec["conversions"] = rec.get("conversions", 0) + new_ad.get("conversions", 0)

            # 일 평균 광고비, 전환당 비용 재계산
            rec["daily_spend"] = rec["total_spend"] / rec["active_days"] if rec["active_days"] else 0
            rec["cost_per_conversion"] = rec["total_spend"] / rec["conversions"] if rec["conversions"] else 0

            # 합산된 총 광고비로 등급 재판정
            new_grade = get_grade(rec["total_spend"], rec.get("media_type", "image"))
            if new_grade:
                rec["grade"] = new_grade

            # 게재 상태: 어느 하나라도 ACTIVE면 게재중

            # 이미지/영상 URL 없으면 업데이트
            if not rec.get("image_url") and new_ad.get("image_url"):
                rec["image_url"] = new_ad["image_url"]
            if not rec.get("video_url") and new_ad.get("video_url"):
                rec["video_url"] = new_ad["video_url"]
            if not rec.get("video_permalink") and new_ad.get("video_permalink"):
                rec["video_permalink"] = new_ad["video_permalink"]
        else:
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

def build_html(ads_data):
    KST = timezone(timedelta(hours=9))
    updated = datetime.now(KST).strftime("%Y-%m-%d %H:%M (KST)")
    all_periods = sorted({p for ad in ads_data for p in ad.get("periods", [])})
    periods_str = " · ".join(all_periods) if all_periods else "전체"

    cards_html = ""
    for ad in ads_data:
        grade_color = get_grade_color(ad["grade"])
        img = ad.get("image_url", "")
        video = ad.get("video_url", "")
        media_type = ad.get("media_type", "image")
        is_video = media_type == "video"

        if img:
            img_tag = f'<img src="{img}" alt="광고 미디어" onerror="this.style.display=\'none\'">'
        else:
            img_tag = '<div class="no-img">미리보기 없음</div>'

        # 영상이면 재생 오버레이 추가 (클릭 시 모달)
        play_overlay = ""
        card_click_attr = ""
        if is_video and video:
            play_overlay = '<div class="play-icon">▶</div>'
            card_click_attr = f' data-video="{video}"'

        periods_tag = " ".join(f'<span class="period-tag">{p}</span>' for p in ad.get("periods", []))
        product = get_product(ad["name"])

        cards_html += f"""
        <div class="card{' has-video' if is_video and video else (' has-permalink' if is_video and video_permalink else '')}" data-grade="{ad['grade']}" data-product="{product}" data-media="{ad.get('grade_type', media_type)}"{card_click_attr}>
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
  .play-icon {{ position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); width:54px; height:54px; border-radius:50%; background:rgba(0,0,0,.55); border:2px solid rgba(255,255,255,.9); color:#fff; font-size:20px; display:flex; align-items:center; justify-content:center; padding-left:4px; pointer-events:none; transition:background .15s; }}
  .card.has-video {{ cursor:pointer; }}
  .card.has-video:hover .play-icon {{ background:rgba(0,0,0,.8); }}
  .modal {{ position:fixed; inset:0; background:rgba(0,0,0,.85); display:none; align-items:center; justify-content:center; z-index:1000; padding:24px; }}
  .modal.open {{ display:flex; }}
  .modal video {{ max-width:90vw; max-height:85vh; border-radius:8px; background:#000; }}
  .modal-close {{ position:absolute; top:20px; right:28px; font-size:32px; color:#fff; cursor:pointer; line-height:1; background:none; border:none; }}
  .card-body {{ padding:16px; }}
  .ad-name {{ font-size:11px; color:var(--muted); margin-bottom:12px; line-height:1.5; word-break:break-all; }}
  .metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .metric {{ display:flex; flex-direction:column; gap:2px; }}
  .metric .label {{ font-size:10px; color:var(--muted); letter-spacing:0.5px; }}
  .metric .value {{ font-size:13px; font-weight:600; display:flex; align-items:center; gap:4px; }}
  .periods {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:4px; }}
  .period-tag {{ font-size:10px; color:var(--muted); border:1px solid var(--border); border-radius:4px; padding:1px 6px; }}
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
<div class="controls">
  <span class="filter-label">유형</span>
  <button class="filter-btn media-btn active" data-media="all">전체</button>
  <button class="filter-btn media-btn" data-media="image">이미지</button>
  <button class="filter-btn media-btn" data-media="video">영상</button>
  <div class="divider"></div>
  <span class="filter-label">등급</span>
  <button class="filter-btn grade-btn active" data-grade="all">전체</button>
  <button class="filter-btn grade-btn" data-grade="SS급">SS급</button>
  <button class="filter-btn grade-btn" data-grade="S급">S급</button>
  <button class="filter-btn grade-btn" data-grade="A급">A급</button>
  <button class="filter-btn grade-btn" data-grade="B급">B급</button>
  <span class="count" id="count"></span>
</div>
<div class="gallery" id="gallery">
  {cards_html or '<div class="empty">고효율 기준(총 광고비 100만원 이상)을 충족하는 광고가 없습니다.</div>'}
</div>
<div class="modal" id="videoModal">
  <button class="modal-close" id="modalClose">&times;</button>
  <video id="modalVideo" controls></video>
</div>
<script>
  const cards = [...document.querySelectorAll('.card')];
  const countEl = document.getElementById('count');
  let activeGrade = 'all';
  let activeMedia = 'all';

  function applyFilters() {{
    let v = 0;
    cards.forEach(c => {{
      const gradeOk = activeGrade === 'all' || c.dataset.grade === activeGrade;
      const mediaOk = activeMedia === 'all' || c.dataset.media === activeMedia;
      const show = gradeOk && mediaOk;
      c.style.display = show ? '' : 'none';
      if (show) v++;
    }});
    countEl.textContent = v + '개';
  }}

  applyFilters();

  document.querySelectorAll('.media-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.media-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeMedia = btn.dataset.media;
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

  // ── 영상 모달 ──
  const modal = document.getElementById('videoModal');
  const modalVideo = document.getElementById('modalVideo');
  const modalClose = document.getElementById('modalClose');

  // 저장된 영상 → 모달 재생
  document.querySelectorAll('.card.has-video').forEach(card => {{
    card.addEventListener('click', () => {{
      const src = card.dataset.video;
      if (!src) return;
      modalVideo.src = src;
      modal.classList.add('open');
      modalVideo.play().catch(() => {{}});
    }});
  }});

  // 다운로드 불가 영상 → 메타 페이지로 이동
  document.querySelectorAll('.card.has-permalink').forEach(card => {{
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
  }}
  modalClose.addEventListener('click', closeModal);
  modal.addEventListener('click', e => {{ if (e.target === modal) closeModal(); }});
  document.addEventListener('keydown', e => {{ if (e.key === 'Escape') closeModal(); }});
</script>
</body>
</html>"""

# ── 메인 ─────────────────────────────────────────────────────────────────────
def collect_media(month_tag, date_start, date_stop, media_tag, media_type):
    """media_tag: 'F_I'/'F_V', media_type: 'image'/'video' → new_results 리스트 반환"""
    ads = fetch_ads(month_tag, media_tag)
    print(f"  → [{month_tag}] {media_tag} 광고 {len(ads)}개 발견")

    raw = {}
    for ad in ads:
        ad_id, ad_name = ad["id"], ad["name"]
        creative_id = ad.get("creative", {}).get("id", "")
        print(f"  처리 중: {ad_name[:50]}...")

        # 1. 인사이트 먼저 조회
        metrics = fetch_insights(ad_id, date_start, date_stop)
        if not metrics:
            continue

        # F_I + 릴스: 영상으로 가져오되 등급/필터는 이미지 기준
        is_reels = (media_tag == "F_I" and "릴스" in ad_name)
        actual_media_type = "video" if is_reels else media_type  # 미디어 fetch용
        grade_type = "image" if (media_type == "image" or is_reels) else "video"  # 등급/필터용

        candidate = {
            "name":                ad_name,
            "creative_id":         creative_id,
            "media_type":          actual_media_type,
            "grade_type":          grade_type,
            "total_spend":         metrics["total_spend"],
            "daily_spend":         metrics["daily_spend"],
            "active_days":         metrics["active_days"],
            "cpc":                 metrics["cpc"],
            "conversions":         metrics["conversions"],
            "cost_per_conversion": metrics["cost_per_conversion"],
            "conversion_rate":     metrics["conversion_rate"],
        }
        raw.setdefault(ad_name, []).append(candidate)

    new_results = []
    for ad_name, candidates in raw.items():
        best = max(candidates, key=lambda x: x["conversions"])
        if len(candidates) > 1:
            print(f"  중복 {len(candidates)}개 → 최고 성과 선택: {ad_name[:40]} (구매 {best['conversions']:.0f}건)")

        # 2. 등급 판정 (릴스는 이미지 기준 적용)
        actual_type = best["media_type"]
        grade_type = best.pop("grade_type", actual_type)
        grade = get_grade(best["total_spend"], grade_type)
        if grade is None:
            continue

        # 3. 등급 통과한 것만 이미지/영상 다운로드
        is_reels_type = (actual_type == "video" and media_tag == "F_I")
        type_label = "릴스(영상/이미지등급)" if is_reels_type else actual_type
        print(f"    → {grade} | 총 {best['total_spend']:,.0f}원 / 구매 {best['conversions']:.0f}건 ({best['active_days']}일 집행) [{type_label}]")
        creative_id = best.pop("creative_id")
        media = fetch_creative_media(creative_id, actual_type)

        img_filename = safe_filename(ad_name, "thumb", "jpg")
        local_img = download_media(media["image_url"], img_filename)

        local_video = ""
        if actual_type == "video" and media["video_url"]:
            vid_filename = safe_filename(ad_name, "video", "mp4")
            local_video = download_media(media["video_url"], vid_filename)

        best["image_url"] = local_img if local_img else media["image_url"]
        best["video_url"] = local_video if local_video else media["video_url"]
        best["video_permalink"] = media.get("video_permalink", "")
        best["grade"] = grade
        new_results.append(best)

    print(f"  → [{month_tag}] {media_tag} 고효율 광고 {len(new_results)}개")
    return new_results

def main():
    if not MONTH_TAG:
        print("❌ MONTH_TAG가 비어 있습니다. 예: 26.05")
        return

    date_start, date_stop = get_time_range()
    print(f"📡 [{MONTH_TAG}] 광고 수집 중... (성과 기간: {date_start} ~ {date_stop})")

    existing = load_archive()
    print(f"  → 기존 아카이브 {len(existing)}개")

    merged = existing
    all_modes = [("F_I", "image"), ("F_V", "video")]
    if MEDIA_MODE == "image":
        modes = [("F_I", "image")]
    elif MEDIA_MODE == "video":
        modes = [("F_V", "video")]
    else:
        modes = all_modes
    print(f"  수집 유형: {MEDIA_MODE} → {[m[0] for m in modes]}")
    for media_tag, media_type in modes:
        print(f"\n🎯 {media_tag} ({media_type}) 수집")
        results = collect_media(MONTH_TAG, date_start, date_stop, media_tag, media_type)
        merged = merge_archive(merged, results, MONTH_TAG)

    save_archive(merged)
    print(f"\n  → 병합 후 총 {len(merged)}개")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(merged))
    print("✅ 완료")

if __name__ == "__main__":
    main()
