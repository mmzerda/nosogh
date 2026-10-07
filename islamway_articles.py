#!/usr/bin/env python3
"""
استرجاع قسم المقالات في طريق الإسلام (ar.islamway.net/article/...) من أرشيف الإنترنت.

المراحل (كلها قابلة للاستئناف، وتُشغَّل بالترتيب):
  discover  : حصر روابط المقالات من فهرس Wayback (CDX) وتسجيل أحدث نسخة سليمة لكل مقالة
  sitemaps  : قراءة فهارس الموقع الرسمية المحفوظة وإضافة ما لم يلتقطه discover
  fetch     : جلب الصفحات الخام وحفظها مضغوطة (raw/) — المرحلة الطويلة
  extract   : استخراج العنوان والكاتب والتاريخ والتصنيف والنص إلى articles.jsonl
  sample    : عرض عينة من المستخرج للتحقق بالعين
  stats     : إحصاء التقدم والتغطية

الصفحات الخام تُحفظ كما هي، فإن احتاج الاستخراج تحسينًا أعيد تشغيل extract وحده دون إعادة الجلب.
"""
import argparse
import gzip
import json
import random
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:  # الاستخراج يعمل بدونه لكن بجودة أقل
    trafilatura = None

CDX = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW = "https://web.archive.org/web/{ts}id_/{url}"
HOST, SECTION = "ar", "article"
SITEMAP_KEY = {"article": "article", "fatwa": "fatw"}  # fatawa.1.xml أو fatwa/1.xml
ARTICLE_RE = None
# الكلمات المفتاحية العامة للموقع كله، ليست وسومًا للمقالة
SITE_KEYWORDS = {"إسلام، سنة، قرآن، دروس، خطب، محاضرات، فتاوى، أناشيد، كتب، فلاشات"}
UA = "IslamwayArticlesRescue/1.0 (volunteer archival; contact: gaininsight.org)"

BASE = DB = RAW = OUT = None


def configure(host, section):
    """يضبط اللغة والقسم. المقالات العربية تبقى في data/ كما كانت؛ وغيرها في data-<لغة>-<قسم>/."""
    global HOST, SECTION, ARTICLE_RE, BASE, DB, RAW, OUT
    HOST, SECTION = host, section
    ARTICLE_RE = re.compile(r"^https?://(?:www\.)?%s\.islamway\.net(?::80|:443)?/%s/(\d+)(?:/[^?#]*)?$"
                            % (re.escape(host), re.escape(section)), re.I)
    root = Path(__file__).resolve().parent
    BASE = root / ("data" if (host, section) == ("ar", "article") else "data-%s-%s" % (host, section))
    DB, RAW, OUT = BASE / "state.sqlite3", BASE / "raw", BASE / ("%ss.jsonl" % section)


