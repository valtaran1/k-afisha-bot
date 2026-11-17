"""
К-Афиша: автопостинг афиш мероприятий Кореи в Telegram. Бесплатно.

Источники (без ключей):
  • Melon Ticket и YES24 Ticket — анонсы открытия продаж (концерты, мюзиклы,
    фанмитинги, классика, шоу) с датами, ценами и числом просмотров;
  • KINTEX, COEX, BEXCO — выставки, авто/мото/авиа шоу, ярмарки, фестивали;
  • Google News по ключевым словам — диджеи, авиашоу, поп-апы, открытия мест
    отдыха, сезонное (горнолыжные курорты, аквапарки, фестивали цветения).

ИИ (Gemini, бесплатный ключ) оставляет только мероприятия, пишет пост на живом
русском с датой/временем, местом, ценой, ссылкой на билеты и хэштегами городов.
Популярные события публикуются сразу, в первую очередь.
"""

import html
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# ======================= НАСТРОЙКИ =======================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL = os.environ.get("CHANNEL", "@k_afiisha")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Kakao Local API (бесплатно): по названию площадки находит точный адрес, как Kakao Map
KAKAO_REST_KEY = os.environ.get("KAKAO_REST_KEY", "")
GEMINI_MODELS = os.environ.get("GEMINI_MODELS",
                               "gemini-flash-latest,gemini-flash-lite-latest,gemma-3-27b-it")
GEMINI_TIMEOUT = 60
USE_GOOGLE_SEARCH = True     # уточнять детали через поиск Google внутри Gemini (если доступно)

MODE = os.environ.get("MODE", "events")      # events | seasonal
MAX_POSTS_PER_RUN = 2        # обычных постов за запуск
MAX_HOT_POSTS_PER_RUN = 4    # популярных (публикуются сверх обычного лимита)
MAX_CHECKS_PER_RUN = 5       # сколько событий отдавать ИИ за запуск (бережём бесплатный лимит)
HOT_SCORE = 75               # с какой оценки событие может быть «горячим» (много просмотров анонса и т.п.)
PAUSE_BETWEEN_POSTS = int(os.environ.get("PAUSE_BETWEEN_POSTS", "60"))
EXPO_DAYS_AHEAD = 45         # выставки публикуем, когда до начала ≤ 45 дней
GENERATE_IMAGES = False      # картинки только с официальных афиш; генерацию не используем
STATE_FILE = "seen.json"

# Поиск новостей: (запрос, приоритет). Сезонные добавляются по месяцу ниже.
NEWS_QUERIES = [
    ("티켓 오픈 내한공연", 70), ("내한 공연 확정", 70), ("티켓 예매 오픈 콘서트", 55),
    ("DJ 공연 페스티벌", 50), ("뮤직 페스티벌 라인업", 55), ("에어쇼 개최", 50),
    ("모터쇼 개최", 50), ("모빌리티쇼", 50), ("모터사이클쇼", 50), ("오토살롱", 45),
    ("박람회 개최 관람객", 35), ("팝업스토어 오픈", 35), ("뷰티 박람회", 40),
    ("패션위크 서울", 45), ("마스터클래스 개최", 35), ("오케스트라 공연 예매", 40),
    ("필하모닉 내한", 55), ("축제 개최 일정", 40), ("개장 관광객 테마파크", 40),
    ("신규 오픈 복합문화공간", 35), ("불꽃축제", 60),
]
SEASONAL_QUERIES = {
    (11, 12, 1, 2, 3): [("스키장 개장", 65), ("스키장 시즌권 할인", 55), ("눈썰매장 개장", 50),
                        ("빙어축제", 50), ("얼음축제 개막", 50), ("크리스마스 마켓", 45),
                        ("빛축제 개막", 45), ("아이스링크 개장", 45)],
    (4, 5): [("벚꽃축제 개막", 60), ("꽃축제 개막", 50)],
    (6, 7, 8): [("워터파크 개장", 65), ("워터파크 할인", 55), ("해수욕장 개장", 55),
                ("물놀이 축제", 45)],
    (9, 10): [("단풍 축제", 50), ("가을 축제 개막", 45), ("불꽃축제", 60)],
}
# Заголовки, которые точно не афиша — отсеиваем без траты лимита ИИ
TITLE_BLACKLIST = ["발매", "컴백", "앨범", "음원", "뮤직비디오", "뮤비", "차트", "별세", "사망",
                   "기부", "결혼", "열애", "논란", "소송", "주가", "실적", "선거", "대통령",
                   "Taipei", "Tokyo", "Bangkok", "Manila", "Jakarta", "Singapore", "Hong Kong",
                   "Macau", "Osaka", "타이베이", "도쿄", "방콕", "싱가포르", "마카오", "오사카",
                   "유학박람회", "채용", "세미나", "포럼", "학술", "심포지엄"]
# Домены, ссылкам на которые можно доверять как на покупку билетов/бронирование
TRUSTED_LINK_DOMAINS = ["ticket.melon.com", "ticket.yes24.com", "tickets.interpark.com",
                        "nol.yanolja.com", "ticketlink.co.kr", "kintex.com", "coex.co.kr",
                        "bexco.co.kr", "go.kr", "or.kr", "ticket.11st.co.kr",
                        "booking.naver.com", "m.booking.naver.com"]
TICKET_DOMAINS = ["ticket.melon.com", "ticket.yes24.com", "tickets.interpark.com", "nol.yanolja.com",
                  "ticketlink.co.kr"]
# Домены СМИ, блогов и соцсетей — это не «официальная ссылка на билеты»
NOT_OFFICIAL = (r"news|daum\.net|naver\.com$|blog|tistory|youtube|youtu\.be|wikipedia|namu\.wiki|"
                r"chosun|joongang|donga|hani\.co|yna\.co|yonhap|kbs|mbc|sbs|jtbc|mk\.co|hankyung|"
                r"edaily|newsis|news1|heraldcorp|koreaherald|koreatimes|ytn|vertexaisearch|google\.")
# =========================================================

KST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}


def now_kst():
    return datetime.now(KST)


def log(*args):
    print(now_kst().strftime("%H:%M:%S"), *args, flush=True)


