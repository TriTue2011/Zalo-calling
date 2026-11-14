"""Đầu-cuối TRONG Home Assistant thật cho ``zalo_call``: gọi dịch vụ bằng ``hass.services.async_call`` (đúng đường của automation /
Công cụ nhà phát triển), app Zalo là máy chủ giả mô phỏng các tuyến thật. Kiểm: yêu cầu app nhận được, bảng chọn mà HA nhìn thấy
(services.yaml), đường AI tự trả lời (định dạng, màu nhấn mạnh, emoji), lỗi.

Chỉ chạy được khi có HA + pytest-homeassistant-custom-component (ảnh ``r1-test`` trên máy chủ); môi trường khác tự bỏ qua:

    python -m pytest tests/test_zalo_call_ha_end_to_end.py -p pytest_homeassistant_custom_component -o asyncio_mode=auto
"""
import os
import sys
import types

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_homeassistant_custom_component")
sys.modules.setdefault("turbojpeg", types.SimpleNamespace(TurboJPEG=object))     # camera của HA nạp thư viện này; không cần thật

import custom_components  # noqa: E402

_REPO_CC = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "custom_components"))
if _REPO_CC not in [os.path.abspath(p) for p in custom_components.__path__]:
    custom_components.__path__.append(_REPO_CC)          # plugin thử của HA đã nạp gói custom_components riêng, che thư mục của kho

from aiohttp import web  # noqa: E402
from homeassistant.core import ServiceCall, SupportsResponse  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers.service import async_get_all_descriptions  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

DOMAIN = "zalo_call"
THREAD = "6643404425553198601"


RULE_TRUSTED = {"reply_ai": True, "agent": "conversation.test", "trusted": True}      # quy tắc tối thiểu cho các test trợ lý trả lời


