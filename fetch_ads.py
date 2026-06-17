import os
import json
import requests
from datetime import datetime, timedelta

# ── 설정 ────────────────────────────────────────────────────────────────────
ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]
DATE_START    = os.environ.get("DATE_START", "")
DATE_STOP     = os.environ.get("DATE_STOP", "")

API_VERSION = "v19.0"
BASE_URL    = f"https://graph.facebook.com/{API_VERSION}"

# ── 성과 등급 기준 ────────────────────────────────────────────────────────────
def get_grade(daily_spend):
    if daily_spend >= 5_000_000:
        return "S급"
    elif daily_spend >= 3_000_000:
        return "A급"
    elif daily_spend >= 1_000_000:
        return "B급"
    return None

def get_grade_color(grade):
    return {"S급": "#FF4B4B", "A급": "#FF9500", "B급": "#34C759"}.get(grade, "#8E8E93")

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

# ── 광고 목록 조회 (크리에이티브 제외) ────────────────────────────────────────
def fetch_ads():
    url = f"{BASE_URL}/{AD_ACCOUNT_ID}/ads"
    params = {
        "fields": "id,name,creative{id}",
        "filtering": json.dumps([{"field": "name", "operator": "CONTAIN", "value": "F_I"}]),
        "limit": 500,
    }
    data = api_get(url, params)
    return data.get("data", [])

# ── 크리에이티브 이미지 개별 조회 ─────────────────────────────────────────────
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

# ── 인사이트 조회 ─────────────────────────────────────────────────────────────
def fetch_insights(ad_id):
    url = f"{BASE_URL}/{ad_id}/insights"

    if DATE_START and DATE_STOP:
        days = (datetime.strptime(DATE_STOP, "%Y-%m-%d") - datetime.strptime(DATE_START, "%Y-%m-%d")).days + 1
        time_param = {"time_range": json.dumps({"since": DATE_START, "until": DATE_STOP})}
    else:
        days = 30
        today = datetime.today()
        time_param = {"time_range": json.dumps({
            "since": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
            "until": today.strftime("%Y-%m-%d"),
        })}

    params = {
        "fields": "spend,cpc,actions,cost_per_action_type,clicks",
        **time_param,
    }
    try:
        data = api_get(url, params).get("data", [])
        return (data[0] if data else None), days
    except Exception:
        return None, days

