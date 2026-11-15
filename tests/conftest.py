"""Small fake panels for tests (no real data), and a guard that fails any test touching the network."""
import ipaddress
import socket
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_LOCAL_NAMES = {"localhost", "localhost.localdomain", ""}


def _is_local(host):
    if isinstance(host, bytes):
        host = host.decode()
    if host is None or host in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True, scope="session")
def no_network():
    """Tests must not reach the network. Loopback stays allowed: asyncio (used by TestClient) connects to
    127.0.0.1 internally on Windows. Subprocesses are not covered; tests/test_api.py runs run.py, which is offline."""
    real_connect, real_connect_ex, real_getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo

    def check(address):
        if isinstance(address, tuple) and not _is_local(address[0]):
            raise RuntimeError(f"network access blocked in tests: {address!r}")

    def connect(self, address):
        check(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        check((host,))
        return real_getaddrinfo(host, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(socket.socket, "connect", connect)
        mp.setattr(socket.socket, "connect_ex", connect_ex)
        mp.setattr(socket, "getaddrinfo", getaddrinfo)
        yield

MONTHS = pd.date_range("2024-07-01", "2026-10-01", freq="MS")


def make_panel(n=4, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        for m in MONTHS:
            lam = 20 * (2.5 if m.month in (10, 11) else 1.0)
            rows.append(dict(product_id=f"P{i}", month=m, units_sold=float(rng.poisson(lam)), status="sellable"))
    panel = pd.DataFrame(rows)
    products = pd.DataFrame(dict(product_id=[f"P{i}" for i in range(n)], name=[f"Item {i}" for i in range(n)],
                                 category=["spices" if i % 2 else "snacks" for i in range(n)],
                                 lead_time_days=130, lead_time_typical_days=105, unit_cost=np.nan,
                                 shelf_life_days=np.nan, case_weight_kg=10.0))
    return panel, products


@pytest.fixture
def panel_products():
    return make_panel()


ROOT = Path(__file__).resolve().parent.parent


SLOW_TIMEOUT = 300      # the demo pipeline takes 1-2 minutes (more on a throttled CPU) and is charged to the first test


def pytest_collection_modifyitems(config, items):
    """Every test that uses the demo pipeline (demo_run) is slow: `pytest -m "not slow"` skips them. Slow tests get
    SLOW_TIMEOUT seconds instead of the 60 s default (pytest-timeout charges fixture setup to the first test)."""
    for item in items:
        if "demo_run" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.slow)
            item.add_marker(pytest.mark.timeout(SLOW_TIMEOUT))


@pytest.fixture(scope="session")
def demo_run(tmp_path_factory):
    """Run the whole pipeline once on synthetic data in a temp folder: {data, out, models} paths.
    --sim-from 2026-06: a short simulation (warm-up Jun-Sep, scored Oct 2026) still writes every output the
    API and the answer loop read; the simulation itself is tested in test_backtest.py."""
    import subprocess
    base = tmp_path_factory.mktemp("demo")
    paths = dict(data=base / "data", out=base / "outputs", models=base / "models")
    subprocess.run([sys.executable, "-W", "ignore", str(ROOT / "run.py"), "--generate", "--data", str(paths["data"]),
                    "--out", str(paths["out"]), "--models", str(paths["models"]), "--today", "2026-11-14", "--sim-from", "2026-06"],
                   check=True, cwd=ROOT, capture_output=True)
    return paths
