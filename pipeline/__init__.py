# Autor: @Raven
"""Pipeline distribuido de reconstrucción de facturas con recuperación ante fallos.

Unidad de trabajo = unidad de recuperación = una factura.

Mecanismos (se activan/desactivan de a uno para los experimentos):
  - Lease con vencimiento  → recuperación ante caída de nodo (``leases``).
  - Epoch fencing          → rechazo de escrituras zombi tardías (``store``).
  - Compuerta VERA         → corrupción silenciosa (cálculo) y alucinación (cobertura) (``gate``).

El plano de cómputo es Ray; si Ray no está disponible, el orquestador cae a un
ejecutor local equivalente (``orchestrator``), para poder correr en un solo proceso.
"""