# ---------- сеть ----------
def http(url, data=None, timeout=30, max_bytes=6_000_000, headers=None):
    """GET/POST с жёстким лимитом общего времени. Возвращает (bytes, charset, final_url)."""
    deadline = time.time() + timeout
    body = None
    h = dict(UA)
    if headers:
        h.update(headers)
    if data is not None:
        body = urllib.parse.urlencode(data).encode() if isinstance(data, dict) else data
        h.setdefault("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=min(timeout, 20)) as r:
        charset = r.headers.get_content_charset()
        final_url = r.geturl()
        chunks, size = [], 0
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"слишком долго: {url}")
            c = r.read(65536)
            if not c:
                break
            chunks.append(c)
            size += len(c)
            if size > max_bytes:
                break
    return b"".join(chunks), charset, final_url


def decode(raw, charset=None):
    for enc in [charset, "utf-8", "cp949", "euc-kr"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("utf-8", errors="ignore")


def get_text(url, data=None, timeout=30, headers=None):
    raw, cs, _ = http(url, data=data, timeout=timeout, headers=headers)
    return decode(raw, cs)


def post_json(url, payload, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def strip_tags(page):
    page = re.sub(r"(?is)<(script|style|noscript|svg).*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</tr>|</h\d>", "\n", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def og_image(page, base):
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page, re.I)
    return urllib.parse.urljoin(base, html.unescape(m.group(1))) if m else None


def first_dates(text):
    """Все даты вида 2026.11.20 / 2026-11-20 / 2026년 11월 20일 в тексте."""
    out = []
    for y, mo, d in re.findall(r"(20\d\d)\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})", text):
        try:
            out.append(datetime(int(y), int(mo), int(d), tzinfo=KST))
        except ValueError:
            pass
    return out


# ---------- источники ----------
def src_melon():
    page = get_text("https://ticket.melon.com/csoon/ajax/listTicketOpen.htm",
                    data={"orderType": "0", "pageIndex": "1", "schGcode": "GENRE_ALL",
                          "schText": "", "schDt": ""},
                    headers={"Referer": "https://ticket.melon.com/csoon/index.htm",
                             "X-Requested-With": "XMLHttpRequest"})
    items = []
    for li in re.findall(r"(?s)<li>(.*?)</li>", page):
        m = re.search(r'detail\.htm\?csoonId=(\d+)"\s+class="tit">\s*(.*?)</a>', li, re.S)
        if not m:
            continue
        cid, title = m.group(1), strip_tags(m.group(2))
        views = re.search(r'txt_review">([\d,]+)<', li)
        views = int(views.group(1).replace(",", "")) if views else 0
        img = re.search(r'<img src=\s*"([^"]+)"', li)
        img = re.sub(r"/melon/resize/.*$", "", img.group(1)) if img else None
        items.append({"key": f"melon:{cid}", "source": "Melon Ticket", "title": title,
                      "url": f"https://ticket.melon.com/csoon/detail.htm?csoonId={cid}",
                      "image": img, "views": views, "kind": "ticket",
                      "score": 50 + 8 * math.log10(views + 1)})
    return items


def src_yes24():
    page = get_text("https://ticket.yes24.com/New/Notice/Ajax/axList.aspx",
                    data={"page": "1", "size": "20", "genre": "", "province": "", "order": "1",
                          "searchType": "All", "searchText": ""},
                    headers={"Referer": "https://ticket.yes24.com/New/Notice/NoticeMain.aspx",
                             "X-Requested-With": "XMLHttpRequest"})
    items = []
    for tr in re.findall(r"(?s)<tr>(.*?)</tr>", page):
        m = re.search(r'href="#id=(\d+)">(.*?)</a>', tr, re.S)
        if not m:
            continue
        bid = m.group(1)
        title = strip_tags(re.sub(r"<span[^>]*>.*?</span>", "", m.group(2)))
        tds = re.findall(r"(?s)<td>(.*?)</td>", tr)
        views = int(re.sub(r"\D", "", strip_tags(tds[-1])) or 0) if tds else 0
        items.append({"key": f"yes24:{bid}", "source": "YES24 Ticket", "title": title,
                      "url": f"https://ticket.yes24.com/New/Notice/NoticeMain.aspx#id={bid}",
                      "yes24_id": bid, "views": views, "kind": "ticket",
                      "score": 48 + 8 * math.log10(views + 1)})
    return items


def _expo_item(prefix, src, ident, title, url, text_around):
    dates = first_dates(text_around)
    start = dates[0] if dates else None
    score = 30
    if re.search(r"모터|모빌리티|오토|자동차|바이크|모터사이클|에어쇼|항공|드론|게임|로봇|AI|테크|"
                 r"패션|뷰티|코스메틱|헤어|네일|캠핑|커피|와인|푸드|디저트|펫|반려|아트페어|"
                 r"페스티벌|축제|마켓|콘서트|쇼|전시회|박람회|페어|엑스포|EXPO|SHOW|FAIR", title, re.I):
        score += 20
    if re.search(r"컨퍼런스|세미나|포럼|학회|총회|심포지엄|심포지움|시상식|설명회|유학|채용|교육생|"
                 r"기업|바이어|수출|상담회|B2B|협회|위원회|\[конференция\]", title, re.I):
        score -= 40
    return {"key": f"{prefix}:{ident}", "source": src, "title": title, "url": url,
            "start": start.isoformat() if start else None, "kind": "expo", "score": score}


def src_kintex():
    t = now_kst()
    url = ("https://www.kintex.com/web/ko/event/list.do?pageIndex=1&pageUnit=60"
           f"&searchStartDt={t:%Y-%m-%d}&searchEndDt={t + timedelta(days=120):%Y-%m-%d}")
    page = re.sub(r"(?s)<!--.*?-->", "", get_text(url))
    items = []
    for m in re.finditer(r"fnView\('\./view\.do',\s*(\d+)\);\"(.*?)</a>", page, re.S):
        seq, chunk = m.group(1), m.group(2)
        label = re.search(r'event-label-ko">([^<]+)<', chunk)
        alt = re.search(r'<img[^>]+src="([^"]+)"[^>]+alt="([^"]*)"', chunk)
        if not alt:
            continue
        title = html.unescape(alt.group(2)).split("/")[0].strip()
        if label and label.group(1).strip() in ("회의", "컨벤션", "기타행사"):
            title += " [конференция]"
        it = _expo_item("kintex", "KINTEX (Гоян)", seq, title,
                        f"https://www.kintex.com/web/ko/event/view.do?seq={seq}", html.unescape(alt.group(2)))
        it["image"] = urllib.parse.urljoin("https://www.kintex.com/", html.unescape(alt.group(1)))
        lines = [l.strip() for l in strip_tags(chunk).split("\n") if l.strip()]
        hall = next((l for l in lines if re.match(r"^(전시홀|제\d\s*전시장|그랜드볼룸|회의실|컨벤션|이벤트홀|야외)", l)), "")
        it["place_ko"] = ("킨텍스 " + hall).strip()
        items.append(it)
    return items


def src_coex():
    t = now_kst()
    items = []
    for p in (1, 2, 3, 4):
        url = ("https://www.coex.co.kr/event/full-schedules/?var_page=%d&search_start_date=%s"
               "&search_end_date=%s&list_type=LIST" % (p, f"{t:%Y.%m.%d}",
                                                       f"{t + timedelta(days=120):%Y.%m.%d}"))
        page = get_text(url)
        found = 0
        for m in re.finditer(r"""<a\s[^>]*href=["']?(https?://www\.coex\.co\.kr/exhibitions/[^"'?\s>]+)"""
                             r"[^>]*>(.*?)</a>", page, re.S):
            link, chunk = m.group(1), m.group(2)
            title = re.search(r"""<h4[^>]*>(.*?)</h4>""", chunk, re.S)
            cate = re.search(r"""BlogEventItemCont-cate[^"']*["']>(.*?)</p>""", chunk, re.S)
            date = re.search(r"""BlogEventItemCont-date["']>(.*?)</div>""", chunk, re.S) or \
                re.search(r"(20\d\d\.\d\d\.\d\d\s*-\s*20\d\d\.\d\d\.\d\d)", chunk)
            img = re.search(r"""<img[^>]+src=["']([^"']+)["']""", chunk)
            if not title:
                continue
            title = strip_tags(title.group(1))
            if cate and strip_tags(cate.group(1)) == "Convention":
                title += " [конференция]"
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            if any(i["key"] == f"coex:{slug[-80:]}" for i in items):
                continue
            it = _expo_item("coex", "COEX (Сеул)", slug[-80:], title, link + "/",
                            strip_tags(date.group(1)) if date else "")
            it["image"] = img.group(1) if img else None
            hall = re.search(r"""BlogEventItemCont-hall["']>(.*?)</div>""", chunk, re.S)
            it["place_ko"] = ("코엑스 " + strip_tags(hall.group(1))).strip() if hall else "코엑스"
            items.append(it)
            found += 1
        if not found:
            break
    return items


def src_bexco():
    page = get_text("https://www.bexco.co.kr/kor/CMS/EventScheduleMgr/list.do?mCode=MN214")
    items, seen = [], set()
    for m in re.finditer(r"""href=["']?[^"'>]*EventScheduleMgr/view\.do\?[^"'>]*?event_seq=(\d+)[^>]*>"""
                         r"(.*?)</a>", page, re.S):
        seq, chunk = m.group(1), m.group(2)
        if seq in seen:
            continue
        seen.add(seq)
        title = re.search(r"""class=["']subject["']>(.*?)</span>""", chunk, re.S)
        kind = re.search(r"""class=["']eventIcon[^"']*["']>(.*?)</span>""", chunk, re.S)
        date = re.search(r"""class=["']date["']>(.*?)</span>""", chunk, re.S)
        img = re.search(r"""<img[^>]+src=["']([^"']+)["']""", chunk)
        if not title:
            continue
        title = strip_tags(title.group(1))
        if kind and strip_tags(kind.group(1)) in ("회의", "기타"):
            title += " [конференция]"
        it = _expo_item("bexco", "BEXCO (Пусан)", seq, title,
                        f"https://www.bexco.co.kr/kor/CMS/EventScheduleMgr/view.do?mCode=MN214&event_seq={seq}",
                        strip_tags(date.group(1)) if date else "")
        if img and "noimg" not in img.group(1):
            it["image"] = urllib.parse.urljoin("https://www.bexco.co.kr/", img.group(1))
        place = re.search(r"""class=["']place["']>(.*?)</span>""", chunk, re.S)
        place = re.sub(r"\s+", " ", strip_tags(place.group(1))) if place else ""
        it["place_ko"] = place if place.startswith("누리마루") else ("벡스코 " + place).strip()
        items.append(it)
    return items


def src_news():
    month = now_kst().month
    queries = list(NEWS_QUERIES)
    for months, qs in SEASONAL_QUERIES.items():
        if month in months:
            queries += [(q, s) for q, s in qs]
    items = []
    for q, score in queries:
        url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:2d")
               + "&hl=ko&gl=KR&ceid=KR:ko")
        try:
            raw, _, _ = http(url, timeout=20)
            root = ET.fromstring(raw)
        except Exception as e:
            log("  поиск новостей не удался:", q, e)
            continue
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            guid = (it.findtext("guid") or link)[-60:]
            src = it.find("source")
            items.append({"key": "news:" + guid, "source": (src.text if src is not None else "новости"),
                          "title": re.sub(r"\s+-\s+[^-]+$", "", title), "url": link,
                          "kind": "seasonal" if any(q == s for qs in SEASONAL_QUERIES.values()
                                                    for s, _ in qs) else "news",
                          "score": score, "query": q})
    return items


SOURCES = [("Melon Ticket", src_melon), ("YES24 Ticket", src_yes24), ("KINTEX", src_kintex),
           ("COEX", src_coex), ("BEXCO", src_bexco), ("Google News", src_news)]


# ---------- подробности события ----------
def gnews_real_url(url):
    """Google News прячет адрес статьи; пробуем получить настоящий."""
    gid = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    page = get_text(f"https://news.google.com/rss/articles/{gid}", timeout=20)
    sg = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sg and ts):
        return None
    inner = (f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,'
             f'null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},'
             f'"{sg.group(1)}"]')
    body = "f.req=" + urllib.parse.quote(json.dumps([[["Fbv4je", inner, None, "generic"]]]))
    raw, cs, _ = http("https://news.google.com/_/DotsSplashUi/data/batchexecute",
                      data=body.encode(), timeout=20)
    txt = decode(raw, cs)
    part = json.loads(txt.split("\n\n")[1])
    return json.loads(part[0][2])[1]