class FakeZaloApp:
    def __init__(self):
        self.requests: list[dict] = []
        self.runner = None
        self.url = ""
        self.fail_next: tuple[int, dict] | None = None
        self.devices: dict = {}                    # cài đặt thiết bị của app (devices.json giả)
        self.device_posts: list[dict] = []
        self.device_queries: list[str] = []          # chuỗi truy vấn của GET /api/settings/devices (client=web|pc)
        self.login_client = "pc"                      # kiểu đăng nhập app đang dùng

    def last(self, part: str) -> dict:
        return [r for r in self.requests if part in r["path"]][-1]

    async def _any(self, request: web.Request) -> web.Response:
        raw = await request.read()
        body = None
        if raw and request.content_type == "application/json":
            body = await request.json()
        if request.path not in ("/api/status", "/api/calls", "/api/app/login", "/api/settings/devices"):
            self.requests.append({"method": request.method, "path": request.path, "json": body,
                                  "raw": raw if body is None else b"", "ctype": request.content_type})
        if self.fail_next and request.path.startswith("/api/thread"):
            status, payload = self.fail_next
            self.fail_next = None
            return web.json_response(payload, status=status)
        p = request.path
        if p == "/api/app/login":
            return web.json_response({"ok": True})
        if p == "/api/status":
            return web.json_response({"version": "test", "login": True, "sync": "running", "uid": "1", "client": self.login_client})
        if p == "/api/calls":
            return web.json_response({"calls": []})
        if p == "/api/settings/devices":
            if request.method == "POST" and isinstance(body, dict):
                self.device_posts.append(body)
                self.devices.update({k: (None if v == "" else v) for k, v in body.items() if k != "client"})
            else:
                self.device_queries.append(request.query_string)
            return web.json_response({"ok": True, "devices": self.devices, "client": request.query.get("client") or (body or {}).get("client") or self.login_client,
                                      "active": self.login_client})
        if p.startswith("/api/thread/"):
            return web.json_response({"ok": True, "data": {"msgId": "8474"}, "message": {"id": "1", "client_id": "1", "text": (body or {}).get("message", "")}})
        if p == "/api/svc/search_sticker":
            kw = (body or {}).get("keyword")
            data = [] if kw == "khong-co" else [{"sticker_id": 111, "cate_id": 10, "type": 7}, {"sticker_id": 222, "cate_id": 20, "type": 7}]
            return web.json_response({"ok": True, "action": "search_sticker", "data": data})
        if p.startswith("/api/svc/"):
            return web.json_response({"ok": True, "action": p.rsplit("/", 1)[1], "data": {"status": 0}})
        return web.json_response({})

    async def start(self):
        app = web.Application(client_max_size=110 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", self._any)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = "http://127.0.0.1:%d" % site._server.sockets[0].getsockname()[1]

    async def stop(self):
        await self.runner.cleanup()


@pytest.fixture
async def app(socket_enabled):
    a = FakeZaloApp()
    await a.start()
    yield a
    await a.stop()


@pytest.fixture
async def setup(hass, app, enable_custom_integrations, tmp_path):
    hass.config.allowlist_external_dirs.add(str(tmp_path))
    assert await async_setup_component(hass, "homeassistant", {})          # zalo_call phụ thuộc conversation, cần lõi homeassistant trước
    entry = MockConfigEntry(domain=DOMAIN, title="Zalo Call", data={"url": app.url, "username": "u", "password": "p", "webhook_id": "wh-test"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def call(hass, service, data):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, return_response=True)


def uid(hass, entry, platform, key):
    return er.async_get(hass).async_get_entity_id(platform, DOMAIN, "%s_%s" % (entry.entry_id, key))


async def test_send_message_plain_sends_only_type_and_message(hass, setup, app):
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Xin chào"})
    r = app.last("/send")
    assert r["path"] == "/api/thread/%s/send" % THREAD and r["json"] == {"type": "user", "message": "Xin chào"}, "tin thường không kèm trường định dạng"


async def test_send_message_with_format_emphasis_and_emoji_reaches_the_app(hass, setup, app):
    await call(hass, "send_message", {"thread_id": THREAD, "type": "group", "message": "**Nhiệt độ** 29°C", "format": "markdown",
                                      "auto_emphasis": "orange", "emoji": "ok", "auto_emoji": True})
    assert app.last("/send")["json"] == {"type": "group", "message": "**Nhiệt độ** 29°C", "format": "markdown",
                                         "auto_emphasis": "orange", "emoji": "ok", "auto_emoji": True}


async def test_send_message_mentions_reach_the_app_and_plain_messages_do_not_carry_them(hass, setup, app):
    mentions = [{"uid": "123456", "name": "Nam"}, {"uid": "-1"}]
    await call(hass, "send_message", {"thread_id": THREAD, "type": "group", "message": "Chào @Nam và @All", "mentions": mentions})
    assert app.last("/send")["json"] == {"type": "group", "message": "Chào @Nam và @All", "mentions": mentions}
    await call(hass, "send_message", {"thread_id": THREAD, "type": "group", "message": "Chào cả nhóm"})
    assert "mentions" not in app.last("/send")["json"], "tin không nhắc ai thì không kèm trường mentions"


async def test_send_file_and_link_and_reaction_and_undo_reach_the_app(hass, setup, app, tmp_path):
    f = tmp_path / "bao-cao.txt"
    f.write_bytes(b"NOI-DUNG")
    await call(hass, "send_file", {"thread_id": THREAD, "type": "user", "file": str(f)})
    r = app.last("/sendfile")
    assert r["path"] == "/api/thread/%s/sendfile" % THREAD and r["ctype"] == "multipart/form-data" and b"NOI-DUNG" in r["raw"] and b"bao-cao.txt" in r["raw"]
    await call(hass, "send_link", {"thread_id": THREAD, "type": "user", "link": "https://example.com", "message": "xem"})
    assert app.last("/sendlink")["json"] == {"type": "user", "link": "https://example.com", "message": "xem"}
    await call(hass, "add_reaction", {"thread_id": THREAD, "type": "user", "icon": "heart", "msg_id": "8474", "cli_msg_id": "1794325228249"})
    assert app.last("/api/svc/add_reaction")["json"]["icon"] == "heart"
    await call(hass, "undo_message", {"thread_id": THREAD, "type": "user", "msg_id": "8474", "cli_msg_id": "1794325228249"})
    assert app.last("/api/svc/undo_message")["json"]["msg_id"] == "8474"


async def test_path_outside_allowlist_is_refused_before_reaching_the_app(hass, setup, app):
    n = len(app.requests)
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_file", {"thread_id": THREAD, "type": "user", "file": "/etc/passwd"})
    assert len(app.requests) == n


async def test_app_error_becomes_a_home_assistant_error(hass, setup, app):
    app.fail_next = (502, {"error": "response inner error_code=114 Tham số không hợp lệ"})
    with pytest.raises(HomeAssistantError, match="114"):
        await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x"})


async def test_home_assistant_sees_the_pickers_in_the_service_descriptions(hass, setup):
    d = (await async_get_all_descriptions(hass))[DOMAIN]
    f = d["send_message"]["fields"]
    emoji_opts = f["emoji"]["selector"]["select"]["options"]
    assert len(emoji_opts) >= 30 and {"value": "ok", "label": "✅  ok"} in emoji_opts and f["emoji"]["selector"]["select"]["custom_value"] is True
    assert f["format"]["selector"]["select"]["options"] == ["plain", "markdown"]
    assert f["auto_emphasis"]["selector"]["select"]["options"] == ["red", "orange", "yellow", "green"]
    assert "boolean" in f["auto_emoji"]["selector"]
    icons = [o["value"] for o in d["add_reaction"]["fields"]["icon"]["selector"]["select"]["options"]]
    assert len(icons) >= 50 and {"like", "heart", "haha", "wow", "cry", "angry"} <= set(icons)


class _Agent(list):
    """Trợ lý hội thoại giả: ghi lại mọi lượt gọi; ``reply`` cho lượt trả lời, ``styler_reply`` cho lượt định dạng riêng, ``fail`` để mô phỏng lỗi."""
    reply = "**Nhiệt độ** 29°C\n- Cửa mở lúc 15:30"
    styler_reply = None
    fail = False


@pytest.fixture
def fake_agent(hass):
    agent = _Agent()

    async def process(call: ServiceCall):
        agent.append(call.data)
        if agent.fail:
            raise RuntimeError("agent hỏng")
        out = agent.styler_reply if "BỘ ĐỊNH DẠNG" in call.data["text"] else agent.reply
        return {"response": {"speech": {"plain": {"speech": out}}}}
    hass.services.async_register("conversation", "process", process, supports_response=SupportsResponse.ONLY)
    return agent


async def test_ai_reply_is_formatted_as_markdown_by_default_and_follows_the_new_switches(hass, setup, app, fake_agent):
    from custom_components.zalo_call.ai import async_ai_reply
    from custom_components.zalo_call import ai as ai_mod
    ai_mod.MIN_INTERVAL = 0
    rt = hass.data[DOMAIN][setup.entry_id]
    await rt.defaults.async_set()
    event = {"content": "trời nay sao", "thread_id": THREAD, "thread_type": "user", "is_self": False}
    await async_ai_reply(hass, setup, event, RULE_TRUSTED)
    assert fake_agent and app.last("/send")["json"] == {"type": "user", "message": "**Nhiệt độ** 29°C\n- Cửa mở lúc 15:30", "format": "markdown"}, \
        "mặc định: markdown (hết dấu ** thô), không tự đổi màu/emoji"
    # bật qua đúng các thực thể trên thiết bị (như người dùng bấm trong giao diện)
    sel = uid(hass, setup, "select", "ai_emphasis")
    sw = uid(hass, setup, "switch", "text_auto_emoji")
    fmt = uid(hass, setup, "select", "ai_format")
    assert sel and sw and fmt, "thiếu thực thể điều khiển AI: %s %s %s" % (sel, sw, fmt)
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "orange"}, blocking=True)
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    await async_ai_reply(hass, setup, event, RULE_TRUSTED)
    # emoji bật + AI tự định dạng trong cùng lượt (có **) → không chồng auto_emoji của quy tắc cứng
    assert app.last("/send")["json"] == {"type": "user", "message": "**Nhiệt độ** 29°C\n- Cửa mở lúc 15:30", "format": "markdown",
                                         "auto_emphasis": "orange"}
    await hass.services.async_call("select", "select_option", {"entity_id": fmt, "option": "plain"}, blocking=True)
    await hass.services.async_call("select", "select_option", {"entity_id": sel, "option": "off"}, blocking=True)
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    await async_ai_reply(hass, setup, event, RULE_TRUSTED)
    assert app.last("/send")["json"] == {"type": "user", "message": "**Nhiệt độ** 29°C\n- Cửa mở lúc 15:30", "format": "plain"}


