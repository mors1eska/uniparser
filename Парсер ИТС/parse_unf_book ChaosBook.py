# Скрипт: parse_unf_book.py
# Цель: спарсить книгу с https://its.1c.ru/db/unfdoc и сохранить в формате Markdown для RAG

from pathlib import Path
import re
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from collections import deque

DEBUG = True


# 🔽 Запрашиваем параметры у пользователя
book_code = input("📘 Введите код книги (например, unfdoc или pubchaos2order): ").strip()
out_dir = input("📁 Путь к папке для сохранения (например, data/ChaosBook/md): ").strip()
category = input("🏷 Категория (например, Книги): ").strip()
section = input("📚 Раздел 1С (например, УНФ или Chaos → Order): ").strip()

# 🔽 Применяем введённые параметры
BOOK = book_code
START_URL = f"https://its.1c.ru/db/{BOOK}"
OUTPUT_DIR = Path(out_dir)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://its.1c.ru"
# START_URL = f"{BASE_URL}/db/unfdoc"
# OUTPUT_DIR = Path("data/SD/md")
# OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def extract_content(page, url):
    try:
        page.goto(url, timeout=20000)
        page.wait_for_selector("iframe[name=w_metadata_doc_frame]", timeout=20000)
        iframe = page.frame(name="w_metadata_doc_frame")
        if not iframe:
            print(f"❌ iframe не найден на странице: {url}")
            return None

        html = iframe.content()
        soup = BeautifulSoup(html, "html.parser")
        content_div = soup.select_one("div.doc-content") or soup.select_one("div#content") or soup.body
        if not content_div:
            print(f"⚠️ Контент не найден: {url}")
            debug_name = "debug_failed_" + sanitize_filename(url.split("/")[-1]) + ".html"
            with open(debug_name, "w", encoding="utf-8") as f:
                f.write(html)
            return None

        return content_div.get_text("\n", strip=True).strip()
    except Exception as e:
        print(f"❌ Ошибка при загрузке {url}: {e}")
        import traceback; traceback.print_exc()
        return None


def sanitize_filename(text):
    text = text.lower()
    text = re.sub(r"[^\w\- ]", "", text)
    text = text.replace(" ", "-")
    return text[:100]


def normalize_url(u: str, *, keep_fragment: bool = False) -> str:
    """
    Приводит ссылку к каноническому виду.
    • по‑умолчанию убирает часть после `#`, но при keep_fragment=True оставляет её;
    • срезает завершающий слэш.
    """
    u = u.strip()
    if "#" in u and not keep_fragment:
        u = u.split("#")[0]
    if u.endswith("/"):
        u = u[:-1]
    return u


def save_as_md(title: str, url: str, content: str, filename_base: str) -> None:
    """
    Сохраняет текст в файл Markdown.

    filename_base – уже готовое «тело» имени файла *без* расширения
    (например  `глава-1-2-если-…`).  
    Функция сама добавляет суффикс `.md`.

    При повторном вызове с тем‑же базовым именем пропускает сохранение,
    тем самым устраняя дубли.
    """
    filename = f"{filename_base}.md"
    subcat   = filename_base      # поле subcategory в YAML‑фронт‑маттере

    out_path = OUTPUT_DIR / filename
    if out_path.exists():         # файл уже есть – ничего не делаем
        return

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"""---
category: {category}
section_1c: {section}
subcategory: {subcat}
question: {title}
url: {url}
---

{content.strip()}
""")
    print("✅", filename)


def extract_content_from_iframe(iframe):
    html = iframe.content()
    soup = BeautifulSoup(html, "html.parser")
    div = soup.select_one("div.doc-content, div#content") or soup.body
    return div.get_text("\n", strip=True) if div else ""


def split_into_sections(html: str) -> list[tuple[str, str]]:
    """
    Разбивает HTML главы на под‑блоки по заголовкам <h2>/<h3>.
    Возвращает список кортежей (title, plain_text).
    """
    soup = BeautifulSoup(html, "html.parser")
    print("      ↳ found headings:",
          [h.get_text(" ", strip=True) for h in soup.find_all(['h2', 'h3', 'strong', 'b'])][:15])
    sections: list[tuple[str, str]] = []

    current_title = None
    buffer: list[str] = []

    heading_tags = ("h1", "h2", "h3", "h4", "h5", "h6", "strong", "b")

    for node in soup.find_all(["h2", "h3", "strong", "b", "p"]):
        is_heading = (
            node.name in heading_tags
            or (
                node.name == "p"
                and (
                    "bold" in (node.get("class") or [])
                    or "font-weight:bold" in node.get("style", "").replace(" ", "").lower()
                    or (node.find("b") and len(node.get_text(strip=True)) <= 120)
                )
            )
        )

        if is_heading:
            if current_title and buffer:
                sections.append((current_title, "\n".join(buffer).strip()))
                buffer = []
            current_title = node.get_text(" ", strip=True)
            continue

        txt = node.get_text(" ", strip=True)
        if txt:
            buffer.append(txt)

    # финальный буфер
    if current_title and buffer:
        sections.append((current_title, "\n".join(buffer).strip()))
    return sections


