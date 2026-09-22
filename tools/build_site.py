#!/usr/bin/env python3
"""Собирает сайт-хаб проекта из docs/*.md в docs/*.html для GitHub Pages.

Запуск из корня репозитория:  python3 tools/build_site.py

Без внешних зависимостей: свой конвертер markdown -> HTML (заголовки, абзацы,
списки с вложенностью, таблицы, жирный/курсив, код, ссылки, изображения,
блоки ```), общий шаблон с шапкой, навигацией, кнопкой «Главная» и подвалом.
docs/index.html написан вручную и скриптом не трогается.

Ссылки на *.md внутри документов переписываются на *.html; ссылки на img/
остаются как есть. Упоминания документов в обратных кавычках вида
`wall-placement.md` тоже становятся ссылками, если файл существует в docs/.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

SITE_NAME = "AR-песочница для клиники"
REPO_URL = "https://github.com/amirrchik22/ar-sandbox-clinic"
CLIENT_SITE_URL = "https://amirrchik22.github.io/ar-sandbox/"

# Навигация: (подпись, адрес). Внешние ссылки открываются в новой вкладке.
NAV = [
    ("Главная", "index.html"),
    ("Размещение у стены", "wall-placement.html"),
    ("3D-проверка", "wall-layout-3d.html"),
    ("Железо", "hardware.html"),
    ("Крепление", "mounting.html"),
    ("Цикл и руки", "pipeline.html"),
    ("Архитектура ПО", "architecture.html"),
    ("План ПО", "software-plan.html"),
    ("План работ", "roadmap.html"),
    ("Презентация для клиента", CLIENT_SITE_URL),
]

# ---------------------------------------------------------------------------
# CSS: единый для всех страниц (копия лежит в docs/index.html)
# ---------------------------------------------------------------------------

CSS = """
:root {
  color-scheme: light;
  --bg: #fafaf8;
  --bg-2: #f1f0ec;
  --text: #1d232a;
  --muted: #5d6771;
  --line: #e2dfd7;
  --accent: #1a8c9c;
  --accent-text: #ffffff;
  --code-bg: #efeee9;
  --font-head: "Manrope", "Helvetica Neue", Arial, sans-serif;
  --font-body: "Golos Text", "Segoe UI", Roboto, Arial, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --bg: #0f1317;
    --bg-2: #161c22;
    --text: #e6e9ec;
    --muted: #9aa5ae;
    --line: #2a323b;
    --accent: #3fb3c3;
    --accent-text: #0f1317;
    --code-bg: #1a2129;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0f1317;
  --bg-2: #161c22;
  --text: #e6e9ec;
  --muted: #9aa5ae;
  --line: #2a323b;
  --accent: #3fb3c3;
  --accent-text: #0f1317;
  --code-bg: #1a2129;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 17px;
  line-height: 1.6;
  font-variant-numeric: tabular-nums;
}
a { color: var(--accent); text-decoration: none; }
a:hover, a:focus-visible { text-decoration: underline; text-underline-offset: 3px; }
h1, h2, h3, h4 {
  font-family: var(--font-head);
  font-weight: 700;
  line-height: 1.2;
  letter-spacing: -0.01em;
  margin: 2.2em 0 0.6em;
}
h1 { font-size: 2rem; margin-top: 0.4em; letter-spacing: -0.02em; }
h2 { font-size: 1.45rem; padding-top: 0.6em; border-top: 1px solid var(--line); }
h3 { font-size: 1.15rem; }
h4 { font-size: 1rem; }
h2 a.anchor, h3 a.anchor { color: inherit; }
p { margin: 0 0 1em; }
ul, ol { margin: 0 0 1em; padding-left: 1.4em; }
li { margin: 0.25em 0; }
li > ul, li > ol { margin-top: 0.25em; margin-bottom: 0.25em; }
li > p { margin-bottom: 0.5em; }
hr { border: 0; border-top: 1px solid var(--line); margin: 2em 0; }
strong { font-weight: 600; }
code {
  font-family: var(--font-mono);
  font-size: 0.86em;
  background: var(--code-bg);
  padding: 0.1em 0.35em;
  border-radius: 4px;
}
pre {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  line-height: 1.45;
  background: var(--code-bg);
  padding: 0.9em 1em;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0 0 1.2em;
}
pre code { background: none; padding: 0; font-size: inherit; }
blockquote {
  margin: 0 0 1em;
  padding: 0.2em 1em;
  border-left: 3px solid var(--accent);
  color: var(--muted);
}
.table-wrap { overflow-x: auto; margin: 0 0 1.4em; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: 0.93rem; line-height: 1.45; }
th, td { text-align: left; vertical-align: top; padding: 0.5em 0.7em; border-bottom: 1px solid var(--line); }
th { font-family: var(--font-head); font-weight: 700; font-size: 0.85rem; color: var(--muted); border-bottom: 2px solid var(--line); white-space: nowrap; }
td { min-width: 8em; }

