"""
국세청 세법해석 키워드 검색 - 부서 배포용 버전
======================================================
개인 프로토타입 버전과의 차이:
- API 인증키(OC)를 코드에 직접 적지 않고 환경변수(NTS_LAW_OC)로 분리
  → 소스를 공유해도 키가 노출되지 않고, 배포 환경마다 다른 키를 쓸 수 있음
- 같은 키워드 반복 검색 시 API를 매번 다시 부르지 않도록 간단한 캐시(10분) 추가
  → 부서원 여러 명이 동시에 써도 하루 호출 한도를 아낄 수 있음
- host=0.0.0.0으로 실행 → 같은 네트워크의 다른 PC에서도 접속 가능
- 운영 배포 시 gunicorn 사용 권장 (requirements.txt에 포함) - README 참고
"""

import os
import time
import requests
import xml.etree.ElementTree as ET
from flask import Flask, request, render_template_string

OC = os.environ.get("NTS_LAW_OC", "")   # 배포 환경에서 환경변수로 주입
API_URL = "https://www.law.go.kr/DRF/lawSearch.do"

app = Flask(__name__)

# 아주 단순한 인메모리 캐시: {키워드: (조회시각, 결과)} - 10분 재사용
_CACHE = {}
_CACHE_TTL = 600  # seconds

PAGE = """
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>국세청 세법해석 검색</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;900&display=swap" rel="stylesheet">
<style>
  :root {
    --green: #03C75A; --green-dark: #02A94D; --green-tint: #E9FBF1;
    --ink: #1F2328; --muted: #6B7280; --line: #E5E8EB; --bg: #F6F8F7;
    --error: #D93025; --error-bg: #FDECEA;
  }
  * { box-sizing: border-box; }
  body { font-family: 'Noto Sans KR', -apple-system, sans-serif; background: var(--bg); color: var(--ink); margin: 0; padding: 48px 16px 80px; }
  .wrap { max-width: 680px; margin: 0 auto; }
  .brand { text-align: center; margin-bottom: 28px; }
  .brand .badge { display: inline-block; font-size: 12px; font-weight: 700; letter-spacing: .06em; color: var(--green-dark); background: var(--green-tint); padding: 4px 10px; border-radius: 999px; margin-bottom: 10px; }
  .brand h1 { font-size: 26px; font-weight: 900; margin: 0; letter-spacing: -0.01em; }
  .search-bar { display: flex; align-items: center; gap: 8px; background: #fff; border: 2px solid var(--line); border-radius: 999px; padding: 6px 8px 6px 20px; width: 100%; transition: border-color .15s, box-shadow .15s; }
  .search-bar:focus-within { border-color: var(--green); box-shadow: 0 0 0 4px var(--green-tint); }
  .search-bar input { flex: 1; border: none; outline: none; font: inherit; font-size: 16px; background: transparent; color: var(--ink); }
  .search-bar input::placeholder { color: #9AA1A9; }
  .search-btn { display: flex; align-items: center; justify-content: center; width: 40px; height: 40px; border-radius: 50%; border: none; cursor: pointer; background: var(--green); color: #fff; flex-shrink: 0; }
  .search-btn:hover { background: var(--green-dark); }
  .search-btn svg { width: 18px; height: 18px; }
  #result { margin-top: 32px; }
  .result-count { font-size: 13.5px; color: var(--muted); margin: 0 4px 14px; }
  .result-count b { color: var(--ink); }
  .item { display: flex; align-items: center; justify-content: space-between; gap: 16px; background: #fff; border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-bottom: 10px; border-left: 3px solid transparent; transition: box-shadow .15s, border-left-color .15s, transform .1s; }
  .item:hover { box-shadow: 0 4px 16px rgba(31,35,40,.06); border-left-color: var(--green); transform: translateY(-1px); }
  .item-main { min-width: 0; flex: 1; }
  .title { font-weight: 700; font-size: 15.5px; line-height: 1.4; }
  .title a { color: var(--ink); text-decoration: none; }
  .title a:hover { color: var(--green-dark); text-decoration: underline; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 9px; }
  .chip { font-size: 12px; color: var(--muted); background: #F1F3F2; border-radius: 6px; padding: 3px 8px; white-space: nowrap; }
  .chip.date { color: var(--green-dark); background: var(--green-tint); font-weight: 600; }
  .view-link { flex-shrink: 0; display: flex; align-items: center; gap: 5px; color: var(--green-dark); font-size: 12.5px; font-weight: 600; white-space: nowrap; text-decoration: none; border: 1px solid var(--green); border-radius: 999px; padding: 7px 12px; transition: background .15s, color .15s; }
  .view-link:hover { background: var(--green); color: #fff; }
  .view-link svg { width: 14px; height: 14px; }
  .empty { text-align: center; color: var(--muted); font-size: 14px; padding: 40px 20px; background: #fff; border: 1px dashed var(--line); border-radius: 14px; }
  .note { color: var(--muted); font-size: 12.5px; text-align: center; margin: 14px 0 0; line-height: 1.6; }
  .error { color: var(--error); background: var(--error-bg); border-radius: 12px; padding: 14px 16px; font-size: 13.5px; }
</style>
</head>
<body>
  <div class="wrap">
    <div class="brand">
      <span class="badge">국세법령정보 · 국세청 법령해석</span>
      <h1>세법해석 키워드 검색</h1>
    </div>

    <form method="get" action="/">
      <div class="search-bar">
        <input type="text" name="q" placeholder="키워드를 입력하세요 (예: 연구개발비 세액공제)" value="{{ q }}">
        <button type="submit" class="search-btn" title="검색">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
          </svg>
        </button>
      </div>
    </form>

    <div id="result">
      {% if error %}
        <div class="error">{{ error }}</div>
      {% endif %}

      {% if q and not error %}
        <p class="result-count">'{{ q }}' 검색 결과 - {% if total_cnt > items|length %}총 <b>{{ total_cnt }}건</b> 중 최신 {{ items|length }}건 표시{% else %}총 <b>{{ total_cnt }}건</b>{% endif %}{% if cached %} (캐시됨){% endif %}</p>
        {% for it in items %}
          <div class="item">
            <div class="item-main">
              <div class="title">
                {% if it.link %}<a href="{{ it.link }}" target="_blank">{{ it.title }}</a>
                {% else %}{{ it.title }}{% endif %}
              </div>
              <div class="chips">
                {% if it.inq %}<span class="chip">질의 {{ it.inq }}</span>{% endif %}
                {% if it.rpl %}<span class="chip">해석 {{ it.rpl }}</span>{% endif %}
                {% if it.date %}<span class="chip date">{{ it.date }}</span>{% endif %}
              </div>
            </div>
            {% if it.link %}
            <a class="view-link" href="{{ it.link }}" target="_blank" title="원문보기">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                <polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
              </svg>
              <span>원문보기</span>
            </a>
            {% endif %}
          </div>
        {% endfor %}
        {% if items|length == 0 %}
          <div class="empty">'{{ q }}'에 대한 검색 결과가 없습니다.<br>다른 키워드로 다시 시도해보세요.</div>
        {% endif %}
      {% endif %}
    </div>

    <p class="note">국세법령정보 공동활용(law.go.kr) 공식 Open API 기반 · 원문은 taxlaw.nts.go.kr로 연결됩니다.</p>
  </div>
</body>
</html>
"""

