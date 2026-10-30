#!/usr/bin/env python3
"""DACTVNET VOD oynatma servisi.

IPTV oynatıcısı /play/<video_id> adresini açınca yt-dlp ile güncel googlevideo
medya adresi çözülür ve (a) proxy modunda Range destekli akış aktarılır veya
(b) redirect modunda 302 ile yönlendirilir. Çözümlenen adresler süre dolmadan
önce önbellekte tutulur, dolunca yeniden çözülür. Video indirilmez/saklanmaz.

Ortam değişkenleri:
  STREAM_MODE     proxy (varsayılan) | redirect
  VIDEOS_URL      videos.json'un ham GitHub adresi (yoksa yerel videos.json)
  REQUIRE_KNOWN   1 (varsayılan): yalnız listedeki video ID'leri oynatılır
  YTDLP_COOKIES   (isteğe bağlı) cookies.txt yolu
  MAX_STREAMS     eşzamanlı akış sınırı (varsayılan 20)
  PORT            yerel çalıştırma portu (varsayılan 8080)
"""
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from flask import Flask, Response, abort, jsonify, redirect, request, send_file, stream_with_context

ROOT = Path(__file__).resolve().parent
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
FORMAT = "best[protocol^=http][ext=mp4]/best[protocol^=http]"  # tek dosya (görüntü+ses birlikte)
MIN_TTL = 300  # adresin bitimine bu kadar saniye kala yeniden çöz
STREAM_MODE = os.environ.get("STREAM_MODE", "proxy").lower()
REQUIRE_KNOWN = os.environ.get("REQUIRE_KNOWN", "1") != "0"
MAX_STREAMS = int(os.environ.get("MAX_STREAMS", "20"))
KNOWN_TTL = 900

log = logging.getLogger("dactvnet.server")
app = Flask(__name__)
_cache, _cache_lock = {}, threading.Lock()
_vid_locks, _vid_locks_guard = {}, threading.Lock()
_resolve_slots = threading.BoundedSemaphore(4)
_stream_slots = threading.BoundedSemaphore(MAX_STREAMS)
_known = {"ids": set(), "at": 0.0, "mtime": None}


class PlayError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------ video listesi
def known_ids():
    now = time.time()
    url = os.environ.get("VIDEOS_URL")
    try:
        if url:
            if now - _known["at"] > KNOWN_TTL or not _known["ids"]:
                r = requests.get(url, timeout=15)
                r.raise_for_status()
                _known.update(ids={v["id"] for v in r.json().get("videos", [])}, at=now)
        else:
            p = ROOT / "videos.json"
            mt = p.stat().st_mtime
            if mt != _known["mtime"]:
                _known.update(ids={v["id"] for v in json.loads(p.read_text("utf-8")).get("videos", [])}, mtime=mt)
    except Exception as e:
        log.warning("videos.json okunamadı: %s", type(e).__name__)
    return _known["ids"]


# ------------------------------------------------------------- çözümleme
def _extract(video_id):
    import yt_dlp  # geç import: testler yt-dlp olmadan da çalışır
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            "format": FORMAT, "socket_timeout": 20}
    if os.environ.get("YTDLP_COOKIES"):
        opts["cookiefile"] = os.environ["YTDLP_COOKIES"]
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if "Sign in to confirm" in msg or "bot" in msg.lower():
            raise PlayError(503, "YouTube bu sunucu IP'sini bot olarak işaretledi. YTDLP_COOKIES kullanın "
                                 "veya sunucuyu ev/konut IP'sinde çalıştırın.")
        if "Video unavailable" in msg or "Private video" in msg:
            raise PlayError(404, "Video kullanılamıyor (özel/silinmiş/bölge kısıtlı).")
        raise PlayError(502, "Medya adresi çözümlenemedi.")


def _expiry(url):
    try:
        return int(parse_qs(urlparse(url).query).get("expire", [""])[0])
    except ValueError:
        return 0


