"""Độ to phần mềm mic/loa và tham số chống vọng — thực thể số trên thiết bị Zalo Call (14/11/2026).

Giá trị nằm ở APP (devices.json), cùng nơi WebUI và dịch vụ `set_default_devices` / `set_app_settings` ghi, nên ba nơi luôn khớp: HA không giữ
bản sao riêng. Thực thể hỏi app mỗi 30 giây và ngay sau khi tự ghi. Độc lập với cài đặt của loa R1: đây là độ to PHẦN MỀM áp lên tiếng gửi/nhận
của cuộc gọi (100 % = 0 dB, 200 % = +6 dB, 50 % = −6 dB)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ZaloCallError
from .const import DOMAIN
from .entity import ZaloCallEntity

SCAN_INTERVAL = timedelta(seconds=30)
DEFAULT_ECHO_MIN_MS, DEFAULT_ECHO_MAX_MS, DEFAULT_ECHO_RATIO = 300, 750, 2.0       # như mặc định của app (ZALO_PC_ECHO_DELAY_MS / ZALO_PC_ECHO_RATIO)

# (khóa trong devices.json của app, tên, biểu tượng, nhỏ nhất, lớn nhất, bước, đơn vị)
SPECS = (
    ("mic_gain", "Độ to mic (phần mềm)", "mdi:microphone-plus", 0, 300, 5, "%"),
    ("speaker_gain", "Độ to tiếng bên kia ra loa (phần mềm)", "mdi:volume-plus", 0, 300, 5, "%"),
    ("echo_delay_min_ms", "Chống vọng: trễ loa→mic thấp nhất", "mdi:timer-sand", 0, 3000, 10, "ms"),
    ("echo_delay_max_ms", "Chống vọng: trễ loa→mic cao nhất", "mdi:timer-sand-complete", 0, 3000, 10, "ms"),
    ("echo_ratio", "Chống vọng: ngưỡng mic/loa", "mdi:ear-hearing-off", 0.5, 10, 0.1, "×"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = hass.data[DOMAIN][entry.entry_id]
    add([AppAudioNumber(entry, rt, *spec) for spec in SPECS], update_before_add=True)


class AppAudioNumber(ZaloCallEntity, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_should_poll = True

    def __init__(self, entry: ConfigEntry, rt, key: str, name: str, icon: str, lo: float, hi: float, step: float, unit: str) -> None:
        super().__init__(entry, "app_" + key)
        self._rt, self._key = rt, key
        self._attr_name, self._attr_icon = name, icon
        self._attr_native_min_value, self._attr_native_max_value = lo, hi
        self._attr_native_step, self._attr_native_unit_of_measurement = step, unit
        self._value: float | None = None
        self._cache: dict[str, Any] = {}

    @property
    def native_value(self) -> float | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self._key.endswith("_gain") and isinstance(self._value, (int, float)):
            import math
            return {"db": round(20 * math.log10(self._value / 100), 1) if self._value > 0 else 0.0}      # 0 % = giữ nguyên = 0 dB
        return {}

    async def async_update(self) -> None:
        try:
            self._cache = await self._rt.api.devices_get()
        except ZaloCallError:
            return                                      # giữ giá trị cũ khi app tạm không trả lời
        raw = self._cache.get(self._key)
        self._value = float(raw) if isinstance(raw, (int, float)) else None     # echo_*: None = app theo mặc định của máy chủ

    async def async_set_native_value(self, value: float) -> None:
        patch: dict[str, Any] = {self._key: int(value) if self._key != "echo_ratio" else round(float(value), 2)}
        if self._key in ("echo_delay_min_ms", "echo_delay_max_ms"):        # app kiểm cặp (thấp < cao) nên luôn gửi đủ hai
            await self.async_update()                                       # đọc LẠI app: ô kia có thể vừa đổi từ WebUI/dịch vụ, không dùng bản cũ
            other = "echo_delay_max_ms" if self._key == "echo_delay_min_ms" else "echo_delay_min_ms"
            cur = self._cache.get(other)
            patch[other] = int(cur) if isinstance(cur, (int, float)) else (DEFAULT_ECHO_MAX_MS if other.endswith("max_ms") else DEFAULT_ECHO_MIN_MS)
        try:
            res = await self._rt.api.devices_set(patch)
        except ZaloCallError as err:
            raise HomeAssistantError(str(err)) from err
        self._cache = (res or {}).get("devices", res) if isinstance(res, dict) else {}
        await self.async_update()
        self.async_write_ha_state()
