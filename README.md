# ichigo-econ-archive — いちごBBS 経済板 アーカイブ収集ツール

2000〜2014年に運営されていた匿名掲示板「いちごびびえす（いちごBBS）」の**経済／経済学板**の残骸を、
Internet Archive の Wayback Machine から集めて、SQLite の掲示板データベースに復元するツールです。

| したいこと | 使うもの |
|---|---|
| **ブラウザで読む** | 公開サイト https://p72.github.io/ichigo-econ-archive/ 、または zip を展開して `index.html` を開く（→「読むだけの人へ」）|
| **検索・集計・AI で読む** | zip の中の `build_posts_db.py` で検索用 DB（`ichigo_posts.sqlite`）を作る（→「検索用 DB を作る・使う」）|
| **自分で集め直す** | 収集ツール `ichigo_archiver.py` で Wayback Machine から集める（→「はじめかた」）|

zip（[releases/ichigo-econ-archive-2026-10-01.zip](releases/ichigo-econ-archive-2026-10-01.zip)、約 34MB）は、収集ツールで集めた結果を
読める形に書き出したものです。中身は公開サイトと同じ HTML に、説明書と検索用 DB を作るツールを添えています。

収集ツールについて:

- Python 3.8 以上の標準ライブラリだけで動きます（追加インストール不要。3.14 で動作確認）
- 取得は 2 秒間隔。中断・再開ができるので、毎日少しずつ回して「コツコツ」集められます
- 時期ごとに違う 3 種類の掲示板システム＋まとめログサイトの HTML を、同じ形のレスに分解します
- 全文検索、アンカー（`>>554`）を辿る表示、閲覧用 HTML・CSV の書き出しができます

## 3 つのもの

| | 何か | 大きさ | 配布 |
|---|---|---|---|
| **収集ツール**（このリポジトリ）| Wayback から集めて収集用 DB を作り、そこからアーカイブを書き出す | 数百 KB | 配る |
| **収集用 DB**（`ichigo.db`）| Wayback の原本（生 HTML）、どこまで集めたかの記録、取り出したレス | 約 850MB | 配らない（原本・メール欄を含むため）|
| **アーカイブ本体**（`ichigo-econ-archive-YYYY-MM-DD.zip`）| 復元した経済板を読める HTML にしたもの＋説明書＋ビルドツール | 約 34MB | 配る |
| **検索用 DB**（`ichigo_posts.sqlite`）| アーカイブ本体から `build_posts_db.py` で作る、検索・分析用の DB | 約 110MB（全文検索つき約 360MB）| 受け取った人が作る |

```
Wayback Machine
   │  収集ツール（enumerate → fetch → parse）
   ▼
ichigo.db ………………………… 収集用 DB（原本・作業記録・レス）           ← 手元だけ
   │  release
   ▼
ichigo-econ-archive-YYYY-MM-DD.zip … アーカイブ本体（読める HTML）    ← 配る
   │  build_posts_db.py（zip に同梱）
   ▼
ichigo_posts.sqlite ………… 検索用 DB                                  ← 受け取った人が作る
```

矢印は一方向です。zip から `ichigo.db` は作れません（原本・メール欄・スパム・取得の記録が入っていないため）。
同じ `ichigo.db` がほしい人は、収集ツールと URL 集（`export-urls`）で Wayback から集め直してください。

> 収集用 DB には、当時の書き込み本文やメールアドレスが含まれます。扱いは末尾の「集めたデータの扱い」を参照してください。

## 読むだけの人へ（zip を展開してブラウザで開く）

Python も DB も要りません。

1. [`releases/`](releases/) にある `ichigo-econ-archive-YYYY-MM-DD.zip` をダウンロードする（約 34MB）
2. **展開する**（Windows は右クリック →「すべて展開」、Mac はダブルクリック）。展開後は約 100MB
3. 展開したフォルダの `index.html` をブラウザで開く

