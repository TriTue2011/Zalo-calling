"""Cài đặt ÂM THANH của thiết bị đặt được từ WebUI, dịch vụ HA (set_app_settings / set_default_devices) và biến môi trường — một nguồn duy nhất
(devices.json) nên không lệch nhau (14/11/2026): độ to mic/loa mặc định (%, độc lập với cài đặt của loa R1) và cửa sổ trễ / tỉ lệ chống vọng.
Run: python3 tests/test_device_audio_settings.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from zalo_pc_app import devices as dev_mod  # noqa: E402
from zalo_pc_app import server as app_server  # noqa: E402
from zalo_pc_app.calls import voice  # noqa: E402


def _store():
    return dev_mod.DeviceStore(os.path.join(tempfile.mkdtemp(prefix="zalo-dev-"), "devices.json"))


def _raises(fn, text):
    try:
        fn()
    except ValueError as e:
        assert text in str(e), (text, str(e))
    else:
        raise AssertionError("phải từ chối: %s" % text)


def test_defaults_keep_current_behaviour():
    d = _store().get()
    assert d["mic_gain"] == 0 and d["speaker_gain"] == 0, "0 = giữ nguyên 100%"
    assert d["echo_delay_min_ms"] is None and d["echo_delay_max_ms"] is None and d["echo_ratio"] is None, "None = theo biến môi trường"


def test_gain_update_validates_and_persists():
    s = _store()
    assert s.update({"mic_gain": 150, "speaker_gain": "220"})["mic_gain"] == 150
    assert s.get()["speaker_gain"] == 220
    assert s.update({"mic_gain": 999})["mic_gain"] == 300, "trần 300%"
    assert s.update({"mic_gain": -5})["mic_gain"] == 0, "sàn 0"
    _raises(lambda: s.update({"mic_gain": "abc"}), "mic_gain")
    _raises(lambda: s.update({"speaker_gain": True}), "speaker_gain")
    again = dev_mod.DeviceStore(s._path)
    assert again.get()["speaker_gain"] == 220 and again.get()["mic_gain"] == 0, "đọc lại từ tệp"


def test_echo_window_and_ratio():
    s = _store()
    r = s.update({"echo_delay_min_ms": 400, "echo_delay_max_ms": 800, "echo_ratio": 2.5})
    assert (r["echo_delay_min_ms"], r["echo_delay_max_ms"], r["echo_ratio"]) == (400, 800, 2.5)
    assert s.update({"echo_delay_min_ms": 0, "echo_delay_max_ms": 0})["echo_delay_max_ms"] == 0, "0 và 0: tắt bù trễ"
    r = s.update({"echo_delay_min_ms": "", "echo_delay_max_ms": "", "echo_ratio": ""})
    assert r["echo_delay_min_ms"] is None and r["echo_ratio"] is None, "bỏ trống: trở về theo biến môi trường"
    _raises(lambda: s.update({"echo_delay_min_ms": 800, "echo_delay_max_ms": 400}), "thap < cao")
    _raises(lambda: s.update({"echo_delay_min_ms": 500, "echo_delay_max_ms": 500}), "thap < cao")
    _raises(lambda: s.update({"echo_delay_min_ms": 100, "echo_delay_max_ms": 99999}), "thap < cao")
    _raises(lambda: s.update({"echo_delay_min_ms": "x", "echo_delay_max_ms": 500}), "thap < cao")
    _raises(lambda: s.update({"echo_ratio": 0.1}), "echo_ratio")
    _raises(lambda: s.update({"echo_ratio": "abc"}), "echo_ratio")
    assert s.get()["echo_ratio"] is None, "giá trị sai không làm đổi cài đặt cũ"


def test_echo_params_feed_the_guard():
    assert voice.echo_params({}) == (None, None)
    assert voice.echo_params({"echo_delay_min_ms": 400, "echo_delay_max_ms": 800, "echo_ratio": 3}) == ((0.4, 0.8), 3.0)
    assert voice.echo_params({"echo_delay_min_ms": 0, "echo_delay_max_ms": 0}) == ((0.0, 0.0), None)
    from zalo_pc_app import audio_rx
    g = audio_rx.EchoGuard(delay=(0.4, 0.8), ratio=3.0)
    assert g.delay == (0.4, 0.8) and g.ratio == 3.0


def test_call_gain_falls_back_to_device_default_and_user_wins():
    tmp = tempfile.mkdtemp(prefix="zalo-gain-")
    app_server.STATE = app_server.AppState(tmp)
    voice.CALL_GAIN.clear()
    app_server._CALL_DEVICES.clear()
    assert voice._gain_of("77", "mic") == 100, "chưa cài gì: giữ nguyên"
    app_server.STATE.devices.update({"mic_gain": 150})
    assert voice._gain_of("77", "mic") == 150 and voice._gain_of("77", "speaker") == 100, "mặc định của thiết bị"
    app_server._CALL_DEVICES["77"] = {"mic_gain": 200}
    assert voice._gain_of("77", "mic") == 200, "quy tắc/cuộc gọi thắng mặc định"
    voice.CALL_GAIN["77"] = {"mic": 80}
    assert voice._gain_of("77", "mic") == 80, "nút −/+ của người dùng thắng tất cả"
    voice.CALL_GAIN.clear()
    app_server._CALL_DEVICES.clear()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    print("ALL DEVICE AUDIO SETTINGS TESTS PASSED")
