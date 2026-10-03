#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
いちごBBS 経済板 アーカイブ収集ツール
Wayback Machine から残骸をコツコツ集めて SQLite データベース化する。

標準ライブラリだけで動作（Python 3.8+ / SQLite FTS5 対応ビルド推奨）。

使い方:
  python ichigo_archiver.py enumerate      # Waybackの保存URL一覧を取得
  python ichigo_archiver.py fetch          # 未取得ページをダウンロード（中断・再開OK）
  python ichigo_archiver.py parse          # HTML を解析してレス単位に分解
  python ichigo_archiver.py stats          # 収集状況
  python ichigo_archiver.py search 日銀 量的緩和
  python ichigo_archiver.py threads        # スレ一覧
  python ichigo_archiver.py export-html out/   # スレごとの閲覧用HTML
  python ichigo_archiver.py export-csv posts.csv
  python ichigo_archiver.py add-url <URL>  # 見つけたURLを手動追加
  python ichigo_archiver.py show 0126 556  # アンカーを辿って会話の流れで表示
  python ichigo_archiver.py handles        # まとめログのコテハン一覧
  python ichigo_archiver.py export-urls urls.tsv  # DB の再現に必要な URL だけ書き出す
  python ichigo_archiver.py import-urls urls.tsv  # その一覧を登録（→ fetch → parse）

詳しくは README.md、HTML の形と解析ルールは docs/FORMATS.md。
"""
import argparse
import bisect
import collections
import csv
import gzip
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

DB_PATH = "ichigo.db"
UA = "ichigo-archiver/1.0 (personal research; polite crawler)"

# 探索対象（ホスト, 経済板っぽいURLだけに絞る正規表現）。見つかったら追加していく
# ichigobbs.net は全板が同居しているので economy に限定（15bbs で絞ると他板も全部入る）
# 移転の歴史（Wikipedia「いちごびびえす」）:
#   2000/06〜2001/11 www22.big.or.jp/~15ch/（いちごちゃんねる）
#   2001/12〜2002/06 ichigobbs.net → 2002/06〜2003/03 ichigobbs.com → 2003/03〜2009/10 ichigobbs.net
#   2009/11〜2014/12 ichigobbs.org。ichigobbs.ath.cx は経済板のまとめログ（ミラー）
#   rssnbl.tripod.co.jp は住人による過去ログ保存（2003年、スレ 23 本）
# "/" を含むものは共用サーバなのでドメイン全体ではなくそのパス以下だけ（prefix マッチ）
TARGETS = [
    ("ichigobbs.net", r"economy|keizai"),   # www. / matari. 等のサブドメインも domain マッチで拾う
    ("ichigobbs.com", r"economy|keizai"),
    ("ichigobbs.org", r"economy|keizai"),
    ("www22.big.or.jp/~15ch/", r"economy|keizai"),
    ("ichigobbs.ath.cx", r"index\.php"),
    # したらばのドメインでも同じ掲示板が見られた時期がある（2003〜、readres.cgi?bo=economy&vi=…）
    ("ichigo.shitaraba.com", r"economy|keizai"),
    # 住人（ろしあんぶる氏）の過去ログ保存。/0648.html = 経済板 0648 を IE で丸ごと保存したもの。
    # 10 桁の名前（/1044094615.html 等）は megabbs 経済板のスレなので対象外
    ("rssnbl.tripod.co.jp", r"/\d{4}(?:old)?\.html$"),
]
URL_FILTER = re.compile(r"economy|keizai|index\.php", re.I)  # --host で追加したホスト用
URL_EXCLUDE = re.compile(r"/tool/jc/jc\.cgi", re.I)  # Amazon 関連商品ウィジェット等のゴミ

CDX = "https://web.archive.org/cdx/search/cdx"
WAIT = 2.0  # リクエスト間隔（秒）。Archive.org に優しく


# ------------------------------------------------------------------ DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS captures(
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,          -- 元URL
  ts TEXT NOT NULL,           -- Wayback timestamp YYYYMMDDhhmmss
  status TEXT, mime TEXT, digest TEXT,
  length INTEGER,             -- CDX の保存サイズ（圧縮後）。転送ページは 400B 台
  thread_key TEXT,
  fetched INTEGER DEFAULT 0,  -- 0未取得 1取得済 -1失敗
  raw BLOB,                   -- gzip 圧縮した生HTML
  encoding TEXT,
  parsed INTEGER DEFAULT 0,
  UNIQUE(url, ts)
);
CREATE TABLE IF NOT EXISTS threads(
  thread_key TEXT PRIMARY KEY,
  title TEXT,
  first_seen TEXT, last_seen TEXT
);
CREATE TABLE IF NOT EXISTS posts(
  thread_key TEXT NOT NULL,
  no INTEGER NOT NULL,
  name TEXT, mail TEXT, date TEXT, uid TEXT,
  body TEXT,
  capture_id INTEGER,
  trip TEXT,                  -- いちご系は投稿時刻の右に背景色と同色で [ xxxx ] と表示
  spam INTEGER DEFAULT 0,     -- 1=宣伝スパム（is_spam 参照）。検索・HTML書き出しでは除外
  PRIMARY KEY(thread_key, no)
);
CREATE INDEX IF NOT EXISTS posts_capture ON posts(capture_id);
-- レスとして分解できなかったページ（スレ一覧・未知形式）は全文で保存
CREATE TABLE IF NOT EXISTS pages(
  capture_id INTEGER PRIMARY KEY,
  url TEXT, ts TEXT, title TEXT, text TEXT
);
-- まとめログ（ichigobbs.ath.cx）のコテハン。k=001 ドラエモン など
CREATE TABLE IF NOT EXISTS handles(
  k TEXT PRIMARY KEY,
  name TEXT,
  post_count INTEGER,         -- まとめログ上の投稿数（seen_ts 時点）
  seen_ts TEXT
);
-- どのレスがどのコテハンか（まとめログの判定のみ。名前一致では付けない）
-- 本文未取得のレスにも付けられるよう posts とは別表。(thread_key,no) で JOIN する
CREATE TABLE IF NOT EXISTS post_handles(
  thread_key TEXT NOT NULL,
  no INTEGER NOT NULL,
  k TEXT NOT NULL,
  source TEXT,                -- list=一覧項目 / url=スレ表示URLの k=&no=
  capture_id INTEGER,
  PRIMARY KEY(thread_key, no)
);
"""
FTS = """
CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
  body, name, thread_key UNINDEXED, no UNINDEXED, tokenize='trigram');
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
  text, title, capture_id UNINDEXED, tokenize='trigram');
"""


class Conn(sqlite3.Connection):
    fts = False


def db():
    # fetch 中に parse や検索を並行して動かしても落ちないよう、ロック解除を長めに待つ（既定 5 秒）
    con = sqlite3.connect(DB_PATH, factory=Conn, timeout=120)
    con.executescript(SCHEMA)
    if "trip" not in [r[1] for r in con.execute("PRAGMA table_info(posts)")]:
        con.execute("ALTER TABLE posts ADD COLUMN trip TEXT")  # 古い ichigo.db 用
    if "length" not in [r[1] for r in con.execute("PRAGMA table_info(captures)")]:
        con.execute("ALTER TABLE captures ADD COLUMN length INTEGER")
    if "spam" not in [r[1] for r in con.execute("PRAGMA table_info(posts)")]:
        con.execute("ALTER TABLE posts ADD COLUMN spam INTEGER DEFAULT 0")
    try:
        con.executescript(FTS)
        con.fts = True
    except sqlite3.OperationalError:
        con.fts = False  # trigram 非対応の古いSQLite → LIKE 検索にフォールバック
    return con