- `index.html`（トップ）から、スレが立った年（2000〜2013 年）ごとの一覧に進めます。一覧にはスレ番号・スレタイ・レス数・期間が並びます
- トップと年ごとの一覧には**スレタイの絞り込み**があります（「日銀 緩和」のように空白で区切ると両方を含むもの。全角・半角は区別しません）。トップでは全年のスレタイから探せます。`index.html?q=日銀` のように URL に付けて開くこともできます
- スレのページでは、本文の `>>554` にマウスを乗せる（スマホはタップ）と、アンカー先のレスがポップアップします
- インターネット接続は要りません。ファイルはすべてこのフォルダの中で完結しています
- **zip を展開せずに中の HTML を直接開かないでください。** Windows のエクスプローラーで zip の中のファイルを開くと、
  そのファイルだけが一時フォルダに取り出されるため、一覧やほかのスレへのリンクが切れます
- スマホでは、ファイルアプリなどで展開してから `index.html` を開いてください

## 検索用 DB を作る・使う（調べもの・AI で読む）

zip の中の `build_posts_db.py` を使うと、閲覧用 HTML から **1 行 = 1 レスの SQLite データベース**（`ichigo_posts.sqlite`）を作れます。
キーワードや人で横断して探したり、集計したり、AI に渡して日本語で質問したりするのに向いています。
収集ツールや `ichigo.db` は要りません。

### 作り方

1. **Python 3.8 以上**を用意する。Windows は python.org の公式インストーラーで入れます（Mac は入っていることが多い）。
   入っているかは、ターミナルで `python --version`（Mac は `python3 --version`）と打つとわかります
2. zip を**展開する**
3. 展開したフォルダで**ターミナルを開く**。Windows はエクスプローラーのアドレス欄に `cmd` と打って Enter、
   Mac はターミナルで `cd ` と打ってから、フォルダをウィンドウにドラッグして Enter
4. 次のどちらかを実行する（Mac は `python` を `python3` に）

```bash
python build_posts_db.py            # 全文検索の索引つき（約 360MB・30 秒ほど）
python build_posts_db.py --no-fts   # 索引なし（約 110MB・数秒）。AI に渡すならこちら
```

同じフォルダに `ichigo_posts.sqlite` ができます。zip を展開せずに `python build_posts_db.py ichigo-econ-archive-2026-10-01.zip` でも作れます。
同じ名前のファイルがあるときは上書きせずに止まるので、作り直すときは消すか `-o 別の名前.sqlite` を付けてください。

### 中身（表と列）

| 表 | 1 行の単位 | 列 |
|---|---|---|
| `posts` | レス 1 件（179,454 行）| `thread_key` スレ（`economy/0126`）、`no` レス番号、`name` 名前、`date` 日付（`2002/05/18(Sat) 18:03`）、`uid` ID（2009 年 5 月以降）、`trip` トリップ、`handle_k` まとめログのコテハン番号、`body` 本文 |
| `threads` | スレ 1 本（1,034 行）| `thread_key`、`title` スレタイ、`posts` レス数 |
| `handles` | まとめログのコテハン 1 人（21 行）| `k` 番号、`name` まとめログでの登録名、`post_count` まとめログ上の投稿数 |
| `posts_fts` | 全文検索の索引（`--no-fts` では作らない）| `body`、`name`、`thread_key`、`no` |

- メール欄とスパムは入っていません。本文中のメールアドレスは `yo***@example.jp` のように伏せてあります
- `handle_k` は、まとめログがそのコテハンの書き込みとして載せていたレスにだけ入っています（名前欄が同じでも、載っていなければ空）
- 表記ゆれはそのまま残しています。「～」と「〜」、「－」と「−」は別の文字なので、探すときは両方試してください

### 調べ方の例（SQL）

```sql
-- 全文検索（3 文字以上の語。索引つきで作ったとき）
SELECT thread_key, no, name, snippet(posts_fts, 0, '【', '】', '…', 15)
  FROM posts_fts WHERE posts_fts MATCH '"量的緩和"' LIMIT 20;

-- 2 文字以下の語や、索引なしのときは LIKE（18 万件でも 1 秒かからない）
SELECT thread_key, no, name, date, substr(body, 1, 80)
  FROM posts WHERE body LIKE '%日銀%' AND date LIKE '2001/%';

-- ある人の書き込み
SELECT thread_key, no, date, body FROM posts
 WHERE name = 'ドラエモン' AND body LIKE '%白川%' ORDER BY date;

-- スレの一部を読む
SELECT no, name, date, body FROM posts
 WHERE thread_key = 'economy/0126' AND no BETWEEN 540 AND 560 ORDER BY no;

-- 年ごとのレス数
SELECT substr(date, 1, 4) AS 年, count(*) FROM posts
 WHERE date GLOB '[0-9][0-9][0-9][0-9]/*' GROUP BY 年;
```