def resolve(video_id, force=False):
    """-> dict(url, headers, expire). Süresi dolmamış önbelleği kullanır."""
    with _vid_locks_guard:
        lock = _vid_locks.setdefault(video_id, threading.Lock())
    with lock:
        with _cache_lock:
            hit = _cache.get(video_id)
        if hit and not force and hit["expire"] - time.time() > MIN_TTL:
            return hit
        with _resolve_slots:
            info = _extract(video_id)
        url = info.get("url")
        if not url or info.get("protocol") not in ("https", "http"):
            raise PlayError(422, "Bu video için doğrudan (tek dosya HTTP) medya akışı yok "
                                 f"(protokol: {info.get('protocol')}). Canlı yayın/prömiyer veya ayrı "
                                 "görüntü-ses formatı desteklenmez.")
        host = urlparse(url).hostname or ""
        if not host.endswith(".googlevideo.com"):
            raise PlayError(502, "Beklenmeyen medya sunucusu; güvenlik nedeniyle reddedildi.")
        entry = {"url": url, "headers": info.get("http_headers") or {},
                 "expire": _expiry(url) or time.time() + 1800}
        with _cache_lock:
            _cache[video_id] = entry
        return entry


def _check_host(url):
    p = urlparse(url)
    if p.scheme != "https" or not (p.hostname or "").endswith(".googlevideo.com"):
        raise PlayError(502, "Geçersiz medya adresi.")


# --------------------------------------------------------------- yollar
@app.errorhandler(PlayError)
def _play_error(e):
    return jsonify(error=e.message), e.status


@app.get("/health")
def health():
    return jsonify(status="ok", mode=STREAM_MODE, cached=len(_cache))


@app.get("/playlist.m3u")
def playlist():
    p = ROOT / "playlist.m3u"
    if not p.exists():
        abort(404)
    return send_file(p, mimetype="audio/x-mpegurl")


def _open_upstream(entry):
    _check_host(entry["url"])
    headers = dict(entry["headers"])
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    method = "HEAD" if request.method == "HEAD" else "GET"
    return requests.request(method, entry["url"], headers=headers, stream=True,
                            timeout=(10, 30), allow_redirects=False)


@app.route("/play/<video_id>", methods=["GET", "HEAD"])
def play(video_id):
    if not VIDEO_ID_RE.match(video_id):
        raise PlayError(400, "Geçersiz video ID.")
    if REQUIRE_KNOWN:
        ids = known_ids()
        if not ids:
            raise PlayError(503, "Video listesi yüklenemedi.")
        if video_id not in ids:
            raise PlayError(404, "Video listede yok.")
    entry = resolve(video_id)
    if STREAM_MODE == "redirect":
        return redirect(entry["url"], code=302)

    if not _stream_slots.acquire(blocking=False):
        raise PlayError(429, "Sunucu meşgul, biraz sonra tekrar deneyin.")
    try:
        try:
            up = _open_upstream(entry)
            if up.status_code in (403, 404, 410):  # adres süresi dolmuş/iptal
                up.close()
                entry = resolve(video_id, force=True)
                up = _open_upstream(entry)
        except requests.RequestException:
            raise PlayError(502, "Medya sunucusuna ulaşılamadı.")
        if up.status_code >= 400 and up.status_code != 416:
            up.close()
            raise PlayError(502, f"Medya sunucusu hata döndürdü ({up.status_code}).")
    except Exception:
        _stream_slots.release()
        raise

    headers = {"Accept-Ranges": up.headers.get("Accept-Ranges", "bytes"),
               "Cache-Control": "no-store"}
    for h in ("Content-Type", "Content-Length", "Content-Range"):
        if h in up.headers:
            headers[h] = up.headers[h]

    def body():
        try:
            if request.method != "HEAD":
                for chunk in up.iter_content(65536):
                    yield chunk
        finally:
            up.close()
            _stream_slots.release()

    return Response(stream_with_context(body()), status=up.status_code, headers=headers)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), threaded=True)
