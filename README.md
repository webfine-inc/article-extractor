# 本文抽出 Flask サービス

`readability-lxml` / `trafilatura` / `lxml` / `beautifulsoup4` を併用し、ノイズ除去＋見出し/段落/箇条書き/引用/コード/表を整形して**プレーンテキスト**出力するツールです。  
UIはコピー可能なテキストエリアで、複数URLに対応（1行=1URL）。

## 仕様ハイライト

- 取得: `requests` + Retry（UA/タイムアウト）
- 代替版優先: AMP/印刷版が見つかり本文が長ければ自動切替（オプション）
- 抽出: `readability.Document` と `trafilatura.extract` の**候補をスコア比較**（`score = text_len * (1 - link_density)`、見出しの有無で補正）
- ノイズ除去: id/class/name/data-* に `nav|menu|header|footer|sidebar|toc|share|sns|ad|sponsor|recommend|related|comment|profile|tag|category|breadcrumb|pager|pagination|cta` を含む要素や caption を除去
- 構造化: 本文コンテナから h2〜h5、p、ul/ol、blockquote、pre/code、table を順序通りに列挙
- 表: 各 `<tr>` をセルを `" | "` 連結行に変換
- 出力テンプレ:  
BEGIN
URL: ...
Title: ...
H2/H3/H4/H5: ...
Body:
...
END

- エラー: URL単位で継続（`ERROR: content_not_found`）

## 将来拡張

- ドメイン別ルール（`SITE_RULES`）: セレクタ指定で優先抽出や除外を調整
- 画像/キャプションやAMP/印刷版の優先度調整
- APIエンドポイント化、認証、ジョブキュー等

## フォルダ構成

app.py
extractor.py
templates/
└─ index.html
static/
└─ app.js
requirements.txt
Dockerfile
gunicorn.conf.py
.env.example
README.md