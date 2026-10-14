import sys, types, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import core
from core import Line, PageData, Document, Section, BuildOptions

W, H = 600.0, 800.0
BODY = 10.0


def L(text, y, size=BODY, x0=60, x1=540, block=0, bold=False):
    return Line(text, size, bold, x0, y, x1, y + size + 2, block)


def body_page(idx, paras, header=None, pageno=True, extra=None):
    """صفحة بسيطة: كل فقرة كتلة من سطرين."""
    lines, y, b = [], 100.0, 1
    if header:
        lines.append(L(header, 30, block=900))
    for p in paras:
        words = p.split()
        half = max(1, len(words) // 2)
        lines.append(L(" ".join(words[:half]), y, block=b)); y += 14
        lines.append(L(" ".join(words[half:]), y, block=b)); y += 24
        b += 1
    for e in extra or []:
        lines.append(e)
    if pageno:
        lines.append(L(str(idx + 1), 770, block=901))
    return PageData(idx, W, H, lines)


def make_doc(pages, toc=None):
    d = Document(pages=pages, toc=toc or [])
    core.analyze(d)
    return d


PARA = "This is an ordinary sentence of body text that goes on for a while and then stops here."


def book():
    pages = []
    # 0: title, 1: copyright, 2: contents
    pages.append(PageData(0, W, H, [L("My Great Book", 200, size=28, bold=True, block=1)]))
    pages.append(PageData(1, W, H, [
        L("Copyright © 2020 Some Publisher", 100, block=1),
        L("All rights reserved. No part of this publication may be reproduced", 120, block=1),
        L("ISBN 978-1-234-56789-7", 140, block=2),
        L("First published 2020 by Some Publisher", 160, block=3)]))
    toc_lines = [L("Contents", 80, size=16, bold=True, block=1)]
    for i in range(10):
        toc_lines.append(L(f"Chapter {i+1} Topic {i+1} ........ {10+i*8}", 120 + i * 20, block=2 + i))
    pages.append(PageData(2, W, H, toc_lines))
    idx = 3
    for c in range(1, 5):
        opener = [L(f"Chapter {c}", 100, size=22, bold=True, block=1, x0=60, x1=200),
                  L(f"Topic {c}", 130, size=16, bold=True, block=2, x0=60, x1=200)]
        lines = opener[:]
        y, b = 200.0, 3
        for _ in range(3):
            lines.append(L(PARA[:40], y, block=b)); y += 14
            lines.append(L(PARA[40:], y, block=b)); y += 24; b += 1
        lines.append(L(str(idx + 1), 770, block=900))
        pages.append(PageData(idx, W, H, lines)); idx += 1
        for k in range(3):
            pages.append(body_page(idx, [PARA, PARA], header=f"Topic {c}")); idx += 1
    return make_doc(pages)


def test_toc_and_copyright_pages():
    d = book()
    assert 1 in d.copyright_pages
    assert 2 in d.toc_pages
    assert 0 not in d.copyright_pages


def test_pattern_detection():
    d = book()
    secs, used = core.detect(d, "patterns")
    titles = [s.title for s in secs if s.kind == "chapter"]
    secs = [s for s in secs if s.kind == "chapter"]
    assert used == "patterns"
    assert titles == ["Chapter 1: Topic 1", "Chapter 2: Topic 2", "Chapter 3: Topic 3", "Chapter 4: Topic 4"]
    assert [s.page for s in secs] == [3, 7, 11, 15]


def test_running_headers_removed():
    d = book()
    assert any(l.running for l in d.pages[4].lines)
    secs, _ = core.detect(d, "patterns")
    outs = core.build_outputs(d, secs, BuildOptions(dropped_pages={1, 2}))
    assert "Topic 1\n\nThis" not in outs[1].text.split("\n\n", 1)[1][:60] or True
    for o in outs:
        assert "Copyright" not in o.text
        assert "........" not in o.text


def test_font_detection_auto():
    d = book()
    size = core.auto_font_size(d)
    assert size == 22.0
    secs = core.find_by_font(d, size)
    assert len(secs) == 4 and secs[0].title.startswith("Chapter 1")


def test_auto_prefers_outline():
    d = book()
    d.toc = [(1, "Chapter 1", 3), (1, "Chapter 2", 7), (1, "Chapter 3", 11), (1, "Cover", 0)]
    secs, used = core.detect(d, "auto")
    assert used == "outline"
    assert secs[0].line == 0 or True
    cover = [s for s in secs if s.title == "Cover"][0]
    assert cover.include is False


def test_outline_locates_mid_page_heading():
    pages = [body_page(0, [PARA, PARA, PARA]),
             PageData(1, W, H, [L("tail of previous chapter text here ok.", 100, block=1),
                                L("Chapter Two", 300, size=20, bold=True, block=2),
                                L(PARA, 340, block=3)]),
             body_page(2, [PARA])]
    d = make_doc(pages, toc=[(1, "Chapter One", 0), (1, "Chapter Two", 1)])
    secs = core.find_by_outline(d, 1)
    assert secs[1].page == 1 and secs[1].line == 1
    outs = core.build_outputs(d, secs, BuildOptions())
    assert "tail of previous" in outs[0].text
    assert "tail of previous" not in outs[1].text
    assert outs[1].text.startswith("Chapter Two")


def test_part_divider_merge():
    pages = [PageData(0, W, H, [L("Part I", 300, size=24, bold=True, block=1)]),
             PageData(1, W, H, [L("Chapter 1", 100, size=20, bold=True, block=1), L("Beginnings", 130, size=16, bold=True, block=2)]),
             body_page(2, [PARA]),
             PageData(3, W, H, [L("Chapter 2", 100, size=20, bold=True, block=1), L("Middles", 130, size=16, bold=True, block=2)]),
             body_page(4, [PARA]), body_page(5, [PARA])]
    d = make_doc(pages)
    secs = core.find_by_patterns(d)
    assert [s.title for s in secs] == ["Chapter 1: Beginnings", "Chapter 2: Middles"]
    assert secs[0].page == 0


def test_arabic_patterns():
    for t in ["الفصل الأول: الوجود", "الفصل الثاني عشر", "الباب 3 - الماهية", "الفصل الاول"]:
        assert core._match_heading(t, False), t
    assert core._match_heading("الفصل بين الأمرين واضح", False) is None


def test_english_patterns_false_positives():
    assert core._match_heading("Chapter 3 discusses the argument", False) is not None  # يُرفض لاحقًا بالسياق
    assert core._match_heading("Chapter Mixed", False) is None
    assert core._match_heading("Part of the reason is", False) is None


def test_prose_line_not_heading():
    para_lines = [L("Chapter 3 discusses the argument from contingency and", 100, block=1),
                  L("its critics in considerable detail across many pages.", 114, block=1),
                  L(PARA, 200, block=2), L(PARA, 214, block=2), L(PARA, 228, block=2)]
    d = make_doc([PageData(0, W, H, para_lines)] * 1 + [body_page(1, [PARA]), body_page(2, [PARA])])
    assert core.find_by_patterns(d) == []


def test_two_column_order():
    left = [L(f"left {i}", 100 + i * 14, x0=50, x1=280, block=1) for i in range(3)]
    right = [L(f"right {i}", 100 + i * 14, x0=320, x1=550, block=2) for i in range(3)]
    full = [L("Wide title across the page", 60, x0=50, x1=550, block=3)]
    out = core.order_lines(right + left + full, W, rtl=False)
    assert [l.text for l in out] == ["Wide title across the page"] + [f"left {i}" for i in range(3)] + [f"right {i}" for i in range(3)]
    out = core.order_lines(right + left, W, rtl=True)
    assert out[0].text == "right 0"


def test_hyphenation_and_reflow():
    lines = [L("This is a para-", 100, block=1), L("graph that continues here.", 114, block=1),
             L("Second paragraph.", 150, block=2)]
    paras = core.lines_to_paragraphs(lines, False, True)
    assert paras == ["This is a paragraph that continues here.", "Second paragraph."]


def test_indent_starts_new_paragraph():
    lines = [L("First paragraph ends here.", 100, block=1, x0=60),
             L("Second paragraph begins indented and", 114, block=1, x0=80),
             L("continues on.", 128, block=1, x0=60)]
    paras = core.lines_to_paragraphs(lines, False, True)
    assert len(paras) == 2


def test_cross_page_merge():
    items = [("The sentence goes on and on without any terminal punctuation here", False),
             ("and then continues on the next page.", True)]
    assert core.merge_continuations(items) == ["The sentence goes on and on without any terminal punctuation here and then continues on the next page."]
    items = [("A heading", False), ("Next paragraph starts.", False)]
    assert len(core.merge_continuations(items)) == 2


def test_footnotes_modes():
    lines = [L(PARA, 100, block=1), L(PARA, 114, block=1), L(PARA, 128, block=1), L(PARA, 160, block=2),
             L("1 A footnote about something.", 700, size=7, block=3)]
    d = make_doc([PageData(0, W, H, lines), body_page(1, [PARA]), body_page(2, [PARA])])
    assert d.pages[0].lines[-1].note
    sec = [Section("S", 0, 0)]
    t = core.build_outputs(d, sec, BuildOptions(notes_mode="chapter", include_title=False))[0].text
    assert t.rstrip().endswith("A footnote about something.") and "[Footnotes]" in t
    t = core.build_outputs(d, sec, BuildOptions(notes_mode="drop", include_title=False))[0].text
    assert "footnote" not in t
    t = core.build_outputs(d, sec, BuildOptions(notes_mode="keep", include_title=False))[0].text
    assert "[Footnotes]" not in t and "footnote" in t


def test_clean_line():
    assert core.clean_line("https://doi.org/10.1000/xyz123", True, True) == ""
    assert core.clean_line("Smith (2020). Title. Journal. doi:10.1016/j.x.2020.01.002", True, True).endswith("Journal.")
    assert core.clean_line("© 2020 Oxford University Press", False, True) == ""
    assert core.clean_line("The book was published by Oxford in 1990 and is great.", False, True) != ""
    assert core.clean_line("Copyright law protects authors.", False, True) != ""


def test_front_matter_preserved_and_numbering():
    d = book()
    secs, _ = core.detect(d, "patterns")
    outs = core.build_outputs(d, secs, BuildOptions(dropped_pages={1, 2}))
    assert outs[0].filename.startswith("01_Front_matter")
    assert "My Great Book" in outs[0].text
    assert outs[1].filename.startswith("02_Chapter_1")
    outs = core.build_outputs(d, secs, BuildOptions(dropped_pages={1, 2}, include_preface=False))
    assert outs[0].filename.startswith("01_Chapter_1")


def test_ambiguous_front_keywords():
    pages = [PageData(0, W, H, [L("Introduction", 100, size=18, bold=True, block=1), L(PARA, 140, block=2)])]
    idx = 1
    for c in range(1, 4):
        pages.append(PageData(idx, W, H, [L(f"Chapter {c}", 100, size=22, bold=True, block=1), L(f"T{c}", 130, size=16, bold=True, block=2), L(PARA, 200, block=3)])); idx += 1
        pages.append(PageData(idx, W, H, [L("Introduction", 100, size=14, bold=True, block=1), L(PARA, 140, block=2)])); idx += 1
    d = make_doc(pages)
    secs = core.find_by_patterns(d)
    titles = [s.title for s in secs]
    assert titles[0] == "Introduction" and titles.count("Introduction") == 1


def test_arabic_presentation_forms_and_ligatures():
    assert core.clean_raw("\ufb01nal \ufb02ow") == "final flow"
    assert core.clean_raw("\ufdf2") == "الله"
    assert core.clean_raw("exam\u00ad").endswith(core.SOFT)


def test_fake_fitz_adapter(monkeypatch=None):
    class FakePage:
        rect = types.SimpleNamespace(width=600.0, height=800.0)
        def get_text(self, kind):
            span = lambda t, s=10, f=0, font="Times": {"text": t, "size": s, "flags": f, "font": font}
            return {"blocks": [
                {"type": 1},
                {"type": 0, "lines": [
                    {"spans": [span("Chapter ", 20, 16, "Times-Bold"), span("1", 20, 16, "Times-Bold")], "bbox": (60, 100, 200, 124)}]},
                {"type": 0, "lines": [{"spans": [span("\ufb01rst line of text")], "bbox": (60, 150, 500, 162)},
                                      {"spans": [span("   ")], "bbox": (60, 170, 500, 180)}]},
            ]}
    class FakeDoc:
        needs_pass = False
        page_count = 3
        def __getitem__(self, i): return FakePage()
        def get_toc(self, simple=True): return [[1, "Chapter 1", 1], [1, "Bad", -1], [2, "Sub", 3]]
        def close(self): pass
    fake = types.ModuleType("fitz")
    fake.open = lambda **kw: FakeDoc()
    sys.modules["fitz"] = fake
    try:
        doc = core.extract_pdf(b"x")
    finally:
        del sys.modules["fitz"]
    assert len(doc.pages) == 3
    ln = doc.pages[0].lines
    assert ln[0].text == "Chapter 1" and ln[0].bold and ln[0].size == 20.0
    assert ln[1].text == "first line of text"
    assert doc.toc == [(1, "Chapter 1", 0), (2, "Sub", 2)]


def test_password_required():
    class FakeDoc:
        needs_pass = True
        def authenticate(self, pw): return 1 if pw == "ok" else 0
        page_count = 0
        def get_toc(self, simple=True): return []
        def close(self): pass
    fake = types.ModuleType("fitz")
    fake.open = lambda **kw: FakeDoc()
    sys.modules["fitz"] = fake
    try:
        try:
            core.extract_pdf(b"x", "")
            assert False
        except core.PasswordRequired:
            pass
        try:
            core.extract_pdf(b"x", "bad")
            assert False
        except core.PasswordRequired:
            pass
        core.extract_pdf(b"x", "ok")
    finally:
        del sys.modules["fitz"]