def candidate_links(page, base):
    links = []
    for href, label in re.findall(r'<a[^>]+href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', page, re.S):
        u = urllib.parse.urljoin(base, html.unescape(href))
        if not u.startswith("http"):
            continue
        dom = urllib.parse.urlparse(u).netloc.lower()
        lab = strip_tags(label)[:60]
        if any(dom.endswith(d) for d in TRUSTED_LINK_DOMAINS) or re.search(
                r"예매|예약|티켓|booking|ticket|공식|홈페이지", lab + u, re.I):
            if u not in [l for l, _ in links]:
                links.append((u, lab))
    return links[:15]


def fetch_details(it):
    """Возвращает (текст, картинка, [официальные ссылки])."""
    if it["source"] == "YES24 Ticket":
        page = get_text("https://ticket.yes24.com/New/Notice/Ajax/axRead.aspx",
                        data={"bId": it["yes24_id"], "genre": "", "province": "", "order": "1"},
                        headers={"Referer": "https://ticket.yes24.com/New/Notice/NoticeMain.aspx",
                                 "X-Requested-With": "XMLHttpRequest"})
        img = re.search(r"""<img[^>]+src=["']([^"']+\.(?:jpg|jpeg|png|gif)[^"']*)["']""", page, re.I)
        img = urllib.parse.urljoin("https://ticket.yes24.com/", img.group(1)) if img else None
        perf = re.search(r'(https?://ticket\.yes24\.com/Perf/\d+)', page)
        links = [perf.group(1)] if perf else []
        return strip_tags(page)[:7000], img, links + [it["url"]]

    url = it["url"]
    if it["source"] not in ("Melon Ticket",) and "news.google.com" in url:
        real = None
        try:
            real = gnews_real_url(url)
        except Exception as e:
            log("  адрес статьи не расшифрован:", e)
        if not real:
            return "", None, []
        url = real
        it["article_url"] = real
    page = get_text(url)
    img = og_image(page, url) or it.get("image")
    text = strip_tags(page)
    if it["kind"] in ("ticket", "expo"):
        return text[:7000], img, [it["url"]]
    return text[:7000], img, [u for u, _ in candidate_links(page, url)]