MISSING_KEY_PAGE = """
<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>설정 필요</title></head>
<body style="font-family:sans-serif; max-width:600px; margin:80px auto; line-height:1.7;">
<h2>⚠️ 인증키(OC)가 설정되지 않았습니다</h2>
<p>환경변수 <code>NTS_LAW_OC</code>에 open.law.go.kr에서 발급받은 API인증키를 설정해주세요.</p>
<p>로컬 실행 예시: <code>NTS_LAW_OC=발급받은키 python app.py</code></p>
</body></html>
"""


def call_api(keyword, display=100):
    params = {
        "OC": OC,
        "target": "ntsCgmExpc",
        "type": "XML",
        "query": keyword,
        "display": display,
    }
    res = requests.get(API_URL, params=params, timeout=10)
    res.raise_for_status()
    return res.content


def search_nts_expc(keyword, display=100):
    now = time.time()
    cached = _CACHE.get(keyword)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1], cached[2], True

    xml_bytes = call_api(keyword, display)
    root = ET.fromstring(xml_bytes)

    total_cnt_node = root.find(".//totalCnt")
    total_cnt = int(total_cnt_node.text) if total_cnt_node is not None and total_cnt_node.text else 0

    items = []
    for node in root.findall(".//cgmExpc"):
        fields = {child.tag: (child.text or "").strip() for child in node}
        if not fields:
            continue
        title = fields.get("안건명", "(제목 없음)")
        link = fields.get("법령해석상세링크", "")
        if link and link.startswith("/"):
            link = "https://www.law.go.kr" + link

        date_raw = fields.get("해석일자", "")
        date_fmt = date_raw
        if len(date_raw) == 8 and date_raw.isdigit():
            date_fmt = f"{date_raw[:4]}.{date_raw[4:6]}.{date_raw[6:]}"

        items.append({
            "title": title, "link": link,
            "inq": fields.get("질의기관명", ""), "rpl": fields.get("해석기관명", ""),
            "date": date_fmt, "date_raw": date_raw,
        })

    items.sort(key=lambda x: x["date_raw"], reverse=True)
    total_cnt = total_cnt or len(items)

    _CACHE[keyword] = (now, items, total_cnt)
    return items, total_cnt, False


@app.route("/")
def index():
    if not OC:
        return MISSING_KEY_PAGE

    q = request.args.get("q", "").strip()
    items, error, total_cnt, cached = [], None, 0, False
    if q:
        try:
            items, total_cnt, cached = search_nts_expc(q)
        except Exception as e:
            error = f"검색 중 오류가 발생했습니다: {e}"
    return render_template_string(PAGE, q=q, items=items, error=error, total_cnt=total_cnt, cached=cached)


@app.route("/healthz")
def healthz():
    return {"status": "ok", "oc_configured": bool(OC)}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
