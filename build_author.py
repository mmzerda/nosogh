#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
مولّد صفحة كاتب في نُسُغ.

يستخرج كل مقالات كاتب من data/articles.jsonl، ينظّف تصنيفاتها الفوضوية
إلى مجموعات واضحة، ويبني صفحة ثابتة: تعريف + تصنيفات قابلة للطيّ (عناوين) + بحث.

التشغيل:  python3 build_author.py "أبو الهيثم محمد درويش"
المخرج:   author.html   (عايِنه ثم انقله حيث شئت)
يعمل على Python 3.6.
"""
import html
import json
import re
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / "data" / "articles.jsonl"
OUT = BASE / "author.html"

# تعريف الكاتب (يمكن تعديله أو توسيعه لكتّاب آخرين لاحقًا)
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
IGNORE_CATS = {"المقالات", ""}  # تصنيف الموقع العام، ليس حقيقيًا


def esc(s):
    return html.escape(s or "")


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def group_of(cats):
    """يعيد اسم المجموعة النظيفة لمقالة، بحسب تصنيفاتها الأصلية."""
    for cat in cats:
        c = norm(cat)
        if c in IGNORE_CATS:
            continue
        for gname, keys in GROUPS.items():
            if any(k in c for k in keys):
                return gname
    return "مقالات أخرى"


def load(author):
    arts = []
    for line in open(SRC, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if norm(r.get("author")) != norm(author):
            continue
        body = (r.get("body") or "").strip()
        arts.append({
            "id": r.get("id"),
            "t": norm(r.get("title")) or "(بلا عنوان)",
            "g": group_of(r.get("categories") or []),
            "s": re.sub(r"\s+", " ", body)[:160],
            "date": r.get("date") or "",
        })
    return arts


PAGE = """<meta charset="utf-8">
<title>{name} — نُسُغ</title>
<style>
:root{{--bg:#f6f3ec;--surface:#fffdf8;--ink:#241f17;--muted:#6f6656;--line:#e4ddcf;--accent:#7a5a2b;--accent-soft:#c9a86318;--gold:#9a7636;
  --disp:"Amiri",Georgia,serif;--body:"Noto Naskh Arabic","Amiri",Georgia,serif;--ui:"Cairo",system-ui,sans-serif;}}
