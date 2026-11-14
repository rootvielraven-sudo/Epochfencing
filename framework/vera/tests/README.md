# framework/vera/tests — port de los tests de Minerva

Portados 1:1 desde `minerva/tests/unit/test_vera_*.py`, con imports reescritos
`minerva.holons.*` → `framework.vera.*`. Blindan el port del core determinista.

## Excluidos (dependen de piezas NO porteadas)
- `test_vera_extraction.py`, `test_vera_report.py`, `test_vera_replay.py`,
  `test_vera_router.py`, `test_vera_benchmark_gate.py`,
  `test_vera_coverage_audit_completo.py`, `test_vera_coverage_endpoint.py`
  → usan extraction (LLM) / replay / report / api routers / dev / persistence.
- `test_vera_lexical_attribution.py` → prueba `LexicalGroundingJudge`, que usa un
  backend de holón (GroundingHolon) NO porteado a MIKA (neutralizado con
  NotImplementedError). La ruta de cobertura/anclaje no lo usa.
