# parse_its_book.py  (универсальный)
# ─────────────────────────────────────────────────────────────────────────────
"""Скачать книгу/справочник с its.1c.ru в Markdown-файлы.

Поддерживает:
  • обычные «книжные» базы   /db/<code>/content/…          (unfdoc, pub…)
  • справочник metod81       /db/metod81/browse/…|content/…

После запуска скрипт спросит:
  ➜ код базы  (пример: unfdoc | pubchaos2order | metod81)
  ➜ базовую папку для Markdown
  ➜ категорию / раздел  – уйдут в YAML.

Для *metod81* дополнительно парсится **только** ветка nav_2503
(«Рабочее место кассира…»). Если нужна другая – поменяйте ROOT_NAV_ID.
"""
from pathlib import Path
import re, time
import json
from collections import deque
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError
DEBUG = True   # global flag, used for verbose logging

# ───────────────────────────  пользовательские параметры
BOOK      = input("📘 Код базы (unfdoc / metod81 / …): ").strip()
OUT_DIR   = Path(input("📁 Куда сохранять md-файлы: ").strip())
CATEGORY  = input("🏷 Категория (YAML): ").strip()
SECTION   = input("📚 Раздел 1С (YAML): ").strip()

OUT_DIR.mkdir(parents=True, exist_ok=True)
START_URL      = f"https://its.1c.ru/db/{BOOK}"
ROOT_NAV_ID    = "2503"          # для metod81: «Рабочее место кассира…»

# ───────────────────────────  мелкие утилиты
def sanitize(t: str) -> str:
    t = re.sub(r"[^\w\- ]", "", t.lower()).replace(" ", "-")
    t = t.replace("/", "-").replace("\\", "-")
    return t[:100] or "untitled"

def normalize(u: str, keep_hash=False) -> str:
    if "#" in u and not keep_hash:  u = u.split("#")[0]
    return u.rstrip("/")

def save_md(title, url, text, subcat_slug, file_slug, date_str):
    """
    Write a markdown file with YAML front‑matter.

    Parameters
    ----------
    title : str            – document human title
    url   : str            – canonical ITS URL
    text  : str            – main markdown body
    subcat_slug : str      – slug to be written in YAML as “subcategory”
    file_slug    : str     – slug used for the *.md filename
    date_str     : str     – dd.mm.yyyy taken from the page
    """
    # JSON‑style quoting guarantees proper escaping of inner quotes
    q_str  = json.dumps(title, ensure_ascii=False)
    url_str = json.dumps(url,   ensure_ascii=False)

    fp = OUT_DIR / f"{file_slug}.md"
    fp.parent.mkdir(parents=True, exist_ok=True)     # ensure path
    if fp.exists():          # duplicate – skip writing
        return

    fp.write_text(
        f"""---
date: "{date_str}"
category: {CATEGORY}
section_1c: {SECTION}
subcategory: {subcat_slug}
question: {q_str}
url: {url_str}
---

{text.strip()}
""",
        "utf-8"
    )
    print("   ✅", fp.name)

def log_snip(t, txt):
    print("      ↳", (txt.strip().replace("\n", " ") or "<EMPTY>")[:80])

# ───────────────────────────  Playwright helpers
def wait_doc_frame(page, timeout=15_000):
    """Возвращает iframe с текстом, либо None (для metod81 paywall-страниц)."""
    try:
        page.wait_for_selector('iframe[name="w_metadata_doc_frame"]',
                               state="attached", timeout=timeout)
        page.wait_for_function(
            """sel=>{const e=document.querySelector(sel);
               return e&&!e.hidden&&e.getAttribute('loading')!=='true'}""",
            arg='iframe[name="w_metadata_doc_frame"]', timeout=timeout)
        return page.frame(name="w_metadata_doc_frame")
    except TimeoutError:
        return None

