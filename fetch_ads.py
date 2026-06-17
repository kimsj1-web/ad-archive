import os
import json
import requests
from datetime import datetime, timedelta

# ── 설정 ────────────────────────────────────────────────────────────────────
ACCESS_TOKEN = os.environ["META_ACCESS_TOKEN"]
AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]   # act_XXXXXXXXXX 형식
DATE_PRESET = os.environ.get("DATE_PRESET", "")     # 비워두면 커스텀 기간 사용
DATE_START  = os.environ.get("DATE_START", "")      # YYYY-MM-DD
DATE_STOP   = os.environ.get("DATE_STOP", "")       # YYYY-MM-DD

API_VERSION = "v19.0"
BASE_URL    = f"https://graph.facebook.com/{API_VERSION}"

# ── 성과 등급 기준 (이미지 기준) ─────────────────────────────────────────────
def get_grade(spend, cpc):
    """
    S급: 일 광고비 500만원 이상
    A급: 일 광고비 300만원 이상
    B급: 일 광고비 100만원 이상
    CPC는 보조 지표 (600원대 목표)
    """
    daily_spend = spend  # 전체 기간 합산 → 일 평균으로 환산은 호출부에서 처리
    if daily_spend >= 5_000_000:
        return "S급"
    elif daily_spend >= 3_000_000:
        return "A급"
    elif daily_spend >= 1_000_000:
        return "B급"
    else:
        return None  # 고효율 기준 미달


def get_grade_color(grade):
    colors = {"S급": "#FF4B4B", "A급": "#FF9500", "B급": "#34C759"}
    return colors.get(grade, "#8E8E93")


# ── Meta API 호출 ────────────────────────────────────────────────────────────
def fetch_ads():
    """광고명에 F_I 포함된 광고 목록 조회"""
    url = f"{BASE_URL}/{AD_ACCOUNT_ID}/ads"
    params = {
        "access_token": ACCESS_TOKEN,
        "fields": "id,name,creative{id,thumbnail_url,image_url,object_story_spec}",
        "filtering": json.dumps([{"field": "name", "operator": "CONTAIN", "value": "F_I"}]),
        "limit": 500,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])


def fetch_insights(ad_id, days):
    """광고 인사이트(성과 지표) 조회"""
    url = f"{BASE_URL}/{ad_id}/insights"

    time_range = {}
    if DATE_PRESET:
        params_time = {"date_preset": DATE_PRESET}
    elif DATE_START and DATE_STOP:
        params_time = {"time_range": json.dumps({"since": DATE_START, "until": DATE_STOP})}
        days = (datetime.strptime(DATE_STOP, "%Y-%m-%d") - datetime.strptime(DATE_START, "%Y-%m-%d")).days + 1
    else:
        # 기본: 최근 30일
        today = datetime.today()
        params_time = {"time_range": json.dumps({
            "since": (today - timedelta(days=30)).strftime("%Y-%m-%d"),
            "until": today.strftime("%Y-%m-%d"),
        })}
        days = 30

    params = {
        "access_token": ACCESS_TOKEN,
        "fields": "spend,cpc,conversions,cost_per_conversion,conversion_rate_ranking",
        "action_attribution_windows": "7d_click",
        **params_time,
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return data[0] if data else None, days


def get_creative_image(creative):
    """크리에이티브에서 이미지 URL 추출"""
    if creative.get("image_url"):
        return creative["image_url"]
    if creative.get("thumbnail_url"):
        return creative["thumbnail_url"]
    spec = creative.get("object_story_spec", {})
    # 단일 이미지
    if "link_data" in spec and "image_url" in spec["link_data"]:
        return spec["link_data"]["image_url"]
    # 동영상 썸네일
    if "video_data" in spec and "image_url" in spec["video_data"]:
        return spec["video_data"]["image_url"]
    return ""


# ── HTML 생성 ────────────────────────────────────────────────────────────────
def build_html(ads_data):
    updated = datetime.now().strftime("%Y-%m-%d %H:%M")

    cards_html = ""
    for ad in ads_data:
        grade       = ad["grade"]
        grade_color = get_grade_color(grade)
        img_url     = ad["image_url"]
        img_tag     = f'<img src="{img_url}" alt="광고 이미지" onerror="this.style.display=\'none\'">' if img_url else '<div class="no-img">이미지 없음</div>'

        cards_html += f"""
        <div class="card" data-grade="{grade}">
            <div class="card-img">
                {img_tag}
                <span class="grade-badge" style="background:{grade_color}">{grade}</span>
            </div>
            <div class="card-body">
                <p class="ad-name">{ad['name']}</p>
                <div class="metrics">
                    <div class="metric">
                        <span class="label">일 평균 광고비</span>
                        <span class="value">{ad['daily_spend']:,.0f}원</span>
                    </div>
                    <div class="metric">
                        <span class="label">CPC</span>
                        <span class="value">{ad['cpc']:,.0f}원</span>
                    </div>
                    <div class="metric">
                        <span class="label">전환 수</span>
                        <span class="value">{ad['conversions']:,.0f}</span>
                    </div>
                    <div class="metric">
                        <span class="label">전환당 비용</span>
                        <span class="value">{ad['cost_per_conversion']:,.0f}원</span>
                    </div>
                    <div class="metric">
                        <span class="label">전환률</span>
                        <span class="value">{ad['conversion_rate']:.2f}%</span>
                    </div>
                </div>
            </div>
        </div>"""

    period_label = DATE_PRESET or (f"{DATE_START} ~ {DATE_STOP}" if DATE_START else "최근 30일")

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>전환배너 고효율 광고 아카이브</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Pretendard:wght@400;500;600;700&display=swap');

  *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

  :root {{
    --bg: #0F0F12;
    --surface: #1A1A20;
    --border: #2A2A35;
    --text: #F0F0F5;
    --muted: #8A8A9A;
    --s: #FF4B4B;
    --a: #FF9500;
    --b: #34C759;
  }}

  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Pretendard', -apple-system, sans-serif;
    min-height: 100vh;
  }}

  header {{
    padding: 40px 32px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 16px;
  }}

  .header-left h1 {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.5px;
  }}

  .header-left p {{
    margin-top: 4px;
    font-size: 13px;
    color: var(--muted);
  }}

  .updated {{
    font-size: 12px;
    color: var(--muted);
  }}

  .controls {{
    padding: 20px 32px;
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }}

  .filter-btn {{
    padding: 6px 16px;
    border-radius: 20px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--muted);
    font-size: 13px;
    cursor: pointer;
    transition: all .15s;
    font-family: inherit;
  }}

  .filter-btn:hover {{ border-color: #555; color: var(--text); }}
  .filter-btn.active {{ background: var(--text); color: var(--bg); border-color: var(--text); font-weight: 600; }}

  .count {{
    margin-left: auto;
    font-size: 13px;
    color: var(--muted);
  }}

  .gallery {{
    padding: 8px 32px 60px;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 20px;
  }}

  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    transition: transform .2s, box-shadow .2s;
  }}

  .card:hover {{
    transform: translateY(-4px);
    box-shadow: 0 12px 32px rgba(0,0,0,.4);
  }}

  .card-img {{
    position: relative;
    aspect-ratio: 1 / 1;
    background: #111;
    overflow: hidden;
  }}

  .card-img img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
  }}

  .no-img {{
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--muted);
    font-size: 12px;
  }}

  .grade-badge {{
    position: absolute;
    top: 10px;
    right: 10px;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 700;
    color: #fff;
    letter-spacing: 0.5px;
  }}

  .card-body {{
    padding: 16px;
  }}

  .ad-name {{
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}

  .metrics {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 10px;
  }}

  .metric {{
    display: flex;
    flex-direction: column;
    gap: 2px;
  }}

  .metric .label {{
    font-size: 10px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}

  .metric .value {{
    font-size: 14px;
    font-weight: 600;
  }}

  .empty {{
    grid-column: 1 / -1;
    text-align: center;
    padding: 80px 0;
    color: var(--muted);
  }}
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
  <button class="filter-btn" data-filter="S급" style="--accent:#FF4B4B">S급</button>
  <button class="filter-btn" data-filter="A급" style="--accent:#FF9500">A급</button>
  <button class="filter-btn" data-filter="B급" style="--accent:#34C759">B급</button>
  <span class="count" id="count"></span>
</div>

<div class="gallery" id="gallery">
  {cards_html if cards_html else '<div class="empty">고효율 기준(일 광고비 100만원 이상)을 충족하는 F_I 광고가 없습니다.</div>'}
</div>

<script>
  const cards = [...document.querySelectorAll('.card')];
  const countEl = document.getElementById('count');

  function updateCount(visible) {{
    countEl.textContent = visible + '개';
  }}

  updateCount(cards.length);

  document.querySelectorAll('.filter-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const filter = btn.dataset.filter;
      let visible = 0;
      cards.forEach(card => {{
        const show = filter === 'all' || card.dataset.grade === filter;
        card.style.display = show ? '' : 'none';
        if (show) visible++;
      }});
      updateCount(visible);
    }});
  }});