async def test_messages_follow_rules_no_rule_means_nothing_group_rule_replies_in_the_group_own_messages_ignored(hass, setup, app, fake_agent):
    from custom_components.zalo_call import ai as ai_mod
    ai_mod.MIN_INTERVAL = 0
    rt = hass.data[DOMAIN][setup.entry_id]
    group = {"content": "x", "thread_id": "9", "thread_type": "group", "is_self": False, "contact_status": "group"}
    own = {"content": "x", "thread_id": THREAD, "thread_type": "user", "is_self": True, "contact_status": "friend"}
    user = {"content": "x", "thread_id": THREAD, "thread_type": "user", "is_self": False, "contact_status": "friend"}
    n = len(app.requests)
    for ev in (group, own, user):                               # KHÔNG CÓ QUY TẮC: không trợ lý, không gửi gì
        await ai_mod.async_handle_message(hass, setup, ev)
    assert not fake_agent and len(app.requests) == n
    rules = {"threads": {}, "groups": {"reply_ai": True, "agent": "conversation.x", "trusted": True}, "contacts": {}, "strangers": {}}
    await rt.defaults.async_set(rules=rules)
    await ai_mod.async_handle_message(hass, setup, own)         # tin tự gửi: bỏ qua dù có quy tắc
    await ai_mod.async_handle_message(hass, setup, user)        # người quen: chỉ có quy tắc nhóm -> vẫn không làm gì
    assert not fake_agent
    await ai_mod.async_handle_message(hass, setup, group)       # nhóm: trả lời vào NHÓM
    assert fake_agent and app.last("/api/thread/9/send")["json"]["type"] == "group", app.requests[-1]


async def test_send_voice_service_posts_the_audio_file_as_multipart_to_the_app(hass, setup, app, tmp_path):
    f = tmp_path / "tts.mp3"
    f.write_bytes(b"ID3-GIA-MP3")
    await call(hass, "send_voice", {"thread_id": THREAD, "type": "user", "file": str(f)})
    r = app.last("/sendvoice")
    assert r["path"] == "/api/thread/%s/sendvoice" % THREAD and r["ctype"] == "multipart/form-data" and b"ID3-GIA-MP3" in r["raw"], r


async def test_translate_message_service_sends_fields_without_params(hass, setup, app):
    await call(hass, "translate_message", {"msgId": "8474", "threadId": THREAD, "type": 0, "srcLang": "vi", "desLang": "en", "text": "Xin chào"})
    j = app.last("/api/svc/translate_message")["json"]
    assert j["msgId"] == "8474" and j["srcLang"] == "vi" and j["desLang"] == "en" and j["text"] == "Xin chào" and "params" not in j, j


async def test_reaction_icon_picker_lists_heart_for_the_service(hass, setup, app):
    await call(hass, "add_reaction", {"thread_id": THREAD, "type": "user", "icon": "heart", "msg_id": "1", "cli_msg_id": "2"})
    j = app.last("/api/svc/add_reaction")["json"]
    assert j["icon"] == "heart" and "r_icon" not in j and "r_type" not in j, j


async def test_send_sticker_by_keyword_searches_then_sends_and_still_accepts_explicit_ids(hass, setup, app):
    await call(hass, "send_sticker", {"thread_id": THREAD, "type": "user", "keyword": "cảm ơn"})
    assert app.last("/api/svc/search_sticker")["json"] == {"keyword": "cảm ơn", "limit": 20}
    j = app.last("/api/svc/send_sticker")["json"]
    assert j["sticker_id"] == 111 and j["cate_id"] == 10 and j["sticker_type"] == 7 and "keyword" not in j and "pick" not in j, j
    await call(hass, "send_sticker", {"thread_id": THREAD, "type": "user", "keyword": "haha", "pick": "random"})
    assert app.last("/api/svc/send_sticker")["json"]["sticker_id"] in (111, 222)
    n = len([r for r in app.requests if "search_sticker" in r["path"]])
    await call(hass, "send_sticker", {"thread_id": THREAD, "type": "user", "sticker_id": 5, "cate_id": 6, "sticker_type": 7})
    assert len([r for r in app.requests if "search_sticker" in r["path"]]) == n, "có sticker_id thì không tìm kiếm"
    assert app.last("/api/svc/send_sticker")["json"]["sticker_id"] == 5
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_sticker", {"thread_id": THREAD, "type": "user", "keyword": "khong-co"})
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_sticker", {"thread_id": THREAD, "type": "user"})


async def _select(hass, entry, key, option):
    ent = uid(hass, entry, "select", key)
    assert ent, "thiếu ô chọn %s" % key
    await hass.services.async_call("select", "select_option", {"entity_id": ent, "option": option}, blocking=True)


