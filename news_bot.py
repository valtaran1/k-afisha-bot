"""
БЕСПЛАТНЫЙ новостной канал: корейские сайты -> выжимка на русском -> Telegram.
Работает на вашем компьютере или бесплатно в GitHub Actions. Платить никому не нужно.

Что делает:
  1. Каждые N минут проверяет RSS-ленты корейских сайтов.
  2. Для новой статьи скачивает текст и главную картинку со страницы.
  3. Бесплатный ИИ выжимает главное и пишет пост на русском:
       - "gemini": бесплатный ключ Google AI Studio (без карты), работает через интернет;
       - "ollama": модель прямо на вашем компьютере, без ключей и лимитов.
  4. Если картинки на сайте нет — бесплатно генерирует её (Pollinations, без ключа).
  5. Публикует пост в канал со ссылкой на источник и не повторяется.

Нужен только Python 3.8+, сторонние библиотеки не требуются.
"""

import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

# ======================= НАСТРОЙКИ =======================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "ТОКЕН_ОТ_BOTFATHER")
CHANNEL = os.environ.get("CHANNEL", "@имя_вашего_канала")

# Какой ИИ использовать: "gemini" или "ollama".
# Можно указать оба через запятую — второй подхватит, если первый упрётся в лимит.
AI_PROVIDERS = os.environ.get("AI_PROVIDERS", "gemini,ollama")

# Gemini: бесплатный ключ на https://aistudio.google.com/apikey
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Модели по очереди: сначала самая сильная (лучший перевод, но меньше бесплатных
# запросов в день), когда её лимит кончится — следующая.
GEMINI_MODELS = os.environ.get("GEMINI_MODELS",
                               "gemini-flash-latest,gemini-flash-lite-latest")
GEMINI_TIMEOUT = 90   # сек; если модель думает дольше — переходим к следующей

# ChatGPT (ПЛАТНО, необязательно): чтобы включить, добавьте "openai" в AI_PROVIDERS
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

# Ollama: установите с https://ollama.com и выполните: ollama pull qwen2.5:7b
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "qwen2.5:7b"   # слабый ПК: qwen2.5:3b, мощный (16+ ГБ ОЗУ): qwen2.5:14b

# Генерировать картинку, если на сайте её нет (бесплатно, Pollinations)
GENERATE_IMAGES = True

# RSS-ленты корейских сайтов: культура и развлечения — там анонсы событий
FEEDS = [
    "https://www.yna.co.kr/rss/culture.xml",        # Yonhap: культура
    "https://www.yna.co.kr/rss/entertainment.xml",  # Yonhap: развлечения, K-pop
    "https://www.hani.co.kr/rss/culture/",          # Hankyoreh: культура
]

CHECK_EVERY_MIN = 30      # как часто проверять сайты
MAX_POSTS_PER_RUN = 2     # сколько постов максимум за одну проверку
MAX_CHECKS_PER_RUN = 8    # сколько статей максимум отдавать ИИ за проверку (бережём бесплатный лимит)
PAUSE_BETWEEN_POSTS = int(os.environ.get("PAUSE_BETWEEN_POSTS", "600"))  # пауза между постами, сек

# Режим "один проход": проверить сайты, выложить новое и выйти (для GitHub Actions)
RUN_ONCE = os.environ.get("RUN_ONCE") == "1"
SEEN_FILE = "seen.json"

# Тематика и стиль канала — правьте под себя
CHANNEL_NAME = "К-Афиша"

STYLE = f"""Ты — главный редактор русскоязычного Telegram-канала «{CHANNEL_NAME}» о жизни,
событиях и новостях Южной Кореи. Ты билингв: вырос в Сеуле в русскоязычной семье,
корейский и русский для тебя оба родные. Твои читатели — русскоговорящие, которые живут
в Корее или интересуются ею.

ГЛАВНОЕ ПРАВИЛО: ты не переводишь, а пересказываешь. Сначала полностью пойми смысл
корейского текста, потом забудь корейские конструкции и напиши пост так, как его с нуля
написал бы русский журналист. Текст не должен выдавать, что он переведён.

Как добиться естественного русского:
- Никакого дословного перевода. Корейские канцелярские обороты (~에 따르면, ~할 예정이다,
  ~것으로 알려졌다, ~관계자는) передавай живым русским языком, а не «согласно…»,
  «планируется осуществление», «стало известно, что».
- Короткие ясные предложения, активный залог, глаголы вместо отглагольных существительных.
- Корейские реалии объясняй коротко в скобках, если русскому читателю они непонятны
  (например: Чхусок — корейский праздник урожая).
- Деньги: сумма в вонах, при необходимости примерно в долларах: «50 000 вон (~$36)».
- Имена и названия:
  • корейские имена и топонимы — по системе Концевича: Ли Джэмён, Юн Сон Нёль;
    но устоявшиеся формы оставляй как принято в русских СМИ: Сеул, Пусан, Самсунг,
    Хёндэ, Ким Чен Ын;
  • названия K-pop групп и сценические имена — как пишут русскоязычные фанаты
    (BTS, BLACKPINK, aespa; Чонгук, Дженни), при первом упоминании можно добавить
    оригинал в скобках;
  • названия компаний, брендов, мест — общепринятым русским вариантом или латиницей.
- Тон: дружелюбный, живой, но без сленга и без канцелярита. Как умный друг, который
  живёт в Корее и рассказывает новость.

Структура поста:
- первая строка — цепляющий заголовок в <b>жирном</b> с одним уместным эмодзи;
- 2–4 коротких абзаца или пункта с самым важным: что произошло, кого касается,
  почему это интересно;
- если это событие (концерт, выставка, фестиваль, спектакль, распродажа и т.п.) —
  обязательно отдельные строки с фактами из статьи: 📅 дата и время, 📍 место,
  💰 цена, 🎟 как попасть / где купить билеты; если каких-то данных нет — пропусти строку;
- до 900 символов, из разметки разрешены только теги <b> и <i>.

Строго:
- не выдумывай фактов, цифр, дат и цитат, которых нет в статье;
- перед ответом перечитай пост глазами носителя русского языка и исправь всё, что
  звучит как перевод.
ФИЛЬТР — канал публикует ТОЛЬКО анонсы и новости о мероприятиях в Корее, которые
читатель может посетить или в которых может поучаствовать: концерты, фанмитинги,
выставки, фестивали, спектакли и мюзиклы, кинопоказы, поп-ап сторы, ярмарки и маркеты,
спортивные матчи, праздники и городские события, туры K-pop артистов по Корее, открытие
сезонных мест (каток, цветение, иллюминация) и т.п.
Политика, экономика, криминал, происшествия, светские сплетни, рецензии без даты
события, реклама — НЕ публикуем: верни post = "SKIP"."""
# =========================================================

UA = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}


