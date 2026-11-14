# Autor: @Raven
"""Tests de EPOCH FENCING ATÓMICO (sección 6): el token se valida contra el estado DURABLE
(disco), fusionado con la escritura bajo lock de fichero → sin TOCTOU entre procesos.

Dos instancias de ``EpochFencedStore`` sobre el MISMO directorio modelan dos procesos: cada
una tiene su propia caché; el fencing correcto NO puede fiarse de la caché local."""
from __future__ import annotations

import multiprocessing as mp
import os
import tempfile
import threading

from pipeline.model import Record
from pipeline.store import EpochFencedStore


def test_fencing_lee_estado_durable_entre_instancias():
    """Instancia B (caché vacía, como otro proceso) debe RECHAZAR un epoch viejo leyendo disco,
    aunque su caché no haya visto el commit de A. (Con la versión vieja, lo aceptaba y pisaba.)"""
    with tempfile.TemporaryDirectory() as d:
        a = EpochFencedStore(d, fence_epochs=True)
        b = EpochFencedStore(d, fence_epochs=True)      # "otro proceso": no vio el commit de A
        assert a.commit(Record("F1", 2, "a", {"x": 1})).accepted
        out = b.commit(Record("F1", 1, "zombi", {"x": 99}))
        assert not out.accepted and out.reason == "stale_epoch"
        # el disco sigue con epoch 2, valor bueno
        fresh = EpochFencedStore(d)
        assert fresh.get("F1").epoch == 2 and fresh.get("F1").extraction == {"x": 1}


def test_sin_fencing_entre_instancias_si_pisa():
    """Contraste: sin fencing, el zombi de otra instancia SÍ pisa (duplicado/corrupción)."""
    with tempfile.TemporaryDirectory() as d:
        a = EpochFencedStore(d, fence_epochs=False)
        b = EpochFencedStore(d, fence_epochs=False)
        assert a.commit(Record("F1", 2, "a", {"x": 1})).accepted
        assert b.commit(Record("F1", 1, "zombi", {"x": 99})).accepted
        assert EpochFencedStore(d).get("F1").extraction == {"x": 99}
        assert b.stale_overwrites == 1


def test_fencing_concurrente_dos_instancias():
    """Muchos hilos sobre DOS instancias commitean epochs en desorden; con fencing, el disco
    termina en el epoch MÁXIMO y nunca queda un JSON corrupto."""
    with tempfile.TemporaryDirectory() as d:
        stores = [EpochFencedStore(d, fence_epochs=True) for _ in range(2)]
        N = 40
        errors: list = []

        def worker(epoch: int):
            try:
                stores[epoch % 2].commit(Record("F1", epoch, f"w{epoch}", {"e": epoch}))
            except Exception as ex:  # noqa: BLE001
                errors.append(ex)

        threads = [threading.Thread(target=worker, args=(e,)) for e in range(1, N + 1)]
        # orden mezclado
        import random
        random.Random(0).shuffle(threads)
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
        final = EpochFencedStore(d).get("F1")
        assert final is not None and final.epoch == N      # ganó el epoch máximo
        assert final.extraction == {"e": N}


def _commit_in_proc(root: str, epoch: int):
    st = EpochFencedStore(root, fence_epochs=True)
    st.commit(Record("F1", epoch, f"p{epoch}", {"e": epoch}))


def test_fencing_multiproceso_real():
    """Dos PROCESOS reales commitean epoch 5 y epoch 3 sobre el mismo store. Pase el orden que
    pase, el disco debe quedar en epoch 5 (el 3 nunca gana)."""
    with tempfile.TemporaryDirectory() as d:
        ctx = mp.get_context("fork")
        ps = [ctx.Process(target=_commit_in_proc, args=(d, e)) for e in (5, 3)]
        for p in ps:
            p.start()
        for p in ps:
            p.join(timeout=30)
        final = EpochFencedStore(d).get("F1")
        assert final is not None and final.epoch == 5
