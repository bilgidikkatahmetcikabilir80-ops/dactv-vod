#!/usr/bin/env python3
"""DACTVNET VOD: YouTube kanalındaki herkese açık videoları bulur, videos.json
veritabanını ve playlist.m3u dosyasını günceller.

Videolar indirilmez. M3U'da süresi dolan googlevideo adresi SAKLANMAZ; her kayıt
server.py'nin /play/<video_id> adresine işaret eder.

Kullanım:  YOUTUBE_API_KEY=... python update.py [--full]
"""
import argparse
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
API = "https://www.googleapis.com/youtube/v3"
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
DURATION_RE = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
log = logging.getLogger("dactvnet")


class ApiError(Exception):
    """YouTube API hatası (API anahtarını içermez)."""


# ---------------------------------------------------------------- yardımcılar
def load_config(path=ROOT / "config.json"):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not (cfg.get("channel_id") or cfg.get("channel_handle")):
        raise ValueError("config.json: channel_id veya channel_handle gerekli")
    return cfg


def api_get(session, endpoint, params, api_key):
    try:
        r = session.get(f"{API}/{endpoint}", params={**params, "key": api_key}, timeout=30)
    except requests.RequestException as e:  # URL (anahtar) loga sızmasın
        raise ApiError(f"{endpoint}: ağ hatası ({type(e).__name__})") from None
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text[:200]
        raise ApiError(f"{endpoint}: HTTP {r.status_code}: {msg}")
    return r.json()


def parse_duration(iso):
    """ISO-8601 süre (PT1H2M3S) -> saniye. Tanınmazsa -1 (bilinmiyor)."""
    m = DURATION_RE.match(iso or "")
    if not m or not any(m.groups()):
        return -1
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def get_uploads_playlist(session, api_key, cfg):
    params = {"part": "contentDetails"}
    if cfg.get("channel_id"):
        params["id"] = cfg["channel_id"]
    else:
        params["forHandle"] = cfg["channel_handle"]
    data = api_get(session, "channels", params, api_key)
    items = data.get("items") or []
    if not items:
        raise ApiError("Kanal bulunamadı (channel_handle / channel_id kontrol edin)")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def best_thumbnail(snippet, video_id):
    thumbs = snippet.get("thumbnails") or {}
    for key in ("maxres", "standard", "high", "medium", "default"):
        if key in thumbs and thumbs[key].get("url"):
            return thumbs[key]["url"]
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def fetch_new_videos(session, api_key, playlist_id, known_ids, full=False):
    """Uploads playlist'ini en yeniden eskiye gezer. Normal modda bilinen ilk
    videoda durur (kota tasarrufu); full=True ise hepsini tarar."""
    found, seen, token = [], set(), None
    while True:
        params = {"part": "snippet,contentDetails,status", "playlistId": playlist_id, "maxResults": 50}
        if token:
            params["pageToken"] = token
        data = api_get(session, "playlistItems", params, api_key)
        hit_known = False
        for it in data.get("items", []):
            vid = (it.get("contentDetails") or {}).get("videoId")
            if not vid or not VIDEO_ID_RE.match(vid) or vid in seen:
                continue
            seen.add(vid)
            if (it.get("status") or {}).get("privacyStatus") != "public":
                continue  # özel / listelenmemiş / silinmiş
            if vid in known_ids:
                hit_known = True
                continue
            sn = it.get("snippet") or {}
            found.append({
                "id": vid,
                "title": (sn.get("title") or vid).strip(),
                "description": sn.get("description") or "",
                "published_at": (it.get("contentDetails") or {}).get("videoPublishedAt")
                                or sn.get("publishedAt") or "",
                "thumbnail": best_thumbnail(sn, vid),
                "duration": -1,
            })
        token = data.get("nextPageToken")
        if not token or (hit_known and not full):
            break
    return found


def fill_durations(session, api_key, videos):
    for i in range(0, len(videos), 50):
        chunk = videos[i:i + 50]
        data = api_get(session, "videos",
                       {"part": "contentDetails", "id": ",".join(v["id"] for v in chunk)}, api_key)
        durs = {x["id"]: parse_duration(x["contentDetails"].get("duration")) for x in data.get("items", [])}
        for v in chunk:
            v["duration"] = durs.get(v["id"], -1)


def load_db(path):
    if not Path(path).exists():
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {v["id"]: v for v in data.get("videos", []) if VIDEO_ID_RE.match(v.get("id", ""))}


def save_json_atomic(path, obj):
    write_atomic(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def write_atomic(path, text):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def attr(s):
    """M3U öznitelik değeri: tırnak ve satır sonlarını temizle."""
    return re.sub(r"\s+", " ", str(s).replace('"', "'")).strip()


def build_m3u(videos, base_url, group_title):
    base = base_url.rstrip("/")
    lines = ["#EXTM3U"]
    for v in sorted(videos, key=lambda x: x.get("published_at", ""), reverse=True):
        title = attr(v["title"])
        dur = v.get("duration", -1)
        lines.append(
            f'#EXTINF:{dur if isinstance(dur, int) else -1} tvg-id="{v["id"]}" tvg-name="{title}" '
            f'tvg-logo="{attr(v["thumbnail"])}" group-title="{attr(group_title)}",{title}'
        )
        lines.append(f"{base}/play/{v['id']}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- ana akış
def run(cfg, api_key, session=None, full=False, root=ROOT):
    session = session or requests.Session()
    base_url = os.environ.get("SERVER_BASE_URL") or cfg.get("server_base_url", "")
    if not base_url.startswith(("http://", "https://")) or "example.com" in base_url:
        log.warning("server_base_url ayarlanmamış; M3U adresleri çalışmaz. config.json veya "
                    "SERVER_BASE_URL değişkenini düzenleyin.")
    videos_path = root / cfg.get("videos_file", "videos.json")
    db = load_db(videos_path)
    log.info("Kayıtlı video sayısı: %d", len(db))

    playlist_id = get_uploads_playlist(session, api_key, cfg)
    new = fetch_new_videos(session, api_key, playlist_id, set(db), full=full)
    if new:
        fill_durations(session, api_key, new)
    for v in new:
        db[v["id"]] = v  # eskiler asla silinmez; id ile tekilleştirilir
    log.info("Yeni video: %d, toplam: %d", len(new), len(db))

    ordered = sorted(db.values(), key=lambda x: x.get("published_at", ""), reverse=True)
    save_json_atomic(videos_path, {"videos": ordered})
    write_atomic(root / cfg.get("playlist_file", "playlist.m3u"),
                 build_m3u(ordered, base_url, cfg.get("group_title", "DACTVNET")))
    return len(new), len(db)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="tüm uploads listesini yeniden tara")
    args = ap.parse_args(argv)
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs" / "update.log", encoding="utf-8")])
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        log.error("YOUTUBE_API_KEY ortam değişkeni tanımlı değil.")
        return 2
    try:
        run(load_config(), api_key, full=args.full)
    except (ApiError, ValueError, OSError) as e:
        log.error("Güncelleme başarısız: %s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