async def test_text_style_defaults_live_on_the_device_and_apply_to_send_message(hass, setup, app):
    keys = ("text_size", "text_color", "text_bold", "text_italic", "text_underline", "text_heading", "text_auto_color")
    assert all(uid(hass, setup, "select", k) for k in keys), "đủ 7 ô cài đặt kiểu chữ trên thiết bị"
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x"})
    assert "style" not in app.last("/send")["json"], "mặc định đều off thì không gửi style"
    await _select(hass, setup, "text_heading", "auto")
    await _select(hass, setup, "text_color", "auto")
    await _select(hass, setup, "text_auto_color", "orange")
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Báo cáo\nNhiệt độ 29°C"})
    assert app.last("/send")["json"]["style"] == {"size": "off", "color": "auto", "bold": "off", "italic": "off", "underline": "off",
                                                  "heading": "auto", "auto_color": "orange"}, app.last("/send")["json"]


async def test_send_message_fields_override_the_device_defaults_per_message(hass, setup, app):
    await _select(hass, setup, "text_bold", "on")
    await _select(hass, setup, "text_color", "red")
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "a", "bold": "off", "font_size": "big", "text_color": "auto", "auto_color": "green"})
    st = app.last("/send")["json"]["style"]
    assert st["bold"] == "off" and st["size"] == "big" and st["color"] == "auto" and st["auto_color"] == "green", st
    j = app.last("/send")["json"]
    assert not {"font_size", "text_color", "bold", "italic", "underline", "heading", "auto_color"} & set(j), "trường ghi đè không đi nguyên sang app: " + str(j)
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "b"})
    st = app.last("/send")["json"]["style"]
    assert st["bold"] == "on" and st["color"] == "red" and st["size"] == "off", st                  # tin sau lại theo mặc định


async def test_send_message_rejects_a_bad_style_value_before_reaching_the_app(hass, setup, app):
    n = len(app.requests)
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x", "font_size": "huge"})
    assert len(app.requests) == n


async def test_ai_reply_uses_the_device_text_style_defaults(hass, setup, app, fake_agent):
    from custom_components.zalo_call.ai import async_ai_reply
    from custom_components.zalo_call import ai as ai_mod
    ai_mod.MIN_INTERVAL = 0
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set()
    await _select(hass, setup, "text_bold", "auto")
    await async_ai_reply(hass, setup, {"content": "hi", "thread_id": THREAD, "thread_type": "user", "is_self": False}, RULE_TRUSTED)
    j = app.last("/send")["json"]
    assert j["format"] == "markdown" and j["style"]["bold"] == "off", j            # AI đã tự định dạng trong cùng lượt (có **) → bỏ quy tắc cứng của mục auto
    assert "[Chỉ dẫn định dạng" in fake_agent[-1]["text"] and len(fake_agent) == 1, "nhúng chỉ dẫn vào CHÍNH lượt hỏi, không gọi thêm"


async def test_picker_lists_for_the_text_style_fields_in_the_service_description(hass, setup):
    f = (await async_get_all_descriptions(hass))[DOMAIN]["send_message"]["fields"]
    assert f["font_size"]["selector"]["select"]["options"] == ["off", "small", "big", "auto"]
    assert f["text_color"]["selector"]["select"]["options"] == ["off", "auto", "red", "orange", "yellow", "green"]
    for k in ("bold", "italic", "underline", "heading"):
        assert f[k]["selector"]["select"]["options"] == ["off", "on", "auto"], k
    assert f["auto_color"]["selector"]["select"]["options"] == ["red", "orange", "yellow", "green"]


async def test_auto_emoji_switch_on_the_device_is_the_default_and_the_field_overrides_it(hass, setup, app):
    sw = uid(hass, setup, "switch", "text_auto_emoji")
    assert sw, "thiếu công tắc tự thêm emoji trên thiết bị"
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Cảnh báo"})
    assert "auto_emoji" not in app.last("/send")["json"], "công tắc tắt (mặc định): không gửi auto_emoji"
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Cảnh báo"})
    assert app.last("/send")["json"]["auto_emoji"] is True, "công tắc bật: mọi tin tự thêm emoji"
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Cảnh báo", "auto_emoji": False})
    assert "auto_emoji" not in app.last("/send")["json"], "trường auto_emoji=false ghi đè công tắc bật"
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Cảnh báo", "auto_emoji": True})
    assert app.last("/send")["json"]["auto_emoji"] is True, "trường auto_emoji=true ghi đè công tắc tắt"


async def test_ai_reply_follows_the_auto_emoji_switch(hass, setup, app, fake_agent):
    from custom_components.zalo_call.ai import async_ai_reply
    from custom_components.zalo_call import ai as ai_mod
    ai_mod.MIN_INTERVAL = 0
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set(text_auto_emoji=True)
    fake_agent.reply = "Cảnh báo cửa mở"                      # trợ lý không tự thêm emoji → quy tắc tức thì ở app lo
    await async_ai_reply(hass, setup, {"content": "hi", "thread_id": THREAD, "thread_type": "user", "is_self": False}, RULE_TRUSTED)
    assert app.last("/send")["json"]["auto_emoji"] is True


async def _ai_event(hass, setup):
    from custom_components.zalo_call import ai as ai_mod
    ai_mod.MIN_INTERVAL = 0
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set()
    return {"content": "hi", "thread_id": THREAD, "thread_type": "user", "is_self": False}


async def test_ai_reply_inline_hint_when_the_agent_formats_it_no_extra_call_and_no_double_styling(hass, setup, app, fake_agent):
    from custom_components.zalo_call.ai import async_ai_reply
    ev = await _ai_event(hass, setup)
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set(text_heading="auto", text_color="auto", text_auto_emoji=True)
    fake_agent.reply = "🚨 **Cảnh báo** cửa mở"
    await async_ai_reply(hass, setup, ev, RULE_TRUSTED)
    assert len(fake_agent) == 1 and "BỘ ĐỊNH DẠNG" not in fake_agent[0]["text"] and "[Chỉ dẫn định dạng" in fake_agent[0]["text"]
    j = app.last("/send")["json"]
    assert j["format"] == "markdown" and j["style"]["heading"] == "off" and j["style"]["color"] == "off" and "auto_emoji" not in j and "ai_markup" not in j, j


