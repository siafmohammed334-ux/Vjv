import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlparse

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context
import yt_dlp

app = Flask(__name__, static_folder="static", static_url_path="")

# النطاقات المسموحة لكل قسم
PLATFORMS = {
    "youtube": ["youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"],
    "tiktok": ["tiktok.com", "vm.tiktok.com", "vt.tiktok.com"],
    "instagram": ["instagram.com", "instagr.am"],
    "facebook": ["facebook.com", "fb.watch", "m.facebook.com", "fb.com"],
    "twitter": ["twitter.com", "x.com", "t.co", "mobile.twitter.com"],
}

# الجودة: كل اختيار له بدائل (fallback) علشان ما يقولش "فشل التحميل لان الجودة قديمة"
QUALITY = {
    "high": [
        "bv*[height<=1080]+ba/b[height<=1080]",
        "bv*+ba/b",
        "best",
    ],
    "medium": [
        "bv*[height<=720]+ba/b[height<=720]",
        "bv*[height<=480]+ba/b[height<=480]",
        "bv*+ba/b",
        "best",
    ],
    "low": [
        "bv*[height<=360]+ba/b[height<=360]",
        "worst[height>=144]/worst",
        "best",
    ],
}


def host_ok(url: str, platform: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == d or host.endswith("." + d) for d in PLATFORMS.get(platform, []))


def base_opts(tmpdir=None):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "geo_bypass": True,
        # تيك توك: نطلب النسخة بدون علامة مائية قدر الإمكان
        "extractor_args": {"tiktok": {"api_hostname": ["api22-normal-c-useast2a.tiktokv.com"]}},
    }
    if tmpdir:
        opts["outtmpl"] = os.path.join(tmpdir, "%(id)s.%(ext)s")
    proxy = os.environ.get("PROXY_URL")
    if proxy:
        opts["proxy"] = proxy
    cookies = os.environ.get("COOKIES_FILE")
    if cookies and os.path.exists(cookies):
        opts["cookiefile"] = cookies
    return opts


def clean_title(t: str) -> str:
    t = re.sub(r'[\\/:*?"<>|\n\r]+', " ", t or "video").strip()
    return t[:80] or "video"


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.post("/api/info")
def info():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    platform = data.get("platform") or ""
    if not url or not host_ok(url, platform):
        return jsonify(error="الرابط ده مش تبع القسم اللي انت فيه. اتأكد من القسم والرابط."), 400
    try:
        with yt_dlp.YoutubeDL({**base_opts(), "skip_download": True}) as ydl:
            i = ydl.extract_info(url, download=False)
        return jsonify(
            title=i.get("title"),
            thumbnail=i.get("thumbnail"),
            duration=i.get("duration"),
            uploader=i.get("uploader"),
        )
    except Exception as e:
        return jsonify(error="مقدرتش اقرا الفيديو. ممكن يكون خاص أو محذوف.", detail=str(e)[:300]), 422


@app.get("/api/download")
def download():
    url = (request.args.get("url") or "").strip()
    platform = request.args.get("platform") or ""
    quality = request.args.get("quality") or "high"
    if quality not in QUALITY:
        quality = "high"
    if not url or not host_ok(url, platform):
        return jsonify(error="رابط غير صالح لهذا القسم"), 400

    tmpdir = tempfile.mkdtemp(prefix="dl_")
    last_err = None
    filepath = None
    title = "video"

    # نجرب كل صيغة بالترتيب لحد ما تنجح (ده حل مشكلة فشل التحميل)
    for fmt in QUALITY[quality]:
        try:
            opts = {**base_opts(tmpdir), "format": fmt}
            with yt_dlp.YoutubeDL(opts) as ydl:
                i = ydl.extract_info(url, download=True)
                title = clean_title(i.get("title"))
            files = [f for f in os.listdir(tmpdir) if not f.endswith((".part", ".ytdl"))]
            if files:
                filepath = os.path.join(tmpdir, files[0])
                break
        except Exception as e:
            last_err = e
            for f in os.listdir(tmpdir):
                try:
                    os.remove(os.path.join(tmpdir, f))
                except OSError:
                    pass

    if not filepath:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify(error="فشل التحميل", detail=str(last_err)[:300]), 422

    ext = os.path.splitext(filepath)[1] or ".mp4"
    size = os.path.getsize(filepath)

    def gen():
        try:
            with open(filepath, "rb") as f:
                while chunk := f.read(1024 * 256):
                    yield chunk
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    from urllib.parse import quote
    headers = {
        "Content-Length": str(size),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(title + ext)}",
    }
    return Response(stream_with_context(gen()), mimetype="video/mp4", headers=headers)


def keep_ytdlp_updated():
    """تحديث yt-dlp عند التشغيل وكل 12 ساعة. أغلب أخطاء 'فشل التحميل' سببها نسخة قديمة."""
    while True:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-U", "--quiet", "yt-dlp"],
                timeout=300,
            )
        except Exception:
            pass
        time.sleep(12 * 3600)


if __name__ == "__main__":
    threading.Thread(target=keep_ytdlp_updated, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