開く道具は、無料の **DB Browser for SQLite**（画面で表を見たり SQL を実行したりできる）、`sqlite3` コマンド、Python の `sqlite3` モジュールなどが使えます。

### AI で読む

`ichigo_posts.sqlite` を、ファイルを扱える AI に渡すと、日本語で質問できます。
手元のファイルを読んで SQL を実行できる AI エージェントでも、ファイルをアップロードして分析できる AI チャットでも使えます。

頼み方の例:

- 「`ichigo_posts.sqlite` は、いちごBBS経済板（2000〜2014年）のレスを集めた SQLite です。`posts` 表が 1 行 1 レスです。
  2002 年に『インフレ目標』を議論したスレと主な論者を、スレ番号とレス番号つきで挙げてください」
- 「名前が『ドラエモン』の書き込みから、財政政策への立場がわかるものを 10 件引用して、立場を要約してください」
- 「`economy/0126` の 540〜560 番の議論の流れをまとめてください」

コツ:

- 最初に上の「中身（表と列）」を伝えると、AI が迷わず SQL を書けます
- 答えには**スレ番号とレス番号を付けてもらい**、原文で確かめてください。公開サイトでは
  `https://p72.github.io/ichigo-econ-archive/economy_0126.html#556` のように、`#レス番号` でそのレスに飛べます
- アップロードできるファイルの大きさはサービスによって上限があります。`--no-fts` の小さい版を使い、それでも大きいときは
  必要なスレだけを SQL で取り出して（CSV などにして）渡してください
- 書き込みは当時の投稿者のものです。AI の要約は、原文と照らし合わせて扱ってください

## はじめかた

```bash
python ichigo_archiver.py enumerate          # ① Wayback の保存 URL 一覧を取り込む（数分）
python ichigo_archiver.py fetch --limit 500  # ② 500 件ダウンロード（約 25 分。何度でも続きから）
python ichigo_archiver.py parse              # ③ 取れたページをレスに分解
python ichigo_archiver.py stats              # 収集状況
```

②③を繰り返すほどレスが増えます。`fetch` は「新しいレスが取れる見込み」の高い順に取ります。

1. **レスがまだ 1 件もないスレ**を、各スレいちばん良い URL（スレ全体 → 範囲指定 → 最新N件 → 1レス）から 1 本ずつ
2. **欠けを埋めうる保存**を、少ない取得で多く埋まる順に。「埋めうるか」は URL のレス番号の範囲と、
   **保存日時とレスの投稿日時**で判定します（保存より後に書かれたレスは載らない、最新 50 件のページには保存時点の最後の 50 件しか載らない）
3. 持っているレスしか載っていない見込みの URL、スレ一覧
4. 中身がない見込みのもの（CDX 上のサイズが小さい転送ページ、多数の URL で中身が同じエラーページ、ドメイン失効後）

並び順は実行時点の DB から計算するので、`fetch` → `parse` を交互に回すと効率よく埋まります。
`fetch --dry-run` で順番と段ごとの件数を確認できます。

## 読む・探す

```bash
python ichigo_archiver.py threads                 # スレ一覧（0レス＝タイトルだけ判明）
python ichigo_archiver.py search 量的緩和 日銀     # 全文検索（AND）
python ichigo_archiver.py search デフレ --k 001    # まとめログのコテハン番号で絞る（001=ドラエモン）
python ichigo_archiver.py show 0126 556           # アンカーを辿って会話の流れで表示
python ichigo_archiver.py handles                 # まとめログのコテハン一覧
python ichigo_archiver.py export-html out         # 閲覧用 HTML（out/index.html を開く）
python ichigo_archiver.py export-csv posts.csv    # 全レスを CSV に
```

`show` の表示例（`↑` アンカー先を遡ったレス、`▶` 指定したレス、`↓` 返信）:

```
■ インフレ･ターゲティング導入の是非について（economy/0126）

↑ 554 ：どんたく名無しさん：2002/05/18(Sat) 17:55
>>550
…

▶ 556 ：ドラエモン：2002/05/18(Sat) 18:03
>>554
…

↓ 558 ：５５４：2002/05/18(Sat) 18:12
>>556
…
```