async def test_ai_reply_when_the_agent_ignores_the_hint_falls_back_to_instant_rules(hass, setup, app, fake_agent):
    from custom_components.zalo_call.ai import async_ai_reply
    ev = await _ai_event(hass, setup)
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set(text_color="auto", text_auto_emoji=True)
    fake_agent.reply = "Nhiệt độ 29 độ"                         # không dấu định dạng, không emoji
    await async_ai_reply(hass, setup, ev, RULE_TRUSTED)
    assert len(fake_agent) == 1, "không gọi thêm lần nào (nhanh)"
    j = app.last("/send")["json"]
    assert j["style"]["color"] == "auto" and j["auto_emoji"] is True and "ai_markup" not in j, j


async def test_ai_reply_inline_can_be_switched_off(hass, setup, app, fake_agent):
    from custom_components.zalo_call.ai import async_ai_reply
    ev = await _ai_event(hass, setup)
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set(text_color="auto", text_ai_inline=False)
    await async_ai_reply(hass, setup, ev, RULE_TRUSTED)
    assert "[Chỉ dẫn định dạng" not in fake_agent[0]["text"]


async def test_send_message_auto_uses_instant_rules_without_calling_the_agent_by_default(hass, setup, app, fake_agent):
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set(text_heading="auto")
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Báo cáo nhà hôm nay\nNhiệt độ 29°C trong phòng khách"})
    assert not fake_agent, "mặc định không gọi AI (không chậm thêm)"
    j = app.last("/send")["json"]
    assert j["style"]["heading"] == "auto" and "ai_markup" not in j, j


async def test_send_message_ai_style_asks_the_agent_once_and_passes_its_markup(hass, setup, app, fake_agent):
    text = "Cảnh báo: cửa chính mở lúc 15:30"
    fake_agent.styler_reply = "```\n🚨 **Cảnh báo:** {red}cửa chính mở{/} lúc 15:30\n```"
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": text, "text_color": "auto", "auto_emoji": True, "ai_style": True})
    assert len(fake_agent) == 1 and "BỘ ĐỊNH DẠNG" in fake_agent[0]["text"] and text in fake_agent[0]["text"]
    assert "emoji" in fake_agent[0]["text"] and "{red}" in fake_agent[0]["text"]
    assert fake_agent[0]["conversation_id"].startswith("zalo_call_style_")
    j = app.last("/send")["json"]
    assert j["ai_markup"] == "🚨 **Cảnh báo:** {red}cửa chính mở{/} lúc 15:30" and j["message"] == text and "ai_style" not in j, j
    # công tắc trên thiết bị bật = mặc định, trường ai_style=false ghi đè
    await hass.data[DOMAIN][setup.entry_id].defaults.async_set(text_ai_smart=True)
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": text, "text_color": "auto", "ai_style": False})
    assert len(fake_agent) == 1 and "ai_markup" not in app.last("/send")["json"]
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": text, "text_color": "auto"})
    assert len(fake_agent) == 2, "công tắc bật: dùng AI khi không có trường ghi đè"


async def test_send_message_ai_style_skips_short_texts_and_survives_agent_failure(hass, setup, app, fake_agent):
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Xong rồi", "text_color": "auto", "ai_style": True})
    assert not fake_agent, "tin ngắn: khỏi nhờ AI (nhanh hơn)"
    fake_agent.fail = True
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Cảnh báo: cửa chính mở lúc 15:30", "text_color": "auto", "ai_style": True})
    j = app.last("/send")["json"]
    assert j["message"].startswith("Cảnh báo") and "ai_markup" not in j and j["style"]["color"] == "auto", "agent lỗi: vẫn gửi, dùng quy tắc tức thì"


async def test_ai_style_prompt_only_lists_the_aspects_set_to_auto():
    from custom_components.zalo_call import styler
    p = styler.build_prompt("x", ["color", "emoji"])
    assert "{red}" in p and "emoji Unicode" in p and "**chữ đậm**" not in p and "__chữ gạch chân__" not in p
    assert styler.auto_aspects({"color": "auto", "bold": "on", "heading": "auto"}, True) == ["heading", "color", "emoji"]
    assert styler.auto_aspects({"color": "red"}, False) == []
    assert styler.looks_marked("**a**") and styler.looks_marked("🚨 x") and not styler.looks_marked("a thường")


# ---- send_video: video THẬT, TẢI LÊN Zalo qua app như zalo_bot cũ (14/11): không đưa địa chỉ HA (hết hạn, tên tệp bị ghi đè), ảnh bìa là khung đầu của chính video ----------
def _form_value(raw: bytes, name: str):
    import re
    m = re.search(rb'name="%s"\r\n\r\n(.*?)\r\n' % name.encode(), raw)
    return m.group(1).decode() if m else None


async def test_send_video_uploads_the_file_with_caption_and_ttl_never_a_public_address(hass, setup, app, tmp_path):
    f = tmp_path / "go2rtc_phong_khach_video.mp4"
    f.write_bytes(b"VIDEO-BYTES-1")
    (tmp_path / "go2rtc_phong_khach_thumb.jpg").write_bytes(b"ANH-BIA-CU")                 # ảnh cũ nằm cạnh: KHÔNG được tự nhặt làm bìa
    await call(hass, "send_video", {"thread_id": THREAD, "type": "group", "file": str(f), "message": "🔔 Phát hiện chuyển động!", "ttl": 60000})
    assert not [x for x in app.requests if x["path"].endswith("/sendvideo")], "không gửi địa chỉ video"
    r = app.last("/sendfile")
    assert r["path"] == "/api/thread/%s/sendfile" % THREAD and r["ctype"] == "multipart/form-data", r
    raw = r["raw"]
    assert b"VIDEO-BYTES-1" in raw and b'filename="go2rtc_phong_khach_video.mp4"' in raw, "đúng nội dung và tên video"
    assert _form_value(raw, "desc") == "🔔 Phát hiện chuyển động!" and _form_value(raw, "ttl") == "60000" and _form_value(raw, "type") == "group"
    assert b"ANH-BIA-CU" not in raw and b'name="thumb"' not in raw, "bìa do app tách từ khung hình đầu của video, không lấy ảnh cũ cạnh tệp"


