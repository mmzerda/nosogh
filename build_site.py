#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
مولّد موقع ثابت متعدد اللغات من ملفات الاسترجاع (data-<lang>-<section>/*.jsonl).

يقرأ كل اللغات والأقسام الموجودة، ويبني موقعًا ثابتًا بلا قاعدة بيانات:
  - صفحة رئيسية لكل لغة: بحث فوري + روابط الأقسام والكتّاب.
  - صفحة لكل مادة (مقالة/فتوى): العنوان، الكاتب، الوسوم، النص، رابط المصدر في الأرشيف.
  - صفحة لكل كاتب: تجمع مواده.
  - فهرس أعلى يختار اللغة.
  - ملف بحث JSON خفيف لكل لغة (عنوان + كاتب + مقتطف)، يعمل داخل المتصفح.

التشغيل:  python3 build_site.py
المخرج:   site/   (انقله إلى public_html أو استضفه حيث شئت)

قابل لإعادة التشغيل: يبني من الصفر في كل مرة، فالمصدر هو ملفات jsonl.
يعمل على Python 3.6.
"""
import hashlib
import html
import json
import re
import shutil
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

# ------------------------------------------------------------ إعدادات
SITE_NAME = "نُسُغ"             # اسم المشروع
BASE = Path(__file__).resolve().parent
OUT = BASE / "site"
PREVIEW_CHARS = 220

# اللغات: الرمز → (الاسم بلغته، الاتجاه)
LANGS = {
    "ar": ("العربية", "rtl"), "fr": ("Français", "ltr"), "en": ("English", "ltr"),
    "de": ("Deutsch", "ltr"), "es": ("Español", "ltr"), "id": ("Indonesia", "ltr"),
    "tr": ("Türkçe", "ltr"), "pt": ("Português", "ltr"), "zh": ("中文", "ltr"),
    "it": ("Italiano", "ltr"), "fa": ("فارسی", "rtl"),
}
SECTIONS = {"article": {"ar": "المقالات", "x": "Articles"},
            "fatwa": {"ar": "الفتاوى", "x": "Fatwas"}}

# الكلمات المفتاحية العامة للموقع (ليست وسومًا حقيقية) — تُتجاهل
GENERIC_TAGS = {
    "islam", "sunnah", "coran", "quran", "qur'an", "leçons", "discours", "cours",
    "fatwas", "chants islamiques", "ouvrages", "flash", "sunna", "koran",
    "إسلام", "سنة", "قرآن", "دروس", "خطب", "محاضرات", "فتاوى", "أناشيد", "كتب", "فلاشات",
}

# تجميع التصنيفات الفوضوية في مجموعات نظيفة (يُطابَق بالاحتواء)
GROUPS = OrderedDict([
    ("مع القرآن وتدبّره", ["مع القرآن", "ليدبروا", "تدبر", "أمثال قرآنية", "إعجاز القرآن",
                            "مقاصد السور", "خواطر حول آيات", "1000 سؤال", "في القرآن"]),
    ("السيرة والشمائل", ["مع الحبيب", "السيرة", "دلائل النبوة", "الشمائل", "الصفات المحمدية",
                          "بيعة العقبة", "الهجرة", "ولادة خير", "وِلادةُ", "حدث في السنة",
                          "مدرسة الهجرة", "من بيعة العقبة"]),
    ("الصحابة والأعلام", ["مع الصديق", "مع الفاروق", "مع عثمان", "مع علي", "عمر بن الخطاب",
                           "مع الصحابة", "صحابيات", "صحابة", "تراجم الصحابة", "خلافة أبي بكر",
                           "مع الأنبياء", "إبراهيم", "سليمان", "عظماء أمة الإسلام", "الموسوعة التاريخية"]),
    ("رقائق وطبّ القلوب", ["خواطر", "طب القلوب", "القلب", "دواء القلوب", "القناعة", "الإحسان",
                            "من أخطائنا", "طب"]),
    ("سلاسل علمية", ["زاد المعاد", "الأحكام السلطانية", "دورة علمية", "دليل المسلم",
                      "أحكام الطهارة", "طريقة أهل الكفر"]),
    ("مواسم وخطب", ["رمضان", "عيد", "خطب", "ابدأ في رمضان", "مقاطع دعوية"]),
])
IGNORE_CATS = {"المقالات", ""}

# تعريف الكتّاب المعروفين (يُوسَّع لاحقًا)
BIO = {
    "أبو الهيثم محمد درويش": {
        "role": "كاتب وداعية · مسؤول قسم المقالات بطريق الإسلام",
        "bio": ("دكتوراه المناهج وطرق التدريس، تخصص تكنولوجيا التعليم، من كلية التربية بجامعة طنطا. "
                "كبير معلمين بالمرحلة الثانوية، ومدرّب استراتيجيات التعلّم، بالقناطر الخيرية "
                "(وزارة التربية والتعليم المصرية). من أغزر كتّاب طريق الإسلام، له سلاسل في تدبّر "
                "القرآن والسيرة والرقائق. محفوظ إرثه هنا بإذنه ونسبته إليه."),
        "initial": "هـ",
    },
}


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def group_of(cats):
    for cat in cats:
        c = norm(cat)
        if c in IGNORE_CATS:
            continue
        for gname, keys in GROUPS.items():
            if any(k in c for k in keys):
                return gname
    return "مقالات أخرى"


def canonical_slug(name, items):
    """سلگ موحّد للكاتب: أشهر معرّف scholar إن وُجد، وإلا بصمة الاسم."""
    ids = []
    for r in items:
        u = r.get("author_url") or ""
        m = re.search(r"/scholar/(\d+)", u)
        if m:
            ids.append(m.group(1))
    if ids:
        return "s" + Counter(ids).most_common(1)[0][0]
    return "n" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


# أنماط صفحة الكاتب الأنيقة (نُسُغ)
AUTHOR_CSS = """
:root{--bg:#f6f3ec;--surface:#fffdf8;--ink:#241f17;--muted:#6f6656;--line:#e4ddcf;--accent:#7a5a2b;--accent-soft:#c9a86318;--gold:#9a7636;
 --disp:"Amiri",Georgia,serif;--body:"Noto Naskh Arabic","Amiri",Georgia,serif;--ui:"Cairo",system-ui,sans-serif;}
@media(prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#17140e;--surface:#201c14;--ink:#ece5d7;--muted:#9c917c;--line:#2e2819;--accent:#c9a863;--accent-soft:#c9a86320;--gold:#d9b872;color-scheme:dark;}}
:root[data-theme="dark"]{--bg:#17140e;--surface:#201c14;--ink:#ece5d7;--muted:#9c917c;--line:#2e2819;--accent:#c9a863;--accent-soft:#c9a86320;--gold:#d9b872;color-scheme:dark;}
*{box-sizing:border-box}html{direction:rtl}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);line-height:1.9;direction:rtl;overflow-x:hidden}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.wrap{max-width:880px;margin:0 auto;padding-inline:20px}
header{border-bottom:1px solid var(--line);background:var(--surface)}
.bar{display:flex;align-items:center;justify-content:space-between;padding-block:14px;font-family:var(--ui)}
.brand{font-family:var(--disp);font-size:1.5rem;font-weight:700;color:var(--ink)}.brand span{color:var(--gold)}
.tgl{font-family:var(--ui);font-size:.8rem;cursor:pointer;background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:8px;padding:5px 11px}
.hero{background:var(--surface);border-bottom:1px solid var(--line);padding-block:38px}
.hero .wrap{display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap}
.ava{width:92px;height:92px;border-radius:50%;flex-shrink:0;background:linear-gradient(135deg,var(--accent),var(--gold));display:flex;align-items:center;justify-content:center;font-family:var(--disp);font-size:2.6rem;color:#fff}
.hinfo{flex:1;min-width:240px}.hinfo h1{font-family:var(--disp);font-size:2.1rem;margin:0 0 4px}
.role{font-family:var(--ui);font-size:.9rem;color:var(--gold);margin-bottom:10px}
.biop{font-size:1.06rem;color:var(--muted);max-width:62ch}
.stats{display:flex;gap:26px;margin-top:16px;font-family:var(--ui)}
.stats b{display:block;font-size:1.5rem;color:var(--accent);font-family:var(--disp);line-height:1}
.stats span{font-size:.76rem;color:var(--muted)}
main{padding-block:26px 70px}
input#q{width:100%;padding:12px 14px;font-size:1rem;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);font-family:var(--body);margin-bottom:18px}
.grp{border:1px solid var(--line);border-radius:12px;margin-bottom:12px;background:var(--surface);overflow:hidden}
.grp>summary{cursor:pointer;padding:14px 18px;font-family:var(--ui);font-weight:600;display:flex;justify-content:space-between;align-items:center;list-style:none}
.grp>summary::-webkit-details-marker{display:none}
.grp>summary .n{font-size:.8rem;color:var(--gold);font-weight:400}
.grp[open]>summary{border-bottom:1px solid var(--line)}
.grp ul{margin:0;padding:6px 0;list-style:none}
.grp li{padding:0}.grp li a{display:block;padding:9px 18px;font-size:1.05rem;color:var(--ink)}
.grp li a:hover{background:var(--accent-soft);text-decoration:none;color:var(--accent)}
#results{margin-top:4px}
.rcard{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:8px}
.rcard a{font-family:var(--disp);font-size:1.15rem}.rcard .g{font-family:var(--ui);font-size:.72rem;color:var(--gold)}.rcard p{margin:4px 0 0;font-size:.92rem;color:var(--muted)}
.cnt{font-family:var(--ui);font-size:.85rem;color:var(--muted);margin-bottom:12px}
.back{font-family:var(--ui);font-size:.85rem;color:var(--muted)}
footer{border-top:1px solid var(--line);color:var(--muted);font-size:.84rem;font-family:var(--ui);padding-block:20px}
"""

AUTHOR_FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
                '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
                '<link href="https://fonts.googleapis.com/css2?family=Amiri:wght@400;700'
                '&family=Cairo:wght@400;600;700&family=Noto+Naskh+Arabic:wght@400;500;700'
                '&display=swap" rel="stylesheet">')

AUTHOR_JS = (
    "var q=document.getElementById('q'),res=document.getElementById('results'),"
    "grp=document.getElementById('groups'),cnt=document.getElementById('cnt'),N=ALL.length;"
    "q.addEventListener('input',function(){"
    "var v=q.value.trim();"
    "if(!v){res.hidden=true;grp.hidden=false;cnt.textContent=N+' مقالة';return;}"
    "grp.hidden=true;res.hidden=false;"
    "var r=ALL.filter(function(a){return (a.t+' '+a.s).indexOf(v)>-1;});"
    "cnt.textContent=r.length+' نتيجة';"
    "res.innerHTML=r.slice(0,300).map(function(a){return '<div class=\\\"rcard\\\"><div class=\\\"g\\\">'+a.g+'</div><a href=\\\"'+a.u+'\\\">'+a.t+'</a>'+(a.s?'<p>'+a.s+'…</p>':'')+'</div>';}).join('');"
    "});"
)


def esc(s):
    return html.escape(s or "")


def clean_body(rec):
    """يزيل تكرار الوصف/العنوان في رأس النص، والسطور الزائدة."""
    body = (rec.get("body") or "").strip()
    for head in (rec.get("title"), rec.get("author"), rec.get("description")):
        if head:
            h = head.strip()
            # يزيل التكرار في أول النص فقط
            while body.startswith(h):
                body = body[len(h):].lstrip(" \n.")
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def real_tags(rec):
    out = []
    for t in rec.get("tags") or []:
        t = (t or "").strip()
        if t and t.lower() not in GENERIC_TAGS and t not in out:
            out.append(t)
    return out[:12]


def slug_author(rec):
    """معرّف ثابت للكاتب من author_url إن وُجد، وإلا من الاسم."""
    u = rec.get("author_url") or ""
    m = re.search(r"/scholar/(\d+)", u)
    if m:
        return "s" + m.group(1)
    name = (rec.get("author") or "").strip()
    if not name:
        return None
    # سلگ ASCII ثابت من بصمة الاسم (يبقى نفسه عبر إعادات البناء)،
    # فلا تتولّد أسماء ملفّات عربية هشّة على git/الاستضافة.
    return "n" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


# ------------------------------------------------------------ قالب HTML
def page(lang, title, body_html, depth=1, extra_head=""):
    name, dire = LANGS.get(lang, (lang, "ltr"))
    root = "../" * depth
    return """<!doctype html>
<html lang="{lang}" dir="{dire}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{title} — {site}</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1f1b16;--mut:#6b6358;--line:#e7e1d6;--accent:#7a5c2e;--card:#fff}}
@media(prefers-color-scheme:dark){{:root{{--bg:#17150f;--fg:#ece7dd;--mut:#9c93ac;--line:#2c281f;--accent:#c7a362;--card:#201d15}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);
 font-family:-apple-system,"Segoe UI","Noto Naskh Arabic",Tahoma,serif;line-height:1.85}}
a{{color:var(--accent);text-decoration:none}} a:hover{{text-decoration:underline}}
header{{border-bottom:1px solid var(--line);background:var(--card)}}
.wrap{{max-width:820px;margin:0 auto;padding:0 18px}}
.top{{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:14px 0}}
.brand{{font-weight:700;font-size:1.25rem;color:var(--fg)}}
.langs a{{margin:0 6px;font-size:.9rem}}
nav.sec{{padding:6px 0 14px}} nav.sec a{{margin-inline-end:14px}}
main{{padding:22px 0 60px}}
h1{{font-size:1.5rem;line-height:1.4;margin:.2em 0 .5em}}
.meta{{color:var(--mut);font-size:.92rem;margin-bottom:1.2em}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:16px 18px;margin:0 0 14px}}
.card h3{{margin:.1em 0 .3em;font-size:1.1rem}} .card p{{margin:.3em 0;color:var(--mut);font-size:.95rem}}
.tags a{{display:inline-block;background:var(--bg);border:1px solid var(--line);
 border-radius:999px;padding:1px 10px;margin:2px;font-size:.82rem}}
.body{{font-size:1.08rem;white-space:pre-wrap}}
.src{{margin-top:2em;padding-top:1em;border-top:1px solid var(--line);font-size:.88rem;color:var(--mut)}}
input#q{{width:100%;padding:12px 14px;font-size:1rem;border:1px solid var(--line);
 border-radius:10px;background:var(--card);color:var(--fg);margin:8px 0 18px}}
.count{{color:var(--mut);font-size:.9rem;margin-bottom:10px}}
footer{{border-top:1px solid var(--line);color:var(--mut);font-size:.85rem;padding:20px 0}}
</style>
{extra_head}
</head>
<body>
<header><div class="wrap">
 <div class="top">
   <a class="brand" href="{root}index.html">{site}</a>
   <div class="langs">{langbar}</div>
 </div>
</div></header>
<main><div class="wrap">
{body}
</div></main>
<footer><div class="wrap">
 {site} — أرشيف نصوص للقراءة والدراسة. الحقوق لأصحابها. المصدر مذكور في كل صفحة.
</div></footer>
</body></html>""".format(
        lang=lang, dire=dire, title=esc(title), site=esc(SITE_NAME),
        root=root, extra_head=extra_head, body=body_html,
        langbar=lang_bar(lang, depth))


def lang_bar(current, depth):
    root = "../" * depth
    out = []
    for code, (nm, _) in LANGS.items():
        if (OUT / code / "index.html").exists() or code == current:
            if code == current:
                out.append('<span>%s</span>' % esc(nm))
            else:
                out.append('<a href="%s%s/index.html">%s</a>' % (root, code, esc(nm)))
    return " · ".join(out)


# ------------------------------------------------------------ البناء
def load_lang(lang):
    """يحمّل كل أقسام لغة ما: قائمة سجلات مع نوع القسم."""
    recs = []
    for sec in SECTIONS:
        # العربية: اقرأ كل ملفّات data/ (يدعم التقسيم إلى شظايا أصغر من حدّ GitHub)
        ar_shards = sorted((BASE / "data").glob("*.jsonl")) if (lang == "ar" and sec == "article") else []
        for fn in ar_shards + [BASE / ("data-%s-%s" % (lang, sec)) / ("%ss.jsonl" % sec)]:
            if not fn.exists():
                continue
            for line in open(fn, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                r["_sec"] = sec
                r["_body"] = clean_body(r)
                if len(r["_body"]) < 60:
                    continue
                recs.append(r)
    # إزالة التكرار بالمعرّف+القسم
    seen, uniq = set(), []
    for r in recs:
        k = (r["_sec"], r.get("id"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def build_lang(lang):
    recs = load_lang(lang)
    if not recs:
        return 0
    d = OUT / lang
    (d / "a").mkdir(parents=True, exist_ok=True)
    (d / "author").mkdir(parents=True, exist_ok=True)

    # سلگ موحّد لكل كاتب حسب اسمه (يدمج معرّفات scholar المتعددة لنفس الشخص)
    by_name = defaultdict(list)
    for r in recs:
        nm = norm(r.get("author"))
        if nm:
            by_name[nm].append(r)
    name_slug = {nm: canonical_slug(nm, its) for nm, its in by_name.items()}

    authors = defaultdict(list)
    index = []
    for r in recs:
        aid, sec = r.get("id"), r["_sec"]
        nm = norm(r.get("author"))
        asl = name_slug.get(nm) if nm else None
        if asl:
            authors[asl].append(r)
        # صفحة المادة
        write_article(lang, r, asl)
        index.append({
            "t": r.get("title") or "(بلا عنوان)",
            "a": r.get("author") or "",
            "u": "a/%s-%d.html" % (sec, aid),
            "s": r["_body"][:PREVIEW_CHARS],
            "au": ("author/%s.html" % asl) if asl else "",
            "c": sec,
        })

    # صفحات الكتّاب
    for asl, items in authors.items():
        write_author(lang, asl, items)

    # ملف البحث
    (d / "search.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")

    # صفحة فهرس الكتّاب
    write_authors_index(lang, authors)

    # الصفحة الرئيسية للّغة
    write_lang_home(lang, recs, authors)
    return len(recs)


def write_authors_index(lang, authors):
    items = sorted(authors.items(), key=lambda kv: -len(kv[1]))
    cards = "".join(
        '<div class="card"><h3><a href="%s.html">%s</a></h3><p>%d %s</p></div>' % (
            asl, esc(its[0].get("author") or "—"), len(its), "مادة")
        for asl, its in items)
    body = '<h1>الكتّاب</h1><div class="count">%d كاتبًا</div>%s' % (len(items), cards)
    (OUT / lang / "author" / "index.html").write_text(
        page(lang, "الكتّاب", body, depth=2), encoding="utf-8")


def write_article(lang, r, asl):
    sec = r["_sec"]
    tags = real_tags(r)
    tagline = ""
    if tags:
        tagline = '<div class="tags">' + "".join(
            '<span class="tags"><a href="#">%s</a></span>' % esc(t) for t in tags) + '</div>'
    author_html = esc(r.get("author") or "—")
    if asl:
        author_html = '<a href="../author/%s.html">%s</a>' % (asl, author_html)
    src = r.get("source_url") or ""
    wb = r.get("wayback_ts") or ""
    arch = ""
    if src and wb:
        arch = ' · <a href="https://web.archive.org/web/%s/%s" rel="nofollow">%s</a>' % (
            esc(wb), esc(src), "النسخة المؤرشفة")
    body = (
        '<h1>%s</h1>' % esc(r.get("title") or "")
        + '<div class="meta">%s · %s%s</div>' % (
            author_html, esc(SECTIONS[sec]["ar"]),
            (" · " + esc(r["date"])) if r.get("date") else "")
        + tagline
        + '<div class="body">%s</div>' % esc(r["_body"])
        + '<div class="src">المصدر: <a href="%s" rel="nofollow">%s</a>%s</div>' % (
            esc(src), esc(src), arch)
    )
    (OUT / lang / "a" / ("%s-%d.html" % (sec, r.get("id")))).write_text(
        page(lang, r.get("title") or SITE_NAME, body, depth=2), encoding="utf-8")


def write_author(lang, asl, items):
    """صفحة كاتب أنيقة: ترويسة تعريف، تصنيفات قابلة للطيّ، وبحث — بروابط للنصوص."""
    name = items[0].get("author") or "—"
    info = BIO.get(norm(name), {"role": "كاتب", "bio": "", "initial": (name or "؟")[:1]})
    arts = []
    for r in items:
        arts.append({
            "id": r.get("id"), "sec": r["_sec"],
            "t": norm(r.get("title")) or "(بلا عنوان)",
            "g": group_of(r.get("categories") or []),
            "s": re.sub(r"\s+", " ", (r.get("_body") or ""))[:160],
        })
    by = defaultdict(list)
    for a in arts:
        by[a["g"]].append(a)
    order = list(GROUPS.keys()) + ["مقالات أخرى"]
    groups_html = ""
    ng = 0
    for g in order:
        its = by.get(g)
        if not its:
            continue
        ng += 1
        its.sort(key=lambda x: x["t"])
        lis = "".join(
            '<li><a href="../a/%s-%s.html" title="%s">%s</a></li>' % (
                i["sec"], i["id"], esc(i["s"]), esc(i["t"])) for i in its)
        groups_html += ('<details class="grp"%s><summary><span>%s</span>'
                        '<span class="n">%d مقالة</span></summary><ul>%s</ul></details>') % (
            ' open' if ng == 1 else '', esc(g), len(its), lis)
    data = json.dumps(
        [{"t": a["t"], "g": a["g"], "s": a["s"],
          "u": "../a/%s-%s.html" % (a["sec"], a["id"])} for a in arts],
        ensure_ascii=False)
    toggle = ("var r=document.documentElement;"
              "r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark')")
    doc = (
        '<!doctype html><html lang="%s" dir="rtl"><head>' % esc(lang)
        + '<meta charset="utf-8">'
        + '<meta name="viewport" content="width=device-width, initial-scale=1">'
        + '<meta name="robots" content="noindex, nofollow">'
        + '<title>%s — %s</title>' % (esc(name), esc(SITE_NAME))
        + AUTHOR_FONTS
        + '<style>' + AUTHOR_CSS + '</style></head><body>'
        + '<header><div class="wrap bar">'
        + '<a class="brand" href="../../index.html">نُسُغ<span>.</span></a>'
        + '<button class="tgl" onclick="' + toggle + '">ليل / نهار</button>'
        + '</div></header>'
        + '<section class="hero"><div class="wrap">'
        + '<div class="ava">%s</div>' % esc(info["initial"])
        + '<div class="hinfo"><h1>%s</h1>' % esc(name)
        + '<div class="role">%s</div>' % esc(info["role"])
        + ('<p class="biop">%s</p>' % esc(info["bio"]) if info["bio"] else "")
        + '<div class="stats"><div><b>%d</b><span>مقالة محفوظة</span></div>' % len(arts)
        + '<div><b>%d</b><span>سلسلة وموضوعًا</span></div></div>' % ng
        + '</div></div></section>'
        + '<main><div class="wrap">'
        + '<div class="back"><a href="index.html">← كل الكتّاب</a></div>'
        + '<input id="q" type="search" placeholder="%s">' % esc("ابحث في مقالات " + name + "…")
        + '<div class="cnt" id="cnt">%d مقالة في %d مجموعة</div>' % (len(arts), ng)
        + '<div id="results" hidden></div>'
        + '<div id="groups">' + groups_html + '</div>'
        + '</div></main>'
        + '<footer><div class="wrap">نُسُغ · كنز أبي الهيثم — النصوص محفوظة من طريق الإسلام، وتُعرض بإذن الكاتب، والحقوق له.</div></footer>'
        + '<script>var ALL=' + data + ';' + AUTHOR_JS + '</script>'
        + '</body></html>'
    )
    (OUT / lang / "author" / ("%s.html" % asl)).write_text(doc, encoding="utf-8")


def write_lang_home(lang, recs, authors):
    search_js = """
<script>
let DATA=[],SEC='';
fetch('search.json').then(r=>r.json()).then(d=>{DATA=d;run();});
function run(){
 const q=document.getElementById('q').value.trim().toLowerCase();
 let r=DATA;
 if(SEC)r=r.filter(x=>x.c===SEC);
 if(q)r=r.filter(x=>(x.t+' '+x.a+' '+x.s).toLowerCase().includes(q));
 document.getElementById('cnt').textContent=r.length+(q||SEC?' نتيجة':' مادة');
 document.getElementById('res').innerHTML=r.slice(0,200).map(x=>
  `<div class="card"><h3><a href="${x.u}">${x.t}</a></h3>`+
  (x.a?`<p>${x.au?`<a href="${x.au}">${x.a}</a>`:x.a}</p>`:'')+
  `<p>${x.s}…</p></div>`).join('');
}
function setSec(s,el){SEC=s;document.querySelectorAll('.filt a').forEach(a=>a.classList.remove('on'));el.classList.add('on');run();}
document.getElementById('q').addEventListener('input',run);
</script>
<style>.filt{margin:0 0 14px}.filt a{cursor:pointer;margin-inline-end:10px;padding:4px 12px;border:1px solid var(--line);border-radius:999px;font-size:.9rem}.filt a.on{background:var(--accent);color:#fff;border-color:var(--accent)}</style>"""
    filt = ('<div class="filt">'
            '<a class="on" onclick="setSec(\'\',this)">الكل</a>'
            '<a onclick="setSec(\'article\',this)">المقالات</a>'
            '<a onclick="setSec(\'fatwa\',this)">الفتاوى</a></div>')
    body = (
        '<input id="q" type="search" placeholder="ابحث في النصوص…" autofocus>'
        + filt
        + '<div class="count" id="cnt">%d مادة</div>' % len(recs)
        + '<div id="res"></div>'
        + '<p style="margin-top:2em"><a href="author/index.html">عرض كل الكتّاب (%d) ←</a></p>' % len(authors)
    )
    (OUT / lang / "index.html").write_text(
        page(lang, LANGS[lang][0], body, depth=1, extra_head=search_js), encoding="utf-8")


def write_root():
    cards = []
    for code, (nm, _) in LANGS.items():
        if (OUT / code / "index.html").exists():
            cards.append('<div class="card"><h3><a href="%s/index.html">%s</a></h3></div>' % (code, esc(nm)))
    body = '<h1>%s</h1><p class="meta">اختر اللغة</p>%s' % (esc(SITE_NAME), "".join(cards))
    (OUT / "index.html").write_text(page("ar", SITE_NAME, body, depth=0), encoding="utf-8")


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    total = 0
    for lang in LANGS:
        n = build_lang(lang)
        if n:
            print("%s: %d مادة" % (lang, n))
            total += n
    write_root()
    print("تم. المجموع %d مادة في %s" % (total, OUT))


if __name__ == "__main__":
    main()
