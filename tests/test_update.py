import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import update


def item(vid, title="T", privacy="public", date="2025-01-01T00:00:00Z"):
    return {"snippet": {"title": title, "description": "d", "thumbnails": {"high": {"url": f"https://i/{vid}.jpg"}}},
            "contentDetails": {"videoId": vid, "videoPublishedAt": date}, "status": {"privacyStatus": privacy}}


class FakeSession:
    """endpoint -> sıralı yanıt listesi"""
    def __init__(self, responses):
        self.responses, self.calls = responses, []
    def get(self, url, params=None, timeout=None):
        ep = url.rsplit("/", 1)[1]
        self.calls.append((ep, params))
        r = MagicMock(status_code=200)
        r.json.return_value = self.responses[ep].pop(0)
        return r


V = lambda n: f"vid{n:08d}"  # 11 karakter


class Tests(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(update.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(update.parse_duration("PT45S"), 45)
        self.assertEqual(update.parse_duration("P0D"), -1)
        self.assertEqual(update.parse_duration(None), -1)

    def test_m3u_escaping_and_order(self):
        vids = [{"id": V(1), "title": 'A "q"\nB', "thumbnail": "http://t/1", "published_at": "2024", "duration": 10},
                {"id": V(2), "title": "New", "thumbnail": "http://t/2", "published_at": "2025", "duration": -1}]
        out = update.build_m3u(vids, "https://s.example/", "G")
        lines = out.splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertIn(f'tvg-id="{V(2)}"', lines[1])          # yeni önce
        self.assertEqual(lines[2], f"https://s.example/play/{V(2)}")
        self.assertIn("A 'q' B", lines[3])
        self.assertEqual(len([l for l in lines if l.startswith("#EXTINF")]), 2)
        self.assertNotIn("googlevideo", out)

    def test_incremental_stops_at_known_and_skips_private(self):
        s = FakeSession({"playlistItems": [{"items": [item(V(3)), item(V(4), privacy="private"), item(V(2))],
                                            "nextPageToken": "NEXT"}]})
        new = update.fetch_new_videos(s, "k", "PL", {V(2)})
        self.assertEqual([v["id"] for v in new], [V(3)])
        self.assertEqual(len(s.calls), 1)                      # 2. sayfa istenmedi

    def test_full_scans_all_pages_and_dedupes(self):
        s = FakeSession({"playlistItems": [{"items": [item(V(3)), item(V(2))], "nextPageToken": "N"},
                                           {"items": [item(V(2)), item(V(1))]}]})
        new = update.fetch_new_videos(s, "k", "PL", {V(2)}, full=True)
        self.assertEqual([v["id"] for v in new], [V(3), V(1)])
        self.assertEqual(len(s.calls), 2)

    def test_run_end_to_end_never_deletes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            cfg = {"channel_handle": "x", "server_base_url": "https://s.test", "group_title": "G"}
            old = {"videos": [{"id": V(1), "title": "Old", "thumbnail": "t", "published_at": "2020", "duration": 5, "description": ""}]}
            (root / "videos.json").write_text(json.dumps(old))
            s = FakeSession({
                "channels": [{"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}],
                "playlistItems": [{"items": [item(V(2), date="2025-05-05T00:00:00Z"), item(V(1))]}],
                "videos": [{"items": [{"id": V(2), "contentDetails": {"duration": "PT2M"}}]}]})
            self.assertEqual(update.run(cfg, "k", s, root=root), (1, 2))
            m3u = (root / "playlist.m3u").read_text()
            self.assertIn(f"https://s.test/play/{V(1)}", m3u)   # eski silinmedi
            self.assertLess(m3u.index(V(2)), m3u.index(V(1)))
            self.assertEqual(update.run(cfg, "k", FakeSession({
                "channels": [{"items": [{"contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}],
                "playlistItems": [{"items": [item(V(2))]}]}), root=root), (0, 2))  # tekrar: yenisi yok

    def test_api_error_hides_key(self):
        s = MagicMock(); s.get.side_effect = update.requests.ConnectionError("https://x?key=SECRET")
        with self.assertRaises(update.ApiError) as c:
            update.api_get(s, "channels", {}, "SECRET")
        self.assertNotIn("SECRET", str(c.exception))


if __name__ == "__main__":
    unittest.main()
