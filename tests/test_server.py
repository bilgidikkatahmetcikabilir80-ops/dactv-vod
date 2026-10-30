import sys, time, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server

VID = "abcdefghijk"
GV = "https://rr1---sn-x.googlevideo.com/videoplayback?expire={}&id=1"


def info(url=None, protocol="https"):
    return {"url": url or GV.format(int(time.time()) + 3600), "protocol": protocol, "http_headers": {"User-Agent": "UA"}}


class FakeUp:
    def __init__(self, status=206, body=b"x" * 10, headers=None):
        self.status_code, self.body = status, body
        self.headers = headers or {"Content-Type": "video/mp4", "Content-Range": "bytes 0-9/100", "Content-Length": "10"}
        self.closed = False
    def iter_content(self, n): yield self.body
    def close(self): self.closed = True


class Tests(unittest.TestCase):
    def setUp(self):
        server._cache.clear()
        server._known.update(ids={VID}, at=time.time(), mtime=None)
        server.REQUIRE_KNOWN, server.STREAM_MODE = True, "proxy"
        self.c = server.app.test_client()
        p = patch.dict("os.environ", {"VIDEOS_URL": "http://unused"}); p.start(); self.addCleanup(p.stop)

    def test_bad_and_unknown_id(self):
        self.assertEqual(self.c.get("/play/..%2Fetc").status_code, 404)
        self.assertEqual(self.c.get("/play/short").status_code, 400)
        self.assertEqual(self.c.get("/play/zzzzzzzzzzz").status_code, 404)

    def test_redirect_mode(self):
        server.STREAM_MODE = "redirect"
        with patch.object(server, "_extract", return_value=info()):
            r = self.c.get(f"/play/{VID}")
        self.assertEqual(r.status_code, 302)
        self.assertIn("googlevideo.com", r.headers["Location"])

    def test_proxy_forwards_range(self):
        with patch.object(server, "_extract", return_value=info()), \
             patch.object(server.requests, "request", return_value=FakeUp()) as rq:
            r = self.c.get(f"/play/{VID}", headers={"Range": "bytes=0-9"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.headers["Content-Range"], "bytes 0-9/100")
        self.assertEqual(r.data, b"x" * 10)
        self.assertEqual(rq.call_args.kwargs["headers"]["Range"], "bytes=0-9")

    def test_cache_and_reresolve_on_403(self):
        calls = []
        def ext(v): calls.append(v); return info()
        ups = [FakeUp(403), FakeUp(200, headers={"Content-Type": "video/mp4"})]
        with patch.object(server, "_extract", side_effect=ext), \
             patch.object(server.requests, "request", side_effect=lambda *a, **k: ups.pop(0)):
            r = self.c.get(f"/play/{VID}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(calls), 2)                        # 403 -> yeniden çözüldü
        with patch.object(server, "_extract", side_effect=ext) as e:
            server.resolve(VID); e.assert_not_called()          # önbellekten

    def test_expired_cache_resolves_again(self):
        server._cache[VID] = {"url": GV.format(1), "headers": {}, "expire": time.time() + 10}
        with patch.object(server, "_extract", return_value=info()) as e:
            server.resolve(VID); e.assert_called_once()

    def test_unsupported_and_foreign_host(self):
        with patch.object(server, "_extract", return_value=info(protocol="m3u8_native")):
            r = self.c.get(f"/play/{VID}")
        self.assertEqual(r.status_code, 422)
        server._cache.clear()
        with patch.object(server, "_extract", return_value=info(url="https://evil.test/x?expire=9999999999")):
            self.assertEqual(self.c.get(f"/play/{VID}").status_code, 502)

    def test_stream_slot_released(self):
        before = server._stream_slots._value
        with patch.object(server, "_extract", return_value=info()), \
             patch.object(server.requests, "request", return_value=FakeUp(200)):
            self.c.get(f"/play/{VID}").data
        self.assertEqual(server._stream_slots._value, before)


if __name__ == "__main__":
    unittest.main()