</script>
</body>
</html>"""
    return html


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main():
    print("📡 메타 광고 데이터 수집 중...")
    ads = fetch_ads()
    print(f"  → F_I 광고 {len(ads)}개 발견")

    results = []
    for ad in ads:
        ad_id   = ad["id"]
        ad_name = ad["name"]
        creative = ad.get("creative", {})
        image_url = get_creative_image(creative)

        insights, days = fetch_insights(ad_id, 30)
        if not insights:
            continue

        total_spend = float(insights.get("spend", 0))
        daily_spend = total_spend / max(days, 1)

        cpc = float(insights.get("cpc", 0))

        # 전환 수 (purchase 또는 첫 번째 action)
        conversions_list = insights.get("conversions", [])
        conversions = sum(float(c.get("value", 0)) for c in conversions_list) if conversions_list else 0

        cpp_list = insights.get("cost_per_conversion", [])
        cost_per_conv = float(cpp_list[0]["value"]) if cpp_list else 0

        # 전환률: Meta는 conversion_rate_ranking을 제공하지만 수치는 별도 계산
        # clicks 기반 전환률 = conversions / clicks * 100
        # insights에 clicks 추가 필요 → 여기선 cost_per_conversion 기반으로 추정
        conversion_rate = (conversions / (total_spend / cpc) * 100) if cpc and total_spend else 0

        grade = get_grade(daily_spend, cpc)
        if grade is None:
            continue  # 고효율 기준 미달 제외

        results.append({
            "name":               ad_name,
            "image_url":          image_url,
            "grade":              grade,
            "daily_spend":        daily_spend,
            "cpc":                cpc,
            "conversions":        conversions,
            "cost_per_conversion": cost_per_conv,
            "conversion_rate":    conversion_rate,
        })

    # S → A → B 순 정렬
    grade_order = {"S급": 0, "A급": 1, "B급": 2}
    results.sort(key=lambda x: (grade_order[x["grade"]], -x["daily_spend"]))

    print(f"  → 고효율 광고 {len(results)}개 (S/A/B급 기준 충족)")

    html = build_html(results)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("✅ index.html 생성 완료")


if __name__ == "__main__":
    main()