# ---------- адрес для навигатора ----------
# Адреса выставочных центров — дословно из подвала их официальных сайтов (проверено 16.11.2026).
# Применяются ТОЛЬКО к залам, которые точно находятся по этому адресу.
CENTER_ADDRESSES = [
    (r"^코엑스(?!.*마곡)", "서울특별시 강남구 영동대로 513"),
    (r"^벡스코\s*(제1전시장|컨벤션홀|오디토리움|전관)", "부산광역시 해운대구 APEC로 55"),
]
SIDO = (r"(?:서울특별시|부산광역시|대구광역시|인천광역시|광주광역시|대전광역시|울산광역시|"
        r"세종특별자치시|경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|"
        r"전라남도|경상북도|경상남도|제주특별자치도|서울|부산|대구|인천|광주|대전|울산|세종|경기|"
        r"강원|충북|충남|전북|전남|경북|경남|제주)")
ROAD_ADDR = re.compile(SIDO + r"\s+[가-힣]+(?:시|군|구)(?:\s+[가-힣]+(?:구|군))?(?:\s+[가-힣0-9]+(?:읍|면))?"
                       r"\s+[가-힣0-9A-Za-z]+(?:로|길)(?:\s*\d+[가-힣]*(?:로|길))?\s*\d+(?:-\d+)?")


def squash(t):
    return re.sub(r"\s+", " ", t or "").strip()


def verbatim(fragment, text):
    """Фрагмент засчитывается, только если он дословно есть в официальном тексте."""
    f = squash(fragment)
    return bool(f) and f in squash(text)


def find_address(text, venue):
    """Адрес берём, только если он стоит СРАЗУ после названия площадки или метки «장소/공연장소»
    (в пределах 80 символов) и рядом нет признаков адреса компании/организатора.
    Если таких адресов нет или они разные — None (тогда даём название места)."""
    t = squash(text)
    anchors = [m.end() for m in re.finditer(r"공연\s*장소|행사\s*장소|장\s*소\s*[:：]|공연장\s*주소|공연장\s*위치", t)]
    if venue and len(venue) >= 2:
        anchors += [m.end() for m in re.finditer(re.escape(venue), t)]
    found = set()
    for a in anchors:
        window = t[a:a + 80]
        for m in ROAD_ADDR.finditer(window):
            before = window[:m.start()]
            if re.search(r"본사|사업자|사업장|고객센터|대표|통신판매|회사|주최|주관|기획사|문의", before):
                continue
            found.add(m.group(0).strip())
    return found.pop() if len(found) == 1 else None


def venue_from_text(text):
    m = re.search(r"(?:공연\s*장소|공연장|장\s*소|행사\s*장소)\s*[:：]\s*([^\n/|·]{2,60})", text)
    return squash(m.group(1)) if m else None


SIDO_RU = {"Сеул": "서울", "Пусан": "부산", "Инчхон": "인천", "Тэгу": "대구", "Тэджон": "대전",
           "Кванджу": "광주", "Ульсан": "울산", "Сечжон": "세종", "Кёнгидо": "경기", "Гоян": "경기",
           "Сувон": "경기", "Сонам": "경기", "Пхаджу": "경기", "Канвондо": "강원", "Чеджу": "제주"}


def _norm(t):
    return re.sub(r"[\W_]+", "", (t or "").lower())


def kakao_lookup(venue, region_hint=None):
    """Ищем площадку в Kakao Map и берём её дорожный адрес — но только если совпадение однозначное:
    название места на карте совпадает с названием из афиши (или полностью входит в него), и у всех
    таких совпадений один и тот же адрес. Иначе None."""
    if not (KAKAO_REST_KEY and venue):
        return None
    url = ("https://dapi.kakao.com/v2/local/search/keyword.json?size=15&query="
           + urllib.parse.quote(venue))
    try:
        raw, _, _ = http(url, timeout=15, headers={"Authorization": f"KakaoAK {KAKAO_REST_KEY}"})
        docs = json.loads(raw.decode()).get("documents", [])
    except Exception as e:
        log("  Kakao: поиск не удался:", str(e)[:120])
        return None
    nv = _norm(venue)
    exact = [d for d in docs if _norm(d.get("place_name")) == nv]
    part = [d for d in docs if len(_norm(d.get("place_name"))) >= 4 and _norm(d.get("place_name")) in nv]
    cands = exact or part
    if region_hint:
        cands = [d for d in cands if (d.get("road_address_name") or d.get("address_name", "")).startswith(region_hint)]
    addrs = {(d.get("road_address_name") or d.get("address_name") or "").strip() for d in cands}
    addrs.discard("")
    if len(addrs) == 1:
        a = addrs.pop()
        log(f"  Kakao: «{venue}» → {a} ({len(cands)} совп.)")
        return a
    log(f"  Kakao: «{venue}» — однозначного совпадения нет (вариантов адреса: {len(addrs)})")
    return None