遡る深さは `--up`（既定 5）、返信の深さは `--down`（既定 1）。スレ番号は `0126` / `126` / `economy/0126` のどれでも可。
閲覧用 HTML では、本文の `>>554` にマウスを乗せる（スマホはタップ）とアンカー先がポップアップします。

## 配布用の zip を作る（release）

```bash
python ichigo_archiver.py release                 # → ichigo-econ-archive-（今日の日付）.zip
python ichigo_archiver.py release --date 2026-10-01 --out 名前.zip
```

zip の中身は、閲覧用 HTML（スレごと＋`index.html`）、説明書 `README.txt`、ビルドツール `build_posts_db.py` です。
配布用なので、**スパムとメール欄は入れず、本文中のメールアドレスは `yo***@example.jp` のように伏せます**
（手元の `ichigo.db` はそのまま）。HTML の各レスには、見た目を変えずに日付・ID・トリップ・コテハン番号を
data 属性で埋め込んであり、`build_posts_db.py` はそれを読んで `ichigo_posts.sqlite` を作ります。

zip を受け取った人が検索用 DB を作る手順は、上の「検索用 DB を作る・使う」にあります。

## URL 集から DB を再現する

Wayback に残る 7 万件超の URL のうち、DB の中身に実際に使われているのは千数百件だけです。
`export-urls` はその URL（日時付き）だけを書き出します。本文は含まないので、配っても書き込みの再配布にはなりません。

```bash
python ichigo_archiver.py export-urls urls.tsv                 # 手元の DB から URL 集を作る（数百 KB）
python ichigo_archiver.py --db new.db import-urls urls.tsv     # 別の DB に登録
python ichigo_archiver.py --db new.db fetch                    # その URL だけ取得（千数百件なら 1 時間前後）
python ichigo_archiver.py --db new.db parse                    # 同じレスが再現される
```

同じレスが複数のスナップショットにあるときは「本文が長い方、同じ長さなら古い方」を採用するので、
取得や解析の順番によらず同じ結果になります（Wayback 側で保存が消えていなければ）。

## コマンド一覧

| コマンド | 内容 |
|---|---|
| `enumerate` | Wayback CDX API から保存 URL を取り込む。`--only ホスト` で一部だけ、`--host 新ホスト` で追加 |
| `add-url URL…` | 見つけた URL を手で追加（`https://web.archive.org/web/2007…/http://…` の形のままで可）|
| `fetch` | 未取得をダウンロード。`--limit N`、`--match 文字列`（URL 絞り込み）、`--dry-run`（順番だけ表示）、`--oldest`（古い順）|
| `parse` | 未解析のページをレスに分解。`--all` で全部作り直し（解析ルールを直したとき）|
| `stats` / `threads` / `handles` | 収集状況 / スレ一覧 / コテハン一覧 |
| `search 語…` | 全文検索。`--k 001` でコテハン絞り込み、`-n` で件数 |
| `show スレ レス` | アンカーを辿って表示。`--up` `--down` |
| `export-html DIR` / `export-csv FILE` | 書き出し |
| `release` | 配布用の zip（閲覧用 HTML＋README.txt＋build_posts_db.py）を作る。`--date` `--out` |
| `export-urls FILE` / `import-urls FILE` | DB の再現に必要な保存 URL だけの一覧（本文なし）を書き出す／読み込む |

どのコマンドも `--db パス` で別の DB ファイルを使えます（既定 `ichigo.db`）。

## 対象サイトと歴史

経済板は時期ごとにシステムとドメインが変わりましたが、**スレ番号は移行時にそのまま引き継がれています**。
そのため、どこで見つかったレスも `economy/0126` のような同じスレにまとめています。