def log_snippet(title: str, content: str):
    snippet = content.strip().replace("\n", " ")[:80]
    print(f"    ↳ snippet: {snippet or '<EMPTY>'}")


def get_doc_iframe(page, timeout=15_000):
    """
    Ждёт, пока рабочий iframe появится в DOM и полностью снимет статус
    loading/hidden, затем возвращает объект Frame.
    """
    # iframe появляется в DOM сразу, но пока у него loading="true" – он hidden
    page.wait_for_selector(
        'iframe[name="w_metadata_doc_frame"]',
        state="attached",        # достаточно, чтобы элемент был в DOM
        timeout=timeout,
    )

    # дожидаемся конца загрузки: элемент видим и нет loading="true"
    page.wait_for_function(
        """selector => {
               const el = document.querySelector(selector);
               return el && !el.hidden && el.getAttribute('loading') !== 'true';
           }""",
        arg='iframe[name="w_metadata_doc_frame"]',
        timeout=timeout
    )
    return page.frame(name="w_metadata_doc_frame")


def safe_goto(page, url: str):
    """
    Переходит на url, ждёт domcontentloaded и возвращает iframe с текстом.
    Если основной iframe пустой, пытается найти непустой дочерний.
    """
    page.goto(url, timeout=30_000, wait_until="domcontentloaded")
    page.wait_for_timeout(300)        # небольшая пауза для скриптов
    try:
        iframe = get_doc_iframe(page)
    except Exception:
        print("⚠️  iframe не прогрузился, пробую ещё раз …")
        page.wait_for_timeout(1000)
        iframe = get_doc_iframe(page)

    # ⏳ Ждём, пока во фрейме появится «живой» текст – хотя бы один
    # заголовок или абзац.  Без этого .content() может вернуть
    # практически пустую разметку <script>…</script>.
    try:
        iframe.wait_for_selector("h1, h2, h3, p", timeout=10_000)
    except Exception:
        # если по‑какому‑то поводу не дождались – продолжим; дальше
        # всё‑равно попытаемся извлечь текст.
        pass

    try:
        if iframe and not iframe.content().strip():
            for child in iframe.child_frames:
                if child.content().strip():
                    iframe = child
                    break
    except Exception:
        pass

    try:
        iframe.wait_for_selector("h1, h2, h3, p", timeout=5_000)
    except Exception:
        pass

    return iframe


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context()
        page = context.new_page()

        # 1. Защита + логин
        page.goto("https://its.1c.ru/")
        input("⏳ Пройди защиту DDoS и нажми Enter...")
        print("🧭 Текущий URL:", page.url)
        with open("debug_page_content.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        # 2. Переход в документацию УНФ
        page.goto(START_URL)
        page.wait_for_timeout(3000)  # дать время на прогрузку авторизации
        print("🧭 После логина:", page.url)

        # DEBUG: сохраняем HTML
        with open("debug_after_login.html", "w", encoding="utf-8") as f:
            f.write(page.content())

        print("📦 Доступные фреймы:")
        for f in page.frames:
            print("  →", f.name, f.url)
        try:
            page.wait_for_selector("iframe[name=w_metadata_doc_frame]", timeout=10000)
            iframe = page.frame(name="w_metadata_doc_frame")
            if not iframe:
                print("❌ iframe w_metadata_doc_frame не найден")
                return

            print(f"🧭 iframe загружен: {iframe.url}")
            with open("debug_iframe_index.html", "w", encoding="utf-8") as f:
                f.write(iframe.content())
            soup = BeautifulSoup(iframe.content(), "html.parser")
            ul_count = len(soup.select("ul"))
            li_count = len(soup.select("li"))
            a_count = len(soup.select("a"))
            print(f"📊 Элементы в iframe: ul={ul_count}, li={li_count}, a={a_count}")

            print("📥 Проверим HTML до раскрытия папок...")
            with open("debug_iframe_before_expand.html", "w", encoding="utf-8") as f:
                f.write(iframe.content())

            # ------------------------------------------------------------------
            #  TOC is rendered separately inside the main document, *not* inside
            #  the w_metadata_doc_frame iframe.  Once JS finishes, all <a> tags
            #  are already present in  #w_metadata_toc  – even for collapsed
            #  branches – so we just scrape them directly.
            # ------------------------------------------------------------------
            print("⏳  Ждём загрузку полного оглавления …")
            page.wait_for_selector(
                f'#w_metadata_toc a[href*="/db/{BOOK}/content/"]',
                timeout=15_000
            )

            print("🔍  Собираем ссылки из #w_metadata_toc …")
            toc_links = page.evaluate(f'''
                Array.from(
                    document.querySelectorAll(
                        '#w_metadata_toc a[href*="/db/{BOOK}/content/"]'
                    )
                ).map(a => {{
                    const href = a.getAttribute('href') || '';
                    return {{
                        title : (a.textContent || '').trim(),
                        url   : href.startsWith('http') ? href
                               : new URL(href, 'https://its.1c.ru').href
                    }};
                }});
            ''')

            links = [
                link for link in toc_links
                if link["title"] and link["url"] and not link["url"].startswith("#")
            ]
        except Exception as e:
            print(f"❌ Ошибка ожидания ссылок в оглавлении: {e}")
            with open("debug_unfdoc_page.html", "w", encoding="utf-8") as f:
                f.write(page.content())
            return

        print(f"📋 Всего ссылок в оглавлении: {len(links)}")

        print(f"🧩 Всего ссылок для обработки: {len(links)}")

        # ------------------------------------------------------------------
        #  Формируем иерархические «читаемые» имена файлов.
        #  • Глава 1  →  глава-1-<название>
        #  • Под‑страницы внутри главы 1  →  глава-1-1-<название>, глава-1-2-…
        # ------------------------------------------------------------------
        chapter_no: int | None = None
        sub_index:  int        = 0

        for ln in links:
            title = ln["title"].strip()
            m = re.match(r"Глава\s+(\d+)\.", title, flags=re.I)
            if m:
                chapter_no = int(m.group(1))
                sub_index  = 0
                rest = title[m.end():].strip()
                ln["fname_base"] = f"глава-{chapter_no}-{sanitize_filename(rest)}"
            elif chapter_no is not None:
                sub_index += 1
                ln["fname_base"] = (
                    f"глава-{chapter_no}-{sub_index}-" + sanitize_filename(title)
                )
            else:
                ln["fname_base"] = sanitize_filename(title)

        # --- обход всех страниц книги в ширину ---
        queue = deque(links)           # начинаем с оглавления
        saved_urls = set()
        saved_titles: set[str] = set()

        while queue:
            link = queue.popleft()

            if not link.get("url"):
                continue

            norm_url = normalize_url(link["url"])
            file_base = link.get("fname_base") or sanitize_filename(link["title"])

            print(f"🔹 {link['title']} — {link['url']}")
            iframe = safe_goto(page, link["url"])
            html = iframe.content()
            content = extract_content_from_iframe(iframe)
            log_snippet(link["title"], content)

            # --- сохраняем саму главу ---
            if content:
                save_as_md(link["title"], link["url"], content, file_base)
                saved_titles.add(link["title"].strip())

            current_page_title = link["title"].strip()

            # --- разбиваем на под‑разделы внутри страницы ---
            for idx, (sub_title, sub_text) in enumerate(split_into_sections(html), start=1):
                sub_url = f"{link['url']}#{sanitize_filename(sub_title)}"
                if sub_title == current_page_title or sub_url in saved_urls:
                    continue
                save_as_md(sub_title, sub_url, sub_text, f"{file_base}-{idx:02d}")
                saved_urls.add(sub_url)
                saved_titles.add(sub_title.strip())

            saved_urls.add(norm_url)

            # ищем все вложенные ссылки на другие разделы той же книги
            try:
                raw_links = iframe.evaluate(
                    f'''
                    Array.from(document.querySelectorAll('a[href*="/db/{BOOK}/content/"]'))
                          .map(a => ({{
                              title: (a.textContent || '').trim(),
                              url: new URL(a.getAttribute('href'), "https://its.1c.ru").href
                          }}))
                    '''
                )
                queued = 0
                for nl in raw_links:
                    nurl = normalize_url(nl["url"], keep_fragment=True)
                    if nurl not in saved_urls:
                        nl["url"] = nurl
                        queue.append(nl)
                        queued += 1
                print(f"🔖 В очередь добавлено: {queued}")
            except Exception as e:
                print(f"⚠️ Ошибка при поиске вложенных ссылок: {e}")

        print("🏁 Парсинг завершён. Закрываем браузер.")
        browser.close()unfdoc


if __name__ == "__main__":
    main()