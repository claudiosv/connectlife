"""Fixtures for end-to-end testing of the ConnectLife integration.

These tests boot a real (in-process) Home Assistant core via
pytest-homeassistant-custom-component, load this repo's own
custom_components/connectlife, and drive it against `connectlife`'s bundled
aiohttp test server instead of the real ConnectLife/HijuConn servers — so the
full path (config flow -> coordinator -> platforms -> service calls -> API
client -> gateway request) is exercised with only the network boundary
faked out.
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator, Iterator

import connectlife.test_server as cl_test_server
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

pytest_plugins = ["pytest_homeassistant_custom_component"]

# Tests that boot a real `hass` must also request `enable_custom_integrations`
# (pytest-homeassistant-custom-component's own convention) so the loader
# looks in custom_components/connectlife instead of only the built-ins.
# Deliberately not autouse: test_api.py drives ConnectLifeApi directly and
# doesn't need a Home Assistant core at all.


@pytest.fixture(autouse=True)
def _reset_gateway_state() -> Iterator[None]:
    """`connectlife.test_server` keeps its fixtures as module-level globals."""
    cl_test_server.appliances.clear()
    cl_test_server.failure_rate = 0
    cl_test_server.timeout_rate = 0
    cl_test_server.auth_error_rate = 0
    cl_test_server.auth_error_type = "invalid_login"
    yield
    cl_test_server.appliances.clear()
    cl_test_server.failure_rate = 0
    cl_test_server.timeout_rate = 0
    cl_test_server.auth_error_rate = 0


@pytest.fixture(autouse=True)
def _stub_matter_climate_module() -> Iterator[None]:
    """Stand in for homeassistant.components.matter, which we don't install.

    __init__.py imports `homeassistant.components.matter.climate` directly
    (not through Home Assistant's own lazy per-integration dependency
    installer) whenever `hass.is_running`, purely to extend two class-level
    allowlists. The real module requires `python-matter-server` (a heavy,
    native-code package this test suite intentionally doesn't pull in) — stub
    it with the two attributes that code touches so entry setup doesn't hard
    -fail on an unrelated, unconfigured integration.
    """
    if "homeassistant.components.matter.climate" in sys.modules:
        yield
        return
    matter_pkg = types.ModuleType("homeassistant.components.matter")
    matter_climate_mod = types.ModuleType("homeassistant.components.matter.climate")
    matter_climate_mod.SUPPORT_DRY_MODE_DEVICES = set()
    matter_climate_mod.SUPPORT_FAN_MODE_DEVICES = set()
    matter_pkg.climate = matter_climate_mod
    sys.modules["homeassistant.components.matter"] = matter_pkg
    sys.modules["homeassistant.components.matter.climate"] = matter_climate_mod
    try:
        yield
    finally:
        del sys.modules["homeassistant.components.matter"]
        del sys.modules["homeassistant.components.matter.climate"]


@pytest.fixture
async def connectlife_gateway(socket_enabled: None) -> AsyncIterator[str]:
    """Start connectlife's bundled aiohttp test server; yield its base URL.

    pytest-homeassistant-custom-component blocks real sockets by default
    (`pytest_socket`) — `socket_enabled` lifts that for exactly this fixture
    so our loopback-only test server can bind and accept connections.
    """
    app = web.Application()
    app.add_routes([web.post("/accounts.login", cl_test_server.login)])
    app.add_routes([web.post("/accounts.getJWT", cl_test_server.get_jwt)])
    app.add_routes([web.post("/oauth/authorize", cl_test_server.authorize)])
    app.add_routes([web.post("/oauth/token", cl_test_server.token)])
    app.add_routes(
        [
            web.get(
                "/clife-svc/pu/get_device_status_list",
                cl_test_server.get_device_status_list,
            )
        ]
    )
    app.add_routes([web.post("/device/pu/property/set", cl_test_server.property_set)])

    server = TestServer(app)
    await server.start_server()
    try:
        yield str(server.make_url(""))
    finally:
        await server.close()


@pytest.fixture
def patch_connectlife_api(monkeypatch: pytest.MonkeyPatch, connectlife_gateway: str) -> str:
    """Redirect every ConnectLifeApi the integration creates at the test gateway.

    `__init__.py` and `config_flow.py` each did `from .api import ConnectLifeApi`,
    which binds the name separately in both modules' namespaces — patching one
    doesn't affect the other, so both need it.
    """
    import custom_components.connectlife as integration
    import custom_components.connectlife.config_flow as config_flow
    from custom_components.connectlife.api import ConnectLifeApi

    def factory(username, password, hass=None):
        # Matches ConnectLifeApi's real signature: __init__.py calls this by
        # keyword, config_flow.py calls it positionally.
        return ConnectLifeApi(
            username, password, hass=hass, test_server=connectlife_gateway
        )

    monkeypatch.setattr(integration, "ConnectLifeApi", factory)
    monkeypatch.setattr(config_flow, "ConnectLifeApi", factory)
    return connectlife_gateway
