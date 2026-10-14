import hashlib
import io
import zipfile
from typing import List

import pandas as pd
import streamlit as st

import core

st.set_page_config(page_title="PDF Chapter Splitter", page_icon="📚", layout="wide")

st.markdown(
    """
    <style>
    [data-testid="stMarkdownContainer"] { direction: rtl; text-align: right; }
    textarea { unicode-bidi: plaintext; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("📚 PDF Chapter Splitter")
st.write("ارفع كتاب PDF، راجع الفصول المكتشفة، ثم نزّل كل فصل كملف TXT أو الكل في ZIP.")

MODES = {
    "auto": "تلقائي (فهرس PDF ثم الأنماط ثم حجم الخط)",
    "outline": "فهرس PDF الداخلي (Bookmarks)",
    "patterns": "عناوين مثل Chapter / Part / الفصل",
    "font": "حجم الخط",
    "manual": "يدوي بالكامل",
}
METHOD_NAMES = {"outline": "فهرس PDF الداخلي", "patterns": "أنماط العناوين", "font": "حجم الخط", "none": "لا شيء"}
NOTES = {
    "chapter": "نقلها إلى نهاية كل فصل",
    "page": "وضعها في نهاية كل صفحة",
    "keep": "تركها في مكانها",
    "drop": "حذفها",
}


@st.cache_data(show_spinner=False, max_entries=2)
def load(_pdf_bytes: bytes, file_hash: str, password: str):
    return core.extract_pdf(_pdf_bytes, password)


def make_zip(files: List[core.Output], encoding: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.writestr(f.filename, f.text.encode(encoding))
    return buf.getvalue()


# ---------------------------------------------------------------- الشريط الجانبي
with st.sidebar:
    st.header("الإعدادات")
    password = st.text_input("كلمة مرور الملف (إن كان محميًا)", type="password")

    st.subheader("اكتشاف الفصول")
    mode = st.selectbox("الطريقة", list(MODES), format_func=MODES.get)
    include_sections = st.checkbox("اعتبار Section / القسم عناوين أقسام", value=False)

    st.subheader("التنظيف")
    drop_copyright = st.checkbox("استبعاد صفحات حقوق النشر المكتشفة", value=True)
    drop_toc = st.checkbox("استبعاد صفحات الفهرس المطبوع", value=True)
    remove_running = st.checkbox("إزالة الترويسات وأرقام الصفحات المتكررة", value=True)
    remove_doi = st.checkbox("إزالة DOI وروابطه", value=True)
    remove_copyright = st.checkbox("إزالة أسطر حقوق النشر الواضحة (©، ISBN، All rights reserved)", value=True)

    st.subheader("شكل النص")
    notes_mode = st.selectbox("الحواشي السفلية", list(NOTES), format_func=NOTES.get)
    reflow = st.checkbox("دمج أسطر الفقرة الواحدة وإصلاح الكلمات المقطوعة بشرطة", value=True)
    include_title = st.checkbox("إضافة العنوان في أول الملف إن لم يكن موجودًا", value=True)
    include_preface = st.checkbox("حفظ ما قبل أول فصل في ملف مستقل", value=True)
    bom = st.checkbox("UTF-8 مع BOM (لبرامج ويندوز القديمة)", value=False)

uploaded = st.file_uploader("اختر ملف PDF", type=["pdf"])
if not uploaded:
    st.stop()

pdf_bytes = uploaded.getvalue()
if len(pdf_bytes) > 150 * 1024 * 1024:
    st.error("حجم الملف أكبر من 150 ميغابايت. جرّب نسخة أصغر.")
    st.stop()

file_hash = hashlib.sha256(pdf_bytes).hexdigest()[:16]

with st.spinner("جارٍ استخراج النص وتحليل الصفحات..."):
    try:
        doc = load(pdf_bytes, file_hash, password)
    except core.PasswordRequired:
        st.warning("الملف محمي بكلمة مرور. أدخلها في الشريط الجانبي (لا تُستخدم إلا لفتح الملف).")
        st.stop()
    except core.PdfError as exc:
        st.error(str(exc))
        st.stop()

n = len(doc.pages)
c1, c2, c3, c4 = st.columns(4)
c1.metric("عدد الصفحات", n)
c2.metric("الأحرف المستخرجة", f"{doc.total_chars:,}")
c3.metric("حجم خط المتن", f"{doc.body_size:g}")
c4.metric("عناصر فهرس PDF", len(doc.toc))

if doc.total_chars < 100:
    st.warning(
        "لم يُستخرج نص كافٍ؛ يبدو أن الملف صور ممسوحة. هذه النسخة لا تدعم OCR، "
        "فاستخدم نسخة فيها نص قابل للتحديد."
    )
    st.stop()

# ---------------------------------------------------------------- إعدادات الاكتشاف الإضافية
outline_level = None
font_size = None
if mode in ("auto", "outline") and doc.toc:
    levels = core.outline_levels(doc)
    default_level = core.auto_outline_level(doc)
    outline_level = st.selectbox(
        "أعمق مستوى في فهرس PDF يُعدّ فصلًا",
        list(levels),
        index=list(levels).index(default_level),
        format_func=lambda l: f"المستوى {l} ({sum(c for k, c in levels.items() if k <= l)} عنصرًا)",
    )
if mode in ("auto", "font"):
    classes = core.font_classes(doc)
    if classes:
        auto_size = core.auto_font_size(doc)
        sizes = [c["size"] for c in classes]
        labels = {c["size"]: f"{c['size']:g} pt — {c['count']} سطرًا في {len(c['pages'])} صفحة، مثل: {c['examples'][0]}"
                  for c in classes}
        if mode == "font":
            font_size = st.selectbox(
                "حجم خط عناوين الفصول", sizes,
                index=sizes.index(auto_size) if auto_size in sizes else 0,
                format_func=labels.get,
            )

if mode == "manual":
    sections, used = [], "none"
else:
    sections, used = core.detect(doc, mode, outline_level, font_size, include_sections)

if sections:
    st.success(f"تم اكتشاف {len(sections)} قسمًا بطريقة: {METHOD_NAMES.get(used, used)}.")
else:
    st.warning("لم أعثر على فصول تلقائيًا. أضف الفصول وأرقام صفحات بدايتها في الجدول أدناه.")

# ---------------------------------------------------------------- الصفحات المستبعدة
reasons = {}
if drop_copyright:
    reasons.update({p: "حقوق نشر" for p in doc.copyright_pages})
if drop_toc:
    reasons.update({p: "فهرس مطبوع" for p in doc.toc_pages})

sig = hashlib.sha256(
    repr((file_hash, mode, outline_level, font_size, include_sections, drop_copyright, drop_toc)).encode()
).hexdigest()[:12]

dropped = st.multiselect(
    "صفحات تُستبعد من الملفات الناتجة (يمكنك الإضافة أو الحذف)",
    options=list(range(1, n + 1)),
    default=sorted(p + 1 for p in reasons),
    format_func=lambda p: f"صفحة {p}" + (f" — {reasons[p - 1]}" if (p - 1) in reasons else ""),
    key=f"dropped_{sig}",
)

# ---------------------------------------------------------------- جدول المراجعة
st.subheader("راجع التقسيم قبل التصدير")
st.caption("عدّل العناوين وأرقام الصفحات (تبدأ من 1)، ألغِ التضمين لاستبعاد قسم، أو أضف صفًّا جديدًا من آخر الجدول.")

if sections:
    df = pd.DataFrame(
        [{"تضمين": s.include, "العنوان": s.title, "صفحة البداية": s.page + 1} for s in sections]
    )
else:
    df = pd.DataFrame([{"تضمين": True, "العنوان": "Chapter 1", "صفحة البداية": 1}])

edited = st.data_editor(
    df,
    key=f"editor_{sig}",
    num_rows="dynamic",
    hide_index=True,
    column_config={
        "تضمين": st.column_config.CheckboxColumn("تضمين", default=True),
        "العنوان": st.column_config.TextColumn("العنوان"),
        "صفحة البداية": st.column_config.NumberColumn("صفحة البداية", min_value=1, max_value=n, step=1, format="%d"),
    },
)

# موضع السطر داخل الصفحة يأتي من الاكتشاف الأصلي إن بقي رقم الصفحة كما هو.
line_slots = {}
for s in sections:
    line_slots.setdefault(s.page, []).append(s.line)

final_sections: List[core.Section] = []
for _, row in edited.iterrows():
    try:
        page = int(row["صفحة البداية"]) - 1
    except (TypeError, ValueError):
        continue
    title = str(row["العنوان"]).strip() if pd.notna(row["العنوان"]) else ""
    if not 0 <= page < n:
        continue
    slots = line_slots.get(page, [])
    line = slots.pop(0) if slots else 0
    include = bool(row["تضمين"]) if pd.notna(row["تضمين"]) else True
    final_sections.append(core.Section(title or f"Section_{page + 1}", page, line, "manual", include))
final_sections.sort(key=lambda s: (s.page, s.line))

if not final_sections:
    st.info("أضف صفًّا واحدًا على الأقل في الجدول.")
    st.stop()

opts = core.BuildOptions(
    remove_doi=remove_doi,
    remove_copyright=remove_copyright,
    remove_running=remove_running,
    notes_mode=notes_mode,
    reflow=reflow,
    include_title=include_title,
    include_preface=include_preface,
    dropped_pages={p - 1 for p in dropped},
)
build_sig = hashlib.sha256(
    repr((file_hash, [(s.title, s.page, s.line, s.include) for s in final_sections], opts, bom)).encode()
).hexdigest()

if st.button("إنشاء ملفات TXT", type="primary"):
    with st.spinner("جارٍ بناء الملفات..."):
        files = core.build_outputs(doc, final_sections, opts)
    st.session_state["result"] = {"sig": build_sig, "files": files}

result = st.session_state.get("result")
if result and result["sig"] != build_sig:
    st.info("تغيّرت الإعدادات أو الجدول بعد آخر إنشاء. اضغط «إنشاء ملفات TXT» مرة أخرى.")
elif result:
    files: List[core.Output] = result["files"]
    if not files:
        st.error("لم يُنتج أي ملف. راجع أرقام الصفحات والأقسام المضمّنة.")
        st.stop()
    encoding = "utf-8-sig" if bom else "utf-8"
    st.success(f"تم تجهيز {len(files)} ملفًا.")

    summary = pd.DataFrame(
        [{"الملف": f.filename, "الكلمات": f.words, "الصفحات": f"{f.start_page + 1}–{f.end_page + 1}"} for f in files]
    )
    st.dataframe(summary, hide_index=True)
    small = [f.filename for f in files if f.words < 100]
    if small:
        st.warning("ملفات قصيرة جدًا (أقل من 100 كلمة) قد تكون عناوين خاطئة أو صفحات فاصلة: " + "، ".join(small))

    st.download_button(
        "⬇️ تنزيل كل الفصول في ZIP",
        data=make_zip(files, encoding),
        file_name=f"{core.safe_filename(uploaded.name.rsplit('.', 1)[0])}_chapters.zip",
        mime="application/zip",
    )

    st.subheader("معاينة وتنزيل ملف واحد")
    chosen = st.selectbox("الملف", [f.filename for f in files])
    f = next(x for x in files if x.filename == chosen)
    st.text_area("معاينة", value=f.text[:8000], height=300)
    st.download_button(
        f"تنزيل {f.filename}", data=f.text.encode(encoding), file_name=f.filename,
        mime="text/plain; charset=utf-8",
    )

st.divider()
st.caption("الاكتشاف التلقائي تقريبي؛ راجع الجدول قبل التصدير. استخدم الأداة للكتب التي يحق لك معالجتها.")
