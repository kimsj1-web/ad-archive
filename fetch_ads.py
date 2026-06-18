import os
import json
import requests
from datetime import datetime, timedelta

# ── 설정 ────────────────────────────────────────────────────────────────────
ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
MONTH_TAG     = os.environ.get("MONTH_TAG", "")
DATE_START    = os.environ.get("DATE_START", "")
DATE_STOP     = os.environ.get("DATE_STOP", "")

API_VERSION  = "v19.0"
BASE_URL     = f"https://graph.facebook.com/{API_VERSION}"
ARCHIVE_FILE = "archive.json"

# ── 성과 등급 기준 (총 광고비 기준) ──────────────────────────────────────────
def get_grade(total_spend):
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
    return {"SS급": "#BF5AF2", "S급": "#FF4B4B", "A급": "#FF9500", "B급": "#34C759"}.get(grade, "#8E8E93")

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
        month_end = datetime(year, month + 1, 1) - timedelta(days=1) if month < 12 else datetime(year + 1, 1, 1) - timedelta(days=1)
        today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)
        # 진행 중인 달이면 오늘까지만, 지난 달이면 월말까지
        end = min(month_end, today)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    today = datetime.today()
    return (today - timedelta(days=7)).strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")

# ── 광고 목록 조회 ────────────────────────────────────────────────────────────
def fetch_ads(month_tag):
    url = f"{BASE_URL}/{AD_ACCOUNT_ID}/ads"
    params = {
        "fields": "id,name,status,creative{id}",
        "filtering": json.dumps([
            {"field": "name", "operator": "CONTAIN", "value": "F_I"},
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

# ── 크리에이티브 이미지 조회 ──────────────────────────────────────────────────
def fetch_creative_image(creative_id):
    if not creative_id:
        return ""
    try:
        url = f"{BASE_URL}/{creative_id}"
        data = api_get(url, {"fields": "thumbnail_url,image_url,object_story_spec"})
        if data.get("image_url"):
            return data["image_url"]
        if data.get("thumbnail_url"):
            return data["thumbnail_url"]
        spec = data.get("object_story_spec", {})
        if "link_data" in spec:
            return spec["link_data"].get("image_url", "")
        if "video_data" in spec:
            return spec["video_data"].get("image_url", "")
    except Exception as e:
        print(f"    이미지 조회 실패 (creative {creative_id}): {e}")
    return ""

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
            new_grade = get_grade(rec["total_spend"])
            if new_grade:
                rec["grade"] = new_grade

            # 게재 상태: 어느 하나라도 ACTIVE면 게재중
            if new_ad.get("status") == "ACTIVE":
                rec["status"] = "ACTIVE"

            # 이미지 없으면 업데이트
            if not rec.get("image_url") and new_ad.get("image_url"):
                rec["image_url"] = new_ad["image_url"]
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
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    all_periods = sorted({p for ad in ads_data for p in ad.get("periods", [])})
    periods_str = " · ".join(all_periods) if all_periods else "전체"

    cards_html = ""
    for ad in ads_data:
        grade_color = get_grade_color(ad["grade"])
        img = ad.get("image_url", "")
        img_tag = f'<img src="{img}" alt="광고 이미지" onerror="this.style.display=\'none\'">' if img else '<div class="no-img">이미지 없음</div>'
        periods_tag = " ".join(f'<span class="period-tag">{p}</span>' for p in ad.get("periods", []))
        product = get_product(ad["name"])
        status = ad.get("status", "")
        is_active = status == "ACTIVE"
        status_label = "게재중" if is_active else "게재완료"
        status_color = "#34C759" if is_active else "#8A8A9A"

        cards_html += f"""
        <div class="card" data-grade="{ad['grade']}" data-product="{product}">
            <div class="card-img">
                {img_tag}
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
                    <div class="metric">
                        <span class="label">집행일수</span>
                        <span class="value">{int(ad.get('active_days', 0))}일 <span class="status-dot" style="background:{status_color}"></span><span class="status-txt" style="color:{status_color}">{status_label}</span></span>
                    </div>
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
  .card-body {{ padding:16px; }}
  .ad-name {{ font-size:11px; color:var(--muted); margin-bottom:12px; line-height:1.5; word-break:break-all; }}
  .metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .metric {{ display:flex; flex-direction:column; gap:2px; }}
  .metric .label {{ font-size:10px; color:var(--muted); letter-spacing:0.5px; }}
  .metric .value {{ font-size:13px; font-weight:600; display:flex; align-items:center; gap:4px; }}
  .status-dot {{ width:6px; height:6px; border-radius:50%; display:inline-block; flex-shrink:0; }}
  .status-txt {{ font-size:11px; font-weight:500; }}
  .periods {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:4px; }}
  .period-tag {{ font-size:10px; color:var(--muted); border:1px solid var(--border); border-radius:4px; padding:1px 6px; }}
  .empty {{ grid-column:1/-1; text-align:center; padding:80px 0; color:var(--muted); }}
</style>
</head>
<body>
<header>
  <div class="header-left">
    <h1>전환배너 고효율 광고 아카이브</h1>
    <p>광고명 F_I · 누적 조회: {periods_str}</p>
  </div>
  <span class="updated">마지막 업데이트: {updated}</span>
</header>
<div class="controls">
  <span class="filter-label">등급</span>
  <button class="filter-btn grade-btn active" data-grade="all">전체</button>
  <button class="filter-btn grade-btn" data-grade="SS급">SS급</button>
  <button class="filter-btn grade-btn" data-grade="S급">S급</button>
  <button class="filter-btn grade-btn" data-grade="A급">A급</button>
  <button class="filter-btn grade-btn" data-grade="B급">B급</button>
  <span class="count" id="count"></span>
</div>
<div class="product-filters">
  <span class="filter-label">제품군</span>
  <button class="filter-btn product-btn active" data-product="all">전체</button>
  <button class="filter-btn product-btn" data-product="빙과">빙과</button>
  <button class="filter-btn product-btn" data-product="제과">제과</button>
</div>
<div class="gallery" id="gallery">
  {cards_html or '<div class="empty">고효율 기준(일 광고비 100만원 이상)을 충족하는 F_I 광고가 없습니다.</div>'}
</div>
<script>
  const cards = [...document.querySelectorAll('.card')];
  const countEl = document.getElementById('count');
  let activeGrade = 'all';
  let activeProduct = 'all';

  function applyFilters() {{
    let v = 0;
    cards.forEach(c => {{
      const gradeOk = activeGrade === 'all' || c.dataset.grade === activeGrade;
      const productOk = activeProduct === 'all' || c.dataset.product === activeProduct;
      const show = gradeOk && productOk;
      c.style.display = show ? '' : 'none';
      if (show) v++;
    }});
    countEl.textContent = v + '개';
  }}

  applyFilters();

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
</script>
</body>
</html>"""

# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    if not MONTH_TAG:
        print("❌ MONTH_TAG가 비어 있습니다. 예: 26.05")
        return

    date_start, date_stop = get_time_range()
    print(f"📡 [{MONTH_TAG}] F_I 광고 수집 중... (성과 기간: {date_start} ~ {date_stop})")

    ads = fetch_ads(MONTH_TAG)
    print(f"  → [{MONTH_TAG}] F_I 광고 {len(ads)}개 발견")

    # 광고명별로 모든 후보 수집 (같은 이름 = 여러 광고 세트에 걸친 동일 소재)
    raw = {}
    for ad in ads:
        ad_id, ad_name = ad["id"], ad["name"]
        creative_id = ad.get("creative", {}).get("id", "")
        print(f"  처리 중: {ad_name[:50]}...")

        image_url = fetch_creative_image(creative_id)
        metrics   = fetch_insights(ad_id, date_start, date_stop)

        if not metrics:
            continue

        ad_status = ad.get("status", "")
        candidate = {
            "name":                ad_name,
            "image_url":           image_url,
            "status":              ad_status,
            "total_spend":         metrics["total_spend"],
            "daily_spend":         metrics["daily_spend"],
            "active_days":         metrics["active_days"],
            "cpc":                 metrics["cpc"],
            "conversions":         metrics["conversions"],
            "cost_per_conversion": metrics["cost_per_conversion"],
            "conversion_rate":     metrics["conversion_rate"],
        }
        raw.setdefault(ad_name, []).append(candidate)

    # 같은 광고명이 여러 개면 구매 수 기준 최고 성과만 선택
    new_results = []
    for ad_name, candidates in raw.items():
        best = max(candidates, key=lambda x: x["conversions"])
        if len(candidates) > 1:
            print(f"  중복 {len(candidates)}개 → 최고 성과 선택: {ad_name[:40]} (구매 {best['conversions']:.0f}건)")

        grade = get_grade(best["total_spend"])
        if grade is None:
            continue

        best["grade"] = grade
        print(f"    → {grade} | 총 {best['total_spend']:,.0f}원 / 구매 {best['conversions']:.0f}건 ({best['active_days']}일 집행)")
        new_results.append(best)

    print(f"  → [{MONTH_TAG}] 고효율 광고 {len(new_results)}개 (SS/S/A/B급)")

    existing = load_archive()
    print(f"  → 기존 아카이브 {len(existing)}개")
    merged = merge_archive(existing, new_results, MONTH_TAG)
    save_archive(merged)
    print(f"  → 병합 후 총 {len(merged)}개")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(merged))
    print("✅ 완료")

if __name__ == "__main__":
    main()