| 時期 | 場所 | システム | URL の形 |
|---|---|---|---|
| 2000/06〜2001/11 | www22.big.or.jp/~15ch/（いちごちゃんねる）| Aska 系 CGI（`aska.cgi` / `readres.cgi`）| `readres.cgi?bo=economy&vi=0126` |
| 2001/12〜2002/06 | ichigobbs.net | 同上 | 同上 |
| 2002/06〜2003/03 | ichigobbs.com | 同上 | 同上 |
| 2003/03〜2009/10 | ichigobbs.net | 15bbs（2ch-Type BBS Ver.2.11、作者サイト「A round」）| `/cgi/15bbs/economy/0126/`（`/L50` `/1-70` `/80` など）|
| 2009/11〜2014/12 | ichigobbs.org | 同上 | 同上 |
| 2009〜2019 | ichigobbs.ath.cx「いちごＢＢＳまとめログ」| まとめログ（ミラー）| `index.php?k=001&c=000&thread=0126&no=5` |
| 2003 | rssnbl.tripod.co.jp（住人ろしあんぶる氏の過去ログ保存）| 当時のページを丸ごと保存したもの | `/0648.html` |

歴史は Wikipedia「[いちごびびえす](https://ja.wikipedia.org/wiki/%E3%81%84%E3%81%A1%E3%81%94%E3%81%B3%E3%81%B3%E3%81%88%E3%81%99)」による。
まとめログの `k=` はコテハン番号、`c=` はカテゴリで、スレの区別には使いません。
各システムの HTML の形と解析の詳細は [docs/FORMATS.md](docs/FORMATS.md) にあります。
Wayback と CDX の仕組み、どれくらい残っていて何がなぜ欠けるのかの分析は [docs/WAYBACK.md](docs/WAYBACK.md) にあります。

## データベース（ichigo.db）

| テーブル | 内容 |
|---|---|
| `captures` | 保存 URL・日時・CDX のサイズ・取得状態・生 HTML（gzip）|
| `threads` | スレタイ・最初／最後に見たスナップ日時 |
| `posts` | レス：番号・名前・メール・日付・ID・トリップ・本文・スパム印 |
| `pages` | レスに分解できなかったページ（スレ一覧など）の全文 |
| `handles` | まとめログのコテハン（番号・名前・投稿数）|
| `post_handles` | **まとめログ掲載**：どのレスが、どのコテハンの書き込みとしてまとめログに載っていたか（名前欄が同じだけでは付けない）|

- 同じレスが複数のスナップショットにある場合は、本文が長い（欠けていない）方、同じ長さなら古いスナップの方を採用します
- **トリップ**：いちご系は投稿時刻の右に背景色と同じ色で `[ va4qsJNk0c ]` と表示されていました（`posts.trip`）
- **ID**：2009年5月から `ID:wWxUqj5PRwac` が表示されるようになりました（`posts.uid`）
- **削除・移動レス**：名前「いちごJam削除」「引越しさん」など。日付の位置に出ていた文言（「いちごJam削除」「移動されました」）を `date` に入れています
- **スパム**：`posts.spam = 1`。でたらめな英字のメールアドレス＋URL、payday loan などの定型、2012 年以降のブランドコピー宣伝など。主に 2010年と 2013〜2014年。`search` と `export-html` では除外し、`export-csv` には `spam` 列付きで全件出します（判定の詳細は [docs/FORMATS.md](docs/FORMATS.md)）
- **中身なしページ**（`captures.parsed = -1`）：いちごの「スレッド表示エラー」（過去ログ送り後）、15bbs 移行後の旧 URL が返す転送ページ、ドメイン失効後の駐車ページ

生 HTML はすべて残しているので、解析ルールを直したら `parse --all` で作り直せます。

## Wayback Machine への配慮

- リクエストは 2 秒間隔、429/503 が返ったら長めに待って再試行します
- 取得済みは二度と取りに行きません。中身がないと推定できるもの（CDX 上のサイズが 1,000 バイト未満の転送ページ、
  多数の URL で中身が同じエラーページ）は後回しにします
- `WAIT`（`ichigo_archiver.py` 冒頭）を短くしないでください

## 集めたデータの扱い

書き込みは当時の投稿者のものです。集めたデータを再配布・公開する場合は、メールアドレス（`posts.mail`）を
除くなど、投稿者のプライバシーに配慮してください。`release` で作る zip は、メール欄とスパムを入れず、
本文中のメールアドレスを伏せた形になっています。

## ライセンス

ツールのコードは [MIT License](LICENSE)（Copyright (c) 2026 p72）。集めたデータ（書き込み）には適用されません。書き込みの権利はそれぞれの投稿者にあります。