# ---------- сеть ----------
def log(*args):
    print(time.strftime("%H:%M:%S"), *args, flush=True)


def http_get(url, timeout=30, max_bytes=8_000_000):
    """Скачивает страницу с ЖЁСТКИМ лимитом общего времени,
    чтобы медленный сайт не подвешивал бота."""
    deadline = time.time() + timeout
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=min(timeout, 20)) as r:
        charset = r.headers.get_content_charset()
        chunks, size = [], 0
        while True:
            if time.time() > deadline:
                raise TimeoutError(f"слишком долго: {url}")
            chunk = r.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                break
    return b"".join(chunks), charset


def decode(raw, charset):
    for enc in [charset, "utf-8", "euc-kr", "cp949"]:
        if not enc:
            continue
        try:
            return raw.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    return raw.decode("utf-8", errors="ignore")


def post_json(url, payload, headers, timeout=120):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


# ---------- RSS ----------
def read_feed(url):
    raw, cs = http_get(url)
    root = ET.fromstring(raw)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        title = (it.findtext("title") or "").strip()
        if link:
            items.append({"link": link, "title": title})
    # Atom-ленты
    ns = "{http://www.w3.org/2005/Atom}"
    for e in root.iter(ns + "entry"):
        l = e.find(ns + "link")
        if l is not None and l.get("href"):
            items.append({"link": l.get("href"),
                          "title": (e.findtext(ns + "title") or "").strip()})
    return items


# ---------- разбор страницы ----------
class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paragraphs, self.image = [], None
        self._in_p, self._skip, self._buf = False, 0, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and a.get("property") in ("og:image", "twitter:image") \
                and not self.image and a.get("content"):
            self.image = a["content"]
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "p":
            self._in_p, self._buf = True, []
        if tag == "br" and self._in_p:
            self._buf.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "p" and self._in_p:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(text) > 30:
                self.paragraphs.append(text)
            self._in_p = False

    def handle_data(self, data):
        if self._in_p and not self._skip:
            self._buf.append(data)