@media (max-width: 560px) {
  .table-wrap:has(table.stack) { overflow-x: visible; }
  td { min-width: 0; }
  table.stack, table.stack tbody, table.stack tr, table.stack td { display: block; width: auto; }
  table.stack thead { display: none; }
  table.stack tr { border: 1px solid var(--line); border-radius: 8px; background: var(--bg-2); padding: 0.7em 0.9em; margin: 0 0 0.8em; }
  table.stack td { border: 0; padding: 0.18em 0; min-width: 0; }
  table.stack td[data-h]::before { content: attr(data-h) ": "; color: var(--muted); font-family: var(--font-head); font-weight: 700; font-size: 0.78rem; }
  table.stack td:first-child { font-family: var(--font-head); font-weight: 700; font-size: 1.02rem; margin-bottom: 0.3em; }
  table.stack td:first-child[data-h]::before { content: none; }
}
figure { margin: 1.4em 0; }
figure img { display: block; width: 100%; max-width: 100%; height: auto; background: #fff; border-radius: 6px; border: 1px solid var(--line); }
figcaption { font-size: 0.85rem; color: var(--muted); margin-top: 0.5em; }
img { max-width: 100%; }
.check { font-family: var(--font-mono); color: var(--muted); margin-right: 0.3em; }

.wrap { max-width: 760px; margin: 0 auto; padding-inline: 20px; }

.site-header { border-bottom: 1px solid var(--line); background: var(--bg); }
.site-header .wrap { padding-block: 14px 10px; }
.brand { font-family: var(--font-head); font-weight: 800; font-size: 1.05rem; color: var(--text); display: inline-block; margin-bottom: 8px; }
.brand:hover { text-decoration: none; color: var(--accent); }
.nav { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 0.9rem; }
.nav a { color: var(--muted); white-space: nowrap; }
.nav a:hover { color: var(--accent); text-decoration: none; }
.nav a.active { color: var(--text); font-weight: 600; }
.nav a.ext::after { content: " \\2197"; font-size: 0.8em; }

.btn {
  display: inline-block;
  font-family: var(--font-head);
  font-weight: 600;
  font-size: 0.9rem;
  border: 1px solid var(--accent);
  color: var(--accent);
  padding: 0.35em 0.9em;
  border-radius: 6px;
}
.btn:hover { background: var(--accent); color: var(--accent-text); text-decoration: none; }
.btn.primary { background: var(--accent); color: var(--accent-text); }
.btn.primary:hover { opacity: 0.9; }

main.wrap { padding-block: 24px 40px; }
.back { margin-bottom: 1.4em; }
.back-bottom { margin-top: 2.5em; }
.toc { background: var(--bg-2); border-radius: 8px; padding: 0.9em 1.2em; margin: 1.4em 0 2em; font-size: 0.92rem; }
.toc-title { font-family: var(--font-head); font-weight: 700; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted); margin-bottom: 0.4em; }
.toc ol { margin: 0; padding-left: 1.3em; columns: 2; column-gap: 2em; }
.toc li { break-inside: avoid; margin: 0.15em 0; }
@media (max-width: 560px) { .toc ol { columns: 1; } }