def goto_and_get_node(page, url):
    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
    time.sleep(.3)                                         # микропауза для JS
    frame = wait_doc_frame(page) or page                   # fallback к самому page
    try:
        frame.wait_for_selector("h1,h2,h3,p", timeout=8_000)
    except TimeoutError:
        pass
    return frame

def node_html(node) -> str:      # page и frame имеют одинаковый .content()
    return node.content()

def extract_plain(node) -> str:
    soup = BeautifulSoup(node_html(node), "html.parser")
    div  = soup.select_one("div.doc-content,#content,body") or soup
    txt  = div.get_text("\n", strip=True)
    # убираем служебную строку‑«шапку» типа
    # “Общий профиль Доступ ограничен … Доступ до 14.10.2025 …”
    txt  = re.sub(r"^Общий профиль.*?Доступ до \d{2}\.\d{2}\.\d{4}\s+", "",
                  txt, flags=re.S)
    return txt

# ───────────────────────────  разделение на h2/h3-подблоки
def split_sections(html: str):
    soup = BeautifulSoup(html, "html.parser")
    cur, buf, out = None, [], []
    is_hdr = lambda n: n.name in ("h2","h3")
    for n in soup.find_all(["h2","h3","p"]):
        if is_hdr(n):
            if cur and buf:
                out.append((cur, "\n".join(buf).strip()))
                buf=[]
            cur = n.get_text(" ", strip=True)
        else:
            txt=n.get_text(" ", strip=True)
            if txt: buf.append(txt)
    if cur and buf: out.append((cur, "\n".join(buf).strip()))
    return out