def navigator_target(it, text, ai_venue, region_hint=None):
    """Возвращает (что_вставлять_в_навигатор, это_адрес?) или (None, False)."""
    place = it.get("place_ko")
    if place:                                     # выставочные центры: место из официального списка
        for pattern, addr in CENTER_ADDRESSES:
            if re.search(pattern, place):
                return addr, True
        addr = kakao_lookup(place, region_hint)
        return (addr, True) if addr else (place, False)
    venue = squash(ai_venue) if ai_venue and verbatim(ai_venue, text) else venue_from_text(text)
    addr = find_address(text, venue) or kakao_lookup(venue, region_hint)
    if addr:
        return addr, True
    if venue:
        return venue, False
    return None, False


SEPARATOR = "➖➖➖➖➖➖➖➖➖➖"   # черта «пост закончился»


def navigator_message(target, is_address, last=True):
    """Отдельное сообщение — ТОЛЬКО адрес (или название места), чтобы скопировать его целиком.
    После последнего адреса — разделительная черта (она не копируется вместе с адресом)."""
    msg = f"<code>{html.escape(target)}</code>"
    return msg + "\n\n" + SEPARATOR if last else msg


def navigator_hint(is_address):
    """Пояснение для основного поста."""
    if is_address:
        return "🧭 Адрес для навигатора — в следующем сообщении 👇 (нажмите, чтобы скопировать)"
    return ("🧭 Точного адреса в афише нет — в следующем сообщении название места 👇 "
            "(нажмите, чтобы скопировать, и найдите в Naver/Kakao Map)")


# ---------- ИИ ----------
_bad_models = set()
_search_ok = USE_GOOGLE_SEARCH


def gemini(prompt, search=False):
    """Возвращает (текст ответа, [ссылки-источники из поиска Google])."""
    global _search_ok
    if not GEMINI_API_KEY:
        raise RuntimeError("не указан GEMINI_API_KEY")
    errors = []
    for model in [m.strip() for m in GEMINI_MODELS.split(",") if m.strip()]:
        if model in _bad_models:
            continue
        is_gemma = model.startswith("gemma")      # Gemma: без поиска и без JSON-режима
        use_search = search and _search_ok and not is_gemma
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"temperature": 0.4}}
        if use_search:
            payload["tools"] = [{"google_search": {}}]
        elif not is_gemma:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
               f"?key={GEMINI_API_KEY}")
        for attempt in (1, 2):
            try:
                res = post_json(url, payload, timeout=GEMINI_TIMEOUT)
                cand = res["candidates"][0]
                text = "".join(p.get("text", "") for p in cand["content"]["parts"])
                sources = [c["web"]["uri"] for c in
                           cand.get("groundingMetadata", {}).get("groundingChunks", []) if "web" in c]
                log(f"  ИИ: {model}" + (" + поиск Google" if use_search else ""))
                return text, sources
            except urllib.error.HTTPError as e:
                msg = e.read().decode(errors="ignore")[:300]
                if use_search and e.code in (400, 403) and attempt == 1:
                    log(f"  поиск Google в Gemini недоступен (HTTP {e.code}) — продолжаю без него")
                    _search_ok = use_search = False
                    payload.pop("tools", None)
                    payload["generationConfig"]["responseMimeType"] = "application/json"
                    continue
                if e.code >= 500 and attempt == 1:
                    log(f"  {model}: перегружен, повтор через 10 сек")
                    time.sleep(10)
                    continue
                errors.append(f"{model}: HTTP {e.code} {msg}")
                if e.code in (429, 404) or e.code >= 500:
                    _bad_models.add(model)
                    break
                raise RuntimeError(errors[-1])
            except Exception as e:
                errors.append(f"{model}: {e}")
                _bad_models.add(model)
                break
        log(f"  {errors[-1][:160] if errors else model} — следующая модель")
    raise RuntimeError("; ".join(errors) or "Gemini недоступен")


def resolve(url):
    try:
        _, _, final = http(url, timeout=15, max_bytes=1)
        return final
    except Exception:
        return None


