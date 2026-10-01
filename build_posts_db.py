#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
いちごBBS経済板 アーカイブ（閲覧用 HTML）から、検索用の SQLite を作るツール。

  python build_posts_db.py                         # このファイルと同じ場所の HTML から作る
  python build_posts_db.py ichigo-econ-archive-2026-10-01.zip   # zip を展開せずに読む
  python build_posts_db.py フォルダ -o ichigo_posts.sqlite --no-fts

標準ライブラリだけで動きます（Python 3.8 以上。全文検索は SQLite 3.34 以上の trigram が必要）。

できる表:
  threads(thread_key, title, posts)                      スレ
  posts(thread_key, no, name, date, uid, trip, handle_k, body)   レス（1 行 1 レス）
  handles(k, name, post_count)                           まとめログのコテハン
  posts_fts                                              全文検索の索引（--no-fts で省略）

検索の例（3 文字以上の語。2 文字以下は posts.body LIKE '%語%' で）:
  SELECT thread_key, no, name, snippet(posts_fts, 0, '【', '】', '…', 20)
    FROM posts_fts WHERE posts_fts MATCH '"量的緩和"' LIMIT 20;
"""
import argparse
import html
import os
import re
import sqlite3
import sys
import time
import zipfile

FORMAT = "ichigo-archive-1"   # ichigo_archiver.py の ARCHIVE_FORMAT と同じ
POST = re.compile(r'<dt id="(\d+)"([^>]*)>(.*?)</dt><dd>(.*?)</dd>', re.S)
ATTR = re.compile(r'data-([a-z]+)="([^"]*)"')

SCHEMA = """
CREATE TABLE threads(thread_key TEXT PRIMARY KEY, title TEXT, posts INTEGER);
CREATE TABLE posts(
  thread_key TEXT NOT NULL, no INTEGER NOT NULL,
  name TEXT, date TEXT, uid TEXT, trip TEXT,
  handle_k TEXT,              -- まとめログに載っていたコテハンの番号（handles.k）
  body TEXT,
  PRIMARY KEY(thread_key, no));
CREATE TABLE handles(k TEXT PRIMARY KEY, name TEXT, post_count INTEGER);
"""
FTS = """
CREATE VIRTUAL TABLE posts_fts USING fts5(
  body, name, thread_key UNINDEXED, no UNINDEXED, tokenize='trigram');
INSERT INTO posts_fts(body, name, thread_key, no) SELECT body, name, thread_key, no FROM posts;
"""


def body_text(dd):
    """<dd> の中身を元の本文に戻す（<br> → 改行、アンカーのリンクを外す）"""
    s = re.sub(r"<br\s*/?>", "\n", dd)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s)


def parse_thread(text):
    m = re.search(r'<meta name="thread_key" content="([^"]+)"', text)
    if not m or f'data-format="{FORMAT}"' not in text:
        return None
    tk = html.unescape(m.group(1))
    t = re.search(r"<h1>(.*?)</h1>", text, re.S)
    title = html.unescape(t.group(1)) if t else None
    posts = []
    for no, attrs, head, dd in POST.findall(text):
        a = {k: html.unescape(v) for k, v in ATTR.findall(attrs)}
        nm = re.search(r"<b>(.*?)</b>", head, re.S)
        posts.append((tk, int(no), html.unescape(nm.group(1)) if nm else "", a.get("date"),
                      a.get("uid"), a.get("trip"), a.get("k"), body_text(dd)))
    return tk, title, posts


def parse_handles(text):
    out = []
    for k, c, row in re.findall(r'<tr data-k="([^"]*)" data-count="(\d+)">(.*?)</tr>', text, re.S):
        cells = re.findall(r"<td>(.*?)</td>", row, re.S)
        out.append((html.unescape(k), html.unescape(cells[1]) if len(cells) > 1 else "", int(c)))
    return out


def iter_files(src):
    """(ファイル名, 中身) を返す。zip でもフォルダでもよい"""
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            for n in sorted(z.namelist()):
                if n.endswith(".html"):
                    yield os.path.basename(n), z.read(n).decode("utf-8")
    else:
        for n in sorted(os.listdir(src)):
            if n.endswith(".html"):
                with open(os.path.join(src, n), encoding="utf-8") as f:
                    yield n, f.read()


def main():
    ap = argparse.ArgumentParser(description="いちごBBS経済板アーカイブから検索用 SQLite を作る")
    ap.add_argument("src", nargs="?", default=os.path.dirname(os.path.abspath(__file__)),
                    help="アーカイブの zip か、HTML のあるフォルダ（既定: このファイルの場所）")
    ap.add_argument("-o", "--out", default="ichigo_posts.sqlite", help="作る SQLite（既定 ichigo_posts.sqlite）")
    ap.add_argument("--no-fts", action="store_true", help="全文検索の索引を作らない（約 100MB → 約 360MB の差）")
    a = ap.parse_args()
    if os.path.exists(a.out):
        sys.exit(f"{a.out} はすでにあります。消すか、-o で別の名前を指定してください")

    t0 = time.time()
    con = sqlite3.connect(a.out)
    con.executescript(SCHEMA)
    nt = np = 0
    for name, text in iter_files(a.src):
        if name == "index.html":
            con.executemany("INSERT OR REPLACE INTO handles VALUES(?,?,?)", parse_handles(text))
            continue
        r = parse_thread(text)
        if not r:
            continue
        tk, title, posts = r
        con.execute("INSERT INTO threads VALUES(?,?,?)", (tk, title, len(posts)))
        con.executemany("INSERT INTO posts VALUES(?,?,?,?,?,?,?,?)", posts)
        nt += 1
        np += len(posts)
    con.commit()
    if nt == 0:
        con.close()
        os.remove(a.out)
        sys.exit(f"{a.src} にアーカイブの HTML（{FORMAT}）が見つかりませんでした")
    print(f"スレ {nt:,} / レス {np:,} を読み込みました（{time.time() - t0:.0f} 秒）")

    if not a.no_fts:
        t1 = time.time()
        try:
            con.executescript(FTS)
            con.commit()
            print(f"全文検索の索引を作りました（{time.time() - t1:.0f} 秒）")
        except sqlite3.OperationalError as e:
            print(f"全文検索の索引は作れませんでした（SQLite {sqlite3.sqlite_version}: {e}）。"
                  "本文の検索は LIKE で行えます")
    con.close()
    print(f"できあがり: {a.out}（{os.path.getsize(a.out) / 1e6:.0f}MB）")


if __name__ == "__main__":
    main()