async def test_send_video_sends_the_explicit_thumbnail_as_its_own_part(hass, setup, app, tmp_path):
    f, t = tmp_path / "clip.mp4", tmp_path / "bia.jpg"
    f.write_bytes(b"V")
    t.write_bytes(b"ANH-BIA-MOI")
    await call(hass, "send_video", {"thread_id": THREAD, "type": "user", "file": str(f), "thumbnail": str(t)})
    raw = app.last("/sendfile")["raw"]
    assert b'name="thumb"' in raw and b"ANH-BIA-MOI" in raw, "ảnh bìa tường minh đi kèm để app dùng thay khung hình đầu"
    assert _form_value(raw, "desc") is None, "không chú thích thì không gửi trường desc"


async def test_send_video_without_extension_still_goes_as_video(hass, setup, app, tmp_path):
    f = tmp_path / "clip-khong-duoi"
    f.write_bytes(b"V")
    await call(hass, "send_video", {"thread_id": THREAD, "type": "user", "file": str(f)})
    assert b'filename="clip-khong-duoi.mp4"' in app.last("/sendfile")["raw"], "app nhận video theo đuôi tên tệp"


# ---- type kiểu zalo_bot: 0/1 lẫn user/group, không phân biệt hoa thường (14/11) ---------------------------------------------------------
@pytest.mark.parametrize("given,want", [("0", "user"), ("1", "group"), (0, "user"), (1, "group"), ("user", "user"), ("group", "group"),
                                        ("Group", "group"), (" GROUP ", "group")])
async def test_type_accepts_zalo_bot_values_and_normalises(hass, setup, app, given, want):
    await call(hass, "send_message", {"thread_id": THREAD, "type": given, "message": "x"})
    assert app.last("/send")["json"]["type"] == want


async def test_type_rejects_garbage_with_a_clear_message(hass, setup):
    import voluptuous as vol
    with pytest.raises(vol.Invalid, match="user hoặc group"):
        await call(hass, "send_message", {"thread_id": THREAD, "type": "2", "message": "x"})


async def test_type_is_normalised_on_other_services_too(hass, setup, app):
    await call(hass, "undo_message", {"thread_id": THREAD, "type": "1", "msg_id": "5", "cli_msg_id": "6"})
    assert app.last("/api/svc/undo_message")["json"]["type"] == "group"


async def test_poll_detail_and_lock_need_only_the_poll_id_like_zalo_bot(hass, setup, app):
    await call(hass, "get_poll_detail", {"poll_id": "123"})
    await call(hass, "lock_poll", {"poll_id": "123"})
    assert app.last("/api/svc/get_poll_detail")["json"]["poll_id"] == "123"
    assert app.last("/api/svc/lock_poll")["json"]["poll_id"] == "123"


# ---- send_images_to_*: nhiều ảnh MỘT LÚC thành album, không gửi từng ảnh rời (13/11) ----------------------------------------------------
async def test_send_images_to_group_sends_one_album_in_order_and_gif_apart(hass, setup, app, tmp_path):
    files = []
    for n in ("a.jpg", "b.jpg", "c.png", "anim.gif"):
        f = tmp_path / n
        f.write_bytes(b"IMG-" + n.encode())
        files.append(str(f))
    await call(hass, "send_images_to_group", {"thread_id": THREAD, "files": ",".join(files)})
    reqs = [r for r in app.requests if r["path"].endswith("/sendfile")]
    assert len(reqs) == 4, [r["path"] for r in app.requests]
    layout = {_form_value(r["raw"], "groupLayoutId") for r in reqs[:3]}
    assert len(layout) == 1 and None not in layout, layout
    assert [_form_value(r["raw"], "idInGroup") for r in reqs[:3]] == ["0", "1", "2"], "ảnh phải đi đúng thứ tự"
    assert {_form_value(r["raw"], "totalItemInGroup") for r in reqs[:3]} == {"3"} and {_form_value(r["raw"], "isGroupLayout") for r in reqs[:3]} == {"1"}
    assert _form_value(reqs[3]["raw"], "groupLayoutId") is None, "GIF không vào album"
    assert b"IMG-a.jpg" in reqs[0]["raw"] and b"IMG-c.png" in reqs[2]["raw"], "đúng thứ tự tệp"


async def test_send_images_to_user_with_one_image_is_not_an_album(hass, setup, app, tmp_path):
    f = tmp_path / "one.jpg"
    f.write_bytes(b"ONE")
    await call(hass, "send_images_to_user", {"thread_id": THREAD, "files": str(f)})
    r = app.last("/sendfile")
    assert _form_value(r["raw"], "groupLayoutId") is None and _form_value(r["raw"], "kind") == "photo", r["raw"][:200]


async def test_send_images_reads_everything_before_sending_so_a_bad_path_sends_nothing(hass, setup, app, tmp_path):
    ok = tmp_path / "ok.jpg"
    ok.write_bytes(b"OK")
    before = len([r for r in app.requests if r["path"].endswith("/sendfile")])
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_images_to_group", {"thread_id": THREAD, "files": "%s,%s" % (ok, tmp_path / "khong-co.jpg")})
    assert len([r for r in app.requests if r["path"].endswith("/sendfile")]) == before, "không được gửi nửa album"


# ---- những chỗ zalo_bot cũ có mà zalo_call thiếu (13/11): tên trường cũ, ttl, quote, chú thích, TTS, media-source, owner_id, từ khoá sticker ------
def _sent(app, suffix):
    return [r for r in app.requests if r["path"].endswith(suffix)]