# ───────────────────────────  основной процесс
with sync_playwright() as p:
    br = p.chromium.launch(headless=False, slow_mo=80)
    ctx, pg = br.new_context(), br.new_page()

    pg.goto("https://its.1c.ru/")
    input("⏳ Пройдите DDoS/логин и Enter… ")

    # если metod81 – сразу в нужную ветку
    if BOOK=="metod81":
        START_URL = f"https://its.1c.ru/db/metod81/browse/13/-1/2115/{ROOT_NAV_ID}"
    pg.goto(START_URL, wait_until="domcontentloaded")

    # ─────────── собираем ссылки TOC
    if BOOK=="metod81":
        print("🔍 Собираю ссылки в ветке nav_"+ROOT_NAV_ID)
        toc_links = pg.evaluate(f"""
        (()=>{{
          const root=document.querySelector('#nav_{ROOT_NAV_ID}');
          if(!root) return [];
          return Array.from(root.querySelectorAll('a[href]')).map(a=>{{
            const href=a.getAttribute('href');
            return {{
              title:a.textContent.trim(),
              url  :href.startsWith('http')?href:new URL(href,'https://its.1c.ru').href
            }};
          }});
        }})()
        """)
        # ── оставляем ТОЛЬКО ссылки, относящиеся к выбранной ветке
        toc_links = [l for l in toc_links
                     if f"/{ROOT_NAV_ID}" in l["url"]          # сами browse‑узлы ветки
                     or "/content/" in l["url"]]               # либо конечные hdoc‑страницы
    else:   # прежняя книжная логика
        pg.wait_for_selector(f'#w_metadata_toc a[href*="/db/{BOOK}/content/"]',
                             timeout=15_000)
        toc_links = pg.evaluate(f"""
        Array.from(document.querySelectorAll(
               '#w_metadata_toc a[href*="/db/{BOOK}/content/"]'))
             .map(a=>({{title:a.textContent.trim(),
                       url:new URL(a.getAttribute('href'),
                                   'https://its.1c.ru').href}}));
        """)

    links = [l for l in toc_links if l["title"] and l["url"]]
    print(f"📋 Найдено ссылок: {len(links)}")

    # ─────────── подготавливаем base-имена
    chap, sub_idx = None, 0
    for ln in links:
        t=ln["title"]
        m=re.match(r"Глава\s+(\d+)\.", t, flags=re.I)
        if m:
            chap=int(m.group(1)); sub_idx=0
            ln["fname_base"]=f"глава-{chap}-{sanitize(t[m.end():])}"
        elif chap:
            sub_idx+=1
            ln["fname_base"]=f"глава-{chap}-{sub_idx}-{sanitize(t)}"
        else:
            ln["fname_base"]=sanitize(t)

    queue = deque(links)          # ссылки к обработке
    done_urls: set[str] = set()   # уже сохранённые URL

    while queue:
        ln = queue.popleft()
        url=normalize(ln["url"])

        # ---------- metod81: browse→список документов ----------
        # Для справочника metod81 страницы /browse/… содержат только список hdoc‑ссылок.
        # Их самих мы не сохраняем – вместо этого ставим найденные /content/… в очередь.
        if BOOK == "metod81" and "/content/" not in url:
            node = goto_and_get_node(pg, url)        # отрисованный browse‑узел
            docs = node.evaluate("""
                Array.from(
                    document.querySelectorAll(
                        '#w_metadata_navlist a[href*="/content/"]'
                    )
                ).map(a => {
                    const href = a.getAttribute('href') || '';
                    return {
                        title: (a.textContent || '').trim(),
                        url  : href.startsWith('http') ? href
                               : new URL(href, 'https://its.1c.ru').href
                    };
                });
            """)
            added = 0
            for d in docs:
                nurl = normalize(d["url"])
                if nurl in done_urls:
                    continue
                d["fname_base"] = sanitize(d["title"])
                d["subcategory"] = sanitize(ln["title"])
                queue.append(d)
                added += 1
            if DEBUG:
                print(f"   ➕ из списка документов: {added}")
            done_urls.add(url)     # сам browse‑url помечаем обработанным
            continue               # переходим к следующему элементу очереди

        if url in done_urls: continue
        node = goto_and_get_node(pg, url)
        html = node_html(node)
        soup = BeautifulSoup(html, "html.parser")
        date_elem = soup.select_one("span.date")
        date_str = date_elem.get_text(strip=True) if date_elem else ""
        text = extract_plain(node)
        log_snip(ln["title"], text)
        if text:
            # prefer an explicit sub‑category from the queue element,
            # otherwise fall back to the file slug
            subcat = ln.get("subcategory", ln["fname_base"])
            save_md(ln["title"], url, text, subcat, ln["fname_base"], date_str)
        # под-заголовки
        done_urls.add(url)

        # ─────────── рекурсивные ссылки той же базы
        if BOOK != "metod81":
            try:
                # Для «книжных» баз берём любые ссылки внутри той‑же db/<BOOK>/ …
                # Для metod81 — берём ТОЛЬКО конечные документы /content/ внутри справочника.
                if BOOK == "metod81":
                    sel = 'a[href*="/db/metod81/content/"]'
                else:
                    sel = f'a[href*="/db/{BOOK}/"]'

                raw = node.evaluate(f"""
                    Array.from(document.querySelectorAll("{sel}")).map(a => {{
                        const href = a.getAttribute("href") || "";
                        return {{
                            title : (a.textContent || "").trim(),
                            url   : href.startsWith("http") ? href
                                    : new URL(href, "https://its.1c.ru").href
                        }};
                    }});
                """)

                added = 0
                for r in raw:
                    nurl = normalize(r["url"], keep_hash=True)
                    if nurl in done_urls:
                        continue

                    # формируем базу имени: наследуем имя родителя + собственный заголовок
                    parent_base = ln.get("fname_base", sanitize(ln["title"]))
                    r["fname_base"] = f"{parent_base}-{sanitize(r['title'])}"
                    queue.append(r)
                    added += 1

                if added and DEBUG:
                    print(f"   ➕ дочерних ссылок: {added}")
            except Exception:
                pass

    print("🏁 Готово")
    br.close()