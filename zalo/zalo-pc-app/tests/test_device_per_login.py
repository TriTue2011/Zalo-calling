"""Cài đặt thiết bị RIÊNG cho từng kiểu đăng nhập (web | pc) — mỗi kiểu là một phiên Zalo riêng (14/11/2026).
- tệp cũ (một bộ phẳng) được kế thừa cho cả hai kiểu, rồi hai bên độc lập;
- sửa bộ đang dùng không đụng bộ kia; sửa được bộ kiểu khác bằng `client`; đổi kiểu đăng nhập thì tự theo bộ của kiểu đó;
- độ to mic/loa của cuộc gọi theo kiểu đang đăng nhập.
Run: python3 tests/test_device_per_login.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from zalo_pc_app import devices as dev  # noqa: E402
from zalo_pc_app import protocol  # noqa: E402
from zalo_pc_app import server as app_server  # noqa: E402
from zalo_pc_app.calls import voice  # noqa: E402


def _path():
    return os.path.join(tempfile.mkdtemp(prefix="zalo-perlogin-"), "devices.json")


def test_legacy_flat_file_is_inherited_by_both_then_independent():
    p = _path()
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"mic_url": "rtsp://cam/x", "mic_gain": 140, "echo_guard": True}, fh)
    active = {"c": "pc"}
    st = dev.DeviceStore(p, client_fn=lambda: active["c"])
    assert st.get("pc")["mic_gain"] == 140 and st.get("web")["mic_gain"] == 140 and st.get("web")["mic_url"] == "rtsp://cam/x", "kế thừa tệp cũ cho cả hai kiểu"
    st.update({"mic_gain": 90})                                       # sửa bộ ĐANG DÙNG (pc)
    assert st.get("pc")["mic_gain"] == 90 and st.get("web")["mic_gain"] == 140, "không đụng bộ kia"
    active["c"] = "web"                                                # đổi kiểu đăng nhập
    assert st.get()["mic_gain"] == 140 and st.active_client() == "web", "tự theo bộ của kiểu mới"
    st.update({"speaker_gain": 60}, "pc")                              # sửa bộ của kiểu KHÔNG đang dùng
    assert st.get("pc")["speaker_gain"] == 60 and st.get("web")["speaker_gain"] == 0
    again = dev.DeviceStore(p, client_fn=lambda: "pc")                 # nạp lại từ tệp (định dạng mới, hai bộ)
    raw = json.load(open(p, encoding="utf-8"))
    assert raw["version"] == 2 and set(raw) >= {"web", "pc"}
    assert again.get()["mic_gain"] == 90 and again.get()["speaker_gain"] == 60 and again.get("web")["mic_gain"] == 140


def test_bad_client_and_masking_and_unknown_active_falls_back_to_pc():
    st = dev.DeviceStore(_path(), client_fn=lambda: "")
    assert st.active_client() == "pc"
    try:
        st.get("android")
    except ValueError as e:
        assert "web hoac pc" in str(e)
    else:
        raise AssertionError("client lạ phải bị từ chối")
    st.update({"mic_url": "rtsp://u:matkhau@h/x"}, "web")
    pub = st.public_all()
    assert pub["active"] == "pc" and pub["web"]["mic_url"] == "rtsp://u:***@h/x" and pub["pc"]["mic_url"] == "", "mật khẩu bị che ở cả hai bộ"
    try:
        st.update({"mic_gain": "abc"}, "web")
    except ValueError:
        pass
    else:
        raise AssertionError("giá trị sai bị từ chối như cũ")


def test_call_gain_follows_the_login_client():
    tmp = tempfile.mkdtemp(prefix="zalo-perlogin-state-")
    saved = protocol.client_name()
    try:
        app_server.STATE = app_server.AppState(tmp)
        voice.CALL_GAIN.clear()
        app_server._CALL_DEVICES.clear()
        protocol.set_client("pc")
        app_server.STATE.devices.update({"mic_gain": 150})
        protocol.set_client("web")
        assert voice._gain_of("5", "mic") == 100, "kiểu web chưa đặt: giữ nguyên"
        app_server.STATE.devices.update({"mic_gain": 70})
        assert voice._gain_of("5", "mic") == 70
        protocol.set_client("pc")
        assert voice._gain_of("5", "mic") == 150, "quay lại kiểu pc: bộ của pc"
    finally:
        protocol.set_client(saved)
        voice.CALL_GAIN.clear()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("ALL DEVICE PER LOGIN TESTS PASSED")
