"""
Regression tests for the stdlib EPUB fallback when the OPF package lives in a
subfolder (e.g. ``OPS/content.opf``), as in most commercial EPUBs.

Before the fix, ``extract_with_zipfile`` looked up OPF hrefs as ZIP root
entries, so every read failed and extraction returned None.
"""

import sys
import textwrap
import zipfile
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from extractor.parsers.epub import (  # noqa: E402
    _resolve_zip_path,
    count_epub_chapters,
    extract_with_zipfile,
)
from extractor.utils import extract_single_file  # noqa: E402


def _make_subfolder_epub(path: Path, extra_entries: dict[str, str] | None = None,
                         extra_manifest: str = "", extra_spine: str = "") -> Path:
    """EPUB 3 with container.xml -> OPS/package.opf and chapters in OPS/text/.

    The manifest lists chapter two before chapter one; the spine says one, two.
    """
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr(
            "META-INF/container.xml",
            textwrap.dedent("""\
                <?xml version="1.0"?>
                <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                  <rootfiles>
                    <rootfile full-path="OPS/package.opf" media-type="application/oebps-package+xml"/>
                  </rootfiles>
                </container>
            """),
        )
        zf.writestr(
            "OPS/package.opf",
            textwrap.dedent(f"""\
                <?xml version="1.0"?>
                <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
                  <metadata/>
                  <manifest>
                    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
                    <item id="c2" href="text/chapter%202.xhtml" media-type="application/xhtml+xml"/>
                    <item id="c1" href="text/chapter1.xhtml" media-type="application/xhtml+xml"/>
                    <item id="css" href="styles/book.css" media-type="text/css"/>
                    {extra_manifest}
                  </manifest>
                  <spine>
                    <itemref idref="c1"/>
                    <itemref idref="c2"/>
                    {extra_spine}
                  </spine>
                </package>
            """),
        )
        zf.writestr("OPS/nav.xhtml", "<html><body><nav>Table of contents</nav></body></html>")
        zf.writestr("OPS/text/chapter1.xhtml", "<html><body><p>Alpha chapter one.</p></body></html>")
        zf.writestr("OPS/text/chapter 2.xhtml", "<html><body><p>Beta chapter two.</p></body></html>")
        zf.writestr("OPS/styles/book.css", "p { margin: 0 }")
        for name, content in (extra_entries or {}).items():
            zf.writestr(name, content)
    return path


class TestResolveZipPath:
    def test_relative_to_opf_dir(self):
        assert _resolve_zip_path("OPS", "text/ch1.xhtml") == "OPS/text/ch1.xhtml"

    def test_percent_decoding_and_fragment(self):
        assert _resolve_zip_path("OPS", "text/chapter%202.xhtml#p1") == "OPS/text/chapter 2.xhtml"

    def test_parent_inside_root_is_allowed(self):
        assert _resolve_zip_path("OPS/text", "../nav.xhtml") == "OPS/nav.xhtml"

    def test_escaping_root_is_rejected(self):
        assert _resolve_zip_path("OPS", "../../evil.xhtml") is None
        assert _resolve_zip_path("", "../evil.xhtml") is None

    def test_absolute_and_url_are_rejected(self):
        assert _resolve_zip_path("OPS", "/etc/passwd") is None
        assert _resolve_zip_path("OPS", "https://example.com/x.xhtml") is None
        assert _resolve_zip_path("OPS", "") is None


class TestSubfolderOpf:
    def test_zipfile_fallback_reads_subfolder_chapters_in_spine_order(self, tmp_path):
        text = extract_with_zipfile(str(_make_subfolder_epub(tmp_path / "book.epub")))

        assert text is not None
        assert "Alpha chapter one." in text
        assert "Beta chapter two." in text
        assert text.index("Alpha chapter one.") < text.index("Beta chapter two.")
        # nav.xhtml is in the manifest but not in the spine
        assert "Table of contents" not in text

    def test_root_escaping_href_is_ignored(self, tmp_path):
        epub = _make_subfolder_epub(
            tmp_path / "book.epub",
            extra_entries={"secret.xhtml": "<p>Should never be read</p>"},
            extra_manifest='<item id="evil" href="../../secret.xhtml" media-type="application/xhtml+xml"/>',
            extra_spine='<itemref idref="evil"/>',
        )
        text = extract_with_zipfile(str(epub))

        assert text is not None
        assert "Should never be read" not in text
        assert "Alpha chapter one." in text

    def test_end_to_end_without_ebooklib(self, tmp_path):
        epub = _make_subfolder_epub(tmp_path / "book.epub")

        with mock.patch("extractor.utils.prepare_dependencies"), \
                mock.patch("extractor.utils.extract_with_ebooklib", return_value=None):
            result = extract_single_file(epub, "text", "no")

        assert result["extraction_method"] == "zipfile"
        assert "Alpha chapter one." in result["text"]
        assert "Beta chapter two." in result["text"]
        assert result["words"] > 0

    def test_count_chapters_uses_spine(self, tmp_path):
        assert count_epub_chapters(str(_make_subfolder_epub(tmp_path / "book.epub"))) == 2