.site-footer { border-top: 1px solid var(--line); color: var(--muted); font-size: 0.85rem; }
.site-footer .wrap { padding-block: 18px 28px; display: flex; flex-wrap: wrap; gap: 6px 20px; justify-content: space-between; }
.site-footer a { color: var(--muted); }
.site-footer a:hover { color: var(--accent); }

@media (max-width: 480px) {
  body { font-size: 16px; }
  h1 { font-size: 1.6rem; }
  h2 { font-size: 1.3rem; }
}
"""

FONTS_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800'
    '&family=Golos+Text:wght@400;500;600&family=JetBrains+Mono:wght@400;500'
    '&display=swap" rel="stylesheet">'
)


# ---------------------------------------------------------------------------
# Конвертер markdown -> HTML
# ---------------------------------------------------------------------------

_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RE_HR = re.compile(r"^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$")
_RE_FENCE = re.compile(r"^\s{0,3}```\s*([\w+-]*)\s*$")
_RE_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_RE_IMAGE_LINE = re.compile(r"^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)\s*$")
_RE_TASK = re.compile(r"^\[([ xX])\]\s+")


class Converter:
    def __init__(self, known_docs: set[str]):
        self.known_docs = known_docs  # имена md-документов без расширения
        self.headings: list[tuple[int, str, str]] = []  # (уровень, id, текст)
        self._ids: dict[str, int] = {}

    # --- вспомогательное -------------------------------------------------

    def _slug(self, text: str) -> str:
        plain = re.sub(r"<[^>]+>", "", text)
        plain = html.unescape(plain).lower().strip()
        slug = re.sub(r"[^\w]+", "-", plain).strip("-") or "section"
        n = self._ids.get(slug, 0)
        self._ids[slug] = n + 1
        return slug if n == 0 else f"{slug}-{n + 1}"

    def rewrite_url(self, url: str) -> str:
        """*.md -> *.html для локальных ссылок; img/ и внешние без изменений."""
        if re.match(r"^[a-z]+:", url):
            return url
        m = re.match(r"^(?:\./)?(?:docs/)?([\w\-./]+?)\.md(#[^\s]*)?$", url)
        if m:
            return f"{m.group(1)}.html{m.group(2) or ''}"
        return re.sub(r"^(?:\./)?docs/", "", url)

    def _code_span(self, content: str) -> str:
        """Инлайновый код; упоминание документа проекта становится ссылкой."""
        esc = html.escape(content, quote=False)
        code = f"<code>{esc}</code>"
        m = re.match(r"^(?:docs/)?([\w\-]+)\.md$", content)
        if m and m.group(1) in self.known_docs:
            return f'<a href="{m.group(1)}.html">{code}</a>'
        m = re.match(r"^(?:docs/)?(wall-layout-3d\.html)$", content)
        if m:
            return f'<a href="{m.group(1)}">{code}</a>'
        m = re.match(r"^(?:docs/)?(img/[\w\-]+\.(?:svg|png|jpg))$", content)
        if m and (DOCS / m.group(1)).exists():
            return f'<a href="{m.group(1)}">{code}</a>'
        return code

    def inline(self, text: str) -> str:
        parts = re.split(r"(`+)(.+?)\1", text)
        out: list[str] = []
        i = 0
        while i < len(parts):
            if i + 2 < len(parts) and parts[i + 1].startswith("`"):
                out.append(self._inline_text(parts[i]))
                out.append(self._code_span(parts[i + 2]))
                i += 3
            else:
                out.append(self._inline_text(parts[i]))
                i += 1
        return "".join(out)

    def _inline_text(self, text: str) -> str:
        if not text:
            return ""
        s = html.escape(text, quote=False)
        # изображения
        s = re.sub(
            r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)",
            lambda m: f'<img src="{self.rewrite_url(m.group(2))}" alt="{m.group(1)}" loading="lazy">',
            s,
        )
        # ссылки
        def _link(m: re.Match) -> str:
            url = self.rewrite_url(m.group(2))
            ext = ' target="_blank" rel="noopener"' if url.startswith("http") else ""
            return f'<a href="{url}"{ext}>{m.group(1)}</a>'
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)", _link, s)
        # голые адреса
        s = re.sub(
            r"(?<![\"'>=\w])(https?://[^\s<)]+[^\s<).,;:])",
            r'<a href="\1" target="_blank" rel="noopener">\1</a>',
            s,
        )
        # жирный и курсив
        s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"__(?=\S)(.+?)(?<=\S)__", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", s)
        s = re.sub(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])", r"<em>\1</em>", s)
        # перенос строки двумя пробелами
        s = re.sub(r"  $", "<br>", s, flags=re.M)
        return s

    # --- блоки -------------------------------------------------------------

    def convert(self, md: str) -> str:
        lines = md.replace("\r\n", "\n").split("\n")
        return "\n".join(self._blocks(lines, tight=False))

    def _blocks(self, lines: list[str], tight: bool) -> list[str]:
        out: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            m = _RE_FENCE.match(line)
            if m:
                lang = m.group(1)
                j = i + 1
                buf: list[str] = []
                while j < n and not re.match(r"^\s{0,3}```\s*$", lines[j]):
                    buf.append(lines[j])
                    j += 1
                cls = f' class="language-{lang}"' if lang else ""
                out.append(f"<pre><code{cls}>{html.escape(chr(10).join(buf), quote=False)}</code></pre>")
                i = j + 1
                continue

            m = _RE_HEADING.match(line)
            if m:
                level = len(m.group(1))
                text = self.inline(m.group(2))
                hid = self._slug(text)
                self.headings.append((level, hid, text))
                if level in (2, 3):
                    out.append(f'<h{level} id="{hid}"><a class="anchor" href="#{hid}">{text}</a></h{level}>')
                else:
                    out.append(f'<h{level} id="{hid}">{text}</h{level}>')
                i += 1
                continue

            if _RE_HR.match(line) and not _RE_LIST.match(line):
                out.append("<hr>")
                i += 1
                continue

            if line.lstrip().startswith("|") and i + 1 < n and _RE_TABLE_SEP.match(lines[i + 1]):
                j = i + 2
                rows = [line]
                while j < n and lines[j].lstrip().startswith("|"):
                    rows.append(lines[j])
                    j += 1
                out.append(self._table(rows[0], lines[i + 1], rows[1:]))
                i = j
                continue

            if line.lstrip().startswith(">"):
                j = i
                buf = []
                while j < n and lines[j].lstrip().startswith(">"):
                    buf.append(re.sub(r"^\s*>\s?", "", lines[j]))
                    j += 1
                inner = "\n".join(self._blocks(buf, tight=False))
                out.append(f"<blockquote>{inner}</blockquote>")
                i = j
                continue

            m = _RE_IMAGE_LINE.match(line)
            if m:
                alt = html.escape(m.group(1), quote=True)
                src = self.rewrite_url(m.group(2))
                out.append(
                    f'<figure><a href="{src}" target="_blank" rel="noopener">'
                    f'<img src="{src}" alt="{alt}" loading="lazy"></a>'
                    f"<figcaption>{alt}</figcaption></figure>"
                )
                i += 1
                continue

            m = _RE_LIST.match(line)
            if m:
                j = self._list_end(lines, i)
                out.append(self._list(lines[i:j]))
                i = j
                continue

            # абзац: до пустой строки или начала другого блока
            j = i
            buf = []
            while j < n and lines[j].strip() and not self._starts_block(lines, j):
                buf.append(lines[j].strip())
                j += 1
            if not buf:  # защита от зацикливания
                buf.append(lines[j].strip())
                j += 1
            text = self.inline(" ".join(buf))
            out.append(text if tight else f"<p>{text}</p>")
            i = j
        return out

    def _starts_block(self, lines: list[str], j: int) -> bool:
        line = lines[j]
        if _RE_FENCE.match(line) or _RE_HEADING.match(line) or _RE_HR.match(line):
            return True
        if _RE_LIST.match(line) or line.lstrip().startswith(">"):
            return True
        if line.lstrip().startswith("|") and j + 1 < len(lines) and _RE_TABLE_SEP.match(lines[j + 1]):
            return True
        return False

    # --- таблицы ----------------------------------------------------------

    @staticmethod
    def _cells(row: str) -> list[str]:
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        # экранированная черта \| внутри ячейки
        parts = re.split(r"(?<!\\)\|", row)
        return [p.replace("\\|", "|").strip() for p in parts]

    def _table(self, head: str, sep: str, body: list[str]) -> str:
        aligns = []
        for c in self._cells(sep):
            if c.startswith(":") and c.endswith(":"):
                aligns.append(' style="text-align:center"')
            elif c.endswith(":"):
                aligns.append(' style="text-align:right"')
            else:
                aligns.append("")
        hcells = self._cells(head)
        ncol = max(len(hcells), len(aligns))
        aligns += [""] * (ncol - len(aligns))
        # от трёх колонок таблица на телефоне превращается в карточки:
        # каждая ячейка подписана своим заголовком (data-h), см. CSS .stack
        cls = " class=\"stack\"" if ncol >= 3 else ""
        labels = [html.escape(re.sub(r"[*`\[\]]", "", hcells[k]).strip(), quote=True)
                  if k < len(hcells) else "" for k in range(ncol)]
        out = ["<div class=\"table-wrap\"><table%s>" % cls, "<thead><tr>"]
        for k in range(ncol):
            txt = self.inline(hcells[k]) if k < len(hcells) else ""
            out.append(f"<th{aligns[k]}>{txt}</th>")
        out.append("</tr></thead><tbody>")
        for row in body:
            cells = self._cells(row)
            out.append("<tr>")
            for k in range(ncol):
                txt = self.inline(cells[k]) if k < len(cells) else ""
                lab = f' data-h="{labels[k]}"' if cls and labels[k] else ""
                out.append(f"<td{aligns[k]}{lab}>{txt}</td>")
            out.append("</tr>")
        out.append("</tbody></table></div>")
        return "".join(out)

    # --- списки -----------------------------------------------------------

    def _list_end(self, lines: list[str], i: int) -> int:
        """Конец блока списка: строки-пункты, продолжения с отступом, пустые
        строки перед такими продолжениями."""
        base = len(_RE_LIST.match(lines[i]).group(1))
        j = i + 1
        n = len(lines)
        while j < n:
            line = lines[j]
            if not line.strip():
                # пустая строка: продолжаем, только если дальше снова список или отступ
                k = j
                while k < n and not lines[k].strip():
                    k += 1
                if k < n and (
                    (_RE_LIST.match(lines[k]) and len(_RE_LIST.match(lines[k]).group(1)) >= base)
                    or (len(lines[k]) - len(lines[k].lstrip()) > base)
                ):
                    j = k
                    continue
                break
            indent = len(line) - len(line.lstrip())
            if _RE_LIST.match(line) and indent >= base:
                j += 1
                continue
            if indent > base:
                j += 1
                continue
            break
        return j

    def _list(self, lines: list[str]) -> str:
        first = _RE_LIST.match(lines[0])
        base = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        start = int(re.match(r"\d+", first.group(2)).group()) if ordered else 1
        items: list[list[str]] = []
        cur: list[str] | None = None
        for line in lines:
            m = _RE_LIST.match(line)
            indent = len(line) - len(line.lstrip())
            if m and indent == base:
                marker_w = len(m.group(1)) + len(m.group(2)) + 1
                cur = [m.group(3)]
                cur_w = marker_w
                items.append(cur)
                continue
            if cur is None:
                continue
            if not line.strip():
                cur.append("")
            else:
                # снимаем отступ пункта, оставляем относительный для вложенных
                strip = min(cur_w, indent)
                cur.append(line[strip:])
        tag = "ol" if ordered else "ul"
        attrs = f' start="{start}"' if ordered and start != 1 else ""
        out = [f"<{tag}{attrs}>"]
        for item in items:
            text = item[0]
            task = _RE_TASK.match(text)
            prefix = ""
            if task:
                prefix = '<span class="check">[x]</span>' if task.group(1) != " " else '<span class="check">[ ]</span>'
                text = text[task.end():]
            body = [text] + item[1:]
            # склеиваем продолжения абзаца первой строки
            inner = self._blocks(body, tight=True)
            out.append(f"<li>{prefix}{' '.join(inner)}</li>")
        out.append(f"</{tag}>")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Шаблон страницы
# ---------------------------------------------------------------------------

def nav_html(current: str) -> str:
    items = []
    for label, href in NAV:
        cls = []
        if href == current:
            cls.append("active")
        ext = href.startswith("http")
        if ext:
            cls.append("ext")
        attrs = f' class="{" ".join(cls)}"' if cls else ""
        if ext:
            attrs += ' target="_blank" rel="noopener"'
        items.append(f'<a href="{href}"{attrs}>{html.escape(label)}</a>')
    return '<nav class="nav" aria-label="Разделы">' + "".join(items) + "</nav>"


def page(title: str, body: str, current: str, toc: str = "") -> str:
    today = _dt.date.today().strftime("%d.%m.%Y")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — {SITE_NAME}</title>
<meta name="description" content="{html.escape(title)}. Документация проекта «{SITE_NAME}».">
{FONTS_LINK}
<style>{CSS}</style>
</head>
<body>
<header class="site-header">
  <div class="wrap">
    <a class="brand" href="index.html">{SITE_NAME}</a>
    {nav_html(current)}
  </div>
</header>
<main class="wrap">
  <p class="back"><a class="btn" href="index.html">&larr; Главная</a></p>
{body}
{toc}
  <p class="back-bottom"><a class="btn" href="index.html">&larr; Главная</a></p>
</main>
<footer class="site-footer">
  <div class="wrap">
    <span>{SITE_NAME} · документация проекта · собрано {today}</span>
    <span><a href="{REPO_URL}" target="_blank" rel="noopener">Репозиторий на GitHub</a> · <a href="{CLIENT_SITE_URL}" target="_blank" rel="noopener">Презентация для клиента</a></span>
  </div>
</footer>
</body>
</html>
"""


