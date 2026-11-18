import posixpath
import re
import zipfile
from urllib.parse import unquote
from extractor.parsers.html import _HTMLTextExtractor


def _resolve_zip_path(base_dir: str, href: str) -> str | None:
    """Resolve an OPF/container href to a ZIP entry name.

    hrefs are relative to the directory of the document that declares them
    (e.g. ``OPS/``). Returns None for empty, absolute, URL-like or
    root-escaping (``..``) references.
    """
    href = unquote(href.split("#", 1)[0]).strip()
    if not href or href.startswith("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", href):
        return None
    path = posixpath.normpath(posixpath.join(base_dir, href))
    if path in (".", "..") or path.startswith("../") or path.startswith("/"):
        return None
    return path


def _find_opf_path(zf: zipfile.ZipFile, names: list[str]) -> str | None:
    """Locate the OPF package via META-INF/container.xml, else the first .opf."""
    if "META-INF/container.xml" in names:
        container = zf.read("META-INF/container.xml").decode("utf-8", errors="replace")
        match = re.search(r'<rootfile\b[^>]*\bfull-path=["\']([^"\']+)["\']', container)
        if match:
            candidate = _resolve_zip_path("", match.group(1))
            if candidate in names:
                return candidate
    opf_files = [n for n in names if n.endswith(".opf")]
    return opf_files[0] if opf_files else None


def _opf_html_paths(opf_text: str, opf_dir: str, names: list[str]) -> list[str]:
    """HTML entries in spine order (fallback: manifest order), resolved against the OPF dir."""
    manifest: dict[str, str] = {}
    manifest_order: list[str] = []
    for tag in re.findall(r"<item\b[^>]*>", opf_text):
        href = re.search(r'\bhref=["\']([^"\']+)["\']', tag)
        if not href or not re.search(r"\.(?:xhtml|html|htm)(?:#.*)?$", href.group(1)):
            continue
        path = _resolve_zip_path(opf_dir, href.group(1))
        if path is None or path not in names:
            continue
        item_id = re.search(r'\bid=["\']([^"\']+)["\']', tag)
        if item_id:
            manifest[item_id.group(1)] = path
        manifest_order.append(path)

    spine = [manifest[i] for i in re.findall(r'<itemref\b[^>]*\bidref=["\']([^"\']+)["\']', opf_text) if i in manifest]
    ordered = spine or manifest_order
    return list(dict.fromkeys(ordered))


def extract_with_ebooklib(epub_path: str) -> str | None:
    try:
        import ebooklib
        from ebooklib import epub
        from bs4 import BeautifulSoup

        book = epub.read_epub(epub_path)
        parts = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            soup = BeautifulSoup(item.get_content(), "html.parser")
            parts.append(soup.get_text(separator="\n"))
        return "\n\n".join(parts)
    except ImportError:
        return None
    except Exception:
        return None


def extract_with_zipfile(epub_path: str) -> str | None:
    """stdlib-only EPUB extractor: unzip → parse HTML files."""
    try:
        with zipfile.ZipFile(epub_path) as zf:
            names = zf.namelist()
            # Read OPF spine to get reading order, fall back to sorted xhtml files
            spine_order: list[str] = []
            opf_path = _find_opf_path(zf, names)
            if opf_path:
                opf_text = zf.read(opf_path).decode("utf-8", errors="replace")
                spine_order = _opf_html_paths(opf_text, posixpath.dirname(opf_path), names)

            html_files = spine_order or sorted(
                n for n in names if n.endswith((".html", ".xhtml"))
            )
            if not html_files:
                return None

            parts = []
            for name in html_files:
                try:
                    raw = zf.read(name).decode("utf-8", errors="replace")
                    parser = _HTMLTextExtractor()
                    parser.feed(raw)
                    parts.append(parser.get_text())
                except Exception:
                    continue
            return "\n\n".join(parts) if parts else None
    except Exception:
        return None


def count_epub_chapters(epub_path: str) -> int:
    """Count spine items (approximate chapter count) without dependencies."""
    try:
        with zipfile.ZipFile(epub_path) as zf:
            opf_files = [n for n in zf.namelist() if n.endswith(".opf")]
            if not opf_files:
                return 0
            opf_text = zf.read(opf_files[0]).decode("utf-8", errors="replace")
            return len(re.findall(r'<itemref\b', opf_text))
    except Exception:
        return 0
