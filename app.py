import os
import concurrent.futures
from flask import Flask, render_template, request, jsonify, Response
from extractor import ContentExtractor

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

# Instantiate a single extractor (thread-safe for read-only ops; requests Session is per-thread)
extractor = ContentExtractor()

+@app.route("/health", methods=["GET"])
+def health():
+    return "ok", 200
+
@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")

@app.route("/extract", methods=["POST"])
def extract():
    """
    Accepts:
      - form-encoded: urls (string, 1 URL per line), prefer_alt ("on"|"off")
      - or JSON: {"urls": "http://a\nhttp://b", "prefer_alt": true}
    Returns: text/plain of concatenated results in the specified template
    """
    if request.is_json:
        data = request.get_json(silent=True) or {}
        raw_urls = data.get("urls", "") or ""
        prefer_alt = bool(data.get("prefer_alt", True))
    else:
        raw_urls = request.form.get("urls", "") or ""
        prefer_alt = (request.form.get("prefer_alt") in ("on", "true", "1", True))

    # normalize and dedupe while preserving order
    seen = set()
    urls = []
    for line in raw_urls.splitlines():
        u = line.strip()
        if not u:
            continue
        if u not in seen:
            seen.add(u)
            urls.append(u)

    if not urls:
        return Response("ERROR: no_urls\n", mimetype="text/plain; charset=utf-8")

    # Process in parallel (IO-bound)
    results = []
    def process(u):
        try:
            return extractor.extract_to_template(u, prefer_alt=prefer_alt)
        except Exception as e:
            return f"BEGIN\nURL: {u}\nERROR: content_not_found ({type(e).__name__})\nEND"

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(2, os.cpu_count() or 2))) as ex:
        for out in ex.map(process, urls):
            results.append(out)

    body = "\n\n".join(results) + "\n"
    return Response(body, mimetype="text/plain; charset=utf-8")


if __name__ == "__main__":
    # Local dev server
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)