def build_toc(headings: list[tuple[int, str, str]]) -> str:
    h2 = [(hid, text) for level, hid, text in headings if level == 2]
    if len(h2) < 3:
        return ""
    items = "".join(f'<li><a href="#{hid}">{text}</a></li>' for hid, text in h2)
    return f'<div class="toc"><div class="toc-title">На этой странице</div><ol>{items}</ol></div>'


def build() -> int:
    md_files = sorted(DOCS.glob("*.md"))
    if not md_files:
        print("docs/*.md не найдены", file=sys.stderr)
        return 1
    known = {p.stem for p in md_files}
    for src in md_files:
        text = src.read_text(encoding="utf-8")
        conv = Converter(known)
        body = conv.convert(text)
        h1 = next((t for level, _, t in conv.headings if level == 1), None)
        title = re.sub(r"<[^>]+>", "", h1) if h1 else src.stem
        title = html.unescape(title)
        toc = build_toc(conv.headings)
        # оглавление ставим сразу после первого h1 и вводных абзацев: проще —
        # перед первым h2
        if toc:
            idx = body.find("<h2 ")
            if idx > 0:
                body = body[:idx] + toc + "\n" + body[idx:]
            toc = ""
        out = DOCS / f"{src.stem}.html"
        out.write_text(page(title, body, out.name, toc), encoding="utf-8")
        print(f"{src.name} -> {out.name} ({out.stat().st_size} байт)")
    # пустой .nojekyll, чтобы GitHub Pages отдавал файлы как есть
    nojekyll = DOCS / ".nojekyll"
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")
        print("создан docs/.nojekyll")
    # проверка ссылок навигации
    missing = [href for _, href in NAV if not href.startswith("http") and not (DOCS / href).exists()]
    if missing:
        print("В навигации нет файлов: " + ", ".join(missing), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(build())