def fetch_article(url):
    raw, cs = http_get(url)
    page = decode(raw, cs)
    p = ArticleParser()
    p.feed(page)
    text = "\n".join(p.paragraphs)
    if len(text) < 200:  # запасной вариант: весь текст страницы без тегов
        body = re.sub(r"(?is)<(script|style).*?</\1>", " ", page)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body)))
    image = p.image
    if image and image.startswith("//"):
        image = "https:" + image
    return text[:8000], image


# ---------- ИИ ----------
_exhausted_models = set()   # модели, у которых сегодня кончился бесплатный лимит


def ask_gemini(prompt):
    if not GEMINI_API_KEY:
        raise RuntimeError("не указан GEMINI_API_KEY")
    errors = []
    for model in [m.strip() for m in GEMINI_MODELS.split(",") if m.strip()]:
        if model in _exhausted_models:
            continue
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={GEMINI_API_KEY}")
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": {"responseMimeType": "application/json",
                                        "temperature": 0.5}}
        try:
            try:
                res = post_json(url, payload, {}, timeout=GEMINI_TIMEOUT)
            except urllib.error.HTTPError as e:
                if e.code < 500:
                    raise
                log(f"  {model}: перегружен (HTTP {e.code}), повтор через 10 сек")
                time.sleep(10)   # временная перегрузка у Google — пробуем ещё раз
                res = post_json(url, payload, {}, timeout=GEMINI_TIMEOUT)
            log(f"  написал пост: {model}")
            return "".join(p.get("text", "")
                           for p in res["candidates"][0]["content"]["parts"])
        except urllib.error.HTTPError as e:
            errors.append(f"{model}: HTTP {e.code}")
            if e.code in (429, 404):      # лимит исчерпан / модель недоступна
                _exhausted_models.add(model)
            elif e.code >= 500:           # перегрузка у Google — модель пропускаем в этом запуске
                _exhausted_models.add(model)
            else:
                raise
        except Exception as e:            # таймаут, обрыв связи
            errors.append(f"{model}: {e}")
            _exhausted_models.add(model)
        log(f"  {errors[-1]} — пробую следующую модель")
    raise RuntimeError("; ".join(errors) or "все модели Gemini недоступны")


def ask_openai(prompt):
    """ChatGPT через API. ВНИМАНИЕ: это платно (ключ на platform.openai.com).
    Включается, только если добавить "openai" в AI_PROVIDERS."""
    if not OPENAI_API_KEY:
        raise RuntimeError("не указан OPENAI_API_KEY")
    res = post_json("https://api.openai.com/v1/chat/completions", {
        "model": OPENAI_MODEL,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}]},
        {"Authorization": f"Bearer {OPENAI_API_KEY}"})
    return res["choices"][0]["message"]["content"]


def ask_ollama(prompt):
    res = post_json(f"{OLLAMA_URL}/api/chat", {
        "model": OLLAMA_MODEL, "stream": False, "format": "json",
        "options": {"temperature": 0.4, "num_ctx": 8192},
        "messages": [{"role": "user", "content": prompt}]}, {}, timeout=600)
    return res["message"]["content"]


def ask_ai(prompt):
    errors = []
    for name in [p.strip() for p in AI_PROVIDERS.split(",") if p.strip()]:
        try:
            fn = {"gemini": ask_gemini, "openai": ask_openai,
                  "ollama": ask_ollama}[name]
            return fn(prompt)
        except urllib.error.HTTPError as e:
            errors.append(f"{name}: HTTP {e.code}"
                          + (" (бесплатный лимит на сегодня исчерпан)" if e.code == 429 else ""))
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError("; ".join(errors))


def make_post(title, text):
    prompt = (f"{STYLE}\n\nЗаголовок статьи: {title}\n\nТекст статьи:\n{text}\n\n"
              'Ответь строго JSON без пояснений: {"post": "текст поста на русском", '
              '"image_prompt": "короткое описание иллюстрации к новости на английском, '
              'без текста и без реальных людей"}')
    out = ask_ai(prompt)
    out = re.sub(r"^```(json)?|```$", "", out.strip()).strip()
    data = json.loads(out[out.find("{"): out.rfind("}") + 1], strict=False)
    return str(data.get("post", "SKIP")).strip(), str(data.get("image_prompt", ""))


