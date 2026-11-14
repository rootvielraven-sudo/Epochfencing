# Autor: @Raven
"""Tests del backend Redis (store con epoch fencing + NDL). Se SALTEAN si no hay Redis."""
from __future__ import annotations

import pytest

from pipeline.mika_env import redis_client
from pipeline.model import Record

rc = redis_client(required=False)
pytestmark = pytest.mark.skipif(rc is None, reason="Redis (mika .env) no alcanzable")


def test_redis_store_epoch_fencing():
    from pipeline.redis_store import RedisEpochFencedStore
    st = RedisEpochFencedStore(rc, "pytest_fence", fence_epochs=True)
    assert st.commit(Record("F1", 2, "w", {"x": 1})).accepted
    out = st.commit(Record("F1", 1, "zombi", {"x": 99}))        # epoch viejo
    assert not out.accepted and out.reason == "stale_epoch"
    assert st.get("F1").extraction == {"x": 1}
    assert st.stale_overwrites == 0
    # idempotencia
    assert st.commit(Record("F1", 2, "w", {"x": 1})).reason == "duplicate"


def test_redis_store_sin_fencing_cuenta_zombi():
    from pipeline.redis_store import RedisEpochFencedStore
    st = RedisEpochFencedStore(rc, "pytest_nofence", fence_epochs=False)
    assert st.commit(Record("F1", 2, "w", {"x": 1})).accepted
    assert st.commit(Record("F1", 1, "zombi", {"x": 99})).accepted
    assert st.get("F1").extraction == {"x": 99}
    assert st.stale_overwrites == 1


def test_redis_ndl_persiste():
    from framework.vera.ndl import NDL, RedisNDLStore
    import uuid
    prefix = f"tf:pytest:ndl:{uuid.uuid4().hex[:8]}:"
    ndl = NDL(store=RedisNDLStore(rc, prefix=prefix))
    ndl.record("factura", "Total a pagar", "Total a pagar: $ 100,00", hallucinated="$ 999,00")
    sig = ndl.check("factura", "Total a pagar", "Total a pagar: $ 100,00", value="$ 999,00")
    assert sig.matched and sig.value_hit
    # limpieza
    for k in rc.scan_iter(match=prefix + "*"):
        rc.delete(k)
