"""
Instagram Research Pipeline
Flask backend for parsing, exporting, and downloading Instagram NDJSON data.
"""

import io
import json
import uuid
import zipfile
import requests
from datetime import datetime, timezone
from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
import pandas as pd

app = Flask(__name__)
app.secret_key = "instagram-research-pipeline"

# ── In-memory store (per-process; fine for single-user local app) ─────────────

store = {
    "posts_df":    None,
    "comments_df": None,
    "zip_cache":   {},   # token -> bytes, cleared after download
}


# ── Parsing ───────────────────────────────────────────────────────────────────

_DEPRECATED_POST_COLS = {"account_ai_label", "ai_agent_owner", "transparency_label",
                         "follower_count", "repost_count"}

def parse_posts(lines):
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            # Rename old field names from pre-fix collections
            if "post_code" in r:
                r.setdefault("shortcode", r.pop("post_code"))
            if "taken_at" in r:
                r["posted_at"] = r.pop("taken_at")
            # Convert timestamps
            posted = r.get("posted_at")
            if posted:
                r["posted_at"] = datetime.fromtimestamp(posted, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            collected = r.get("collected_at")
            if collected:
                r["collected_at"] = datetime.fromtimestamp(collected / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            # Drop columns removed from the schema
            for col in _DEPRECATED_POST_COLS:
                r.pop(col, None)
            records.append(r)
        except Exception:
            continue
    return pd.DataFrame(records) if records else pd.DataFrame()


def parse_comments(lines):
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            created = r.get("created_at")
            if created:
                r["created_at"] = datetime.fromtimestamp(created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            collected = r.get("collected_at")
            if collected:
                r["collected_at"] = datetime.fromtimestamp(collected / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            records.append(r)
        except Exception:
            continue
    return pd.DataFrame(records) if records else pd.DataFrame()


def df_stats(df, kind):
    if df is None or df.empty:
        return {}
    if kind == "posts":
        def _count_url(col):
            if col not in df.columns:
                return 0
            return int(df[col].dropna().astype(str).apply(lambda x: x not in ("", "nan", "None")).sum())

        def _count_carousel():
            if "carousel_urls" not in df.columns:
                return 0
            total = 0
            for val in df["carousel_urls"].dropna():
                s = str(val)
                if s not in ("", "nan", "None"):
                    total += len(s.split("|"))
            return total

        return {
            "total":          len(df),
            "media_types":    df["media_type"].value_counts().to_dict() if "media_type" in df else {},
            "ai_labeled":     int(df["ai_label"].notna().sum()) if "ai_label" in df else 0,
            "partials":       int(df["is_partial"].sum()) if "is_partial" in df else 0,
            "date_range":     {
                "from": str(df["posted_at"].min()) if "posted_at" in df else "–",
                "to":   str(df["posted_at"].max()) if "posted_at" in df else "–",
            },
            "image_count":    _count_url("image_url"),
            "video_count":    _count_url("video_url"),
            "carousel_count": _count_carousel(),
        }
    if kind == "comments":
        return {
            "total":       len(df),
            "posts":       int(df["media_id"].nunique()) if "media_id" in df else 0,
            "avg_likes":   float(round(df["like_count"].mean(), 1)) if "like_count" in df else 0,
            "with_replies": int((df["child_comment_count"] > 0).sum()) if "child_comment_count" in df else 0,
        }
    return {}


def df_preview(df, n=200):
    if df is None or df.empty:
        return {"columns": [], "rows": []}
    preview = df.head(n).fillna("").astype(str)
    return {
        "columns": list(preview.columns),
        "rows":    preview.values.tolist(),
    }


def df_to_csv_bytes(df):
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    result = {}

    if "posts" in request.files:
        f = request.files["posts"]
        lines = f.read().decode("utf-8").splitlines()
        store["posts_df"] = parse_posts(lines)
        result["posts"] = {
            "stats":   df_stats(store["posts_df"], "posts"),
            "preview": df_preview(store["posts_df"]),
        }

    if "comments" in request.files:
        f = request.files["comments"]
        lines = f.read().decode("utf-8").splitlines()
        store["comments_df"] = parse_comments(lines)
        result["comments"] = {
            "stats":   df_stats(store["comments_df"], "comments"),
            "preview": df_preview(store["comments_df"]),
        }

    return jsonify(result)


@app.route("/export/<kind>")
def export(kind):
    if kind == "posts":
        df = store["posts_df"]
        name = "posts"
    elif kind == "comments":
        df = store["comments_df"]
        name = "comments"
    elif kind == "merged":
        p = store["posts_df"]
        c = store["comments_df"]
        if p is None or c is None or p.empty or c.empty:
            return jsonify({"error": "Both files required for merge"}), 400
        # Bring key post fields into the comments frame
        post_cols = ["media_id", "post_url", "caption_text", "media_type",
                     "username", "like_count", "comment_count",
                     "ai_label", "ai_high_risk", "posted_at"]
        available = [c for c in post_cols if c in p.columns]
        df = c.merge(p[available], on="media_id", how="left", suffixes=("", "_post"))
        name = "merged"
    else:
        return jsonify({"error": "Unknown export type"}), 400

    if df is None or df.empty:
        return jsonify({"error": "No data loaded"}), 400

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv = df_to_csv_bytes(df)
    return send_file(
        io.BytesIO(csv),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"instagram_{name}_{ts}.csv"
    )


@app.route("/download/media/progress")
def download_media_progress():
    df = store["posts_df"]
    if df is None or df.empty:
        def err_stream():
            yield 'data: {"error": "No posts loaded"}\n\n'
        return Response(stream_with_context(err_stream()), mimetype="text/event-stream")

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Referer":    "https://www.instagram.com/",
    }
    rows_list = df.to_dict("records")

    def _is_empty(val):
        return not val or str(val) in ("", "nan", "None")

    def generate():
        total = 0
        for row in rows_list:
            if not _is_empty(row.get("image_url")):
                total += 1
            if not _is_empty(row.get("video_url")):
                total += 1
            carousel = row.get("carousel_urls")
            if not _is_empty(carousel):
                total += len(str(carousel).split("|"))

        yield f"data: {json.dumps({'total': total, 'downloaded': 0, 'errors': 0})}\n\n"

        downloaded = 0
        errors = 0
        zip_buf = io.BytesIO()

        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for row in rows_list:
                media_id = row.get("media_id", "unknown")
                username = row.get("username", "unknown")

                image_url = row.get("image_url")
                if not _is_empty(image_url):
                    try:
                        r = requests.get(str(image_url), headers=req_headers, timeout=10)
                        if r.status_code == 200:
                            zf.writestr(f"images/{username}_{media_id}.jpg", r.content)
                            downloaded += 1
                        else:
                            errors += 1
                    except Exception:
                        errors += 1
                    yield f"data: {json.dumps({'downloaded': downloaded, 'errors': errors, 'total': total})}\n\n"

                video_url = row.get("video_url")
                if not _is_empty(video_url):
                    try:
                        r = requests.get(str(video_url), headers=req_headers, timeout=30)
                        if r.status_code == 200:
                            zf.writestr(f"videos/{username}_{media_id}.mp4", r.content)
                            downloaded += 1
                        else:
                            errors += 1
                    except Exception:
                        errors += 1
                    yield f"data: {json.dumps({'downloaded': downloaded, 'errors': errors, 'total': total})}\n\n"

                carousel = row.get("carousel_urls")
                if not _is_empty(carousel):
                    for i, url in enumerate(str(carousel).split("|")):
                        try:
                            r = requests.get(url.strip(), headers=req_headers, timeout=10)
                            if r.status_code == 200:
                                zf.writestr(f"carousel/{username}_{media_id}_{i}.jpg", r.content)
                                downloaded += 1
                            else:
                                errors += 1
                        except Exception:
                            errors += 1
                        yield f"data: {json.dumps({'downloaded': downloaded, 'errors': errors, 'total': total})}\n\n"

        token = uuid.uuid4().hex
        zip_buf.seek(0)
        store["zip_cache"][token] = zip_buf.read()
        yield f"data: {json.dumps({'done': True, 'token': token, 'downloaded': downloaded, 'errors': errors, 'total': total})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.route("/download/media/result/<token>")
def download_media_result(token):
    data = store["zip_cache"].pop(token, None)
    if data is None:
        return jsonify({"error": "Not found or already downloaded"}), 404
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(data),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"instagram_media_{ts}.zip",
    )


@app.route("/status")
def status():
    return jsonify({
        "posts":    len(store["posts_df"]) if store["posts_df"] is not None else 0,
        "comments": len(store["comments_df"]) if store["comments_df"] is not None else 0,
    })


if __name__ == "__main__":
    import webbrowser, threading
    def open_browser():
        webbrowser.open("http://127.0.0.1:5000")
    threading.Timer(1.0, open_browser).start()
    app.run(debug=False, port=5000)