# ── HTML 생성 ────────────────────────────────────────────────────────────────
def build_html(ads_data):
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")
    period_label = f"{DATE_START} ~ {DATE_STOP}" if DATE_START else "최근 30일"

    cards_html = ""
    for ad in ads_data:
        grade_color = get_grade_color(ad["grade"])
        img = ad["image_url"]
        img_tag = f'<img src="{img}" alt="광고 이미지" onerror="this.style.display=\'none\'">' if img else '<div class="no-img">이미지 없음</div>'

        cards_html += f"""
        <div class="card" data-grade="{ad['grade']}">
            <div class="card-img">
                {img_tag}
                <span class="grade-badge" style="background:{grade_color}">{ad['grade']}</span>
            </div>
            <div class="card-body">
                <p class="ad-name" title="{ad['name']}">{ad['name']}</p>
                <div class="metrics">
                    <div class="metric"><span class="label">일 평균 광고비</span><span class="value">{ad['daily_spend']:,.0f}원</span></div>
                    <div class="metric"><span class="label">CPC</span><span class="value">{ad['cpc']:,.0f}원</span></div>
                    <div class="metric"><span class="label">전환 수</span><span class="value">{ad['conversions']:,.0f}</span></div>
                    <div class="metric"><span class="label">전환당 비용</span><span class="value">{ad['cost_per_conversion']:,.0f}원</span></div>
                    <div class="metric"><span class="label">전환률</span><span class="value">{ad['conversion_rate']:.2f}%</span></div>
                </div>
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
  .controls {{ padding:20px 32px; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }}
  .filter-btn {{ padding:6px 16px; border-radius:20px; border:1px solid var(--border); background:transparent; color:var(--muted); font-size:13px; cursor:pointer; transition:all .15s; font-family:inherit; }}
  .filter-btn:hover {{ border-color:#555; color:var(--text); }}
  .filter-btn.active {{ background:var(--text); color:var(--bg); border-color:var(--text); font-weight:600; }}
  .count {{ margin-left:auto; font-size:13px; color:var(--muted); }}
  .gallery {{ padding:8px 32px 60px; display:grid; grid-template-columns:repeat(auto-fill,minmax(260px,1fr)); gap:20px; }}
  .card {{ background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; transition:transform .2s,box-shadow .2s; }}
  .card:hover {{ transform:translateY(-4px); box-shadow:0 12px 32px rgba(0,0,0,.4); }}
  .card-img {{ position:relative; aspect-ratio:1/1; background:#111; overflow:hidden; }}
  .card-img img {{ width:100%; height:100%; object-fit:cover; }}
  .no-img {{ width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:var(--muted); font-size:12px; }}
  .grade-badge {{ position:absolute; top:10px; right:10px; padding:3px 10px; border-radius:20px; font-size:12px; font-weight:700; color:#fff; }}
  .card-body {{ padding:16px; }}
  .ad-name {{ font-size:12px; color:var(--muted); margin-bottom:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }}
  .metric {{ display:flex; flex-direction:column; gap:2px; }}
  .metric .label {{ font-size:10px; color:var(--muted); letter-spacing:0.5px; }}
  .metric .value {{ font-size:14px; font-weight:600; }}
  .empty {{ grid-column:1/-1; text-align:center; padding:80px 0; color:var(--muted); }}
</style>
</head>
<body>
<header>
  <div class="header-left">
    <h1>전환배너 고효율 광고 아카이브</h1>
    <p>광고명 F_I · 조회 기간: {period_label}</p>
  </div>
  <span class="updated">마지막 업데이트: {updated}</span>
</header>
<div class="controls">
  <button class="filter-btn active" data-filter="all">전체</button>
  <button class="filter-btn" data-filter="S급">S급</button>
  <button class="filter-btn" data-filter="A급">A급</button>
  <button class="filter-btn" data-filter="B급">B급</button>
  <span class="count" id="count"></span>
</div>
<div class="gallery" id="gallery">
  {cards_html or '<div class="empty">고효율 기준(일 광고비 100만원 이상)을 충족하는 F_I 광고가 없습니다.</div>'}
</div>
<script>
  const cards = [...document.querySelectorAll('.card')];
  const countEl = document.getElementById('count');
  function updateCount(n) {{ countEl.textContent = n + '개'; }}
  updateCount(cards.length);
  document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const f = btn.dataset.filter;
      let v = 0;
      cards.forEach(c => {{ const show = f==='all'||c.dataset.grade===f; c.style.display=show?'':'none'; if(show)v++; }});
      updateCount(v);
    }});
  }});
</script>
</body>
</html>"""

# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    print("📡 메타 광고 데이터 수집 중...")
    ads = fetch_ads()
    print(f"  → F_I 광고 {len(ads)}개 발견")

    results = []
    for ad in ads:
        ad_id   = ad["id"]
        ad_name = ad["name"]

        # 크리에이티브 이미지 개별 조회
        creative_id = ad.get("creative", {}).get("id", "")
        print(f"  이미지 조회: {ad_name[:30]}...")
        image_url = fetch_creative_image(creative_id)

        # 인사이트
        insights, days = fetch_insights(ad_id)
        if not insights:
            print(f"  ⚠️  인사이트 없음, 스킵: {ad_name[:30]}")
            continue

        total_spend = float(insights.get("spend", 0))
        daily_spend = total_spend / max(days, 1)
        cpc         = float(insights.get("cpc", 0) or 0)
        clicks      = float(insights.get("clicks", 0) or 0)

        # 전환 수 (purchase 액션)
        actions = insights.get("actions", [])
        purchase = next((float(a["value"]) for a in actions if a["action_type"] == "purchase"), 0)
        conversions = purchase if purchase else sum(float(a["value"]) for a in actions)

        # 전환당 비용
        cpp_list = insights.get("cost_per_action_type", [])
        cost_per_conv = float(cpp_list[0]["value"]) if cpp_list else (total_spend / conversions if conversions else 0)

        # 전환률
        conversion_rate = (conversions / clicks * 100) if clicks else 0

        grade = get_grade(daily_spend)
        if grade is None:
            continue

        results.append({
            "name": ad_name, "image_url": image_url, "grade": grade,
            "daily_spend": daily_spend, "cpc": cpc,
            "conversions": conversions, "cost_per_conversion": cost_per_conv,
            "conversion_rate": conversion_rate,
        })

    grade_order = {"S급": 0, "A급": 1, "B급": 2}
    results.sort(key=lambda x: (grade_order[x["grade"]], -x["daily_spend"]))
    print(f"  → 고효율 광고 {len(results)}개")

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(build_html(results))
    print("✅ index.html 생성 완료")

if __name__ == "__main__":
    main()