# ------------------------------------------------------------------ HTTP
def http_get(url, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            wait = 30 * (i + 1) if e.code in (429, 503) else 5 * (i + 1)
            print(f"  HTTP {e.code} → {wait}s 待って再試行", file=sys.stderr)
            time.sleep(wait)
        except Exception as e:
            print(f"  {e} → 再試行", file=sys.stderr)
            time.sleep(5 * (i + 1))
    raise RuntimeError("取得失敗: " + url)


# ------------------------------------------------------------------ URL正規化
def _num(x):
    return x.zfill(4) if x.isdigit() else x


def thread_key(url):
    """ホスト違い・ポート違い・www有無を吸収して、同じスレを同じキーにする。
    経済板は時期ごとに掲示板システムが違うが、スレ番号は共通（移行時に引き継がれた）
    なので、どの形のURLも economy/NNNN にまとめる:
      15bbs      /cgi/15bbs/economy/0126/                 → economy/0126
      Aska CGI   readres.cgi?bo=economy&vi=0126            → economy/0126
      ath.cx     index.php?k=006&c=000&thread=1162&no=508  → economy/1162
                 （いちごＢＢＳまとめログ。k=コテハン絞込, c=カテゴリなのでキーに含めない）
    """
    for _ in range(3):  # Wayback には &amp; / &amp;amp; のまま保存された URL もある
        url = html.unescape(url)
    p = urllib.parse.urlsplit(url if "://" in url else "http://" + url)
    path = p.path
    q = urllib.parse.parse_qs(p.query)
    if "readres.cgi" in path and "vi" in q:
        return f"{q.get('bo', ['economy'])[0]}/{_num(q['vi'][0])}"
    if "ichigobbs.ath.cx" in p.netloc and "thread" in q:
        return f"economy/{_num(q['thread'][0])}"
    m = re.fullmatch(r"/(\d{4})(?:old)?\.html", path)
    if "rssnbl.tripod.co.jp" in p.netloc and m:  # 住人の過去ログ保存（/0648.html, /0623old.html）
        return f"economy/{m.group(1)}"
    for k in ("key", "thread", "tid"):
        if k in q:
            bbs = q.get("bbs", [""])[0]
            return f"{bbs}/{q[k][0]}".strip("/")
    # /cgi/15bbs/economy/0004/ → economy/0004
    m = re.search(r"/([A-Za-z]+)/(\d{3,})(?:/|\.dat|\.html?|$)", path)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    return None  # スレではない（一覧ページなど）


# ------------------------------------------------------------------ enumerate
def cmd_enumerate(args):
    con = db()
    targets = TARGETS + [(h, "*") for h in (args.host or [])]  # "*" → URL_FILTER
    if args.only:  # 例 --only ichigobbs.org → そのホストだけ CDX を引き直す
        targets = [t for t in targets if any(o in t[0] for o in args.only)]
    total = 0
    for host, flt in targets:
        flt = re.compile(flt, re.I) if isinstance(flt, str) and flt != "*" else URL_FILTER
        params = {
            "url": host, "matchType": "prefix" if "/" in host else "domain", "output": "json",
            "fl": "original,timestamp,statuscode,mimetype,digest,length",
            "filter": "statuscode:200", "collapse": "digest",
        }
        url = CDX + "?" + urllib.parse.urlencode(params)
        print("CDX:", host)
        try:
            rows = json.loads(http_get(url) or b"[]")
        except Exception as e:
            print("  失敗:", e)
            continue
        n = 0
        for r in rows[1:]:
            orig, ts, st, mime, dg, ln = r
            ln = int(ln) if ln.isdigit() else None
            if flt.pattern and not flt.search(orig) or URL_EXCLUDE.search(orig):
                continue
            if mime and not mime.startswith("text"):
                continue
            con.execute(  # 既存行は length だけ埋める（取得状態は触らない）
                "INSERT INTO captures(url,ts,status,mime,digest,length,thread_key) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(url,ts) DO UPDATE SET length=excluded.length",
                (orig, ts, st, mime, dg, ln, thread_key(orig)))
            n += 1
        con.commit()
        print(f"  {len(rows)-1} 件中 {n} 件を登録候補に")
        total += n
        time.sleep(WAIT)
    print("完了。stats で確認してな")


def cmd_add_url(args):
    """https://web.archive.org/web/20070125053442/http://... 形式でも元URLでもOK"""
    con = db()
    for u in args.urls:
        m = re.match(r"https?://web\.archive\.org/web/(\d{14})\w*/(.+)", u)
        if m:
            ts, orig = m.group(1), m.group(2)
        else:
            ts, orig = "2", u  # 最新スナップショットを Wayback に選ばせる
        con.execute("INSERT OR IGNORE INTO captures(url,ts,thread_key) VALUES(?,?,?)",
                    (orig, ts, thread_key(orig)))
        print("追加:", orig, ts)
    con.commit()


# ------------------------------------------------------------------ fetch
# ホストごとの「掲示板として生きていた期間の終わり」。これ以降のスナップは駐車ページ・移転告知の可能性大
HOST_END = {"ichigobbs.net": "20091023", "ichigobbs.com": "20030401",
            "ichigobbs.org": "20141201", "big.or.jp": "20011201"}


SMALL_PAGE = 1000  # CDX length（圧縮後バイト）。転送ページは 400B 台、レスのあるページは 1000B 超
MISS_LIMIT = 2     # 載せうるはずの保存を読んでも出てこなかった回数がこれに達した欠けは、Wayback に無いとみなす


def fetch_priority(url, ts):
    """小さいほど先に取る。レスがまとまって取れるURLを優先"""
    host = urllib.parse.urlsplit(url).hostname or ""
    for h, end in HOST_END.items():
        if host.endswith(h) and ts[:8] > end:
            return 9
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(p.query)
    if "readres.cgi" in p.path:
        if "rr" in q:
            return 3                      # 1レスだけ
        if "rm" in q:
            return 2                      # 最新N件
        return 1 if ("rs" in q or "re" in q) else 0
    m = re.search(r"/economy/\d{3,}/(.*)$", p.path)
    if m:
        rest = m.group(1)
        if rest == "":
            return 0                      # スレ全体
        if re.fullmatch(r"\d+-\d*/?", rest):
            return 1                      # 範囲指定・N以降
        if re.fullmatch(r"[Ll]\d+/?", rest):
            return 2                      # 最新N件
        return 3                          # 1レス
    return 2                              # まとめログ・一覧ページ等


def url_post_range(url):
    """URL から、そのページに載っているレス番号の範囲 (lo, hi) を読む。hi=None は「lo 以降」。
    範囲が読めない（スレ全体・最新N件）ときは None"""
    for _ in range(3):
        url = html.unescape(url)
    p = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qs(p.query)
    num = lambda k: int(q[k][0]) if k in q and q[k][0].isdigit() else None
    if "readres.cgi" in p.path:
        if num("rr"):
            return num("rr"), num("rr")
        if num("rs") or num("re"):
            return num("rs") or 1, num("re")
        return None
    if "thread" in q and num("no"):          # まとめログ: 指定レスの前後（手前 10 件ほど）が載る
        return max(1, num("no") - 10), num("no")
    m = re.search(r"/economy/\d{3,}/(\d+)(?:-(\d*))?/?$", p.path)
    if m:
        lo = int(m.group(1))
        if m.group(2) is None:
            return lo, lo                    # 1レス
        return lo, int(m.group(2)) if m.group(2) else None
    return None


POST_TS = re.compile(r"(\d{4})/(\d\d)/(\d\d)\s*(?:\([^)]*\))?\s*(\d\d):(\d\d)")


def post_ts(date):
    """レスの日付 '2002/05/18(Sat) 18:03' → Wayback と比べられる '20020518180300'"""
    m = POST_TS.search(date or "")
    return "".join(m.groups()) + "00" if m else None


def latest_n(url):
    """最新 N 件のページなら N（/L50 → 50、rm=30 → 30）。それ以外は None"""
    p = urllib.parse.urlsplit(html.unescape(url))
    m = re.search(r"/[Ll](\d+)/?$", p.path) or re.search(r"(?:^|&)rm=(\d+)", p.query)
    return int(m.group(1)) if m else None


def fetch_order(con, rows):
    """取得順: 段0 レスがまだ無いスレ（各スレの最良の1本から）→ 段1 欠けを埋めうる保存を、
    少ない取得で多く埋まる順（貪欲法）→ 段2 持っているレスしか載っていない見込みの URL・一覧
    → 段3 転送・エラー・失効後。
    「埋めうるか」は保存日時とレスの投稿日時で判定する: 保存より後に書かれたレスは載らない、
    最新 N 件のページには保存時点の最後の N 件しか載らない"""
    shared = {d for (d,) in con.execute(
        "SELECT digest FROM captures WHERE digest IS NOT NULL "
        "GROUP BY digest HAVING count(DISTINCT url) >= 5")}
    info = {i: (d, ln) for i, d, ln in con.execute(
        "SELECT id,digest,length FROM captures WHERE fetched=0")}
    have = collections.defaultdict(dict)                    # スレ → {レス番号: 投稿日時}
    for tk, no, date in con.execute("SELECT thread_key,no,date FROM posts"):
        have[tk][no] = post_ts(date)
    # 1レスページ（rr= や /NNNN/80）には >>1 の抜粋も載るので、そこ由来の他のレスは本文が欠けている
    trunc = collections.defaultdict(set)
    for tk, no, url in con.execute("SELECT p.thread_key,p.no,c.url FROM posts p "
                                   "JOIN captures c ON c.id=p.capture_id"):
        r = url_post_range(url)
        if r and r[0] == r[1] and r[0] != no:
            trunc[tk].add(no)
    # スレの長さの見込み: 持っているレスの最大番号と、URL に出てくるレス番号の最大
    last = {tk: max(nos) for tk, nos in have.items()}
    for (url, tk) in con.execute("SELECT url,thread_key FROM captures WHERE thread_key IS NOT NULL"):
        r = url_post_range(url)
        if r and tk in last:
            last[tk] = max(last[tk], min(r[1] or r[0], 1100))
    # 欠け（と本文が切れたレス）ごとに、投稿日時の下限を前後の既知レスから推定する
    miss, by_ts = {}, {}
    for tk, nos in have.items():
        known = sorted((n, t) for n, t in nos.items() if t)
        kn = [n for n, _ in known]
        lst = []
        for x in range(1, last[tk] + 1):
            if x in nos and x not in trunc[tk]:
                continue
            if x in nos and nos[x]:
                lo = nos[x]
            else:
                i = bisect.bisect_left(kn, x)
                lo = known[i - 1][1] if i else "0"
            lst.append((x, lo))
        miss[tk] = lst
        by_ts[tk] = sorted((t, n) for n, t in nos.items() if t)

    def last_no_at(tk, ts):  # 保存日時 ts の時点での最後のレス番号（見積もり）
        seq = by_ts.get(tk, [])
        i = bisect.bisect_right(seq, (ts, 10 ** 6))
        return max((n for _, n in seq[:i]), default=0)

    def base(r):
        d, ln = info.get(r[0], (None, None))
        small = ln is not None and ln < SMALL_PAGE  # 15bbs 移行後の readres.cgi 転送ページ等
        return 8 if d in shared or small else fetch_priority(r[1], r[2])

    def covers(r):
        cid, url, ts, tk = r
        rg, n = url_post_range(url), latest_n(url)
        edge = last_no_at(tk, ts) - n if n else None
        out = set()
        for x, lo in miss.get(tk, ()):
            if lo > ts:
                continue                                # 保存より後に書かれたレス
            if rg is not None and not (rg[0] <= x <= (rg[1] or 10 ** 6)):
                continue
            if edge is not None and x <= edge:
                continue                                # 最新 N 件に入らない
            out.add((tk, x))
        return out

    # 外れの記録: 取得済みの保存（レスが取れたもの）が埋めるはずだった欠けが、まだ欠けているなら外れ 1 回。
    # MISS_LIMIT 回外れた欠けは Wayback に無いとみなして候補から外す（外さないと、同じ欠けを載せうる
    # 保存を 1 件ずつ全部試すまで段 1 がなくならない）
    misses = collections.Counter()
    for r in con.execute("SELECT id,url,ts,thread_key FROM captures WHERE fetched=1 AND parsed=1 "
                         "AND thread_key IS NOT NULL AND id NOT IN (SELECT capture_id FROM pages)"):
        if r[3] in miss:
            misses.update(covers(r))
    for tk in miss:
        miss[tk] = [(x, lo) for x, lo in miss[tk] if misses[(tk, x)] < MISS_LIMIT]

    rows = sorted(rows, key=base)  # 安定ソート: 同じ優先度の中は新しいスナップが先のまま
    rank, keyed, cand = {}, [], {}
    for r in rows:
        pr, tk = base(r), r[3] or r[1]
        n = rank[tk] = rank.get(tk, -1) + 1   # そのスレの何本目か（良い優先度から数える）
        if pr >= 8:
            keyed.append(((3, pr, n), r, 3))
        elif r[3] is None:  # スレ一覧・まとめログ一覧（タイトルとまとめログ掲載だけ）
            keyed.append(((2, pr, n), r, 2))
        elif tk not in have:
            keyed.append(((0, n, pr), r, 0))
        else:
            c = covers(r)
            if c:
                cand[r[0]] = (r, c, pr)
            else:
                keyed.append(((2, pr, n), r, 2))
    # 段1: まだ埋まっていない欠けを一番多く含む保存から順に選ぶ（同数なら良い優先度・新しい保存）
    left = set().union(*(c for _, c, _ in cand.values())) if cand else set()
    order = 0
    while cand:
        cid = max(cand, key=lambda k: (len(cand[k][1] & left), -cand[k][2], cand[k][0][2]))
        r, c, pr = cand.pop(cid)
        gain = c & left
        if not gain:  # 残りは、もう選んだ保存で埋まる見込みのものだけ
            keyed.append(((2, pr, 0), r, 2))
            for r2, _, pr2 in cand.values():
                keyed.append(((2, pr2, 0), r2, 2))
            break
        left -= gain
        keyed.append(((1, order), r, 1))
        order += 1
    keyed.sort(key=lambda x: x[0])
    return [(r, tier) for _, r, tier in keyed]


def cmd_fetch(args):
    con = db()
    q, prm = "SELECT id,url,ts,thread_key FROM captures WHERE fetched=0", []
    for m in args.match or []:  # URL の部分一致で絞る（例 --match 15bbs）
        q += " AND url LIKE ?"
        prm.append(f"%{m}%")
    rows = con.execute(q + " ORDER BY ts DESC", prm).fetchall()
    if args.oldest:
        ordered = [(r, None) for r in reversed(rows)]
    else:
        ordered = fetch_order(con, rows)
    if args.limit:
        ordered = ordered[: args.limit]
    if args.dry_run:
        tiers = collections.Counter(t for _, t in ordered)
        for (cid, url, ts, tk), tier in ordered[:50]:
            print(f"段{tier} 優先{fetch_priority(url, ts)} {ts} {url}")
        print(f"-- {len(ordered)} 件（先頭50件を表示）段ごとの件数:",
              dict(sorted(tiers.items(), key=lambda x: (x[0] is None, x[0]))))
        return
    rows = [r[:3] for r, _ in ordered]
    print(f"{len(rows)} 件を取得します（間隔 {WAIT}s）")
    for i, (cid, url, ts) in enumerate(rows, 1):
        wb = f"https://web.archive.org/web/{ts}id_/{url}"  # id_ = 改変なしの原本
        try:
            data = http_get(wb)
            con.execute("UPDATE captures SET raw=?, fetched=1, parsed=0 WHERE id=?",
                        (gzip.compress(data), cid))
            print(f"[{i}/{len(rows)}] OK {len(data):>7}B {url}")
        except Exception as e:
            con.execute("UPDATE captures SET fetched=-1 WHERE id=?", (cid,))
            print(f"[{i}/{len(rows)}] NG {url} ({e})")
        con.commit()
        time.sleep(WAIT)


# ------------------------------------------------------------------ parse
def decode(raw):
    head = raw[:3000].decode("ascii", "ignore")
    m = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    cands = []
    if m:
        c = m.group(1).lower()
        cands.append({"shift_jis": "cp932", "x-sjis": "cp932", "sjis": "cp932",
                      "euc-jp": "euc_jp", "x-euc-jp": "euc_jp"}.get(c, c))
    cands += ["cp932", "euc_jp", "utf-8"]
    best = None
    for c in cands:
        try:
            s = raw.decode(c, errors="replace")
        except LookupError:
            continue
        bad = s.count("�")
        if best is None or bad < best[2]:
            best = (s, c, bad)
        if bad == 0:
            break
    return best[0], best[1]


TAG = re.compile(r"<[^>]+>")


def clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = TAG.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", s)  # 壊れたログに混ざる NUL 等の制御文字
    return re.sub(r"[ \t　]+\n", "\n", s).strip()


def strip_wayback(s):
    # 念のため Wayback のツールバーを除去
    return re.sub(r"<!-- BEGIN WAYBACK TOOLBAR INSERT -->.*?<!-- END WAYBACK TOOLBAR INSERT -->",
                  "", s, flags=re.S)


def parse_title(s):
    # ath.cx は <H1> がサイト名なので、レス一覧直前の <B>スレタイ</B> を先に見る
    for pat in (r"<br><br><b>([^<]*)</b><br><br>\s*<dl>", r"<h1[^>]*>(.*?)</h1>", r"<font[^>]*color=[\"']?red[^>]*>(.*?)</font>",
                r"<title>(.*?)</title>"):
        m = re.search(pat, s, re.I | re.S)
        if m and clean(m.group(1)):
            return clean(m.group(1))[:200]
    return None


# 2ch/したらば系 HTML: <dt>1 ：<b>名前</b>：2004/01/01(木) 12:34 ID:xxx<dd>本文
# Aska系 HTML       : <dt>301:  <b>名前[:Lv.3]</b>　 2001/11/22(Thu) 16:42<dd>本文
#   （名前と日付の間に区切り記号がない。スレタイ行に閉じない <DT> があるので
#    ヘッダ部が別の <dt> をまたがないようにする）
DT = re.compile(r"<dt[^>]*>((?:(?!<dt).)*?)<dd[^>]*>(.*?)(?=<dt|</dl|$)", re.I | re.S)
DATE = re.compile(r"\d{2,4}/\d{1,2}/\d{1,2}\s*(?:\([^)]*\))?\s*\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?")


def parse_posts(s):
    posts = []
    for head, body in DT.findall(s):
        h = clean(head)
        # ath.cx は「327 名前：ドラエモン 投稿日：2008/10/27(Mon) 20:05」と番号の後に区切りがない
        m = re.match(r"\s*(\d+)\s*[：:]?\s*(.*)", h, re.S)
        if not m:
            continue
        no = int(m.group(1))
        rest = re.sub(r"^名前\s*[：:]\s*", "", m.group(2))
        rest = re.sub(r"\s*(?:投稿日|日付)\s*[：:]\s*(?=\d)", " ", rest)
        mail = None
        mm = re.search(r'href=["\']mailto:([^"\']*)', head, re.I)
        if mm:
            mail = html.unescape(mm.group(1))
        dm = DATE.search(rest)
        if dm:  # 日付を先に見つけて、その前を名前・後ろを ID 等とみなす（時刻の ":" で割らない）
            name = rest[: dm.start()].strip(" \t\n　：:")
            date, tail = dm.group(0), rest[dm.end():]
        elif re.search(r"<b>.*?</b>", head, re.I | re.S):
            # 削除・移動レス: 「<b>いちごJam削除</b>　 いちごJam削除　[ trip ]」「<b>引越しさん</b>　 移動されました」
            # 日付の位置に出ている文言をそのまま date に入れる
            mb = re.search(r"<b>(.*?)</b>(.*)", head, re.I | re.S)
            name = clean(mb.group(1)).strip()
            tail = clean(mb.group(2))
            date = re.sub(r"\[[^\]]*\]|ID:\s*\S+", "", tail).strip(" \t\n　：:")
        else:
            parts = re.split(r"\s*[：:]\s*", rest, maxsplit=1)
            name = parts[0].strip() if parts else ""
            date = tail = parts[1].strip() if len(parts) > 1 else ""
        uid = None
        mi = re.search(r"ID:\s*(\S+)", tail)  # 2ch系
        if mi:
            uid = mi.group(1)
            if not dm:
                date = date[: mi.start()].strip()
        # いちご系のトリップは投稿時刻の右に背景色と同色で「[ va4qsJNk0c ]」
        # （2009/05 から ID も同じ位置に出るようになったが、形式は未確認）
        mt = re.search(r"\[\s*([\w./+!#]{6,})\s*\]", tail)
        trip = mt.group(1) if mt else None
        sp = lambda x: re.sub(r"\s+", " ", x).strip() if x else x  # 名前・日付の中の改行や連続空白
        posts.append(dict(no=no, name=sp(name), mail=mail, date=sp(date), uid=uid, trip=trip,
                          body=clean(body)))
    if posts:
        return posts
    # dat 形式: 名前<>メール<>日付 ID<>本文<>タイトル
    lines = [l for l in s.splitlines() if l.count("<>") >= 3]
    for i, l in enumerate(lines, 1):
        f = l.split("<>")
        posts.append(dict(no=i, name=clean(f[0]), mail=f[1], date=clean(f[2]),
                          uid=None, trip=None, body=clean(f[3])))
    return posts


# まとめログ（ath.cx）の付加情報
#   コテハン一覧 : <A HREF="index.php?k=001&c=000">ドラエモン(15378)</A>、選択中は <B>cloudy(881)</B>
#   一覧項目     : <LI><A HREF="index.php?k=006&c=000&thread=1458&no=353#353">スレタイ No.353</A>
#                  [2009/05/20] <B>cloudy</B> 抜粋…
#   スレ表示URL  : k=006&thread=1162&no=508 → 1162 の >>508 が cloudy（他のレスは別人）
HANDLE_LINK = re.compile(r'index\.php\?k=(\d+)&(?:amp;)?c=\d+"?>([^<]*?)\((\d+)\)</a>', re.I)
HANDLE_CUR = re.compile(r"<b>([^<(]*?)\((\d+)\)</b>", re.I)
MATOME_ITEM = re.compile(
    r'<li><a href="index\.php\?k=(\d+)&(?:amp;)?c=\d+&(?:amp;)?thread=(\d+)&(?:amp;)?no=(\d+)[^"]*">'
    r"(.*?)</a>\s*\[[\d/]+\]\s*<b>([^<]*)</b>", re.I | re.S)


def parse_matome(s, url):
    """戻り値: (handles{k:(name,count)}, [(thread_key,no,k,source)], {thread_key:title})"""
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    cur_k = q.get("k", ["000"])[0]
    handles = {k: (clean(n).strip(), int(c)) for k, n, c in HANDLE_LINK.findall(s)}
    m = HANDLE_CUR.search(s)
    if m and cur_k not in handles:  # 選択中のコテハンだけリンクになっていない
        handles[cur_k] = (clean(m.group(1)).strip(), int(m.group(2)))
    # 000 は「全て」、999 は「その他」（999 のページでは太字の「全て(26108)」を拾ってしまう）
    handles = {k: v for k, v in handles.items() if k not in ("000", "999") and v[0] != "全て"}
    by_name = {n: k for k, (n, _) in handles.items()}
    owned, titles = [], {}
    for k, th, no, label, name in MATOME_ITEM.findall(s):
        tk = f"economy/{_num(th)}"
        t = re.sub(r"\s*No\.\d+\s*$", "", clean(label))
        if t:
            titles.setdefault(tk, t)
        if k == "000":  # 全体一覧では k が無いので、同じページのコテハン一覧で名前から引く
            k = by_name.get(clean(name).strip())
        if k:
            owned.append((tk, int(no), k, "list"))
    if cur_k != "000" and "thread" in q and "no" in q and q["no"][0].isdigit():
        owned.append((f"economy/{_num(q['thread'][0])}", int(q["no"][0]), cur_k, "url"))
    return handles, owned, titles


# 宣伝スパム（主に 2010年・2014年に大量に来ていた）
#   ・でたらめな英字のメールアドレス（tviscqmbldq@yahoo.co.jp 等）＋本文に URL
#   ・payday loan / cheap airfare / ブランド品「グッチ 財布|...|||http://」等の定型
# 経済板なので「loan」「ｗｗｗ＋URL」だけでは判定しない（普通の書き込みに多い）
SPAM_BODY = re.compile(r"payday|airfare|viagra|cialis|casino|\|\|\|\s*https?://", re.I)


def random_mail(m):
    if not m or "@" not in m:
        return False
    local = m.split("@")[0]
    return (re.fullmatch(r"[a-z]{5,14}", local) is not None
            and sum(c in "aeiou" for c in local) / len(local) <= 0.25)


# 2012年以降（板がほぼ放置された時期）のブランドコピー宣伝。それ以前は「腕時計」「discount」等が
# 普通の書き込み（ニュース・行動経済学の用語）に出てくるので、時期で限る
SPAM_BRAND = re.compile(r"ブランドコピー|スーパーコピー|コピー(?:長)?財布|michael[ _-]?kors|louis[ _-]?vuitton"
                        r"|ヴィトン|シャネル|\bchanel|\bgucci|グッチ|\bprada|プラダ|coach[ _-]?outlet"
                        r"|moncler|モンクレール|ロレックス|\brolex", re.I)
SPAM_MAIL = re.compile(r"@[\w.-]*outlet", re.I)  # cheappradaoutlet.us 等


def is_spam(p):
    body, mail, date = p["body"], p["mail"] or "", p.get("date") or ""
    return ((random_mail(mail) and "http" in body) or bool(SPAM_BODY.search(body))
            or bool(SPAM_MAIL.search(mail))
            or (date >= "2012" and "http" in body and bool(SPAM_BRAND.search(body))))


# ドメイン失効後の駐車ページ・プロバイダのエラーページ（掲示板の中身なし）
# いちご自身の「スレッド表示エラー」（過去ログ送り・削除後）と、Aska URL → 15bbs への転送ページも中身なし
JUNK = re.compile(r"top\.location=\"[^\"]*\?fp=|data-adblockkey=|Error\. Page cannot be displayed"
                  r"|<title>[^<]*スレッド表示エラー"
                  r"|http-equiv=\"?refresh\"?[^>]*url=/cgi/15bbs/", re.I)
REDIRECT_ONLY = re.compile(r"\s*/cgi/15bbs/[\w/]+\s*$")


def store_matome(con, s, url, ts, cid):
    handles, owned, titles = parse_matome(s, url)
    for k, (name, cnt) in handles.items():  # 新しいスナップの名前・投稿数を採用
        con.execute(
            "INSERT INTO handles VALUES(?,?,?,?) ON CONFLICT(k) DO UPDATE SET "
            " name=excluded.name, post_count=excluded.post_count, seen_ts=excluded.seen_ts "
            "WHERE excluded.seen_ts >= handles.seen_ts", (k, name, cnt, ts))
    con.executemany("INSERT OR REPLACE INTO post_handles VALUES(?,?,?,?,?)",
                    [o + (cid,) for o in owned])
    for tk, t in titles.items():  # 本文未取得のスレにもタイトルを付けておく
        con.execute(
            "INSERT INTO threads(thread_key,title,first_seen,last_seen) VALUES(?,?,?,?) "
            "ON CONFLICT(thread_key) DO UPDATE SET title=COALESCE(threads.title, excluded.title)",
            (tk, t, ts, ts))


def cmd_parse(args):
    con = db()
    q = "SELECT id,url,ts,raw,thread_key FROM captures WHERE fetched=1"
    if args.all:
        # 作り直し。残すと「本文が長い方を採用」で同じ長さの旧結果が勝ってしまい、修正が反映されない
        con.executescript("DELETE FROM posts; DELETE FROM threads; DELETE FROM pages;"
                          "DELETE FROM handles; DELETE FROM post_handles;")
    else:
        q += " AND parsed=0"
    q += " ORDER BY ts"  # first_seen/last_seen・タイトルを古い順に積む
    rows = con.execute(q).fetchall()
    np_ = ng = nj = 0
    for cid, url, ts, raw, tk in rows:
        s, enc = decode(gzip.decompress(raw))
        s = strip_wayback(s)
        if "<!-- saved from url=" in s[:2000]:
            # Internet Explorer で保存したページ（住人の過去ログ保存）は、長い行が空白の位置で折り返されて
            # 改行と字下げが入っている。HTML ではソースの改行は空白 1 つ（改行は <br> だけ）なので戻す
            s = re.sub(r"[ \t]*\r?\n[ \t]*", " ", s)
        if JUNK.search(s[:5000]) or REDIRECT_ONLY.fullmatch(s):
            con.execute("UPDATE captures SET parsed=-1, encoding=? WHERE id=?", (enc, cid))
            nj += 1
            continue
        title = parse_title(s)
        if "ichigobbs.ath.cx" in url:
            store_matome(con, s, url, ts, cid)
        posts = parse_posts(s) if tk else []
        if posts:
            con.execute(
                "INSERT INTO threads(thread_key,title,first_seen,last_seen) VALUES(?,?,?,?) "
                "ON CONFLICT(thread_key) DO UPDATE SET "
                " title=COALESCE(threads.title, excluded.title),"
                " first_seen=MIN(threads.first_seen, excluded.first_seen),"
                " last_seen=MAX(threads.last_seen, excluded.last_seen)",
                (tk, title, ts, ts))
            for p in posts:
                # 同じレスが複数スナップにあれば、本文が長い方（欠けてない方）を採用。
                # 同じ長さなら古いスナップを採用（後年の移転ログは名前が既定の名無しに
                # 置き換わっていることがある）。解析の順番によらず同じ結果になる
                old = con.execute("SELECT length(p.body),c.ts FROM posts p "
                                  "LEFT JOIN captures c ON c.id=p.capture_id "
                                  "WHERE p.thread_key=? AND p.no=?", (tk, p["no"])).fetchone()
                if old and (old[0] > len(p["body"])
                            or (old[0] == len(p["body"]) and (old[1] or "") <= ts)):
                    continue
                con.execute("INSERT OR REPLACE INTO posts(thread_key,no,name,mail,date,uid,trip,"
                            "body,capture_id,spam) VALUES(?,?,?,?,?,?,?,?,?,?)",
                            (tk, p["no"], p["name"], p["mail"], p["date"], p["uid"], p["trip"],
                             p["body"], cid, int(is_spam(p))))
            np_ += len(posts)
        else:
            con.execute("INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?)",
                        (cid, url, ts, title, clean(s)))
            ng += 1
        con.execute("UPDATE captures SET parsed=1, encoding=? WHERE id=?", (enc, cid))
    if con.fts:  # 全文検索インデックスを作り直す
        con.executescript("""
          DELETE FROM posts_fts; DELETE FROM pages_fts;
          INSERT INTO posts_fts(body,name,thread_key,no)
            SELECT body,name,thread_key,no FROM posts WHERE spam=0;
          INSERT INTO pages_fts(text,title,capture_id) SELECT text,title,capture_id FROM pages;""")
    con.commit()
    print(f"解析: {len(rows)} ページ → レス {np_} 件 / 全文保存ページ {ng} 件 / 中身なし {nj} 件")


# ------------------------------------------------------------------ 閲覧系
def cmd_stats(args):
    con = db()
    one = lambda q: con.execute(q).fetchone()[0]
    print("保存URL候補   :", one("SELECT count(*) FROM captures"))
    print("  取得済/失敗 :", one("SELECT count(*) FROM captures WHERE fetched=1"), "/",
          one("SELECT count(*) FROM captures WHERE fetched=-1"))
    print("  中身なし    :", one("SELECT count(*) FROM captures WHERE parsed=-1"),
          "（スレッド表示エラー・15bbsへの転送・ドメイン失効後の駐車ページ）")
    print("スレッド      :", one("SELECT count(*) FROM threads"))
    print("レス          :", one("SELECT count(*) FROM posts"),
          "（うちスパム", one("SELECT count(*) FROM posts WHERE spam=1"), "）")
    print("全文ページ    :", one("SELECT count(*) FROM pages"))
    print("コテハン      :", one("SELECT count(*) FROM handles"), "人 / まとめログ掲載レス",
          one("SELECT count(*) FROM post_handles"))
    r = con.execute("SELECT min(ts),max(ts) FROM captures").fetchone()
    print("スナップ期間  :", r)


def cmd_threads(args):
    con = db()
    for tk, t, n, a, b in con.execute(
            "SELECT t.thread_key,t.title,count(p.no),t.first_seen,t.last_seen FROM threads t "
            "LEFT JOIN posts p USING(thread_key) GROUP BY t.thread_key ORDER BY t.thread_key"):
        print(f"{tk:<20} {n:>5}レス  {a[:8]}〜{b[:8]}  {t or ''}")


def cmd_handles(args):
    con = db()
    print("k    名前                 投稿数(まとめログ)  まとめログ掲載  うち本文あり")
    for k, name, cnt, ts, n, nb in con.execute(
            "SELECT h.k,h.name,h.post_count,h.seen_ts,count(ph.no),count(p.no) FROM handles h "
            "LEFT JOIN post_handles ph USING(k) "
            "LEFT JOIN posts p ON p.thread_key=ph.thread_key AND p.no=ph.no "
            "GROUP BY h.k ORDER BY h.k"):
        print(f"{k}  {name:<18} {cnt:>8} ({ts[:8]})  {n:>7}  {nb:>7}")


# アンカー: >>554 ＞＞５５４ ≫554 >>315-316 >>1,3 >51（> 1個＋数字もほぼアンカー）
ANCHOR = re.compile(r"(?:>{1,2}|＞{1,2}|≫)\s?([0-9０-９]{1,4}(?:\s*[-－ー~〜]\s*[0-9０-９]{1,4}"
                    r"|(?:\s*[,，、]\s*[0-9０-９]{1,4})+)?)")
ZEN = str.maketrans("０１２３４５６７８９－，、〜", "0123456789-,,~")


def anchors(body, max_range=30):
    """本文中のアンカー先レス番号（重複なし・昇順）"""
    out = set()
    for m in ANCHOR.finditer(body or ""):
        t = re.sub(r"\s", "", m.group(1).translate(ZEN)).replace("ー", "-").replace("~", "-")
        for part in t.split(","):
            a, _, b = part.partition("-")
            if not a.isdigit():
                continue
            lo, hi = int(a), int(b) if b.isdigit() else int(a)
            if 0 < lo <= hi and hi - lo <= max_range:
                out.update(range(lo, hi + 1))
            elif lo > 0:
                out.add(lo)  # >>100-900 のような広すぎる範囲は先頭だけ
    return sorted(out)


def resolve_thread(con, t):
    """0126 / 126 / economy/0126 のどれでも thread_key に"""
    if "/" in t:
        return t
    return f"economy/{t.zfill(4)}" if t.isdigit() else t


def cmd_show(args):
    """レスを会話の流れで表示: アンカー先を遡ったもの → 対象 → 対象への返信"""
    con = db()
    tk = resolve_thread(con, args.thread)
    title = (con.execute("SELECT title FROM threads WHERE thread_key=?", (tk,)).fetchone() or [None])[0]
    posts = {r[0]: r for r in con.execute(
        "SELECT no,name,mail,date,uid,trip,body,spam FROM posts WHERE thread_key=?", (tk,))}
    if args.no not in posts:
        print(f"{tk} >>{args.no} は未取得です（取得済み {len(posts)} レス）")
        return
    refs = {no: anchors(p[6]) for no, p in posts.items()}
    # 遡り: アンカー先をさらに辿る（深さ --up まで）
    up, frontier = set(), {args.no}
    for _ in range(args.up):
        frontier = {a for n in frontier for a in refs.get(n, []) if a < n} - up
        up |= frontier
    # 返信: 対象を指すレス、さらにそれを指すレス（深さ --down まで）
    down, frontier = set(), {args.no}
    for _ in range(args.down):
        frontier = {n for n, rs in refs.items() if n > min(frontier) and frontier & set(rs)} - down
        down |= frontier
    print(f"■ {title or ''}（{tk}）")
    for no in sorted(up | {args.no} | down):
        if no not in posts:
            continue
        _, nm, ml, d, u, tr, b, sp = posts[no]
        mark = "▶" if no == args.no else ("↑" if no in up else "↓")
        meta = "".join([f" ◆{tr}" if tr else "", f" ID:{u}" if u else "", " [スパム]" if sp else ""])
        print(f"\n{mark} {no} ：{nm}{meta}：{d}\n{b}")
    missing = sorted(a for a in up if a not in posts)
    if missing:
        print(f"\n（アンカー先で未取得: {', '.join('>>%d' % a for a in missing)}）")


def cmd_search(args):
    con = db()
    words = args.words
    if args.k:  # コテハンで絞る（FTS と JOIN しにくいので LIKE で）
        cond = " AND ".join(["p.body LIKE ?"] * len(words))
        rows = con.execute(
            f"SELECT p.thread_key,p.no,substr(p.body,1,120) FROM posts p JOIN post_handles ph "
            f"USING(thread_key,no) WHERE ph.k=? AND p.spam=0 AND {cond} LIMIT ?",
            [args.k] + [f"%{w}%" for w in words] + [args.n]).fetchall()
    elif con.fts and all(len(w) >= 3 for w in words):
        q = " AND ".join('"%s"' % w.replace('"', '""') for w in words)
        rows = con.execute(
            "SELECT thread_key,no,snippet(posts_fts,0,'【','】','…',20) FROM posts_fts "
            "WHERE posts_fts MATCH ? LIMIT ?", (q, args.n)).fetchall()
    else:  # 2文字以下の語は trigram で引けないので LIKE
        cond = " AND ".join(["body LIKE ?"] * len(words))
        rows = con.execute(f"SELECT thread_key,no,substr(body,1,120) FROM posts WHERE spam=0 AND {cond} "
                           "LIMIT ?", [f"%{w}%" for w in words] + [args.n]).fetchall()
    for tk, no, sn in rows:
        print(f"[{tk} >>{no}] {sn.replace(chr(10), ' ')}")
    print(f"-- {len(rows)} 件")


def link_anchors(body):
    """本文を HTML に。>>554 は #554 へのリンク（data-to にアンカー先番号を列挙）"""
    e, out, pos = html.escape, [], 0
    for m in ANCHOR.finditer(body or ""):
        to = anchors(m.group(0))
        if not to:
            continue
        out.append(e(body[pos:m.start()]))
        out.append(f'<a class="anc" href="#{to[0]}" data-to="{" ".join(map(str, to))}">'
                   f'{e(m.group(0))}</a>')
        pos = m.end()
    out.append(e(body[pos:]))
    return "".join(out).replace("\n", "<br>")


# アンカーにマウスを乗せる（スマホはタップ）とアンカー先レスをポップアップ
ANCHOR_JS = """<script>
(function(){
  var pop=document.createElement('div');pop.id='pop';document.body.appendChild(pop);
  function show(a){
    var h='';a.dataset.to.split(' ').forEach(function(n){
      var dt=document.getElementById(n);
      h+= dt ? '<dl>'+dt.outerHTML.replace(/ id="\\d+"/,'')+dt.nextElementSibling.outerHTML+'</dl>'
             : '<p class="miss">&gt;&gt;'+n+' は未取得</p>';});
    pop.innerHTML=h;pop.style.display='block';
    var r=a.getBoundingClientRect();
    pop.style.left=Math.max(8,Math.min(r.left,innerWidth-pop.offsetWidth-8))+scrollX+'px';
    pop.style.top=(r.bottom+6+scrollY)+'px';
  }
  document.addEventListener('mouseover',function(ev){
    var a=ev.target.closest('a.anc');if(a)show(a);
    else if(!ev.target.closest('#pop'))pop.style.display='none';});
  document.addEventListener('click',function(ev){
    var a=ev.target.closest('a.anc');
    if(a&&matchMedia('(hover:none)').matches&&pop.style.display!=='block'){ev.preventDefault();show(a);}
    else if(!ev.target.closest('#pop')&&!a)pop.style.display='none';});
})();
</script>"""
ANCHOR_CSS = ("a.anc{color:#36c}#pop{display:none;position:absolute;z-index:9;max-width:40em;"
              "max-height:60vh;overflow:auto;background:#fffbe8;border:1px solid #cb9;"
              "box-shadow:0 2px 8px #0003;padding:.2em .8em;font-size:.95em}"
              "#pop dd{margin-bottom:.6em}.miss{color:#999}")


# 配布用 HTML の形式。build_posts_db.py はこの形を読んで ichigo_posts.sqlite を作る
#   スレ:  <html data-format="ichigo-archive-1"> <meta name="thread_key" content="economy/0126"> <h1>スレタイ</h1>
#          <dt id="556" data-date="…" data-uid="…" data-trip="…" data-k="001">556 ：<b>名前</b>［コテハン］：日付 …</dt>
#          <dd>本文（改行は <br>、>>アンカーは <a class="anc">）</dd>
#   一覧:  index.html の <table id="handles"> に まとめログのコテハン（data-k / data-count）
ARCHIVE_FORMAT = "ichigo-archive-1"
EMAIL = re.compile(r"([\w.+-]{1,2})[\w.+-]*@([\w-]+(?:\.[\w-]+)+)")


def mask_emails(s):
    """配布用: 本文中のメールアドレスを yo***@example.jp の形に伏せる"""
    return EMAIL.sub(r"\1***@\2", s or "")


REPO_URL = "https://github.com/p72/ichigo-econ-archive"


def repo_link(u):
    """README の相対リンクを GitHub 上の場所に（サイトや zip の中には無いファイルなので）"""
    if re.match(r"[a-z]+:|#", u):
        return u
    if u.endswith(".zip"):
        return f"{REPO_URL}/raw/main/{u}"
    if u.endswith("/"):
        return f"{REPO_URL}/tree/main/{u}"
    return f"{REPO_URL}/blob/main/{u}"


def md_inline(s):
    """`コード` **太字** [文字](URL) と URL の直書き"""
    e = html.escape
    out, pos = [], 0
    for m in re.finditer(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)|(https?://[^\s<>（）)]+)", s):
        out.append(e(s[pos:m.start()]))
        if m.group(1) is not None:
            out.append(f"<code>{e(m.group(1))}</code>")
        elif m.group(2) is not None:
            out.append(f"<strong>{md_inline(m.group(2))}</strong>")
        elif m.group(3) is not None:
            out.append(f'<a href="{e(repo_link(m.group(4)))}">{md_inline(m.group(3))}</a>')
        else:
            out.append(f'<a href="{e(m.group(5))}">{e(m.group(5))}</a>')
        pos = m.end()
    out.append(e(s[pos:]))
    return "".join(out)


def md_to_html(text):
    """README.md 用の小さな Markdown 変換（見出し・段落・箇条書き・引用・表・コードブロック）"""
    lines = text.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    cell = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("```"):
                j += 1
            out.append("<pre><code>" + html.escape("\n".join(lines[i + 1:j])) + "</code></pre>")
            i = j + 1
        elif re.match(r"#{1,6} ", ln):
            n = len(ln.split(" ")[0])
            out.append(f"<h{n}>{md_inline(ln[n + 1:])}</h{n}>")
            i += 1
        elif ln.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i][1:].strip())
                i += 1
            out.append("<blockquote><p>" + "<br>".join(md_inline(b) for b in buf if b) + "</p></blockquote>")
        elif ln.startswith("|") and i + 1 < len(lines) and re.match(r"\|[-| :]+\|?$", lines[i + 1].strip()):
            rows = [cell(ln)]
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(cell(lines[i]))
                i += 1
            th = "".join(f"<th>{md_inline(c)}</th>" for c in rows[0])
            tds = "".join("<tr>" + "".join(f"<td>{md_inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            out.append(f"<table><tr>{th}</tr>{tds}</table>")
        elif re.match(r"(- |\d+\. )", ln):
            tag = "ol" if ln[0].isdigit() else "ul"
            items = []
            while i < len(lines) and (re.match(r"(- |\d+\. )", lines[i]) or
                                      (lines[i].startswith("  ") and lines[i].strip() and items)):
                if lines[i].startswith("  "):          # 字下げで続く行は直前の項目の続き
                    items[-1] += "\n" + lines[i].strip()
                else:
                    items.append(re.sub(r"^(- |\d+\. )", "", lines[i]))
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{md_inline(t)}</li>" for t in items) + f"</{tag}>")
        elif not ln.strip():
            i += 1
        else:
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(r"(```|#{1,6} |>|\||- |\d+\. )", lines[i]):
                buf.append(lines[i])
                i += 1
            out.append("<p>" + md_inline("\n".join(buf)) + "</p>")
    return "\n".join(out)


def write_readme_html(outdir):
    """README.md（収集ツールと同じ場所）を readme.html にする。無ければ何もしない。戻り値: 書いたか"""
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")
    if not os.path.exists(src):
        return False
    with open(src, encoding="utf-8") as f:
        body = md_to_html(f.read())
    style = ("body{font-family:sans-serif;max-width:52em;margin:auto;padding:1em;line-height:1.6}"
             "table{border-collapse:collapse;margin:.5em 0}td,th{border:1px solid #ccc;padding:.2em .5em;vertical-align:top}"
             "pre{background:#f6f6f6;padding:.6em;overflow:auto}code{background:#f3f3f3;padding:0 .2em}"
             "pre code{background:none;padding:0}blockquote{margin:1em 0;padding:.2em 1em;border-left:4px solid #cb9;"
             "background:#fffbe8}h2{border-bottom:1px solid #ddd;margin-top:1.6em}")
    with open(os.path.join(outdir, "readme.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f'<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                f'<title>README - いちごBBS経済板 アーカイブ</title><style>{style}</style>'
                f'<nav><a href="index.html">トップ</a> ＞ README（このアーカイブとツールの説明）</nav>{body}'
                f'<hr><p>元の README.md: <a href="{REPO_URL}#readme">{REPO_URL}</a></p>')
    return True


# スレタイの絞り込み（年ページとトップ）。tr.t の data-title を見る。空白区切りは AND。
# 全角・半角と大文字・小文字は比べるときだけ NFKC でそろえる（データの表記ゆれはそのまま残す）
FILTER_JS = """<script>
function setupFilter(inp, tbl, cnt, hideWhenEmpty) {
  var rows = [].slice.call(document.querySelectorAll('#' + tbl + ' tr.t'));
  var norm = function (s) { return (s.normalize ? s.normalize('NFKC') : s).toLowerCase(); };
  rows.forEach(function (r) { r._t = norm(r.getAttribute('data-title')); });
  var i = document.getElementById(inp), c = document.getElementById(cnt), tb = document.getElementById(tbl);
  function run() {
    var ws = norm(i.value).split(/\\s+/).filter(Boolean), n = 0;
    rows.forEach(function (r) {
      var ok = ws.every(function (w) { return r._t.indexOf(w) >= 0; });
      r.style.display = ok ? '' : 'none'; if (ok) n++;
    });
    if (hideWhenEmpty) tb.style.display = ws.length ? '' : 'none';
    c.textContent = ws.length ? rows.length + '本中 ' + n + '本' : (hideWhenEmpty ? '' : rows.length + '本');
  }
  i.addEventListener('input', run);
  var m = location.search.match(/[?&]q=([^&]*)/);
  if (m) i.value = decodeURIComponent(m[1].replace(/\\+/g, ' '));
  run();
}
</script>"""
# トップページの全文検索の案内（このアーカイブを読み込ませた AI の検索ページ。ユーザーが用意したもの）
FULLTEXT_LINKS = (
    '<h2>全文検索</h2><p>本文まで含めて全文検索をしたい方は、こちらをお使いください。</p><ul>'
    '<li>GPT: <a href="https://ichigo-economics-archive.hina0077.chatgpt.site">'
    'https://ichigo-economics-archive.hina0077.chatgpt.site</a></li>'
    '<li>Claude: <a href="https://claude.ai/artifact/8Pa7sqYNmvmLrkjWBuDUTc">'
    'https://claude.ai/artifact/8Pa7sqYNmvmLrkjWBuDUTc</a></li></ul>')
FILTER_BOX = ('<p>スレタイで絞り込み: <input id="q" type="search" size="30" '
              'placeholder="例: 日銀 緩和（空白で区切ると両方を含むもの）"> <span id="qc"></span></p>')


def write_archive_html(con, outdir, mask=False):
    """閲覧用 HTML を書き出す。スパムとメール欄は含めない。
      index.html       トップ（年ごとのリンク・コテハン一覧）
      year_YYYY.html   その年に立ったスレの一覧（スレの年は、取れている一番古いレスの日付）
      economy_NNNN.html  スレ 1 本
    戻り値: (スレ数, レス数)"""
    os.makedirs(outdir, exist_ok=True)
    e = html.escape
    style = ("body{font-family:sans-serif;max-width:52em;margin:auto;padding:1em;line-height:1.5}"
             "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:.15em .5em}"
             "td.n{text-align:right}nav{margin:.5em 0}")
    head = lambda title: (f'<!doctype html><html lang="ja" data-format="{ARCHIVE_FORMAT}"><meta charset="utf-8">'
                          f'<meta name="viewport" content="width=device-width"><title>{e(title)}</title>')
    threads = []  # (tk, title, rows, year, first, last)
    for tk, title in con.execute("SELECT thread_key,title FROM threads ORDER BY thread_key"):
        rows = con.execute("SELECT p.no,p.name,ph.k,h.name,p.date,p.uid,p.trip,p.body FROM posts p "
                           "LEFT JOIN post_handles ph USING(thread_key,no) LEFT JOIN handles h USING(k) "
                           "WHERE p.thread_key=? AND p.spam=0 ORDER BY p.no", (tk,)).fetchall()
        if not rows:
            continue  # まとめログ一覧でタイトルだけ分かっているスレ
        dates = sorted(r[4] for r in rows if r[4] and r[4][:4].isdigit())
        year = dates[0][:4] if dates else "不明"
        threads.append((tk, title or tk, rows, year, dates[0][:10] if dates else "", dates[-1][:10] if dates else ""))
    years = sorted({t[3] for t in threads})
    ypage = lambda y: f"year_{y}.html" if y != "不明" else "year_unknown.html"

    # スレ 1 本ずつ
    attr = lambda k, v: f' data-{k}="{e(v)}"' if v else ""
    for tk, title, rows, year, first, last in threads:
        body = "".join(
            f'<dt id="{no}"{attr("date", d)}{attr("uid", u)}{attr("trip", tr)}{attr("k", k)}>'
            f'{no} ：<b>{e(nm or "")}</b>{("［"+e(hn)+"］") if hn else ""}：'
            f'{e(d or "")} {("◆"+e(tr)) if tr else ""} {("ID:"+e(u)) if u else ""}</dt>'
            f'<dd>{link_anchors(mask_emails(b) if mask else b)}</dd>'
            for no, nm, k, hn, d, u, tr, b in rows)
        with open(os.path.join(outdir, tk.replace("/", "_") + ".html"), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'{head(title)}<meta name="thread_key" content="{e(tk)}">'
                    f'<style>body{{font-family:sans-serif;max-width:52em;margin:auto;padding:1em}}'
                    f'dd{{margin:0 0 1.2em 1.5em;line-height:1.6}}dt{{color:#060}}{ANCHOR_CSS}</style>'
                    f'<nav><a href="index.html">トップ</a> ＞ <a href="{ypage(year)}">{e(year)}年</a></nav>'
                    f'<h1>{e(title)}</h1><p>{e(tk)} ／ {len(rows)} レス ／ {e(first)}〜{e(last)}</p>'
                    f'<dl>{body}</dl>{ANCHOR_JS}')

    # 年ごとの一覧
    for i, y in enumerate(years):
        ts = [t for t in threads if t[3] == y]
        prev = f'<a href="{ypage(years[i-1])}">◀ {e(years[i-1])}年</a>' if i else ""
        nxt = f'<a href="{ypage(years[i+1])}">{e(years[i+1])}年 ▶</a>' if i + 1 < len(years) else ""
        trs = "".join(
            f'<tr class="t" data-title="{e(title)}"><td class="n">{e(tk.split("/")[-1])}</td>'
            f'<td><a href="{tk.replace("/", "_")}.html">{e(title)}</a></td>'
            f'<td class="n">{len(rows)}</td><td>{e(first)}〜{e(last)}</td></tr>'
            for tk, title, rows, year, first, last in ts)
        with open(os.path.join(outdir, ypage(y)), "w", encoding="utf-8", newline="\n") as f:
            f.write(f'{head(f"{y}年に立ったスレ - いちごBBS経済板 アーカイブ")}<style>{style}</style>'
                    f'<nav><a href="index.html">トップ</a> ｜ {prev} {nxt}</nav>'
                    f'<h1>{e(y)}年に立ったスレ</h1><p>{len(ts)} スレ / {sum(len(t[2]) for t in ts)} レス</p>'
                    f'{FILTER_BOX}'
                    f'<table id="tl"><tr><th>番号</th><th>スレタイ</th><th>レス</th><th>期間</th></tr>{trs}</table>'
                    f'<nav>{prev} {nxt}</nav>'
                    + FILTER_JS + "<script>setupFilter('q','tl','qc',false);</script>")

    # トップ
    total = sum(len(t[2]) for t in threads)
    readme = ('<p>このアーカイブの作り方・収集ツール・検索用 DB の作り方は '
              '<a href="readme.html">README（説明）</a>をご覧ください。</p>') if write_readme_html(outdir) else ""
    readme += (f'<p>リポジトリ（main ブランチ）: <a href="{REPO_URL}/tree/main">{REPO_URL}</a>'
               '（収集ツール・ドキュメント・この zip の最新版）</p>')
    yrows = "".join(
        f'<tr><td><a href="{ypage(y)}">{e(y)}年</a></td>'
        f'<td class="n">{sum(1 for t in threads if t[3] == y)}</td>'
        f'<td class="n">{sum(len(t[2]) for t in threads if t[3] == y)}</td></tr>' for y in years)
    arows = "".join(
        f'<tr class="t" data-title="{e(title)}"><td class="n">{e(tk.split("/")[-1])}</td>'
        f'<td><a href="{tk.replace("/", "_")}.html">{e(title)}</a></td><td>{e(year)}</td>'
        f'<td class="n">{len(rows)}</td><td>{e(first)}〜{e(last)}</td></tr>'
        for tk, title, rows, year, first, last in threads)
    hrows = "".join(
        f'<tr data-k="{e(k)}" data-count="{c}"><td>{e(k)}</td><td>{e(n)}</td><td class="n">{c}</td></tr>'
        for k, n, c in con.execute("SELECT k,name,post_count FROM handles ORDER BY k"))
    with open(os.path.join(outdir, "index.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f'{head("いちごBBS経済板 アーカイブ")}<style>{style}</style>'
                f'<h1>いちごBBS経済板 アーカイブ</h1>'
                f'<p>いちごびびえす（いちごBBS、2000〜2014年）の「経済／経済学」板のうち、Internet Archive の '
                f'Wayback Machine に残っていた分をスレごとにまとめ直したものです。'
                f'{len(threads)} スレ / {total} レス。</p>'
                f'<p>スレは、立った年（取れている一番古い書き込みの日付）ごとに分けています。'
                f'本文の &gt;&gt;554 にマウスを乗せる（スマホはタップ）と、アンカー先のレスが出ます。</p>{readme}'
                f'{FULLTEXT_LINKS}'
                f'<h2>スレタイから探す（全年）</h2>{FILTER_BOX}'
                f'<table id="tl" style="display:none"><tr><th>番号</th><th>スレタイ</th><th>年</th>'
                f'<th>レス</th><th>期間</th></tr>{arows}</table>'
                f'<h2>年ごとのスレ一覧</h2>'
                f'<table><tr><th>年</th><th>スレ</th><th>レス</th></tr>{yrows}</table>'
                f'<h2>まとめログのコテハン</h2><p>まとめログ（ichigobbs.ath.cx）に番号付きで載っていた常連。'
                f'各レスの名前の後ろの［］は、そのレスがまとめログにそのコテハンの書き込みとして載っていたもの'
                f'（［］の中はまとめログでの登録名）。</p>'
                f'<table id="handles"><tr><th>番号</th><th>名前</th><th>まとめログ上の投稿数</th></tr>'
                f'{hrows}</table>'
                + FILTER_JS + "<script>setupFilter('q','tl','qc',true);</script>")
    return len(threads), total


def cmd_export_html(args):
    con = db()
    n, total = write_archive_html(con, args.dir, mask=args.mask_email)
    print(f"{n} スレ / {total} レスを {args.dir}/ に書き出したで")


RELEASE_README = """いちごBBS経済板 アーカイブ（{date} 版）
==========================================

  ブラウザで読む         → このフォルダの index.html を開く（下の「読む」）
  検索・集計・AI で読む  → build_posts_db.py で検索用 DB を作る（下の「検索用の SQLite を作る」）
  自分で集め直す         → 収集ツール ichigo_archiver.py で Wayback Machine から集める（下のリポジトリ）

この zip は、収集ツールで集めた結果を読める形に書き出したものです。
公開サイトと同じ HTML に、説明書と検索用 DB を作るツールを添えています。

  リポジトリ（main ブランチ）  https://github.com/p72/ichigo-econ-archive/tree/main
                               （収集ツール・ドキュメント・zip の最新版）
  公開サイト                   https://p72.github.io/ichigo-econ-archive/

■ 中身
  index.html            トップ（ここから読み始める）。年ごとの一覧へのリンクと、まとめログのコテハン一覧
  year_YYYY.html        その年に立ったスレの一覧（スレ番号・スレタイ・レス数・期間）
  economy_NNNN.html     スレ 1 本 = 1 ファイル（{threads:,} スレ / {posts:,} レス）
  build_posts_db.py     この HTML から検索用の SQLite（ichigo_posts.sqlite）を作るツール
  readme.html           README（このアーカイブとツールの詳しい説明。SQL の例や AI での使い方も）
  README.txt            このファイル

■ 読む（Python も DB も要りません）
  1. この zip を展開する（Windows は右クリック →「すべて展開」、Mac はダブルクリック）
  2. 展開したフォルダの index.html をブラウザで開く
  トップから年ごとの一覧に進み、スレを選んでください。インターネット接続は要りません。
  トップと年ごとの一覧の入力欄に文字を打つと、スレタイで絞り込めます（空白で区切ると両方を含むもの）。
  本文の >>554 にマウスを乗せる（スマホはタップ）と、アンカー先のレスが出ます。

  ※ zip を展開せずに中の HTML を直接開かないでください。Windows のエクスプローラーで zip の中の
    ファイルを開くと、そのファイルだけが一時フォルダに取り出されるため、ほかのページへのリンクが切れます。

■ 検索用の SQLite を作る（調べもの・AI で読む）
  1 行 = 1 レスの SQLite（ichigo_posts.sqlite）を作れます。キーワードや人で横断して探したり、
  AI に渡して日本語で質問したりするのに向いています。

  作らなくても、作成済みのもの（全文検索の索引なしの版）をリポジトリの releases/ で配っています。
    ichigo_posts-{date}.sqlite.zip（約 34MB。展開すると ichigo_posts.sqlite、約 110MB）
    https://github.com/p72/ichigo-econ-archive/tree/main/releases
  自分で作るときは次のとおりです。

  1. Python 3.8 以上を用意する（Windows は python.org の公式インストーラー）
  2. このフォルダでターミナルを開く
       Windows: エクスプローラーのアドレス欄に cmd と打って Enter
       Mac:     ターミナルで「cd 」と打ち、このフォルダをドラッグして Enter
  3. 次のどちらかを実行する（Mac は python を python3 に）
       python build_posts_db.py            全文検索の索引つき（約 360MB・30 秒ほど）
       python build_posts_db.py --no-fts   索引なし（約 110MB・数秒）。AI に渡すならこちら

  表: posts（1 行 1 レス: thread_key, no, name, date, uid, trip, handle_k, body）、
      threads（スレ: thread_key, title, posts）、handles（まとめログのコテハン）、posts_fts（全文検索）
  例: SELECT thread_key, no, name, date, body FROM posts WHERE body LIKE '%日銀%' AND date LIKE '2001/%';

  AI に渡すときは、上の表の説明を最初に伝え、答えにスレ番号とレス番号を付けてもらって、
  原文（economy_NNNN.html#レス番号）で確かめてください。
  SQL の例や AI への頼み方は readme.html（README）の「検索用 DB を作る・使う」に詳しく書いています。

■ 何が入っているか
  いちごびびえす（いちごBBS、2000〜2014年）の「経済／経済学」板のうち、Internet Archive の
  Wayback Machine に残っていた分を、スレごとにまとめ直したものです。出どころは次のとおり。
    - いちごびびえす本体（www22.big.or.jp/~15ch/、ichigobbs.net / .com / .org、ichigo.shitaraba.com）
    - いちごＢＢＳまとめログ（ichigobbs.ath.cx）
    - 住人による過去ログ保存（rssnbl.tripod.co.jp）
  Wayback に残っていないレスは入っていません（スレ番号 1〜1678 のうち、一度も保存されなかったスレもあります）。
  同じレスが複数の保存にあるときは、本文が長い方、同じ長さなら古い保存の方を採っています。

■ 個人情報の扱い
  - メール欄は入れていません
  - 本文中に書かれたメールアドレスは yo***@example.jp のように伏せています
  - 宣伝スパムは除いています

■ 権利について
  書き込みの権利は、それぞれの投稿者にあります。この zip は、失われた掲示板を記録として読めるように
  まとめたものです。ご自分の書き込みの削除を希望される方は、配布元にご連絡ください。

■ 作り方
  この zip は、収集ツール（ichigo_archiver.py）の release コマンドで {date} に作りました。
  収集ツールは Wayback から集め直す・作り直すためのもので、リポジトリの main ブランチで配布しています。
    https://github.com/p72/ichigo-econ-archive/tree/main
"""


def cmd_release(args):
    """配布用 zip を作る: 閲覧用 HTML（スパム・メール欄なし、本文中のアドレスは伏せ字）＋README.txt
    ＋build_posts_db.py。名前は ichigo-econ-archive-YYYY-MM-DD.zip"""
    import shutil
    import tempfile
    con = db()
    date = args.date or time.strftime("%Y-%m-%d")
    name = f"ichigo-econ-archive-{date}"
    out = args.out or f"{name}.zip"
    tool = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_posts_db.py")
    if not os.path.exists(tool):
        sys.exit(f"{tool} が見つかりません")
    with tempfile.TemporaryDirectory() as tmp:
        d = os.path.join(tmp, name)
        nt, np_ = write_archive_html(con, d, mask=True)
        with open(os.path.join(d, "README.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(RELEASE_README.format(date=date, threads=nt, posts=np_))
        shutil.copy(tool, d)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for fn in sorted(os.listdir(d)):
                z.write(os.path.join(d, fn), f"{name}/{fn}")
    print(f"{out} を作りました（{nt} スレ / {np_} レス / {os.path.getsize(out) / 1e6:.1f}MB）")
    if args.no_db:
        return
    # 検索用 DB（全文検索の索引なしの軽い版）も、いま作った zip から作って zip にしておく。
    # Python を使わない人や、AI にそのまま渡したい人向け。中の名前は ichigo_posts.sqlite
    import subprocess
    db_out = os.path.join(os.path.dirname(os.path.abspath(out)), f"ichigo_posts-{date}.sqlite.zip")
    with tempfile.TemporaryDirectory() as tmp:
        sq = os.path.join(tmp, "ichigo_posts.sqlite")
        subprocess.run([sys.executable, tool, out, "-o", sq, "--no-fts"], check=True, stdout=subprocess.DEVNULL)
        with zipfile.ZipFile(db_out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            z.write(sq, "ichigo_posts.sqlite")
        size = os.path.getsize(sq)
    print(f"{db_out} を作りました（検索用 DB {size / 1e6:.0f}MB → zip {os.path.getsize(db_out) / 1e6:.1f}MB）")


def cmd_export_urls(args):
    """今の DB の中身を再現するのに必要な保存 URL だけを書き出す（本文は含まない）。
    採用されたレスの出どころ＋まとめログのコテハン判定の出どころ"""
    con = db()
    rows = con.execute("""
        SELECT c.ts, c.url, c.thread_key, count(p.no) AS n FROM captures c
        LEFT JOIN posts p ON p.capture_id=c.id
        WHERE c.id IN (SELECT capture_id FROM posts UNION SELECT capture_id FROM post_handles)
        GROUP BY c.id ORDER BY c.thread_key, c.ts""").fetchall()
    with open(args.file, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# いちごBBS経済板 URL集 {time.strftime('%Y-%m-%d')} / {len(rows)} URL / "
                f"レス {sum(r[3] for r in rows)} 件分\n"
                "# import-urls で読み込み → fetch → parse で同じ DB を再現できます\n")
        f.write("ts\turl\tthread_key\tposts\twayback\n")
        for ts, url, tk, n in rows:
            f.write(f"{ts}\t{url}\t{tk or ''}\t{n}\thttps://web.archive.org/web/{ts}id_/{url}\n")
    print(f"{len(rows)} URL を {args.file} に書き出したで")


def cmd_import_urls(args):
    """export-urls の TSV を読み込んで captures に登録（取得は fetch で）"""
    con = db()
    n = 0
    with open(args.file, encoding="utf-8") as f:
        for line in f:
            if line.startswith("#") or line.startswith("ts\t") or not line.strip():
                continue
            ts, url = line.rstrip("\n").split("\t")[:2]
            cur = con.execute("INSERT OR IGNORE INTO captures(url,ts,thread_key) VALUES(?,?,?)",
                              (url, ts, thread_key(url)))
            n += cur.rowcount
    con.commit()
    print(f"{n} URL を新しく登録しました。fetch → parse で取得・解析してください")


def cmd_export_csv(args):
    con = db()
    with open(args.file, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["thread_key", "title", "no", "name", "handle", "mail", "date", "id", "trip", "spam", "body"])
        for r in con.execute("SELECT p.thread_key,t.title,p.no,p.name,h.name,p.mail,p.date,p.uid,p.trip,p.spam,p.body "
                             "FROM posts p LEFT JOIN threads t USING(thread_key) "
                             "LEFT JOIN post_handles ph USING(thread_key,no) LEFT JOIN handles h USING(k) "
                             "ORDER BY p.thread_key,p.no"):
            w.writerow(r)
    print("書き出し:", args.file)


def main():
    global DB_PATH
    ap = argparse.ArgumentParser(description="いちごBBS経済板 アーカイブ収集ツール")
    ap.add_argument("--db", default=DB_PATH)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("enumerate"); s.add_argument("--host", nargs="*")
    s.add_argument("--only", nargs="*", help="TARGETS のうち名前にこの文字列を含むホストだけ"); s.set_defaults(f=cmd_enumerate)
    s = sp.add_parser("add-url"); s.add_argument("urls", nargs="+"); s.set_defaults(f=cmd_add_url)
    s = sp.add_parser("fetch"); s.add_argument("--limit", type=int)
    s.add_argument("--match", nargs="*", help="URL に含む文字列で絞る")
    s.add_argument("--oldest", action="store_true", help="優先度を使わず古い順に取る")
    s.add_argument("--dry-run", action="store_true", help="取得せず順番だけ表示")
    s.set_defaults(f=cmd_fetch)
    s = sp.add_parser("parse"); s.add_argument("--all", action="store_true"); s.set_defaults(f=cmd_parse)
    sp.add_parser("stats").set_defaults(f=cmd_stats)
    sp.add_parser("threads").set_defaults(f=cmd_threads)
    sp.add_parser("handles").set_defaults(f=cmd_handles)
    s = sp.add_parser("release", help="配布用 zip（閲覧用 HTML＋README＋build_posts_db.py）を作る")
    s.add_argument("--date", help="版の日付（既定: 今日）"); s.add_argument("--out", help="zip の名前")
    s.add_argument("--no-db", action="store_true",
                   help="検索用 DB の zip（ichigo_posts-YYYY-MM-DD.sqlite.zip）を作らない")
    s.set_defaults(f=cmd_release)
    s = sp.add_parser("export-urls", help="DB の再現に必要な保存 URL だけを書き出す")
    s.add_argument("file"); s.set_defaults(f=cmd_export_urls)
    s = sp.add_parser("import-urls", help="export-urls の一覧を登録（その後 fetch → parse）")
    s.add_argument("file"); s.set_defaults(f=cmd_import_urls)
    s = sp.add_parser("show", help="レスをアンカーを辿って会話の流れで表示")
    s.add_argument("thread", help="スレ番号（0126 / 126 / economy/0126）")
    s.add_argument("no", type=int, help="レス番号")
    s.add_argument("--up", type=int, default=5, help="アンカー先を遡る深さ（既定 5）")
    s.add_argument("--down", type=int, default=1, help="返信を辿る深さ（既定 1）")
    s.set_defaults(f=cmd_show)
    s = sp.add_parser("search"); s.add_argument("words", nargs="+"); s.add_argument("-n", type=int, default=30)
    s.add_argument("--k", help="まとめログのコテハン番号で絞る（例 001）")
    s.set_defaults(f=cmd_search)
    s = sp.add_parser("export-html"); s.add_argument("dir")
    s.add_argument("--mask-email", action="store_true", help="本文中のメールアドレスを伏せ字にする（配布用）")
    s.set_defaults(f=cmd_export_html)
    s = sp.add_parser("export-csv"); s.add_argument("file"); s.set_defaults(f=cmd_export_csv)
    a = ap.parse_args()
    DB_PATH = a.db
    a.f(a)


if __name__ == "__main__":
    main()