_last_image_time = 0


def generate_image(prompt):
    """Бесплатная генерация через Pollinations (без ключа, 1 картинка в ~45 сек)."""
    global _last_image_time
    if not GENERATE_IMAGES or not prompt:
        return None
    wait = 50 - (time.time() - _last_image_time)
    if wait > 0:
        time.sleep(wait)
    url = ("https://image.pollinations.ai/prompt/"
           + urllib.parse.quote(prompt[:300]) + "?width=768&height=512")
    try:
        data, _ = http_get(url, timeout=90)
        return data if len(data) > 5000 else None
    except Exception as e:
        log("  картинку сгенерировать не удалось:", e)
        return None
    finally:
        _last_image_time = time.time()


# ---------- Telegram ----------
def tg(method, fields, file_field=None, file_bytes=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/{method}"
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                 f"{v}\r\n").encode()
    if file_bytes:
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
                 f"filename=\"image.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()
        body += file_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.loads(r.read().decode())
    if not res.get("ok"):
        raise RuntimeError(res)


def publish(text, image_bytes):
    if image_bytes and len(text) <= 1024:
        try:
            tg("sendPhoto", {"chat_id": CHANNEL, "caption": text, "parse_mode": "HTML"},
               "photo", image_bytes)
            return
        except Exception as e:
            log("  Фото не отправилось, публикую текстом:", e)
    tg("sendMessage", {"chat_id": CHANNEL, "text": text, "parse_mode": "HTML",
                       "disable_web_page_preview": "true"})


# ---------- основной цикл ----------
def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen)[-5000:], f)


def first_run(seen):
    """При первом запуске помечаем старые новости как прочитанные,
    чтобы бот не вывалил в канал весь архив."""
    for feed in FEEDS:
        try:
            for it in read_feed(feed)[3:]:
                seen.add(it["link"])
        except Exception as e:
            log("Лента недоступна:", feed, e)
    save_seen(seen)


def run_once(seen):
    new = []
    for feed in FEEDS:
        log("Читаю ленту:", feed)
        try:
            new += [it for it in read_feed(feed) if it["link"] not in seen]
        except Exception as e:
            log("Лента недоступна:", feed, e)
    log("Новых статей:", len(new))

    posted = checked = 0
    for it in new:
        if posted >= MAX_POSTS_PER_RUN or checked >= MAX_CHECKS_PER_RUN:
            break
        log("Обрабатываю:", it["title"])
        try:
            text, image_url = fetch_article(it["link"])
            log("  статья скачана, символов:", len(text))
        except Exception as e:
            log("  статью скачать не удалось:", e)
            seen.add(it["link"]); save_seen(seen)
            continue

        checked += 1
        try:
            post, image_prompt = make_post(it["title"], text)
        except Exception as e:
            # ИИ сейчас недоступен — статью НЕ помечаем, попробуем в следующий раз
            log("  ИИ недоступен, остановка до следующего запуска:", e)
            break

        seen.add(it["link"]); save_seen(seen)
        if post.upper().startswith("SKIP"):
            log("  не событие — пропущено")
            continue

        try:
            image = None
            if image_url:
                try:
                    image, _ = http_get(image_url)
                except Exception:
                    image = None
            if not image:
                image = generate_image(image_prompt)

            link = html.escape(it["link"], quote=True)
            post += f'\n\n<a href="{link}">Источник</a>'
            log("  картинка:", "есть" if image else "нет")
            if posted > 0:  # пауза только между постами
                log(f"  пауза {PAUSE_BETWEEN_POSTS} сек перед следующим постом")
                time.sleep(PAUSE_BETWEEN_POSTS)
            publish(post, image)
            posted += 1
            log("  опубликовано")
        except Exception as e:
            log("  ошибка публикации:", e)
    log(f"Готово: опубликовано {posted}, проверено ИИ {checked}")


def main():
    seen = load_seen()
    if not seen:
        first_run(seen)
    if RUN_ONCE:
        run_once(seen)
        return
    log("Бот запущен.")
    while True:
        run_once(seen)
        time.sleep(CHECK_EVERY_MIN * 60)


if __name__ == "__main__":
    main()