configure("ar", "article")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db():
    BASE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS articles(
        id INTEGER PRIMARY KEY, original TEXT, ts TEXT,
        status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
        http INTEGER, raw_path TEXT, fetched_at TEXT, error TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    return con


def session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    return s


# ---------------------------------------------------------------- discover
def discover(args):
    con, s = db(), session()
    resume = con.execute("SELECT v FROM meta WHERE k='cdx_resume'").fetchone()
    resume = resume[0] if resume else None
    if resume == "DONE" and not args.restart:
        print("الحصر مكتمل سابقًا. استعمل --restart لإعادته.")
        return
    if args.restart:
        resume = None
    page = 0
    while True:
        params = {
            "url": "%s.islamway.net/%s/" % (HOST, SECTION), "matchType": "prefix",
            "output": "json", "fl": "original,timestamp,statuscode,mimetype",
            "filter": ["statuscode:200", "mimetype:text/html"],
            "limit": args.page_size, "showResumeKey": "true",
        }
        if resume:
            params["resumeKey"] = resume
        rows = get_json(s, CDX, params)
        if not rows:
            break
        resume = None
        # آخر سطرين: سطر فارغ ثم مفتاح الاستئناف
        if len(rows) >= 2 and rows[-2] == [] and len(rows[-1]) == 1:
            resume = rows[-1][0]
            rows = rows[:-2]
        added = 0
        for r in rows[1:] if rows and rows[0] and rows[0][0] == "original" else rows:
            if len(r) < 2:
                continue
            m = ARTICLE_RE.match(r[0])
            if not m:
                continue
            aid, ts = int(m.group(1)), r[1]
            cur = con.execute("SELECT ts FROM articles WHERE id=?", (aid,)).fetchone()
            if cur is None:
                con.execute("INSERT INTO articles(id, original, ts) VALUES(?,?,?)", (aid, r[0], ts))
                added += 1
            elif ts > cur[0]:
                # نسخة أحدث: نحدّثها فقط إن لم تُجلب بعد
                con.execute("UPDATE articles SET original=?, ts=? WHERE id=? AND status!='done'",
                            (r[0], ts, aid))
        con.execute("INSERT OR REPLACE INTO meta VALUES('cdx_resume', ?)", (resume or "DONE",))
        con.commit()
        page += 1
        total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        print(f"[{now()}] صفحة {page}: +{added} مقالة جديدة، المجموع {total}", flush=True)
        if not resume:
            break
        time.sleep(args.delay)
    print("انتهى الحصر.")


def get_json(s, url, params, tries=8):
    for i in range(tries):
        try:
            r = s.get(url, params=params, timeout=120)
            if r.status_code == 200:
                return r.json() if r.text.strip() else []
            wait = 60 * (i + 1) if r.status_code in (429, 503) else 10 * (i + 1)
            print(f"CDX {r.status_code}، انتظار {wait}ث", flush=True)
        except (requests.RequestException, ValueError) as e:
            wait = 15 * (i + 1)
            print(f"CDX خطأ: {e}، انتظار {wait}ث", flush=True)
        time.sleep(wait)
    raise SystemExit("تعذّر الوصول إلى فهرس CDX بعد عدة محاولات. أعد التشغيل لاحقًا، وسيستأنف.")


# ---------------------------------------------------------------- sitemaps
LOC_RE = re.compile(rb"<loc>\s*(.*?)\s*</loc>", re.S | re.I)


def get_raw(s, url, tries=4):
    """يعيد (البيانات، سبب الفشل)."""
    why = None
    for i in range(tries):
        try:
            r = s.get(url, timeout=120)
            if r.status_code == 200:
                data = r.content
                if data[:2] == b"\x1f\x8b":  # sitemap مضغوط .gz
                    data = gzip.decompress(data)
                return data, None
            why = f"HTTP {r.status_code}"
            if r.status_code == 404:
                return None, why
            wait = 60 * (i + 1) if r.status_code in (429, 503) else 10 * (i + 1)
        except (requests.RequestException, OSError) as e:
            why, wait = str(e)[:120], 15 * (i + 1)
        time.sleep(wait)
    return None, why


def norm_key(url):
    return re.sub(r"^https?://([^/:]+)(:80|:443)?", r"\1", url).rstrip("/").lower()


def sitemaps(args):
    """يقرأ فهارس المقالات الرسمية المحفوظة في الأرشيف، ويضيف كل مقالة فيها لم يلتقطها discover."""
    import html as htmlmod
    con, s = db(), session()
    con.execute("CREATE TABLE IF NOT EXISTS sitemap_ids(id INTEGER PRIMARY KEY)")
    rows = get_json(s, CDX, {
        "url": "%s.islamway.net/sitemap" % HOST, "matchType": "prefix", "output": "json",
        "fl": "original,timestamp", "filter": ["statuscode:200"]})
    latest = {}  # أحدث نسخة لكل فهرس
    for r in (rows[1:] if rows else []):
        k = norm_key(r[0])
        if k not in latest or r[1] > latest[k][1]:
            latest[k] = (r[0], r[1])
    # فهارس المقالات أولًا، ثم الفهرس الرئيسي ليدلّنا على ما لم يُحفظ مباشرة
    sk = SITEMAP_KEY.get(SECTION, SECTION)
    keys = sorted(latest, key=lambda k: (sk not in k, k))
    queue = [latest[k] for k in keys if sk in k or "index" in k]
    print(f"فهارس محفوظة في الأرشيف: {len(latest)}، منها للمقالات والفهارس الرئيسية: {len(queue)}", flush=True)
    done, nfiles = set(), 0
    while queue:
        url, ts = queue.pop(0)
        key = norm_key(url)
        if key in done:
            continue
        data, why = get_raw(s, WAYBACK_RAW.format(ts=ts, url=url))
        time.sleep(args.delay)
        if not data:
            print(f"  تعذّر ({why}): {url}", flush=True)
            continue
        done.add(key)
        nfiles += 1
        locs = [htmlmod.unescape(m.decode("utf-8", "replace")) for m in LOC_RE.findall(data)]
        if b"<sitemapindex" in data[:3000]:
            extra = [(loc, "2026") for loc in locs
                     if sk in loc and norm_key(loc) not in done and norm_key(loc) not in latest]
            queue.extend(extra)
            print(f"  فهرس رئيسي {url}: {len(locs)} فهرسًا فرعيًا، يُضاف {len(extra)} للبحث", flush=True)
            continue
        found = new = 0
        for loc in locs:
            m = ARTICLE_RE.match(loc)
            if not m:
                continue
            aid = int(m.group(1))
            found += 1
            con.execute("INSERT OR IGNORE INTO sitemap_ids VALUES(?)", (aid,))
            cur = con.execute("INSERT OR IGNORE INTO articles(id, original, ts) VALUES(?,?,?)",
                              (aid, loc, "2026"))  # "2026": الأرشيف يختار أقرب نسخة متاحة
            new += cur.rowcount
        con.commit()
        if found:
            print(f"  {url}: {found} مقالة، منها {new} جديدة", flush=True)
    total = con.execute("SELECT COUNT(*) FROM sitemap_ids").fetchone()[0]
    have = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    print(f"قُرئ {nfiles} ملف فهرس. المقالات في الفهارس الرسمية: {total}. المجموع للجلب الآن: {have}")


# ---------------------------------------------------------------- fetch
class Throttle:
    """يضبط سرعة الطلبات ويبطئها تلقائيًا عند الحظر المؤقت (429)."""
    def __init__(self, delay):
        self.delay, self.base = delay, delay
        self.next_at = 0.0

    def wait(self):
        t = time.monotonic()
        if t < self.next_at:
            time.sleep(self.next_at - t)
        self.next_at = max(time.monotonic(), self.next_at) + self.delay

    def penalize(self):
        self.delay = min(self.delay * 2, 30)
        self.next_at = time.monotonic() + 120
        print(f"حظر مؤقت من الأرشيف: توقف دقيقتين، والفاصل صار {self.delay:.1f}ث", flush=True)

    def relax(self):
        self.delay = max(self.base, self.delay * 0.98)


def raw_path(aid):
    return RAW / str(aid // 1000) / f"{aid}.html.gz"


def fetch_one(s, thr, row):
    aid, original, ts = row
    url = WAYBACK_RAW.format(ts=ts, url=original)
    thr.wait()
    try:
        r = s.get(url, timeout=90, allow_redirects=True)
    except requests.RequestException as e:
        return aid, "error", None, str(e)[:300]
    if r.status_code == 429:
        thr.penalize()
        return aid, "retry", 429, "rate limited"
    if r.status_code != 200:
        return aid, "error", r.status_code, f"HTTP {r.status_code}"
    html = r.content
    if len(html) < 2000 or b"<html" not in html[:5000].lower():
        return aid, "error", 200, f"short/non-html ({len(html)} bytes)"
    p = raw_path(aid)
    p.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(p, "wb") as f:
        f.write(html)
    thr.relax()
    return aid, "done", 200, None


def fetch(args):
    con, s = db(), session()
    thr = Throttle(args.delay)
    q = ("SELECT id, original, ts FROM articles WHERE status IN ('pending','retry')"
         " OR (status='error' AND attempts < ?) ORDER BY id")
    rows = con.execute(q, (args.max_attempts,)).fetchall()
    if args.limit:
        rows = rows[: args.limit]
    total = len(rows)
    print(f"[{now()}] للجلب: {total} مقالة، بـ {args.workers} عامل وفاصل {args.delay}ث", flush=True)
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for aid, status, http, err in ex.map(lambda r: fetch_one(s, thr, r), rows):
            con.execute(
                "UPDATE articles SET status=?, http=?, error=?, attempts=attempts+1,"
                " raw_path=CASE WHEN ?='done' THEN ? ELSE raw_path END, fetched_at=? WHERE id=?",
                (status, http, err, status, str(raw_path(aid)), now(), aid))
            done += 1
            if done % 50 == 0 or done == total:
                con.commit()
                rate = done / max(time.time() - t0, 1)
                eta = (total - done) / rate / 3600 if rate else 0
                print(f"[{now()}] {done}/{total}  ({rate*60:.0f}/دقيقة، الباقي ~{eta:.1f} ساعة)", flush=True)
    con.commit()
    print("انتهت جولة الجلب. أعد تشغيل fetch لإعادة محاولة الفاشل.")


# ---------------------------------------------------------------- extract
def clean(t):
    return re.sub(r"[ \t\u00a0]+", " ", re.sub(r"\n{3,}", "\n\n", t or "")).strip()


def jsonld_items(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            it = stack.pop()
            if isinstance(it, dict):
                yield it
                if "@graph" in it:
                    stack.extend(it["@graph"] if isinstance(it["@graph"], list) else [it["@graph"]])


def meta(soup, *names):
    for n in names:
        t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
        if t and t.get("content"):
            return t["content"].strip()
    return None


def extract_article(aid, html, source_url=None, ts=None):
    soup = BeautifulSoup(html, "lxml")
    rec = {"id": aid, "source_url": source_url, "wayback_ts": ts,
           "title": None, "author": None, "author_url": None, "date": None,
           "categories": [], "tags": [], "description": None, "body": None,
           "publisher": None, "publisher_url": None, "from_islamhouse": False}

    for it in jsonld_items(soup):
        typ = it.get("@type")
        typ = typ if isinstance(typ, list) else [typ]
        if any(t in ("Article", "NewsArticle", "BlogPosting") for t in typ):
            rec["title"] = rec["title"] or it.get("headline")
            a = it.get("author")
            a = a[0] if isinstance(a, list) and a else a
            if isinstance(a, dict):
                rec["author"] = rec["author"] or a.get("name")
                rec["author_url"] = rec["author_url"] or a.get("url")
            elif isinstance(a, str):
                rec["author"] = rec["author"] or a
            rec["date"] = rec["date"] or it.get("datePublished")
            rec["body"] = rec["body"] or it.get("articleBody")
        if "BreadcrumbList" in typ:
            for el in it.get("itemListElement", []):
                if not isinstance(el, dict):
                    continue
                item = el.get("item")
                name = el.get("name") or (item.get("name") if isinstance(item, dict) else None)
                if name:
                    rec["categories"].append(name)

    rec["title"] = rec["title"] or meta(soup, "og:title", "twitter:title") or (
        soup.title.get_text(strip=True) if soup.title else None)
    if rec["title"]:
        rec["title"] = re.sub(r"\s*[-|–]\s*(طريق الإسلام|islam\s?way[^-|–]*)\s*$", "", rec["title"], flags=re.I).strip()
    rec["date"] = rec["date"] or meta(soup, "article:published_time", "date", "pubdate")
    rec["description"] = meta(soup, "og:description", "description")
    rec["author"] = rec["author"] or meta(soup, "article:author", "author")

    # صفحات الكتّاب في الموقع: /scholar/<id>/...
    if not rec["author_url"]:
        a = soup.find("a", href=re.compile(r"/scholar/\d+"))
        if a:
            rec["author_url"] = a["href"]
            rec["author"] = rec["author"] or a.get_text(strip=True) or None

    # المصدر أو الناشر الأصلي (صفحات /source/ في الموقع)، وهل نُقلت المادة من إسلام هاوس
    a = soup.find("a", href=re.compile(r"/source/\d+"))
    if a:
        rec["publisher"] = a.get_text(strip=True) or None
        rec["publisher_url"] = a["href"]
    low = html.lower() if isinstance(html, bytes) else html.encode("utf-8", "ignore").lower()
    rec["from_islamhouse"] = (b"islamhouse" in low) or ("إسلام هاوس" in (rec.get("publisher") or ""))

    for a in soup.find_all("a", href=re.compile(r"/tag/|/tags/")):
        t = a.get_text(strip=True)
        if t and t not in rec["tags"]:
            rec["tags"].append(t)
    if not rec["categories"]:
        bc = soup.select("[class*=breadcrumb] a")
        rec["categories"] = [x.get_text(strip=True) for x in bc if x.get_text(strip=True)][1:]
    kw = meta(soup, "keywords")
    if kw and not rec["tags"] and kw.strip() not in SITE_KEYWORDS:
        rec["tags"] = [k.strip() for k in re.split(r"[,،]", kw) if k.strip()]

    if not rec["body"] and trafilatura is not None:
        try:
            rec["body"] = trafilatura.extract(html, include_comments=False, include_tables=True,
                                              favor_recall=True)
        except TypeError:  # إصدارات قديمة من trafilatura على Python 3.6
            rec["body"] = trafilatura.extract(html, include_comments=False, include_tables=True)
    if not rec["body"]:
        rec["body"] = fallback_body(soup)
    # القالب القديم يضيف " - اسم الكاتب" إلى العنوان
    if rec["title"] and rec["author"] and rec["title"].endswith(" - " + rec["author"]):
        rec["title"] = rec["title"][: -len(" - " + rec["author"])].strip()
    rec["body"] = clean(rec["body"])
    # حذف تكرار العنوان وسطر "منذ التاريخ" من رأس النص، مع أخذ التاريخ منه إن غاب
    lines = rec["body"].split("\n")
    while lines and (lines[0].strip() == (rec["title"] or "") or re.match(r"^منذ \d{4}-\d{2}-\d{2}$", lines[0].strip())):
        m = re.match(r"^منذ (\d{4}-\d{2}-\d{2})$", lines[0].strip())
        if m and not rec["date"]:
            rec["date"] = m.group(1)
        lines.pop(0)
    rec["body"] = "\n".join(lines).strip()
    rec["categories"] = list(dict.fromkeys(rec["categories"]))
    return rec


def fallback_body(soup):
    for t in soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        t.decompose()
    best, score = None, 0
    for el in soup.find_all(["article", "div", "section"]):
        text = el.get_text("\n", strip=True)
        links = sum(len(a.get_text(strip=True)) for a in el.find_all("a"))
        s = len(text) - 2 * links
        if s > score:
            best, score = el, s
    return best.get_text("\n", strip=True) if best else None


def extract(args):
    con = db()
    rows = con.execute("SELECT id, original, ts, raw_path FROM articles WHERE status='done' ORDER BY id").fetchall()
    n = ok = 0
    tmp = OUT.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as out:
        for aid, original, ts, rp in rows:
            try:
                with gzip.open(rp, "rb") as f:
                    html = f.read()
                rec = extract_article(aid, html, original, ts)
            except Exception as e:  # نسجل ونكمل
                print(f"تعذر استخراج {aid}: {e}", file=sys.stderr)
                continue
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            ok += bool(rec["body"] and len(rec["body"]) > 200)
            if n % 2000 == 0:
                print(f"[{now()}] استُخرج {n}", flush=True)
    tmp.replace(OUT)
    print(f"تم: {n} مقالة في {OUT}، منها {ok} بنص يتجاوز 200 حرف.")


def sample(args):
    if not OUT.exists():
        raise SystemExit("لا يوجد ملف مستخرج بعد. شغّل extract أولًا.")
    lines = OUT.read_text(encoding="utf-8").splitlines()
    for line in random.sample(lines, min(args.n, len(lines))):
        r = json.loads(line)
        print("=" * 70)
        print(f"#{r['id']}  {r['title']}")
        print(f"الكاتب: {r['author']}   التاريخ: {r['date']}   التصنيف: {' > '.join(r['categories'])}")
        print(f"الوسوم: {', '.join(r['tags'][:8])}")
        print(f"المصدر: {r['source_url']}")
        print("-" * 70)
        print((r["body"] or "")[: args.chars])


def stats(args):
    con = db()
    print("حالة الجلب:")
    for st, c in con.execute("SELECT status, COUNT(*) FROM articles GROUP BY status"):
        print(f"  {st:8} {c}")
    total = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    r = con.execute("SELECT v FROM meta WHERE k='cdx_resume'").fetchone()
    print(f"المجموع المحصور: {total}   الحصر: {'مكتمل' if r and r[0]=='DONE' else 'غير مكتمل'}")
    for err, c in con.execute("SELECT error, COUNT(*) FROM articles WHERE status='error'"
                              " GROUP BY error ORDER BY 2 DESC LIMIT 5"):
        print(f"  خطأ: {err}  ×{c}")
    try:
        sm = con.execute("SELECT COUNT(*), SUM(a.status='done') FROM sitemap_ids s"
                         " LEFT JOIN articles a ON a.id=s.id").fetchone()
        print(f"في الفهارس الرسمية: {sm[0]}  جُلب منها: {sm[1] or 0}")
    except sqlite3.OperationalError:
        pass
    if OUT.exists():
        n = withbody = withauthor = ih = 0
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                n += 1
                withbody += bool(r["body"] and len(r["body"]) > 200)
                withauthor += bool(r["author"])
                ih += bool(r.get("from_islamhouse"))
        print(f"المستخرج: {n}  بنص كامل: {withbody}  بكاتب معروف: {withauthor}")
        print(f"منقولة من إسلام هاوس (تقديرًا): {ih}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lang", default="ar", help="واجهة الموقع: ar, fr, en, de, es ...")
    ap.add_argument("--section", default="article", help="القسم: article أو fatwa")
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True  # متوافق مع Python 3.6
    d = sub.add_parser("discover"); d.set_defaults(fn=discover)
    d.add_argument("--page-size", type=int, default=5000)
    d.add_argument("--delay", type=float, default=3.0)
    d.add_argument("--restart", action="store_true")
    sm = sub.add_parser("sitemaps"); sm.set_defaults(fn=sitemaps)
    sm.add_argument("--delay", type=float, default=2.0)
    f = sub.add_parser("fetch"); f.set_defaults(fn=fetch)
    f.add_argument("--workers", type=int, default=2)
    f.add_argument("--delay", type=float, default=1.5, help="ثوانٍ بين الطلبات (مجموع العمال)")
    f.add_argument("--limit", type=int, default=0, help="للتجربة: اجلب هذا العدد فقط")
    f.add_argument("--max-attempts", type=int, default=4)
    e = sub.add_parser("extract"); e.set_defaults(fn=extract)
    s = sub.add_parser("sample"); s.set_defaults(fn=sample)
    s.add_argument("-n", type=int, default=3)
    s.add_argument("--chars", type=int, default=800)
    st = sub.add_parser("stats"); st.set_defaults(fn=stats)
    args = ap.parse_args()
    configure(args.lang, args.section)
    print("[%s.islamway.net/%s → %s]" % (HOST, SECTION, BASE.name), flush=True)
    args.fn(args)


if __name__ == "__main__":
    main()