STYLE = """Ты — главный редактор русскоязычного Telegram-канала «К-Афиша | События в Корее».
Ты билингв: корейский и русский для тебя оба родные. Читатели — русскоязычные, живущие в Корее
или едущие туда. Канал публикует ТОЛЬКО афиши мероприятий, которые можно посетить.

ПУБЛИКУЕМ: концерты и выступления любых артистов в Корее (K-pop, рок, джаз, классика, оркестры,
филармонии, диджеи, стендап, мюзиклы, балет, шоу, фанмитинги в Корее), фестивали, выставки
(авто, мото, авиа, технологии, мода, бьюти, искусство, еда, игры и т.д.), авиашоу, ярмарки и
маркеты, мастер-классы и тренинги для публики, массовые городские события (бесплатные тоже),
открытие новых мест досуга, сезонные места отдыха (горнолыжные курорты, катки, аквапарки,
пляжи, фестивали цветения/огней) — с датами открытия, ценами и акциями.
НЕ ПУБЛИКУЕМ (верни "skip": true): релизы треков и альбомов, коллаборации, камбэки, чарты,
новости о звёздах, рецензии, политику, экономику, происшествия, B2B-конференции, семинары,
профессиональные форумы, события за пределами Кореи, уже прошедшие события.

ФИЛЬТР ИЗВЕСТНОСТИ (только для певцов, певиц, групп, рэперов, диджеев и музыкантов НЕклассических
жанров — K-pop, поп, рок, инди, хип-хоп, R&B, трот, баллады, джаз, электроника): публикуем, ТОЛЬКО
если артист известен за пределами Кореи — мировые звёзды, иностранные артисты с мировым именем,
K-pop группы и солисты с международным фандомом (мировые туры, выступления за рубежом, мировые
чарты, известность в России/СНГ). Если артист популярен только внутри Кореи (местные трот-, балладные,
инди-исполнители, локальные группы, малоизвестные новички) или ты не уверен, что он известен за
рубежом — "skip": true, reason «известен только в Корее». Классическая музыка, оркестры, оперы,
балет, мюзиклы, выставки, фестивали, шоу, сезонные места и прочие НЕконцертные события этот
фильтр НЕ касается. Сборные фестивали публикуем, если в составе есть хотя бы один артист с
международной известностью.

ОБЯЗАТЕЛЬНО в посте (если чего-то из обязательного нет в материалах — "skip": true):
- 📅 дата(ы) проведения и 🕐 время начала. Для многодневных выставок/фестивалей/сезонных мест —
  даты и часы работы; если часов нет в материалах, напиши «🕐 часы работы — на официальном сайте».
  Для концертов и шоу время начала обязательно.
- 📍 место: площадка и город.
- 🔗 ссылка на официальную покупку билетов/бронирование — выбирай ТОЛЬКО из списка «Ссылки»
  (поле link_index). Если ни одна не подходит — "skip": true.
Если есть в материалах: 💰 цены (в вонах, при желании ~в долларах), 🎟 дата и время открытия
продаж (и предпродажи), акции и скидки, возраст.

Стиль: не переводи дословно — перескажи так, как написал бы русский журналист с нуля. Коротко,
живо, без канцелярита. Корейские имена — по Концевичу, устоявшиеся формы как в русских СМИ
(Сеул, Пусан, Самсунг). Названия K-pop групп — как пишут фанаты (BTS, THE BOYZ), при первом
упоминании можно оригинал в скобках. Корейские реалии кратко поясняй.
Название события (концерта, тура, выставки) переводи на русский и СРАЗУ дублируй оригиналом
в скобках, как в афише: «Снова, сегодня» (다시, 오늘); «Новогодний концерт-кроссовер»
(서초문화원 송년음악회 [크로스오버 콘서트]).
Если выступает артист или группа — 1–2 предложения о них: кто это, жанр, чем известны. Бери из
материалов; об известных артистах можно общеизвестное, но без цифр, дат и имён, в которых не уверен.
Структура: 1) <b>Заголовок</b> с одним эмодзи; 2) кто выступает / что за событие и почему стоит
сходить (2–4 коротких предложения); 3) блок фактов строками 📅 🕐 📍 💰 🎟 (только то, что
известно; 🕐 — только время начала, продолжительность пиши строкой ⏱). В строке 📍 — место по-русски и в скобках корейское название как в афише. Адрес НЕ пиши —
бот отправит его отдельным сообщением;
4) последней строкой 3–6 хэштегов: город (#Сеул #Пусан #Инчхон #Тэгу #Тэджон #Кванджу #Ульсан
#Сувон #Гоян #Сонам #Чеджу …), провинция (#Кёнгидо #Канвондо #Чхунчхон #Кёнсан #Чолла #ЧеджуДо) —
провинцию ставь ТОЛЬКО если город в ней находится; Сеул, Пусан, Инчхон, Тэгу, Тэджон, Кванджу, Ульсан —
отдельные города, провинцию для них НЕ ставь (Гоян, Сувон, Сонам — это #Кёнгидо),
категория (#концерт #kpop #фанмитинг #мюзикл #классика #диджей #выставка #автошоу #мотошоу
#авиашоу #технологии #мода #бьюти #мастеркласс #фестиваль #маркет #бесплатно #горнолыжка
#аквапарк #сезонное #новоеместо и т.п.). Хэштеги без пробелов и дефисов.
Разметка: только <b> и <i>; перенос строки — обычный перевод строки, НЕ <br>. Длина поста без хэштегов — до 750 символов. Ссылку в текст
НЕ вставляй, её добавит бот.
Не выдумывай ничего, чего нет в материалах (и в результатах поиска, если он есть).

Популярность ("priority"): "hot" — большой концерт/звезда (K-pop группы, мировые артисты,
крупные площадки KSPO DOME, Гочхок, Чамсиль, Inspire Arena), билеты разлетятся быстро, крупное
авто/авиашоу, громкое открытие; иначе "normal".
"""

ANSWER_FORMAT = ('Ответь строго одним JSON-объектом без пояснений: {"skip": true/false, '
                 '"reason": "кратко почему skip", "priority": "hot" или "normal", '
                 '"event_key": "латиницей: название-город-ГГГГММДД первой даты", '
                 '"link_index": номер ссылки из списка, "post": "текст поста", '
                 '"venue_ko": "место проведения на корейском ДОСЛОВНО как в материалах (скопируй, не переводи '
                 'и не дополняй); если в материалах нет — пустая строка", '
                 '"city_ru": "город по-русски, если он точно известен из материалов (Сеул, Пусан, Гоян…), иначе пусто"}')


def parse_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    return json.loads(text[text.find("{"): text.rfind("}") + 1], strict=False)


def make_post(it, text, links):
    today = now_kst()
    use_search = USE_GOOGLE_SEARCH and (it["kind"] in ("news", "seasonal") or not text.strip())
    link_list = "\n".join(f"{i}. {u}" for i, u in enumerate(links)) or "(нет)"
    prompt = (f"{STYLE}\nСегодня {today:%Y-%m-%d} (KST).\n"
              + ("Используй поиск Google, чтобы проверить дату, время, место, цены, акции и найти "
                 "официальную страницу покупки билетов/бронирования. Найденные официальные ссылки "
                 "можешь указать в поле \"extra_links\": [\"https://...\"].\n" if use_search else "")
              + f"\nИсточник: {it['source']}\nЗаголовок: {it['title']}\n"
              + f"Ссылки:\n{link_list}\n\nМатериал:\n{text[:6500] or '(текста нет — только заголовок)'}\n\n"
              + ANSWER_FORMAT)
    out, sources = gemini(prompt, search=use_search)
    data = parse_json(out)
    # ссылки, найденные поиском: берём только реальные адреса доверенных/официальных доменов
    extra = [u for u in data.get("extra_links", []) if isinstance(u, str)]
    for s in sources[:6]:
        r = resolve(s)
        if r:
            extra.append(r)
    return data, extra


# ---------- Telegram ----------
ALLOWED_TAG = re.compile(r"</?(b|i)>|<a href=\"[^\"<>]+\">|</a>")


