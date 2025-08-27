import os
import re
import time
import html
import logging
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from bs4 import BeautifulSoup, NavigableString, Tag
from readability import Document
import trafilatura

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("extractor")

NOISE_PATTERNS = re.compile(
    r"(nav|menu|header|footer|sidebar|aside|toc|table-of-contents|index|"
    r"share|sns|social|ad|ads|advert|sponsor|recommend|related|"
    r"comment|reply|profile|author|tag|category|breadcrumb|pager|pagination|"
    r"subscribe|newsletter|widget|banner|modal|popup|cookie|gdpr|cta|"
    r"prev|next)", re.I
)

CAPTION_PATTERNS = re.compile(r"(caption|figcaption|photo-credit|credit)", re.I)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Example per-site rules scaffold for future extension
SITE_RULES = {
    # "example.com": {
    #     "prefer_selector": "article",
    #     "remove_selectors": [".promo", ".breadcrumbs"]
    # }
}


def _mk_session():
    s = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
        raise_on_status=False,
    )
    s.mount("http://", HTTPAdapter(max_retries=retries))
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({"User-Agent": DEFAULT_UA, "Accept-Language": "ja,en;q=0.8"})
    return s


def _text_len(s: str) -> int:
    return len((s or "").strip())


def _norm_ws(s: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", (s or "")).strip()


def _get_domain(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _has_headings(soup: BeautifulSoup) -> bool:
    return bool(soup.find(["h2", "h3", "h4", "h5"]))


def _link_density(soup: BeautifulSoup) -> float:
    total_text = soup.get_text(separator=" ", strip=True)
    total_len = len(total_text) if total_text else 0
    if total_len == 0:
        return 1.0
    link_chars = 0
    for a in soup.find_all("a"):
        link_chars += len(a.get_text(" ", strip=True))
    ld = link_chars / max(1, total_len)
    return min(max(ld, 0.0), 1.0)


def _unwrap_all_anchors(soup: BeautifulSoup):
    for a in soup.find_all("a"):
        a.replace_with(a.get_text(" ", strip=True))


def _remove_scripts_styles(soup: BeautifulSoup):
    for bad in soup(["script", "style", "noscript", "template"]):
        bad.decompose()


def _drop_noise(soup: BeautifulSoup):
    # Generic noise by id/class/name
    for el in soup.find_all(True):
        # fast path: skip tiny tags
        if not el.attrs:
            continue
        id_hit = any(
            NOISE_PATTERNS.search(str(v)) for k, v in el.attrs.items() if k in ("id", "name")
        )
        class_hit = any(
            NOISE_PATTERNS.search(c) for c in el.get("class", []) if isinstance(c, str)
        )
        data_hit = any(
            NOISE_PATTERNS.search(str(v))
            for k, v in el.attrs.items()
            if k.startswith("data-")
        )
        if id_hit or class_hit or data_hit:
            el.decompose()

    # Captions
    for el in soup.find_all(True):
        if CAPTION_PATTERNS.search(" ".join(el.get("class", [])) + " " + (el.get("id") or "")):
            el.decompose()

    # Obvious layout junk
    for selector in ("header", "footer", "nav", "aside"):
        for el in soup.select(selector):
            el.decompose()


def _prefer_alt_version(url: str, html_text: str, session: requests.Session) -> (str, str):
    """
    Prefer AMP or print version if available and likely better.
    Returns (best_url, best_html)
    """
    try:
        soup = BeautifulSoup(html_text, "lxml")
    except Exception:
        return url, html_text

    # AMP link
    amp_link = soup.find("link", rel=lambda v: v and "amphtml" in v.lower())
    # Fallback: anchor with /amp or ?amp
    amp_a = soup.find("a", href=re.compile(r"(\?|/)amp(\b|=)", re.I))

    # Print link variants
    print_link = soup.find("link", rel=lambda v: v and "alternate" in v.lower() and "print" in (soup.get("type","") + soup.get("media","")).lower())
    # Heuristic anchors
    print_a = soup.find("a", href=re.compile(r"(print|output=print)", re.I))

    candidates = []
    base = url
    if amp_link and amp_link.get("href"):
        candidates.append(urljoin(base, amp_link["href"]))
    elif amp_a and amp_a.get("href"):
        candidates.append(urljoin(base, amp_a["href"]))

    if print_link and print_link.get("href"):
        candidates.append(urljoin(base, print_link["href"]))
    elif print_a and print_a.get("href"):
        candidates.append(urljoin(base, print_a["href"]))

    # Deduplicate
    seen = set()
    uniq = []
    for c in candidates:
        if c not in seen:
            uniq.append(c)
            seen.add(c)

    if not uniq:
        return url, html_text

    def fetch_len(u):
        try:
            r = session.get(u, timeout=15)
            r.raise_for_status()
            r.encoding = r.encoding or r.apparent_encoding or "utf-8"
            return u, r.text, len(BeautifulSoup(r.text, "lxml").get_text())
        except Exception:
            return u, "", 0

    best_u, best_t, best_len = url, html_text, len(BeautifulSoup(html_text, "lxml").get_text())
    for cu in uniq:
        fu, ft, fl = fetch_len(cu)
        # Prefer if content text length is significantly larger (heuristic)
        if fl > best_len * 1.1:
            best_u, best_t, best_len = fu, ft, fl

    return best_u, best_t


class ContentExtractor:
    def __init__(self):
        self.session = _mk_session()

    def _fetch(self, url: str) -> (str, str):
        r = self.session.get(url, timeout=20)
        r.raise_for_status()
        # Trust server encoding if provided, else use chardet fallback
        r.encoding = r.encoding or r.apparent_encoding or "utf-8"
        return r.url, r.text

    def _apply_site_rules(self, domain: str, soup: BeautifulSoup):
        rules = SITE_RULES.get(domain)
        if not rules:
            return
        for sel in rules.get("remove_selectors", []):
            for el in soup.select(sel):
                el.decompose()

    def _readability_candidate(self, html_text: str, domain: str):
        try:
            doc = Document(html_text)
            content_html = doc.summary(html_partial=True)
            # Title from readability
            title = _norm_ws(doc.short_title() or "")
            # Clean + noise drop
            soup = BeautifulSoup(content_html, "lxml")
            _remove_scripts_styles(soup)
            self._apply_site_rules(domain, soup)
            _drop_noise(soup)
            _unwrap_all_anchors(soup)
            text = soup.get_text(separator=" ", strip=True)
            score = len(text) * (1.0 - _link_density(soup))
            if _has_headings(soup):
                score *= 1.15  # favor structured content
            return {
                "name": "readability",
                "title": title,
                "soup": soup,
                "text": text,
                "score": score,
            }
        except Exception:
            return None

    def _trafilatura_candidate(self, html_text: str, domain: str):
        """
        Try to get a structured-ish output. We'll attempt XML first; if not available,
        we fall back to plain text while still returning a soup built from paragraphs.
        """
        try:
            # Prefer precision to reduce noise; include tables if any
            xml = trafilatura.extract(
                html_text,
                include_comments=False,
                include_tables=True,
                favor_recall=False,
                output_format="xml",
            )
        except TypeError:
            # Older trafilatura versions may use 'output_format' name 'outputformat'
            try:
                xml = trafilatura.extract(
                    html_text,
                    include_comments=False,
                    include_tables=True,
                    favor_recall=False,
                    outputformat="xml",
                )
            except Exception:
                xml = None
        except Exception:
            xml = None

        soup = None
        title = ""
        if xml and "<" in xml and "</" in xml:
            # Very light mapping from TEI-ish to HTML
            # Map <head> -> <h2>, keep <p>, convert <quote> -> <blockquote>
            mapped = (
                xml.replace("<head", "<h2").replace("</head>", "</h2>")
                    .replace("<quote", "<blockquote").replace("</quote>", "</blockquote>")
                    .replace("<list", "<ul").replace("</list>", "</ul>")
                    .replace("<item", "<li").replace("</item>", "</li>")
            )
            soup = BeautifulSoup(mapped, "lxml")
            # title element may exist in xml head; trafilatura sometimes embeds <title>
            maybe_title = soup.find("title")
            if maybe_title:
                title = _norm_ws(maybe_title.get_text(" ", strip=True))
        else:
            # Plain text extraction
            txt = trafilatura.extract(
                html_text,
                include_comments=False,
                include_tables=True,
                favor_recall=False,
            )
            if not txt:
                return None
            # Wrap into <p> blocks to handle downstream uniformly
            soup = BeautifulSoup("", "lxml")
            for para in (txt or "").splitlines():
                para = _norm_ws(para)
                if para:
                    p = soup.new_tag("p")
                    p.string = para
                    soup.append(p)

        _remove_scripts_styles(soup)
        self._apply_site_rules(domain, soup)
        _drop_noise(soup)
        _unwrap_all_anchors(soup)
        text = soup.get_text(separator=" ", strip=True)
        score = len(text) * (1.0 - _link_density(soup))
        # Less structural confidence than readability unless actual headings exist
        if _has_headings(soup):
            score *= 1.10
        else:
            score *= 0.92
        return {
            "name": "trafilatura",
            "title": title,
            "soup": soup,
            "text": text,
            "score": score,
        }

    def _pick_best(self, cand_a, cand_b):
        candidates = [c for c in (cand_a, cand_b) if c and _text_len(c.get("text")) > 50]
        if not candidates:
            # return the one that at least exists
            c = cand_a or cand_b
            return c
        # Highest score wins
        return max(candidates, key=lambda c: c["score"])

    def _page_title(self, raw_html: str) -> str:
        try:
            soup = BeautifulSoup(raw_html, "lxml")
            t = soup.find("title")
            return _norm_ws(t.get_text(" ", strip=True)) if t else ""
        except Exception:
            return ""

    def _h1_title(self, soup: BeautifulSoup) -> str:
        h1 = soup.find("h1")
        if h1:
            return _norm_ws(h1.get_text(" ", strip=True))
        return ""

    def _format_table(self, table: Tag) -> list:
        rows = []
        for tr in table.find_all("tr"):
            cells = []
            for cell in tr.find_all(["th", "td"]):
                txt = _norm_ws(cell.get_text(" ", strip=True))
                cells.append(txt)
            if cells:
                rows.append(" | ".join(cells))
        return rows

    def _emit_blocks(self, soup: BeautifulSoup) -> list:
        """
        Traverse content soup in document order and emit plain-text lines under headings.
        """
        lines = []
        current_level = None
        opened_body = False

        def open_body(level_label: str, heading_text: str):
            nonlocal opened_body, current_level
            lines.append(f"{level_label}: {heading_text}")
            lines.append("Body:")
            opened_body = True
            current_level = level_label

        # If content before first heading exists, put it under a synthetic H2
        emitted_any = False

        for el in soup.find_all(["h2", "h3", "h4", "h5", "p", "ul", "ol", "pre", "code", "blockquote", "table"], recursive=True):
            name = el.name.lower()
            if name in ("h2", "h3", "h4", "h5"):
                txt = _norm_ws(el.get_text(" ", strip=True))
                if not txt:
                    continue
                open_body(name.upper(), txt)
                emitted_any = True
                continue

            # Open intro section if no heading encountered yet
            if not emitted_any and not opened_body:
                open_body("H2", "Introduction")
                emitted_any = True

            if name == "p":
                t = _norm_ws(el.get_text(" ", strip=True))
                if t:
                    lines.append(t)

            elif name in ("ul", "ol"):
                # flatten nested li
                for i, li in enumerate(el.find_all("li", recursive=False), start=1):
                    bullet = "-" if name == "ul" else f"{i}."
                    t = _norm_ws(li.get_text(" ", strip=True))
                    if t:
                        lines.append(f"{bullet} {t}")

            elif name == "blockquote":
                t = el.get_text("\n", strip=True)
                if t:
                    for ln in t.splitlines():
                        ln = _norm_ws(ln)
                        if ln:
                            lines.append(f"> {ln}")

            elif name == "pre":
                code_text = el.get_text("\n", strip=False)
                code_text = code_text.rstrip("\n")
                lines.append("```")
                lines.extend(code_text.splitlines())
                lines.append("```")

            elif name == "code":
                # avoid duplicating if code is inside pre
                if el.parent and el.parent.name == "pre":
                    continue
                code_text = el.get_text(" ", strip=True)
                if code_text:
                    lines.append("`" + code_text + "`")

            elif name == "table":
                table_lines = self._format_table(el)
                if table_lines:
                    lines.append("[TABLE]")
                    lines.extend(table_lines)
                    lines.append("[/TABLE]")

        if not lines:
            # No recognizable content
            lines.append("ERROR: content_not_found")

        return lines

    def extract_to_template(self, url: str, prefer_alt: bool = True) -> str:
        """
        Returns:
          Plain text in the template:

          BEGIN
          URL: ...
          Title: ...
          H2: ...
          Body:
          ...
          END
        """
        try:
            final_url, raw_html = self._fetch(url)
        except Exception as e:
            return f"BEGIN\nURL: {url}\nERROR: content_not_found ({type(e).__name__})\nEND"

        # Optionally prefer AMP/print variants
        if prefer_alt:
            try:
                final_url, raw_html = _prefer_alt_version(final_url, raw_html, self.session)
            except Exception:
                pass

        domain = _get_domain(final_url)
        page_title = self._page_title(raw_html)

        # Build candidates
        cand_read = self._readability_candidate(raw_html, domain)
        cand_tra = self._trafilatura_candidate(raw_html, domain)

        best = self._pick_best(cand_read, cand_tra)
        if not best or not best.get("soup"):
            return f"BEGIN\nURL: {final_url}\nERROR: content_not_found\nEND"

        # Title priority: h1 from best soup > readability title > page <title>
        title = self._h1_title(best["soup"]) or (cand_read.get("title") if cand_read else "") or page_title or ""

        # Final cleanup before emission
        _remove_scripts_styles(best["soup"])
        _drop_noise(best["soup"])
        _unwrap_all_anchors(best["soup"])

        lines = self._emit_blocks(best["soup"])
        # Assemble template
        out = ["BEGIN", f"URL: {final_url}", f"Title: {title if title else '(no title)'}"]
        out.extend(lines)
        out.append("END")
        return "\n".join(out)
