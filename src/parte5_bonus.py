"""
Parte 5 · Bonus, el Detección de anillos de colusión (collusion rings)

Ver análisis completo en DECISIONS.md (D12).
Stub: sin los datos correctos (cardholder_id) el grafo no puede detectar
colusión real entre merchants.
"""
from __future__ import annotations


def detect_collusion_rings() -> list[set[int]]:
    """
    Detecta grupos de >= 3 merchants que muestran señales de colusión
    (transacciones cruzadas anómalas en patrones grafos).
    Devuelve lista de sets con merchant_ids del posible ring.

    Limitación crítica: el dataset no contiene `cardholder_id` ni entidad
    compradora. Sin esa información, no es posible construir el grafo
    merchant-merchant necesario para detectar ciclos de colusión — por eso
    esta función no recibe ni usa un DataFrame de transacciones: no hay
    input que la haga producir un resultado real todavía.

    Con los datos disponibles (solo merchant_id + amount + fecha), el único
    proxy observable sería merchants que comparten patrones de importe
    idéntico en la misma ventana temporal — señal muy débil y ruidosa.

    Ver DECISIONS.md D12 para análisis detallado: por qué es difícil,
    qué datos se necesitan, qué algoritmos investigaría, tiempo estimado.
    """
    # Sin cardholder_id no podemos construir el grafo bipartito merchant-cliente
    # que es la base de cualquier detección de colusión en acquiring.
    # Devolvemos lista vacía con documentación honesta del motivo.
    return []