async def test_zalo_bot_field_names_still_work_and_ttl_reaches_the_app(hass, setup, app, tmp_path):
    f = tmp_path / "a.jpg"
    f.write_bytes(b"IMG-OLD-NAME")
    await call(hass, "send_image", {"thread_id": THREAD, "type": "user", "image_path": str(f), "account_selection": "tai-khoan-cu"})
    assert b"IMG-OLD-NAME" in _sent(app, "/sendfile")[-1]["raw"]
    v, t = tmp_path / "v.mp4", tmp_path / "t.jpg"
    v.write_bytes(b"VID-OLD-NAME")
    t.write_bytes(b"THUMB-OLD-NAME")
    await call(hass, "send_video", {"thread_id": THREAD, "type": "group", "video_path_or_url": str(v), "thumbnail_url": str(t), "ttl": 86400000})
    raw = _sent(app, "/sendfile")[-1]["raw"]
    assert b"VID-OLD-NAME" in raw and b"THUMB-OLD-NAME" in raw and b'name="thumb"' in raw and _form_value(raw, "ttl") == "86400000", "tên cũ video_path_or_url / thumbnail_url vẫn chạy"
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "Xin chào", "ttl": 3600000})
    assert _sent(app, "/send")[-1]["json"]["ttl"] == 3600000
    await call(hass, "send_link", {"thread_id": THREAD, "type": "user", "link": "https://example.test", "thumbnail": "https://example.test/i.jpg", "ttl": 5000})
    j = _sent(app, "/sendlink")[-1]["json"]
    assert j["thumbnail"] == "https://example.test/i.jpg" and j["ttl"] == 5000, j


async def test_send_message_quote_goes_through_quote_message_and_needs_the_ids(hass, setup, app):
    q = {"content": "tin gốc", "uidFrom": "111", "msgId": "8474", "cliMsgId": "99", "msgType": "webchat", "ts": 1794000000000}
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "trả lời", "quote": q, "ttl": 1000})
    j = app.last("/api/svc/quote_message")["json"]
    assert (j["text"], j["qmsg_owner"], j["msg_id"], j["qmsg_cli_id"], j["qmsg"], j["qmsg_type"], j["ttl"]) == ("trả lời", "111", "8474", "99", "tin gốc", 1, 1000), j
    assert j["thread_id"] == THREAD and j["type"] == "user" and j["qmsg_ts"] == 1794000000000, j
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x", "quote": {"content": "thiếu mã"}})
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x", "quote": "không phải JSON"})


async def test_captions_and_ttl_for_single_image_and_other_files(hass, setup, app, tmp_path):
    img, doc = tmp_path / "p.jpg", tmp_path / "d.pdf"
    img.write_bytes(b"JPG")
    doc.write_bytes(b"PDF")
    await call(hass, "send_image", {"thread_id": THREAD, "type": "group", "file": str(img), "message": "Có người", "ttl": 86400000})
    r = _sent(app, "/sendfile")[-1]
    assert _form_value(r["raw"], "desc") == "Có người" and _form_value(r["raw"], "ttl") == "86400000", "ảnh đơn mang chú thích theo ảnh"
    n = len(app.requests)
    await call(hass, "send_file", {"thread_id": THREAD, "type": "user", "file": str(doc), "message": "Báo cáo"})
    new = app.requests[n:]
    assert [x["path"].rsplit("/", 1)[1] for x in new] == ["send", "sendfile"], "tệp khác: tin chữ đi TRƯỚC rồi mới tới tệp (như zca-js)"
    assert new[0]["json"]["message"] == "Báo cáo" and b"PDF" in new[1]["raw"]


async def test_send_images_ttl_reaches_every_photo_of_the_album(hass, setup, app, tmp_path):
    files = []
    for n in ("a.jpg", "b.jpg"):
        f = tmp_path / n
        f.write_bytes(b"X" + n.encode())
        files.append(str(f))
    await call(hass, "send_images_to_group", {"thread_id": THREAD, "files": ",".join(files), "ttl": 7200000})
    reqs = _sent(app, "/sendfile")[-2:]
    assert [_form_value(r["raw"], "ttl") for r in reqs] == ["7200000", "7200000"] and [_form_value(r["raw"], "idInGroup") for r in reqs] == ["0", "1"]


async def test_send_voice_reads_text_with_tts_and_accepts_media_source(hass, setup, app, monkeypatch):
    import types
    import custom_components.zalo_call.services as S
    seen = {}

    async def fake_tts(_hass, message, engine=None, language=None, options=None):
        seen["args"] = (message, engine, language, options)
        return "mp3", b"VOICE-TTS"
    monkeypatch.setattr(S, "async_tts_audio", fake_tts)
    await call(hass, "send_voice", {"thread_id": THREAD, "type": "user", "message": "Có người ở cửa", "tts_entity": "tts.edge", "language": "vi", "file": "/bo/qua.mp3"})
    r = _sent(app, "/sendvoice")[-1]
    assert b"VOICE-TTS" in r["raw"] and b'filename="tts.mp3"' in r["raw"], r["raw"][:200]
    assert seen["args"] == ("Có người ở cửa", "tts.edge", "vi", None), "có chữ thì chữ thắng tệp, như zalo_bot cũ"
    import homeassistant.components.media_source as media_source

    async def fake_resolve(_hass, src, _target):
        assert src == "media-source://media_source/local/chuong.mp3", src
        return types.SimpleNamespace(url="%s/audio/chuong.mp3" % app.url, mime_type="audio/mpeg")
    monkeypatch.setattr(media_source, "async_resolve_media", fake_resolve)
    await call(hass, "send_voice", {"thread_id": THREAD, "type": "user", "file": "media-source://media_source/local/chuong.mp3"})
    assert b'filename="chuong.mp3"' in _sent(app, "/sendvoice")[-1]["raw"]
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_voice", {"thread_id": THREAD, "type": "user"})
    async def boom(*_a, **_k):
        raise RuntimeError("không có thực thể TTS")
    monkeypatch.setattr(S, "async_tts_audio", boom)
    with pytest.raises(ServiceValidationError):
        await call(hass, "send_voice", {"thread_id": THREAD, "type": "user", "message": "x"})


