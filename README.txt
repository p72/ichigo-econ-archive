いちごBBS経済板 アーカイブ（2026-10-01 版）
==========================================

アーカイブを読みたい人は zip を、自分で集め直したい人は収集ツールを使ってください。
zip は収集ツールで集めた結果を、読める形に書き出したものです。

■ 中身
  index.html            トップ（ここから読み始める）。年ごとの一覧へのリンクと、まとめログのコテハン一覧
  year_YYYY.html        その年に立ったスレの一覧（スレ番号・スレタイ・レス数・期間）
  economy_NNNN.html     スレ 1 本 = 1 ファイル（1,034 スレ / 179,454 レス）
  build_posts_db.py     この HTML から検索用の SQLite（ichigo_posts.sqlite）を作るツール
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
  この zip は、収集ツール（ichigo_archiver.py）の release コマンドで 2026-10-01 に作りました。
  収集ツールは Wayback から集め直す・作り直すためのもので、別に配布しています。