@media(prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#17140e;--surface:#201c14;--ink:#ece5d7;--muted:#9c917c;--line:#2e2819;--accent:#c9a863;--accent-soft:#c9a86320;--gold:#d9b872;color-scheme:dark;}}}}
:root[data-theme="dark"]{{--bg:#17140e;--surface:#201c14;--ink:#ece5d7;--muted:#9c917c;--line:#2e2819;--accent:#c9a863;--accent-soft:#c9a86320;--gold:#d9b872;color-scheme:dark;}}
*{{box-sizing:border-box}}html{{direction:rtl}}
body{{margin:0;background:var(--bg);color:var(--ink);font-family:var(--body);line-height:1.9;direction:rtl;overflow-x:hidden}}
a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}
.wrap{{max-width:880px;margin:0 auto;padding-inline:20px}}
header{{border-bottom:1px solid var(--line);background:var(--surface)}}
.bar{{display:flex;align-items:center;justify-content:space-between;padding-block:14px;font-family:var(--ui)}}
.brand{{font-family:var(--disp);font-size:1.5rem;font-weight:700;color:var(--ink)}}.brand span{{color:var(--gold)}}
.tgl{{font-family:var(--ui);font-size:.8rem;cursor:pointer;background:transparent;color:var(--muted);border:1px solid var(--line);border-radius:8px;padding:5px 11px}}
.hero{{background:var(--surface);border-bottom:1px solid var(--line);padding-block:38px}}
.hero .wrap{{display:flex;gap:24px;align-items:flex-start;flex-wrap:wrap}}
.ava{{width:92px;height:92px;border-radius:50%;flex-shrink:0;background:linear-gradient(135deg,var(--accent),var(--gold));display:flex;align-items:center;justify-content:center;font-family:var(--disp);font-size:2.6rem;color:#fff}}
.hinfo{{flex:1;min-width:240px}}.hinfo h1{{font-family:var(--disp);font-size:2.1rem;margin:0 0 4px}}
.role{{font-family:var(--ui);font-size:.9rem;color:var(--gold);margin-bottom:10px}}
.biop{{font-size:1.06rem;color:var(--muted);max-width:62ch}}
.stats{{display:flex;gap:26px;margin-top:16px;font-family:var(--ui)}}
.stats b{{display:block;font-size:1.5rem;color:var(--accent);font-family:var(--disp);line-height:1}}
.stats span{{font-size:.76rem;color:var(--muted)}}
main{{padding-block:26px 70px}}
input#q{{width:100%;padding:12px 14px;font-size:1rem;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);font-family:var(--body);margin-bottom:18px}}
.grp{{border:1px solid var(--line);border-radius:12px;margin-bottom:12px;background:var(--surface);overflow:hidden}}
.grp>summary{{cursor:pointer;padding:14px 18px;font-family:var(--ui);font-weight:600;display:flex;justify-content:space-between;align-items:center;list-style:none}}
.grp>summary::-webkit-details-marker{{display:none}}
.grp>summary .n{{font-size:.8rem;color:var(--gold);font-weight:400}}
.grp[open]>summary{{border-bottom:1px solid var(--line)}}
.grp ul{{margin:0;padding:6px 0;list-style:none}}
.grp li{{padding:0}}.grp li a{{display:block;padding:9px 18px;font-size:1.05rem;color:var(--ink);border-bottom:1px solid transparent}}
.grp li a:hover{{background:var(--accent-soft);text-decoration:none;color:var(--accent)}}
#results{{margin-top:4px}}
.rcard{{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin-bottom:8px}}
.rcard a{{font-family:var(--disp);font-size:1.15rem}}.rcard .g{{font-family:var(--ui);font-size:.72rem;color:var(--gold)}}.rcard p{{margin:4px 0 0;font-size:.92rem;color:var(--muted)}}
.cnt{{font-family:var(--ui);font-size:.85rem;color:var(--muted);margin-bottom:12px}}
footer{{border-top:1px solid var(--line);color:var(--muted);font-size:.84rem;font-family:var(--ui);padding-block:20px}}
</style>
<header><div class="wrap bar">
 <div class="brand">نُسُغ<span>.</span></div>
 <button class="tgl" onclick="var r=document.documentElement;r.setAttribute('data-theme',r.getAttribute('data-theme')==='dark'?'light':'dark')">ليل / نهار</button>
</div></header>
<section class="hero"><div class="wrap">
 <div class="ava">{initial}</div>
 <div class="hinfo"><h1>{name}</h1><div class="role">{role}</div>
 <p class="biop">{bio}</p>
 <div class="stats"><div><b>{count}</b><span>مقالة محفوظة</span></div><div><b>{ngroups}</b><span>سلسلة وموضوعًا</span></div></div></div>
</div></section>
<main><div class="wrap">
 <input id="q" type="search" placeholder="ابحث في مقالات {name}…">
 <div class="cnt" id="cnt">{count} مقالة في {ngroups} مجموعة</div>
 <div id="results" hidden></div>
 <div id="groups">{groups}</div>
</div></main>
<footer><div class="wrap">نُسُغ · كنز أبي الهيثم — النصوص محفوظة من طريق الإسلام، وتُعرض بإذن الكاتب، والحقوق له.</div></footer>
<script>
var ALL={data};
var q=document.getElementById('q'),res=document.getElementById('results'),grp=document.getElementById('groups'),cnt=document.getElementById('cnt');
q.addEventListener('input',function(){{
  var v=q.value.trim();
  if(!v){{res.hidden=true;grp.hidden=false;cnt.textContent=ALL.length+' مقالة';return;}}
  grp.hidden=true;res.hidden=false;
  var r=ALL.filter(function(a){{return (a.t+' '+a.s).indexOf(v)>-1;}});
  cnt.textContent=r.length+' نتيجة';
  res.innerHTML=r.slice(0,300).map(function(a){{return '<div class="rcard"><div class="g">'+a.g+'</div><a href="#">'+a.t+'</a>'+(a.s?'<p>'+a.s+'…</p>':'')+'</div>';}}).join('');
}});
</script>
"""


def main():
    author = sys.argv[1] if len(sys.argv) > 1 else "أبو الهيثم محمد درويش"
    arts = load(author)
    if not arts:
        print("لا توجد مقالات لهذا الكاتب بهذا الاسم بالضبط.")
        return
    info = BIO.get(norm(author), {"role": "كاتب", "bio": "", "initial": author[:1]})
    # تجميع
    by = defaultdict(list)
    for a in arts:
        by[a["g"]].append(a)
    order = list(GROUPS.keys()) + ["مقالات أخرى"]
    groups_html = ""
    ng = 0
    for g in order:
        items = by.get(g)
        if not items:
            continue
        ng += 1
        items.sort(key=lambda x: x["t"])
        lis = "".join('<li><a href="#" title="%s">%s</a></li>' % (esc(i["s"]), esc(i["t"])) for i in items)
        groups_html += ('<details class="grp"%s><summary><span>%s</span>'
                        '<span class="n">%d مقالة</span></summary><ul>%s</ul></details>') % (
            ' open' if ng == 1 else '', esc(g), len(items), lis)
    data = json.dumps([{"t": a["t"], "g": a["g"], "s": a["s"]} for a in arts], ensure_ascii=False)
    OUT.write_text(PAGE.format(
        name=esc(author), role=esc(info["role"]), bio=esc(info["bio"]),
        initial=esc(info["initial"]), count=len(arts), ngroups=ng,
        groups=groups_html, data=data), encoding="utf-8")
    print("تم: %d مقالة في %d مجموعة → %s" % (len(arts), ng, OUT))


if __name__ == "__main__":
    main()