async def test_get_stickers_query_delete_owner_and_group_id_alias(hass, setup, app):
    await call(hass, "get_stickers", {"query": "haha"})
    assert app.last("/api/svc/search_sticker")["json"] == {"keyword": "haha", "limit": 20}
    await call(hass, "delete_message", {"thread_id": THREAD, "type": "group", "msg_id": "1", "cli_msg_id": "2", "uid_from": "777"})
    assert app.last("/api/svc/delete_message")["json"]["owner_id"] == "777"
    await call(hass, "get_group_info", {"group_id": "555"})
    assert app.last("/api/svc/get_group_info")["json"]["thread_id"] == "555"


async def test_unknown_keys_are_reported_not_silently_dropped(hass, setup, app, caplog):
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x", "khoa_la": 1})
    assert "bỏ qua khoá không có trong dịch vụ này: khoa_la" in caplog.text, caplog.text[-300:]
    caplog.clear()
    await call(hass, "send_message", {"thread_id": THREAD, "type": "user", "message": "x", "account_selection": "cu", "ttl": 5})
    assert "bỏ qua khoá" not in caplog.text, "account_selection và ttl là khoá hợp lệ/được bỏ qua êm"


# ---- cài đặt ÂM THANH của thiết bị: HA ghi thẳng vào app (một nguồn với WebUI), thực thể số đọc lại từ app (14/11) -----------------------
async def test_set_default_devices_writes_audio_settings_to_the_app_and_get_reads_them_back(hass, setup, app):
    await hass.services.async_call(DOMAIN, "set_default_devices", {"mic_gain": 150, "speaker_gain": 80, "echo_delay_min_ms": 400,
                                                                     "echo_delay_max_ms": 800, "echo_ratio": 2.5}, blocking=True)
    assert {"mic_gain": 150, "speaker_gain": 80, "echo_delay_min_ms": 400, "echo_delay_max_ms": 800, "echo_ratio": 2.5} in app.device_posts, app.device_posts
    got = (await call(hass, "get_default_devices", {}))
    assert got["mic_gain"] == 150 and got["echo_delay_max_ms"] == 800 and got["echo_ratio"] == 2.5, got
    app.devices.update({"echo_delay_min_ms": 400, "echo_delay_max_ms": 800})
    await hass.services.async_call(DOMAIN, "set_default_devices", {"echo_delay_min_ms": 450}, blocking=True)
    assert {"echo_delay_min_ms": 450, "echo_delay_max_ms": 800} in app.device_posts, "đưa một ô trễ: lấy ô kia từ app, vì app kiểm cặp thấp < cao"
    await hass.services.async_call(DOMAIN, "set_default_devices", {"echo_use_default": True}, blocking=True)
    assert app.devices["echo_delay_min_ms"] is None and app.devices["echo_ratio"] is None, "trở về mặc định của máy chủ"


async def test_audio_number_entities_follow_the_app_and_write_through(hass, setup, app):
    app.devices.update({"mic_gain": 120, "echo_delay_min_ms": 450, "echo_delay_max_ms": 700})
    ent = {k: er.async_get(hass).async_get_entity_id("number", DOMAIN, "%s_app_%s" % (setup.entry_id, k))
           for k in ("mic_gain", "speaker_gain", "echo_delay_min_ms", "echo_delay_max_ms", "echo_ratio")}
    assert all(ent.values()), ent
    from homeassistant.helpers.entity_component import async_update_entity
    await async_update_entity(hass, ent["mic_gain"])
    st = hass.states.get(ent["mic_gain"])
    assert float(st.state) == 120 and st.attributes["db"] == 1.6, st.attributes        # 120 % ≈ +1,6 dB
    await hass.services.async_call("number", "set_value", {"entity_id": ent["mic_gain"], "value": 200}, blocking=True)
    assert app.device_posts[-1] == {"mic_gain": 200} and app.devices["mic_gain"] == 200
    await hass.services.async_call("number", "set_value", {"entity_id": ent["echo_delay_min_ms"], "value": 500}, blocking=True)
    assert app.device_posts[-1] == {"echo_delay_min_ms": 500, "echo_delay_max_ms": 700}, "app kiểm cặp (thấp < cao) nên luôn gửi đủ hai"
    await hass.services.async_call("number", "set_value", {"entity_id": ent["echo_ratio"], "value": 3.25}, blocking=True)
    assert app.device_posts[-1] == {"echo_ratio": 3.25}


# ---- đồng bộ theo từng kiểu đăng nhập (web | pc): HA thấy kiểu đang dùng và sửa/đọc được bộ cài đặt của từng kiểu (14/11) ---------------------
async def test_status_sensor_shows_the_active_login_client(hass, setup, app):
    sid = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, "%s_login_state" % setup.entry_id)
    assert sid, "không thấy cảm biến trạng thái"
    from homeassistant.helpers.entity_component import async_update_entity
    await async_update_entity(hass, sid)
    a = hass.states.get(sid).attributes
    assert a.get("kieu_dang_nhap") == "pc" and a.get("kieu_dang_nhap_ten") == "PC (Zalo PC)", a


async def test_default_devices_services_target_the_login_client(hass, setup, app):
    await hass.services.async_call(DOMAIN, "set_default_devices", {"mic_gain": 130, "client": "web"}, blocking=True)
    assert {"mic_gain": 130, "client": "web"} in app.device_posts, "ghi vào bộ của kiểu web dù app đang đăng nhập kiểu pc"
    got = await call(hass, "get_default_devices", {"client": "web"})
    assert "client=web" in app.device_queries and got["kieu_dang_nhap"] == "web" and got["kieu_dang_nhap_dang_dung"] == "pc", got
    got = await call(hass, "get_default_devices", {})
    assert got["kieu_dang_nhap"] == "pc", "bỏ trống: kiểu đang dùng"
    import voluptuous as vol
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(DOMAIN, "set_default_devices", {"mic_gain": 1, "client": "android"}, blocking=True)
