"""Tải/phát video qua /api/file/download hỗ trợ Range (14/11/2026): MP4 phân mảnh của camera cần đọc theo đoạn, không thì Chrome đo sai thời lượng (WebUI 0:00).
- parse_byte_range: các dạng Range của trình duyệt, nhiều khoảng/rác thì trả cả tệp, ngoài tệp thì 416;
- _send_bytes: 206 + Content-Range đúng đoạn, 200 vẫn báo Accept-Ranges, loại nội dung theo đuôi tệp.
Run: python3 tests/test_byte_range.py"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from zalo_pc_app import server as app_server  # noqa: E402

P = app_server.parse_byte_range


def test_parse_forms():
    assert P("bytes=0-99", 1000) == (0, 99)
    assert P("bytes=500-", 1000) == (500, 999)
    assert P("bytes=-100", 1000) == (900, 999)                    # 100 byte cuối
    assert P("bytes=-5000", 1000) == (0, 999)                     # xin nhiều hơn tệp có: lấy cả tệp
    assert P("bytes=900-5000", 1000) == (900, 999)                # cuối vượt tệp thì cắt về cuối tệp
    assert P("BYTES=0-0", 1000) == (0, 0)


def test_parse_whole_file_cases():
    assert P(None, 1000) is None and P("", 1000) is None
    assert P("bytes=0-1,5-9", 1000) is None, "nhiều khoảng: trả cả tệp, không làm multipart"
    assert P("items=0-9", 1000) is None and P("bytes=a-b", 1000) is None
    assert P("bytes=0-9", 0) is None, "tệp rỗng"


def test_parse_unsatisfiable():
    assert P("bytes=1000-", 1000) is False and P("bytes=2000-2100", 1000) is False
    assert P("bytes=50-10", 1000) is False and P("bytes=-0", 1000) is False


class _Stub:
    def __init__(self, rng=None):
        self.headers = {"Range": rng} if rng else {}
        self.sent, self.status = {}, None
        self.wfile = io.BytesIO()

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


def _send(data, name, rng=None):
    st = _Stub(rng)
    app_server.Handler._send_bytes(st, data, name)
    return st


def test_send_bytes_partial_and_full():
    data = bytes(range(256)) * 4                                  # 1024 byte
    st = _send(data, "clip.mp4", "bytes=10-19")
    assert st.status == 206 and st.sent["Content-Range"] == "bytes 10-19/1024" and st.sent["Content-Length"] == "10"
    assert st.wfile.getvalue() == data[10:20]
    st = _send(data, "clip.mp4")
    assert st.status == 200 and st.sent["Accept-Ranges"] == "bytes" and st.sent["Content-Length"] == "1024"
    assert st.sent["Content-Type"] == "video/mp4", "loại nội dung theo đuôi tệp"
    assert st.wfile.getvalue() == data
    st = _send(data, "x.bin", "bytes=5000-")
    assert st.status == 416 and st.sent["Content-Range"] == "bytes */1024" and st.wfile.getvalue() == b""
    assert _send(data, "tep-la").sent["Content-Type"] == "application/octet-stream"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("ALL BYTE RANGE TESTS PASSED")
