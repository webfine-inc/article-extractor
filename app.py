import os
import concurrent.futures
from flask import Flask, render_template, request, Response
from extractor import ContentExtractor

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

# 単一インスタンス（内部でHTTPはスレッドごとに行う）
extractor = ContentExtractor()

@app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/extract", methods=["POST"])
def extract():
    """
    受け取り:
      - form: urls(1行=1URL), prefer_alt(on/off)
      - JSON: {"urls":"http://a\nhttp://b", "prefer_alt": true}
    返却: 指定テンプレの text/plain
    """
    if request.is_json:
        data = request.get_json(silent=True) or {}
        raw_urls = data.get("urls", "") or ""
        prefer_alt = bool(data.get("prefer_alt", True))
    else:
        raw_urls = request.form.get("urls", "") or ""
        prefer_alt = (request.form.get("prefer_alt") in ("on", "true", "1", True))

    # 正規化＆重複除去（順序保持）
    seen = set()
    urls = []
    for line in raw_urls.splitlines():
        u = (line or "").strip()
        if not u:
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)

    if not urls:
        return Response("ERROR: no_urls\n", mimetype="text/plain; charset=utf-8")

    results = []

    def process(u):
        try:
            return extractor.extract_to_template(u, prefer_alt=prefer_alt)
        except Exception as e:
            # URL単位で継続
            return f"BEGIN\nURL: {u}\nERROR: content_not_found ({type(e).__name__})\nEND"

    max_workers = min(8, max(2, os.cpu_count() or 2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for out in ex.map(process, urls):
            results.append(out)

    body = "\n\n".join(results) + "\n"
    return Response(body, mimetype="text/plain; charset=utf-8")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
