"""منطق تقسيم كتب PDF إلى فصول (بدون أي اعتماد على واجهة Streamlit).

الخطوات:
1. extract_pdf: استخراج الأسطر مع حجم الخط وإحداثياتها عبر PyMuPDF.
2. analyze: ترتيب القراءة (أعمدة)، الترويسات المتكررة، الحواشي، صفحات الفهرس وحقوق النشر.
3. detect: اكتشاف الفصول (فهرس PDF الداخلي، أنماط العناوين، حجم الخط).
4. build_outputs: بناء نص كل فصل (تنظيف، دمج أسطر الفقرات، معالجة الحواشي).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Set, Tuple


class PdfError(Exception):
    pass


class PasswordRequired(PdfError):
    pass


# ----------------------------------------------------------------------------
# نماذج البيانات
# ----------------------------------------------------------------------------

@dataclass
class Line:
    text: str
    size: float
    bold: bool
    x0: float
    y0: float
    x1: float
    y1: float
    block: int
    running: bool = False  # ترويسة/تذييل/رقم صفحة
    note: bool = False     # حاشية سفلية محتملة


@dataclass
class PageData:
    index: int
    width: float
    height: float
    lines: List[Line]
    rtl: bool = False

    @property
    def chars(self) -> int:
        return sum(len(l.text) for l in self.lines)


@dataclass
class Document:
    pages: List[PageData]
    toc: List[Tuple[int, str, int]] = field(default_factory=list)  # (level, title, page0)
    body_size: float = 10.0
    toc_pages: Set[int] = field(default_factory=set)
    copyright_pages: Set[int] = field(default_factory=set)

    @property
    def total_chars(self) -> int:
        return sum(p.chars for p in self.pages)


@dataclass
class Section:
    title: str
    page: int          # صفر-أساس
    line: int = 0      # فهرس السطر داخل الصفحة بترتيب القراءة
    kind: str = "chapter"
    include: bool = True


@dataclass
class BuildOptions:
    remove_doi: bool = True
    remove_copyright: bool = True
    remove_running: bool = True
    notes_mode: str = "chapter"  # keep | chapter | page | drop
    reflow: bool = True
    include_title: bool = True
    include_preface: bool = True
    dropped_pages: Set[int] = field(default_factory=set)


@dataclass
class Output:
    filename: str
    title: str
    text: str
    start_page: int
    end_page: int
    words: int


# ----------------------------------------------------------------------------
# تنظيف النص الخام
# ----------------------------------------------------------------------------

SOFT = "\u0001"  # علامة: السطر انتهى بشرطة وصل اختيارية
_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl",
              "\ufb05": "st", "\ufb06": "st"}
_PRES_FORMS = re.compile(r"[\uFB50-\uFDFF\uFE70-\uFEFF]")
ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
TERMINAL = tuple('.!?؟:;"”’»)]…۔')


def clean_raw(s: str) -> str:
    if s.endswith("\u00ad"):
        s = s[:-1] + SOFT
    s = s.replace("\u00ad", "").replace("\u200b", "").replace("\xa0", " ")
    for k, v in _LIGATURES.items():
        s = s.replace(k, v)
    if _PRES_FORMS.search(s):
        s = _PRES_FORMS.sub(lambda m: unicodedata.normalize("NFKC", m.group()), s)
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def is_rtl_text(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if ARABIC_RE.match(c)) / len(letters) > 0.5


# ----------------------------------------------------------------------------
# استخراج PDF (PyMuPDF)
# ----------------------------------------------------------------------------

def extract_pdf(pdf_bytes: bytes, password: str = "") -> Document:
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise PdfError(f"تعذّر فتح ملف PDF: {exc}") from exc

    try:
        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                raise PasswordRequired("الملف محمي بكلمة مرور.")

        pages: List[PageData] = []
        for i in range(doc.page_count):
            page = doc[i]
            data = page.get_text("dict")
            lines: List[Line] = []
            for bno, b in enumerate(data.get("blocks", [])):
                if b.get("type", 0) != 0:
                    continue
                for l in b.get("lines", []):
                    spans = [s for s in l.get("spans", []) if s.get("text", "").strip()]
                    if not spans:
                        continue
                    raw = "".join(s["text"] for s in l["spans"])
                    text = clean_raw(raw)
                    if not text:
                        continue
                    total = sum(len(s["text"]) for s in spans) or 1
                    # الحجم السائد بعدد الأحرف
                    by_size: Dict[float, int] = defaultdict(int)
                    for s in spans:
                        by_size[round(float(s.get("size", 0)), 1)] += len(s["text"])
                    size = max(by_size.items(), key=lambda kv: kv[1])[0]
                    bold_chars = sum(
                        len(s["text"]) for s in spans
                        if (int(s.get("flags", 0)) & 16) or "bold" in str(s.get("font", "")).lower()
                    )
                    x0, y0, x1, y1 = l["bbox"]
                    lines.append(Line(text, size, bold_chars / total >= 0.6, x0, y0, x1, y1, bno))
            pages.append(PageData(i, float(page.rect.width), float(page.rect.height), lines))

        toc: List[Tuple[int, str, int]] = []
        try:
            for lvl, title, pg in doc.get_toc(simple=True):
                if isinstance(pg, int) and 1 <= pg <= len(pages):
                    toc.append((int(lvl), clean_raw(str(title)), pg - 1))
        except Exception:  # noqa: BLE001
            toc = []
    finally:
        doc.close()

    result = Document(pages=pages, toc=toc)
    analyze(result)
    return result


# ----------------------------------------------------------------------------
# تحليل الصفحات: ترتيب القراءة، ترويسات، حواشي، فهرس، حقوق نشر
# ----------------------------------------------------------------------------

def order_lines(lines: List[Line], width: float, rtl: bool) -> List[Line]:
    """ترتيب القراءة: يدعم صفحات العمودين، ويكسر التدفق عند الكتل العريضة."""
    if not lines:
        return lines
    blocks: Dict[int, List[Line]] = defaultdict(list)
    for ln in lines:
        blocks[ln.block].append(ln)
    infos = []
    for bid, ls in blocks.items():
        infos.append({
            "id": bid,
            "x0": min(l.x0 for l in ls), "x1": max(l.x1 for l in ls),
            "y0": min(l.y0 for l in ls), "y1": max(l.y1 for l in ls),
        })
    mid, tol = width / 2, width * 0.04

    def wide(b) -> bool:
        return (b["x1"] - b["x0"]) > width * 0.6 or (b["x0"] < mid - tol and b["x1"] > mid + tol)

    def row_key(b):
        return (round(b["y0"] / 3), -b["x1"] if rtl else b["x0"])

    infos.sort(key=lambda b: (b["y0"], b["x0"]))
    sequence: List[dict] = []
    group: List[dict] = []

    def flush():
        if not group:
            return
        left = [b for b in group if (b["x0"] + b["x1"]) / 2 < mid]
        right = [b for b in group if (b["x0"] + b["x1"]) / 2 >= mid]
        columnar = (
            left and right
            and max(b["x1"] for b in left) <= mid + tol
            and min(b["x0"] for b in right) >= mid - tol
        )
        if columnar:
            first, second = (right, left) if rtl else (left, right)
            sequence.extend(sorted(first, key=lambda b: b["y0"]))
            sequence.extend(sorted(second, key=lambda b: b["y0"]))
        else:
            sequence.extend(sorted(group, key=row_key))
        group.clear()

    for b in infos:
        if wide(b):
            flush()
            sequence.append(b)
        else:
            group.append(b)
    flush()

    ordered: List[Line] = []
    for b in sequence:
        ordered.extend(blocks[b["id"]])
    return ordered


def estimate_body_size(pages: List[PageData]) -> float:
    c: Counter = Counter()
    for p in pages:
        for l in p.lines:
            c[round(l.size * 2) / 2] += len(l.text)
    return c.most_common(1)[0][0] if c else 10.0


_PAGE_NUM_RE = re.compile(r"^\W*(?:page\s+|صفحة\s+)?(?:\d{1,4}|[ivxlcdm]{1,7}|[٠-٩۰-۹]{1,4})\W*$", re.I)


def _run_key(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text.lower())).strip()


def mark_running(pages: List[PageData], body: float) -> None:
    n = len(pages)
    if n < 3:
        return
    counts: Dict[str, Set[int]] = defaultdict(set)
    for p in pages:
        for l in p.lines:
            if l.y1 <= p.height * 0.10 or l.y0 >= p.height * 0.90:
                counts[_run_key(l.text)].add(p.index)
    for p in pages:
        for l in p.lines:
            in_zone = l.y1 <= p.height * 0.10 or l.y0 >= p.height * 0.90
            if not in_zone:
                continue
            if len(l.text) <= 14 and _PAGE_NUM_RE.match(l.text):
                l.running = True
            elif len(counts[_run_key(l.text)]) >= 3 and l.size <= body * 1.1:
                l.running = True


def mark_notes(pages: List[PageData], body: float) -> None:
    for p in pages:
        normal = [l for l in p.lines if not l.running and l.size > body * 0.9]
        if len(normal) < 3:
            continue
        last_normal_y = max(l.y0 for l in normal)
        for l in p.lines:
            if (not l.running and l.size <= body * 0.9 and l.y0 > p.height * 0.5
                    and l.y0 >= last_normal_y - 1):
                l.note = True


_LEADER = re.compile(r"(?:\.\s?){3,}\s*[\divxlcdm٠-٩]{1,6}\s*$|…+\s*[\divxlcdm٠-٩]{1,6}\s*$", re.I)
_ENDS_NUM = re.compile(r"\S\s+[\divxlcdm٠-٩]{1,6}\s*$", re.I)


def is_toc_page(page: PageData, n_pages: int) -> bool:
    lines = [l for l in page.lines if not l.running]
    if len(lines) < 8:
        return False
    leaders = sum(1 for l in lines if _LEADER.search(l.text))
    if leaders >= 5:
        return True
    front_zone = page.index < max(25, int(n_pages * 0.12))
    ends_num = sum(1 for l in lines if _ENDS_NUM.search(l.text))
    lens = sorted(len(l.text) for l in lines)
    median = lens[len(lens) // 2]
    return front_zone and ends_num / len(lines) >= 0.5 and median < 70


_COPY_MARKERS = [
    r"©", r"\ball rights reserved\b", r"\bisbn\b", r"library of congress",
    r"british library", r"cataloguing[- ]in[- ]publication|cataloging[- ]in[- ]publication",
    r"\bfirst published\b", r"\bprinted (?:and bound )?in\b", r"\bpublished by\b",
    r"\btypeset\b", r"\bno part of this\b", r"\bcopyright\b",
    r"جميع الحقوق محفوظة", r"حقوق الطبع", r"رقم الإيداع", r"الترقيم الدولي",
]
_COPY_RES = [re.compile(m, re.I) for m in _COPY_MARKERS]


def is_copyright_page(page: PageData, n_pages: int) -> bool:
    if page.index >= max(12, -(-n_pages * 8 // 100)):
        return False
    text = "\n".join(l.text for l in page.lines)
    hits = sum(1 for r in _COPY_RES if r.search(text))
    chars = len(text)
    return (hits >= 3 and chars < 4000) or (hits >= 2 and chars < 1800)


def analyze(doc: Document) -> None:
    for p in doc.pages:
        p.rtl = is_rtl_text(" ".join(l.text for l in p.lines))
        p.lines = order_lines(p.lines, p.width, p.rtl)
    doc.body_size = estimate_body_size(doc.pages)
    mark_running(doc.pages, doc.body_size)
    mark_notes(doc.pages, doc.body_size)
    n = len(doc.pages)
    doc.toc_pages = {p.index for p in doc.pages if is_toc_page(p, n)}
    doc.copyright_pages = {p.index for p in doc.pages if is_copyright_page(p, n)}


# ----------------------------------------------------------------------------
# اكتشاف الفصول
# ----------------------------------------------------------------------------

_EN_NUMS = (r"twenty(?:[-\s](?:one|two|three|four|five|six|seven|eight|nine))?|thirty|forty|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
            r"one|two|three|four|five|six|seven|eight|nine|ten")
_ROMAN = r"(?=[ivxlcdm])m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_NUM = rf"(?:\d{{1,3}}|{_ROMAN}|{_EN_NUMS})"


def _en_re(words: str) -> "re.Pattern[str]":
    return re.compile(
        rf"^(?P<kw>{words})\s+(?P<num>{_NUM})(?![\w])\s*(?:[:.\-–—]\s*|\s+)?(?P<title>.*)$", re.I)


EN_CHAPTER = _en_re(r"chapter|chap\.")
EN_PART = _en_re(r"part")
EN_SECTION = _en_re(r"section")


def _flex(word: str) -> str:
    w = re.sub("[أإا]", "[أإا]", word)
    w = w.replace("ى", "[ىي]").replace("ة", "[ةه]")
    return w.replace(" ", r"\s+")


_AR_ORD = [
    "الحادي عشر", "الحادية عشرة", "الثاني عشر", "الثانية عشرة", "الثالث عشر", "الثالثة عشرة",
    "الرابع عشر", "الخامس عشر", "العشرون", "الأول", "الأولى", "الثاني", "الثانية", "الثالث",
    "الثالثة", "الرابع", "الرابعة", "الخامس", "الخامسة", "السادس", "السادسة", "السابع",
    "السابعة", "الثامن", "الثامنة", "التاسع", "التاسعة", "العاشر", "العاشرة",
]
_AR_NUM = r"(?:[\d٠-٩۰-۹]+|" + "|".join(_flex(w) for w in _AR_ORD) + r")"


def _ar_re(words: str) -> "re.Pattern[str]":
    return re.compile(
        rf"^(?P<kw>{words})\s+(?P<num>{_AR_NUM})(?![\u0621-\u064A])\s*(?:[:.\-–—،]\s*|\s+)?(?P<title>.*)$")


AR_CHAPTER = _ar_re("الفصل|الباب|المقالة")
AR_PART = _ar_re("الجزء|الكتاب")
AR_SECTION = _ar_re("القسم|المبحث")

_FRONT_EN = (
    r"preface|foreword|prologue|epilogue|afterword|contents|table of contents|"
    r"acknowledg(?:e)?ments?|bibliography|references|works cited|glossary|index|"
    r"about the authors?|dedication|list of (?:figures|tables|illustrations|abbreviations)|"
    r"abbreviations|further reading|notes|introduction|conclusions?|abstract|summary|"
    r"appendix(?:\s+[a-z0-9]+)?(?:\s*[:.\-–—]\s*.{0,80})?"
)
_FRONT_AR = (
    r"المقدمة|مقدمة|تمهيد|تقديم|الفهرس|فهرس|المحتويات|الخاتمة|خاتمة|المراجع|قائمة المراجع|"
    r"المصادر|المصادر والمراجع|الملاحق|ملحق(?:\s+\S+)?|ثبت المصطلحات|شكر وتقدير|الشكر والتقدير|إهداء"
)
FRONT_RE = re.compile(rf"^(?:{_FRONT_EN}|{_FRONT_AR})$", re.I)
AMBIGUOUS = {"introduction", "conclusion", "conclusions", "abstract", "summary", "notes",
             "المقدمة", "مقدمة", "الخاتمة", "خاتمة", "تمهيد"}

SKIP_TITLES = re.compile(
    r"^\s*(cover|title page|half[- ]title|copyright(?: page)?|contents|table of contents|also by|"
    r"praise for|series page|الغلاف|صفحة العنوان|حقوق النشر|الفهرس|المحتويات)\s*$", re.I)


def _match_heading(text: str, include_sections: bool):
    pats = [("chapter", EN_CHAPTER), ("chapter", AR_CHAPTER), ("part", EN_PART), ("part", AR_PART)]
    if include_sections:
        pats += [("section", EN_SECTION), ("section", AR_SECTION)]
    for kind, pat in pats:
        m = pat.match(text)
        if m:
            return kind, m.group("title").strip()
    return None


def _strong(ln: Line, body: float) -> bool:
    letters = [c for c in ln.text if c.isalpha()]
    caps = len(letters) >= 3 and all(c.isupper() for c in letters)
    return ln.size >= body * 1.08 or ln.bold or caps


def _norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.lower())


def _extend_title(lines: List[Line], idx: int, body: float, include_sections: bool) -> str:
    ln = lines[idx]
    extra: List[str] = []
    j = idx + 1
    while j < len(lines) and len(extra) < 2:
        nxt = lines[j]
        if nxt.running or nxt.note or len(nxt.text) > 100:
            break
        if _match_heading(nxt.text, include_sections):
            break
        same_block = nxt.block == ln.block
        near = nxt.y0 - ln.y1 < 2.5 * max(ln.size, 1)
        big = nxt.size >= body * 1.05 or nxt.bold
        ok = ((same_block and len(nxt.text) < 80 and (big or abs(nxt.size - ln.size) <= 1.5))
              or (near and big and not nxt.text.endswith((".", "؟", "?"))))
        if not ok:
            break
        extra.append(nxt.text)
        j += 1
    return ln.text + (": " + " ".join(extra) if extra else "")


def find_by_patterns(doc: Document, include_sections: bool = False) -> List[Section]:
    body = doc.body_size
    found: List[Tuple[Section, bool]] = []  # (section, ambiguous)
    for p in doc.pages:
        if p.index in doc.copyright_pages:
            continue
        is_toc = p.index in doc.toc_pages
        blen = Counter(l.block for l in p.lines)
        for idx, ln in enumerate(p.lines):
            if ln.running or ln.note or len(ln.text) > 120 or ln.y0 > p.height * 0.85:
                continue
            text = ln.text.rstrip(SOFT)
            if _LEADER.search(text):
                continue
            strong = _strong(ln, body)

            hit = None if is_toc else _match_heading(text, include_sections)
            if hit:
                kind, title_part = hit
                words = len(title_part.split())
                weak = (len(text) <= 90 and not text.endswith((",", ";"))
                        and (blen[ln.block] == 1 or (blen[ln.block] == 2 and not title_part)))
                if title_part[:1].islower() and not strong:
                    continue  # «Chapter 3 discusses ...» جملة عادية وليست عنوانًا
                if not (strong or (weak and words <= 12)):
                    continue
                if title_part.endswith(".") and words > 5 and not strong:
                    continue
                title = text if title_part else _extend_title(p.lines, idx, body, include_sections)
                found.append((Section(title, p.index, idx, kind), False))
                continue

            key = re.sub(r"[\s:.\-–—]+$", "", text.strip()).lower()
            if FRONT_RE.match(key) and (strong or blen[ln.block] == 1) and ln.y0 < p.height * 0.6:
                found.append((Section(text.strip(), p.index, idx, "front"), key in AMBIGUOUS))

    chapter_pages = [s.page for s, _ in found if s.kind in ("chapter", "part")]
    first, last = (min(chapter_pages), max(chapter_pages)) if chapter_pages else (None, None)
    result: List[Section] = []
    seen: Set[str] = set()
    for s, ambiguous in sorted(found, key=lambda t: (t[0].page, t[0].line)):
        if ambiguous and first is not None and first <= s.page <= last:
            continue
        key = _norm(s.title)
        if s.kind != "front" or not key.startswith("appendix"):
            if key in seen:
                continue
        seen.add(key)
        result.append(s)
    return merge_part_dividers(result)


def merge_part_dividers(sections: List[Section]) -> List[Section]:
    """صفحة «Part I» التي يليها فصل مباشرة لا تصبح ملفًا مستقلًا؛ يبدأ الفصل من موضعها."""
    out: List[Section] = []
    i = 0
    while i < len(sections):
        s = sections[i]
        if s.kind == "part" and i + 1 < len(sections):
            nxt = sections[i + 1]
            if nxt.kind == "chapter" and nxt.page - s.page <= 1:
                out.append(Section(nxt.title, s.page, s.line, "chapter"))
                i += 2
                continue
        out.append(s)
        i += 1
    return out


# -- بحسب حجم الخط ------------------------------------------------------------

def font_classes(doc: Document) -> List[dict]:
    body = doc.body_size
    stats: Dict[float, dict] = {}
    for p in doc.pages:
        if p.index in doc.toc_pages or p.index in doc.copyright_pages:
            continue
        for ln in p.lines:
            if ln.running or ln.note or ln.size < body * 1.15 or len(ln.text) > 120 or ln.y0 > p.height * 0.7:
                continue
            k = round(ln.size * 2) / 2
            st = stats.setdefault(k, {"size": k, "count": 0, "pages": set(), "examples": []})
            st["count"] += 1
            st["pages"].add(p.index)
            if len(st["examples"]) < 3:
                st["examples"].append(ln.text[:40])
    return sorted(stats.values(), key=lambda d: -d["size"])


def auto_font_size(doc: Document) -> Optional[float]:
    for c in font_classes(doc):
        if len(c["pages"]) >= 3:
            return c["size"]
    return None


def find_by_font(doc: Document, size: float, tol: float = 0.4) -> List[Section]:
    body = doc.body_size
    out: List[Section] = []
    seen: Set[str] = set()
    for p in doc.pages:
        if p.index in doc.toc_pages or p.index in doc.copyright_pages:
            continue
        i = 0
        lines = p.lines
        while i < len(lines):
            ln = lines[i]
            ok = (not ln.running and not ln.note and abs(ln.size - size) <= tol
                  and len(ln.text) <= 120 and ln.y0 <= p.height * 0.7)
            if not ok:
                i += 1
                continue
            parts = [ln.text]
            j = i + 1
            while (j < len(lines) and len(parts) < 4 and abs(lines[j].size - size) <= tol
                   and not lines[j].running and (lines[j].block == ln.block
                                                  or lines[j].y0 - lines[j - 1].y1 < 1.5 * size)):
                parts.append(lines[j].text)
                j += 1
            title = " ".join(parts)
            if re.fullmatch(r"[\d\W]+|[ivxlcdm]+", title.strip(), re.I):
                title = _extend_title(lines, i, body, False)
            words = len(title.split())
            if not (title.endswith(".") and words > 10) and _norm(title) not in seen:
                seen.add(_norm(title))
                out.append(Section(title.strip(), p.index, i, "chapter"))
            i = j
    return out


# -- بحسب فهرس PDF الداخلي -----------------------------------------------------

def outline_levels(doc: Document) -> Dict[int, int]:
    return dict(sorted(Counter(l for l, _, _ in doc.toc).items()))


def auto_outline_level(doc: Document) -> int:
    levels = outline_levels(doc)
    for lvl in sorted(levels):
        if levels[lvl] >= 2:
            return lvl
    return max(levels) if levels else 1


def _locate(page: PageData, title: str) -> int:
    nt = _norm(title)
    if not nt:
        return 0
    for i, ln in enumerate(page.lines):
        if ln.running:
            continue
        nl = _norm(ln.text)
        if len(nl) >= 4 and (nt.startswith(nl) or (len(nt) >= 4 and nl.startswith(nt))):
            return i
    return 0


def find_by_outline(doc: Document, max_level: Optional[int] = None) -> List[Section]:
    if max_level is None:
        max_level = auto_outline_level(doc)
    out: List[Section] = []
    for lvl, title, pg in doc.toc:
        if lvl > max_level:
            continue
        out.append(Section(title, pg, _locate(doc.pages[pg], title), "outline"))
    out.sort(key=lambda s: (s.page, s.line))
    return out


# -- الواجهة العامة -------------------------------------------------------------

def detect(doc: Document, mode: str = "auto", outline_level: Optional[int] = None,
           font_size: Optional[float] = None, include_sections: bool = False) -> Tuple[List[Section], str]:
    """يعيد (الأقسام، اسم الطريقة المستخدمة)."""
    sections: List[Section] = []
    used = "none"
    if mode in ("auto", "outline") and doc.toc:
        level = outline_level or auto_outline_level(doc)
        cand = find_by_outline(doc, level)
        if (mode == "outline" and cand) or len(cand) >= 3:
            sections, used = cand, "outline"
    if not sections and mode in ("auto", "patterns"):
        cand = find_by_patterns(doc, include_sections)
        if (mode == "patterns" and cand) or sum(1 for s in cand if s.kind in ("chapter", "part")) >= 2:
            sections, used = cand, "patterns"
    if not sections and mode in ("auto", "font"):
        size = font_size or auto_font_size(doc)
        if size:
            cand = find_by_font(doc, size)
            if cand:
                sections, used = cand, "font"
    for s in sections:
        if SKIP_TITLES.match(s.title):
            s.include = False
    return sections, used


# ----------------------------------------------------------------------------
# تنظيف DOI وحقوق النشر على مستوى السطر
# ----------------------------------------------------------------------------

_DOI_RES = [
    re.compile(r"\b(?:https?://(?:dx\.)?doi\.org/|doi:\s*)10\.\d{4,9}/\S+", re.I),
    re.compile(r"\b10\.\d{4,9}/\S+"),
]
_STRONG_COPY = re.compile(
    r"©|\ball rights reserved\b|\bno part of this (?:publication|book|work)\b|"
    r"\bISBN(?:-1[03])?:?\s*[\dXx\-\s]{10,}|library of congress cataloging|"
    r"\bprinted in (?:the )?(?:usa|u\.s\.a\.|uk|united states|united kingdom|great britain)\b|"
    r"copyright\s*(?:©|\(c\))?\s*\d{4}|جميع الحقوق محفوظة|حقوق الطبع والنشر",
    re.I,
)


def clean_line(text: str, remove_doi: bool, remove_copyright: bool) -> str:
    if remove_copyright and len(text) < 200 and _STRONG_COPY.search(text):
        return ""
    if remove_doi:
        before = text
        for r in _DOI_RES:
            text = r.sub("", text)
        if text != before:
            text = re.sub(r"\s{2,}", " ", text).strip(" \t|–—-,;")
            if re.fullmatch(r"(?:doi:?|https?://\S*)?", text, re.I):
                return ""
    return text


# ----------------------------------------------------------------------------
# بناء نص الفصول
# ----------------------------------------------------------------------------

_BULLET_SYM = re.compile(r"^[•●▪■◦‣\-–—*]\s+")
_BULLET_NUM = re.compile(r"^(?:\(?\d{1,3}[.)]|\([a-z]\))\s+")


def _join(a: str, b: str) -> str:
    if a.endswith(SOFT):
        return a[:-1] + b
    if re.search(r"[A-Za-z\u00C0-\u024F]-$", a) and b[:1].islower():
        return a[:-1] + b
    return a + " " + b


def lines_to_paragraphs(lines: List[Line], rtl: bool, reflow: bool) -> List[str]:
    if not lines:
        return []
    if not reflow:
        paras, cur, last = [], [], None
        for l in lines:
            if last is not None and l.block != last and cur:
                paras.append("\n".join(cur))
                cur = []
            cur.append(l.text.replace(SOFT, ""))
            last = l.block
        if cur:
            paras.append("\n".join(cur))
        return paras

    ext: Dict[int, List[float]] = {}
    for l in lines:
        e = ext.setdefault(l.block, [l.x0, l.x1])
        e[0], e[1] = min(e[0], l.x0), max(e[1], l.x1)

    paras: List[str] = []
    cur: Optional[str] = None
    prev: Optional[Line] = None
    for l in lines:
        new = prev is None or l.block != prev.block
        if not new:
            left, right = ext[l.block]
            width = right - left
            prev_term = prev.text.rstrip(SOFT).endswith(TERMINAL)
            indent = (right - l.x1) if rtl else (l.x0 - left)
            prev_len = prev.x1 - prev.x0
            if abs(l.size - prev.size) > 1.0 or l.bold != prev.bold:
                new = True
            elif _BULLET_SYM.match(l.text) or (prev_term and _BULLET_NUM.match(l.text)):
                new = True
            elif prev_term and indent > 0.8 * l.size:
                new = True
            elif prev_term and width > 0 and prev_len < 0.7 * width:
                new = True
        if new:
            if cur is not None:
                paras.append(cur)
            cur = l.text
        else:
            cur = _join(cur, l.text)  # type: ignore[arg-type]
        prev = l
    if cur is not None:
        paras.append(cur)
    return [p.replace(SOFT, "") for p in paras]


def _ends_open(p: str) -> bool:
    return len(p) > 40 and not p.rstrip().endswith(TERMINAL)


def merge_continuations(items: List[Tuple[str, bool]]) -> List[str]:
    """items: (فقرة، هل هي أول فقرة في صفحة). تدمج الفقرة المقطوعة مع تاليتها."""
    out: List[str] = []
    for text, page_start in items:
        if out and _ends_open(out[-1]) and not _BULLET_SYM.match(text):
            first = text[:1]
            lower_start = first.islower() or first in ",;)"
            arabic = bool(ARABIC_RE.match(first)) and bool(ARABIC_RE.search(out[-1][-1:]))
            if lower_start or (page_start and arabic):
                out[-1] = _join(out[-1], text)
                continue
        out.append(text)
    return out


Pos = Tuple[int, int]


def render_range(doc: Document, start: Pos, end: Pos, opts: BuildOptions) -> str:
    items: List[Tuple[str, bool]] = []
    notes_all: List[str] = []
    last_page = min(end[0], len(doc.pages) - 1)
    for pi in range(start[0], last_page + 1):
        if pi in opts.dropped_pages:
            continue
        page = doc.pages[pi]
        lo = start[1] if pi == start[0] else 0
        hi = end[1] if pi == end[0] else len(page.lines)
        seg = [l for l in page.lines[lo:hi] if not (l.running and opts.remove_running)]
        seg = [replace(l, text=clean_line(l.text, opts.remove_doi, opts.remove_copyright)) for l in seg]
        seg = [l for l in seg if l.text.strip()]
        separate = opts.notes_mode != "keep"
        body = [l for l in seg if not (l.note and separate)]
        notes = [l for l in seg if l.note and separate]
        for k, para in enumerate(lines_to_paragraphs(body, page.rtl, opts.reflow)):
            items.append((para, k == 0))
        note_paras = lines_to_paragraphs(notes, page.rtl, opts.reflow)
        if notes and opts.notes_mode == "page":
            items.append(("[Footnotes]", False))
            items.extend((n, False) for n in note_paras)
        elif notes and opts.notes_mode == "chapter":
            notes_all.extend(note_paras)
    paras = merge_continuations(items)
    if notes_all:
        paras.append("[Footnotes]")
        paras.extend(notes_all)
    return "\n\n".join(p for p in paras if p.strip()).strip()


def safe_filename(name: str, max_len: int = 70) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = re.sub(r"\s+", "_", name).strip("._ ")
    return name[:max_len] or "chapter"


def build_outputs(doc: Document, sections: List[Section], opts: BuildOptions) -> List[Output]:
    n = len(doc.pages)
    secs = sorted(sections, key=lambda s: (s.page, s.line))
    outputs: List[Output] = []
    used_names: Set[str] = set()

    def add(title: str, start: Pos, end: Pos, number: int) -> None:
        text = render_range(doc, start, end, opts)
        if not text:
            return
        if opts.include_title and _norm(title)[:20] not in _norm(text[:400]):
            text = title + "\n\n" + text
        base = f"{number:02d}_{safe_filename(title)}"
        name, k = base + ".txt", 2
        while name in used_names:
            name, k = f"{base}_{k}.txt", k + 1
        used_names.add(name)
        end_page = min(end[0] if end[1] > 0 else end[0] - 1, n - 1)
        outputs.append(Output(name, title, text + "\n", start[0], max(end_page, start[0]), len(text.split())))

    number = 0
    if secs and opts.include_preface and (secs[0].page, secs[0].line) > (0, 0):
        number += 1
        add("Front_matter", (0, 0), (secs[0].page, secs[0].line), number)
    for i, s in enumerate(secs):
        if not s.include:
            continue
        end: Pos = (secs[i + 1].page, secs[i + 1].line) if i + 1 < len(secs) else (n, 0)
        number += 1
        add(s.title, (s.page, s.line), end, number)
    return outputs