def sanitize(text):
    """Переносы <br> → новая строка; прочие HTML-теги убираем; остальное экранируем."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?p\s*>", "\n", text)
    text = re.sub(r"</?(?!(?:b|i)>)(?!a\s+href=)(?!/a>)[a-zA-Z][^>]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    out, pos = [], 0
    for m in ALLOWED_TAG.finditer(text):
        out.append(html.escape(html.unescape(text[pos:m.start()]), quote=False))
        out.append(m.group(0))
        pos = m.end()
    out.append(html.escape(html.unescape(text[pos:]), quote=False))
    return "".join(out)


def tg(method, fields, photo=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    if photo:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; "
                 f"filename=\"image.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
        body += photo + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        res = json.loads(e.read().decode() or "{}")
    if not res.get("ok"):
        raise RuntimeError(res)
    return res.get("result", {}).get("message_id")


def publish(text, photo, nav=None):
    """Пост (с официальной афишей, если есть) + отдельное сообщение с адресом для навигатора."""
    mid = None
    if not nav and nav is not False:
        text = text + "\n\n" + SEPARATOR   # адреса нет — черта в конце самого поста
    if photo and len(text) <= 1024:
        try:
            mid = tg("sendPhoto", {"chat_id": CHANNEL, "caption": text, "parse_mode": "HTML"}, photo)
        except Exception as e:
            log("  фото с подписью не ушло:", str(e)[:200])
    elif photo:
        try:
            tg("sendPhoto", {"chat_id": CHANNEL}, photo)
        except Exception as e:
            log("  фото не ушло:", str(e)[:200])
    if mid is None:
        mid = tg("sendMessage", {"chat_id": CHANNEL, "text": text, "parse_mode": "HTML",
                                 "disable_web_page_preview": "true"})
    if nav:
        tg("sendMessage", {"chat_id": CHANNEL, "text": nav, "parse_mode": "HTML"})
    return mid


_last_gen = 0


def get_image(url, prompt):
    global _last_gen
    if url:
        try:
            raw, _, _ = http(url, timeout=25)
            if len(raw) > 3000:
                return raw
        except Exception:
            pass
    if not (GENERATE_IMAGES and prompt):
        return None
    wait = 50 - (time.time() - _last_gen)
    if wait > 0:
        time.sleep(wait)
    try:
        raw, _, _ = http("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:300])
                         + "?width=768&height=512", timeout=90)
        return raw if len(raw) > 5000 else None
    except Exception as e:
        log("  картинку сгенерировать не удалось:", e)
        return None
    finally:
        _last_gen = time.time()


# ---------- состояние ----------
def load_state():
    st = {"done": {}, "events": {}, "seasonal_last": ""}
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            st.update(data)
    return st


def save_state(st):
    cutoff = (now_kst() - timedelta(days=120)).isoformat()
    st["done"] = {k: v for k, v in st["done"].items() if v >= cutoff}
    st["events"] = {k: v for k, v in st["events"].items() if v >= cutoff}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)


def norm_title(t):
    t = re.sub(r"\d+\s*차|티켓\s*오픈\s*안내|티켓오픈안내|티켓\s*오픈|오픈\s*안내|선예매|일반예매|"
               r"단독판매|좋은좌석|\[.*?\]|\(.*?\)", "", t)
    return re.sub(r"[\W_]+", "", t).lower()[:40]


# ---------- основной проход ----------
DIAG_URLS = {"COEX": "https://www.coex.co.kr/event/full-schedules/",
             "BEXCO": "https://www.bexco.co.kr/kor/CMS/EventScheduleMgr/list.do?mCode=MN214",
             "KINTEX": "https://www.kintex.com/web/ko/event/list.do"}


def diagnose(name):
    """Если источник ничего не вернул — пишем в журнал, что пришло вместо страницы."""
    url = DIAG_URLS.get(name)
    if not url:
        return
    try:
        raw, cs, final = http(url, timeout=20)
        page = decode(raw, cs)
        title = re.search(r"(?is)<title>(.*?)</title>", page)
        sample = re.findall(r"<a\s[^>]{0,160}", page)
        sample = [a for a in sample if re.search(r"exhibitions/|event_seq|fnView", a)][:2]
        log(f"  диагностика {name}: {len(raw)} байт, адрес {final}, заголовок "
            f"«{strip_tags(title.group(1))[:60] if title else '-'}», ссылки: {sample}")
    except urllib.error.HTTPError as e:
        log(f"  диагностика {name}: HTTP {e.code}")
    except Exception as e:
        log(f"  диагностика {name}: {e}")


def collect(st):
    items = []
    for name, fn in SOURCES:
        try:
            got = fn()
            log(f"Источник {name}: {len(got)}")
            if not got and name != "Google News":
                diagnose(name)
            items += got
        except Exception as e:
            log(f"Источник {name} недоступен: {e}")
    today = now_kst()
    queue, seen_titles = [], set()
    for it in items:
        if it["key"] in st["done"]:
            continue
        nt = norm_title(it["title"])
        if not nt or nt in seen_titles or nt in st["events"]:
            continue
        if any(w.lower() in it["title"].lower() for w in TITLE_BLACKLIST):
            st["done"][it["key"]] = today.isoformat()
            continue
        if it["kind"] == "expo":
            if it["score"] < 0:
                st["done"][it["key"]] = today.isoformat()
                continue
            if it.get("start"):
                start = datetime.fromisoformat(it["start"])
                if start < today - timedelta(days=1):
                    st["done"][it["key"]] = today.isoformat()
                    continue
                if start > today + timedelta(days=EXPO_DAYS_AHEAD):
                    continue            # рано — опубликуем ближе к дате
        seen_titles.add(nt)
        it["norm"] = nt
        queue.append(it)
    queue.sort(key=lambda x: -x["score"])
    log(f"В очереди на проверку: {len(queue)}")
    return queue


def run_events(st):
    queue = collect(st)
    posted = hot = checked = 0
    for it in queue:
        if checked >= MAX_CHECKS_PER_RUN or (posted >= MAX_POSTS_PER_RUN and hot >= MAX_HOT_POSTS_PER_RUN):
            break
        if posted >= MAX_POSTS_PER_RUN and it["score"] < HOT_SCORE:
            break       # обычные посты на этот запуск набраны; дальше только кандидаты в «горячие»
        log(f"▶ [{it['source']}] {it['title']}  (оценка {it['score']:.0f})")
        try:
            text, img_url, links = fetch_details(it)
        except Exception as e:
            log("  подробности не получены:", str(e)[:200])
            text, img_url, links = "", it.get("image"), ([it["url"]] if it["kind"] != "news" else [])
        checked += 1
        try:
            data, extra = make_post(it, text, links)
        except Exception as e:
            log("  ИИ недоступен, остановка до следующего запуска:", str(e)[:300])
            break
        st["done"][it["key"]] = now_kst().isoformat()
        save_state(st)
        if data.get("skip"):
            log("  пропуск:", data.get("reason", ""))
            continue
        is_hot = data.get("priority") == "hot"
        if (is_hot and hot >= MAX_HOT_POSTS_PER_RUN) or (not is_hot and posted >= MAX_POSTS_PER_RUN):
            st["done"].pop(it["key"], None)          # опубликуем в следующий запуск
            save_state(st)
            log("  лимит постов на этот запуск исчерпан — опубликую в следующий")
            continue
        ekey = re.sub(r"[^a-z0-9-]", "", str(data.get("event_key", "")).lower())[:80]
        if ekey and ekey in st["events"]:
            log("  уже публиковали это событие")
            continue
        # официальная ссылка
        link = None
        try:
            idx = int(data.get("link_index"))
            if 0 <= idx < len(links):
                link = links[idx]
        except (TypeError, ValueError):
            pass
        if not link:
            # сначала билетные сервисы и госсайты, затем официальные сайты мест (не СМИ и не блоги)
            ranked = sorted(extra, key=lambda u: not any(
                urllib.parse.urlparse(u).netloc.lower().endswith(d) for d in TRUSTED_LINK_DOMAINS))
            for u in ranked:
                dom = urllib.parse.urlparse(u).netloc.lower()
                trusted = any(dom.endswith(d) for d in TRUSTED_LINK_DOMAINS)
                if not dom or (not trusted and re.search(NOT_OFFICIAL, dom)):
                    continue
                link = u
                break
        if not link:
            log("  нет официальной ссылки на билеты — пропуск")
            continue
        target, is_addr = navigator_target(it, text, data.get("venue_ko", ""),
                                           SIDO_RU.get(str(data.get("city_ru", "")).strip()))
        if not target:
            log("  место проведения не подтверждено официальным источником — пропуск")
            continue
        log(f"  навигатор: {'адрес' if is_addr else 'название места'} «{target}»")
        post = sanitize(str(data.get("post", "")).strip())
        body, _, tags = post.rpartition("\n")
        if not tags.strip().startswith("#"):
            body, tags = post, ""
        label = "Официальная страница" if it["kind"] == "expo" else "Билеты и подробности"
        post = (f"{body.rstrip()}\n\n{navigator_hint(is_addr)}"
                f"\n\n🔗 <a href=\"{html.escape(link, quote=True)}\">{label}</a>"
                + (f"\n\n{tags.strip()}" if tags.strip() else ""))
        if it["kind"] in ("news", "seasonal"):
            img_url = None                       # фото из СМИ не берём — только официальная афиша
            if any(urllib.parse.urlparse(link).netloc.lower().endswith(d) for d in TICKET_DOMAINS):
                try:
                    img_url = og_image(get_text(link, timeout=20), link)
                except Exception:
                    img_url = None
        photo = get_image(img_url, None)
        if posted + hot > 0:
            time.sleep(PAUSE_BETWEEN_POSTS)
        try:
            publish(post, photo, navigator_message(target, is_addr))
        except Exception as e:
            log("  ошибка публикации:", str(e)[:300])
            continue
        if is_hot:
            hot += 1
        else:
            posted += 1
        st["events"][ekey or it["norm"]] = now_kst().isoformat()
        st["events"][it["norm"]] = now_kst().isoformat()
        save_state(st)
        log("  ✅ опубликовано" + (" (🔥 популярное)" if is_hot else ""))
    log(f"Готово: опубликовано {posted + hot} (популярных {hot}), проверено ИИ {checked}")


SEASON_TOPICS = {
    (11, 12, 1, 2, 3): "горнолыжные курорты Кореи (открытие сезона, работающие трассы, цены на "
                       "ски-пассы, аренду, акции и скидки), а также катки и зимние фестивали",
    (6, 7, 8): "аквапарки и пляжи Кореи (даты открытия, цены, акции и скидки, часы работы)",
    (4, 5): "фестивали цветения и весенние места отдыха Кореи (даты, места, как добраться)",
    (9, 10): "осенние фестивали и места любования кленами в Корее (даты, места, цены)",
}


def run_seasonal(st):
    month = now_kst().month
    topic = next((t for ms, t in SEASON_TOPICS.items() if month in ms), None)
    week = now_kst().strftime("%G-W%V")
    if not topic or st.get("seasonal_last") == week:
        log("Сезонный обзор на этой неделе уже был или не по сезону")
        return
    prompt = (f"{STYLE}\nСегодня {now_kst():%Y-%m-%d} (KST).\nСделай сезонный обзор-подборку: {topic}. "
              "С помощью поиска Google найди АКТУАЛЬНУЮ информацию на эту неделю: 3–6 мест, по каждому "
              "строка: название (город/провинция) — статус/дата открытия — цена — акция. Укажи только то, "
              "что подтверждено официальными или свежими источниками. Пост до 950 символов, в конце "
              "хэштеги. Официальные ссылки мест верни в \"extra_links\", корейские названия мест (как на их "
              "официальных сайтах) — в \"places_ko\": [\"...\"]. " + ANSWER_FORMAT)
    try:
        out, sources = gemini(prompt, search=True)
    except Exception as e:
        log("Сезонный обзор: ИИ недоступен:", e)
        return
    if not _search_ok:
        log("Сезонный обзор требует поиска Google в Gemini, а он недоступен — пропуск")
        return
    data = parse_json(out)
    if data.get("skip"):
        log("Сезонный обзор пропущен:", data.get("reason"))
        return
    links = [u for u in data.get("extra_links", []) if isinstance(u, str)][:4]
    post = sanitize(str(data.get("post", "")).strip())
    if links:
        post += "\n\n" + "\n".join(f"🔗 <a href=\"{html.escape(u, quote=True)}\">"
                                   f"{html.escape(urllib.parse.urlparse(u).netloc)}</a>" for u in links)
    names = [squash(n) for n in data.get("places_ko", []) if isinstance(n, str) and squash(n)][:6]
    if names:
        post += ("\n\n🧭 Названия мест для навигатора — следующими сообщениями 👇 Нажмите, чтобы скопировать, "
                 "и вставьте в поиск Naver Map или Kakao Map.")
    publish(post, None, nav=False if names else None)
    for i, n in enumerate(names):
        addr = kakao_lookup(n)
        tg("sendMessage", {"chat_id": CHANNEL,
                           "text": navigator_message(addr or n, bool(addr), last=(i == len(names) - 1)),
                           "parse_mode": "HTML"})
    st["seasonal_last"] = week
    save_state(st)
    log("✅ сезонный обзор опубликован")


def main():
    st = load_state()
    log(f"Режим: {MODE}")
    if MODE == "seasonal":
        run_seasonal(st)
    else:
        run_events(st)
    save_state(st)


if __name__ == "__main__":
    main()
