"""WebSocket push-update client for the ConnectLife integration.

Ported from Connectlife-LLC/HomeAssistantPlugin's websocket.py. Registers a
"phone code" with the ConnectLife backend, fetches a push-notification
server/channel, then keeps a wss:// connection open that delivers
base64+JSON device-status-change events in real time — on top of (not
instead of) the DataUpdateCoordinator's periodic polling.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


class ApiClientProtocol(Protocol):
    """What HisenseWebSocket needs from the API client."""

    async def _api_request(
        self, method: Literal["GET", "POST"], path: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]: ...

    @property
    def oauth_session(self) -> Any: ...


@dataclass
class PushChannel:
    push_channel: str

    @classmethod
    def from_json(cls, data: dict) -> PushChannel:
        return cls(push_channel=data.get("pushChannel", ""))


@dataclass
class NotificationInfo:
    push_channels: list[PushChannel] = field(default_factory=list)
    push_server_ip: str = ""
    push_server_port: str = ""
    push_server_ssl_port: str = ""
    hb_interval: int = 30
    hb_fail_times: int = 3

    @classmethod
    def from_json(cls, data: dict) -> NotificationInfo:
        return cls(
            push_channels=[PushChannel.from_json(c) for c in data.get("pushChannels", [])],
            push_server_ip=data.get("pushServerIp", ""),
            push_server_port=data.get("pushServerPort", ""),
            push_server_ssl_port=data.get("pushServerSslPort", ""),
            hb_interval=data.get("hbInterval", 30),
            hb_fail_times=data.get("hbFailTimes", 3),
        )


class ConnectLifeWebSocket:
    """WebSocket client for real-time ConnectLife device status push updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: ApiClientProtocol,
        message_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        self.hass = hass
        self.api_client = api_client
        self.message_callback = message_callback
        self.session = async_get_clientsession(hass)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._phone_code: str = ""
        self._notification_info: NotificationInfo | None = None
        self._task: asyncio.Task | None = None
        self._closing = False
        self._ping_interval = 30
        self._fail_count = 0
        self._max_fails = 3
        self._last_message_time = 0.0

    async def _register_phone_code(self, phone_code: str) -> bool:
        try:
            response = await self.api_client._api_request(
                "POST", "/msg/registerPhoneDevice", data={"phoneCode": phone_code}
            )
            return response.get("resultCode") == 0
        except Exception as err:
            _LOGGER.error("Failed to register phone code: %s", err)
            return False

    async def _get_notification_info(self, phone_code: str) -> NotificationInfo | None:
        try:
            response = await self.api_client._api_request(
                "GET",
                "/msg/get_msg_and_channels",
                data={"pageNo": "1", "pageSize": "10", "phoneCode": phone_code, "queryType": 2},
            )
            return NotificationInfo.from_json(response)
        except Exception as err:
            _LOGGER.error("Failed to get notification info: %s", err)
            return None

    async def _connect_ws(self) -> None:
        if not self._notification_info or not self._phone_code:
            _LOGGER.error("Missing notification info or phone code")
            return

        channel = (
            self._notification_info.push_channels[0].push_channel
            if self._notification_info.push_channels
            else ""
        )
        if not channel:
            _LOGGER.error("No push channel available")
            return

        try:
            access_token = await self.api_client.oauth_session.async_get_access_token()
            ws_url = (
                f"wss://{self._notification_info.push_server_ip}:"
                f"{self._notification_info.push_server_ssl_port}/ws/{channel}"
                f"?phoneCode={self._phone_code}&token={access_token}"
            )

            self._ws = await self.session.ws_connect(
                ws_url, heartbeat=self._ping_interval, ssl=True
            )
            _LOGGER.info("ConnectLife WebSocket connection established")
            self._fail_count = 0
            await self._listen()

        except aiohttp.ClientError as err:
            _LOGGER.error("WebSocket connection failed: %s", err)
            self._fail_count += 1
            if self._fail_count >= self._max_fails:
                _LOGGER.error("Max WebSocket connection failures reached")
                return

            retry_delay = min(30, 5 * (2 ** (self._fail_count - 1)))
            await asyncio.sleep(retry_delay)

            if not self._closing:
                self._notification_info = await self._get_notification_info(self._phone_code)
                if self._notification_info:
                    await self._connect_ws()

    async def _listen(self) -> None:
        if not self._ws:
            return

        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    current_time = time.time()
                    if current_time - self._last_message_time < 1:
                        continue
                    self._last_message_time = current_time

                    try:
                        decoded_content = base64.b64decode(msg.data).decode("utf-8")
                        data = json.loads(decoded_content)
                        self.message_callback(data)
                    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as err:
                        _LOGGER.error("Failed to decode WebSocket message: %s", err)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    _LOGGER.error("WebSocket error: %s", self._ws.exception())
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
        except Exception as err:
            _LOGGER.error("WebSocket listener error: %s", err)
        finally:
            if not self._closing and self._fail_count < self._max_fails:
                self.hass.loop.create_task(self._delayed_reconnect())

    async def _delayed_reconnect(self) -> None:
        try:
            await asyncio.sleep(5)
            self._notification_info = await self._get_notification_info(self._phone_code)
            if self._notification_info:
                await self._connect_ws()
        except Exception as err:
            _LOGGER.error("Error during WebSocket reconnection: %s", err)

    async def async_connect(self) -> None:
        try:
            self._phone_code = str(uuid.uuid4())

            if not await self._register_phone_code(self._phone_code):
                _LOGGER.error("Failed to register phone code")
                return

            self._notification_info = await self._get_notification_info(self._phone_code)
            if not self._notification_info:
                _LOGGER.error("Failed to get notification info")
                return

            self._ping_interval = self._notification_info.hb_interval
            self._max_fails = self._notification_info.hb_fail_times

            self._closing = False
            self._task = self.hass.async_create_task(self._connect_ws())
        except Exception as err:
            _LOGGER.error("Failed to connect to ConnectLife WebSocket: %s", err)

    async def async_disconnect(self) -> None:
        self._closing = True
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._ws = None
        self._task = None
