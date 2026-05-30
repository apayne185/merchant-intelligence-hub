# DECISIONS.md — `Apellido, Nombre`

> **Copia este archivo a la raíz del proyecto y rellénalo.** Es uno de los entregables más importantes (15 pts).
>
> **Regla**: por cada decisión técnica relevante responde **4 preguntas**:
> 1. **Qué hice** (acción concreta, no descripción genérica).
> 2. **Por qué** (criterio o evidencia, no "best practice").
> 3. **Qué descarté** (alternativas que consideraste y por qué no).
> 4. **Qué supuse** (supuesto que verificarías con un stakeholder real).
>
> Mínimo **6 decisiones** cubriendo Partes 1, 3 y 4. Evita texto LLM-genérico — el evaluador lo detecta y resta 8 pts.

---

## Parte 1 · Pandas

### D1 · Tratamiento de tipos en `load_clean`

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:
- **Qué supuse**:

### D2 · Estrategia de deduplicación

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:
- **Qué supuse**:

### D3 · Heurística de `merchants_at_risk`

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:
- **Qué supuse**:

---

## Parte 3 · Modelado ML

### D4 · Features descartadas (incluye trampas detectadas)

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:
- **Qué supuse**:

### D5 · Split temporal-aware y prevención de leakage

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:
- **Qué supuse**:

### D6 · Métricas y umbralización

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:
- **Qué supuse**:

---

## Parte 4 · FastAPI + Agno

### D7 · Por qué Agno (vs LangChain / LlamaIndex / código casero)

- **Qué hice**:
- **Por qué**:
- **Qué me gustó / no me gustó del framework**:

### D8 · Modelo elegido + estimación de coste a 5.000 emails/día

- **Modelo**:
- **Tokens medios por request** (input + output):
- **Coste por request** (€):
- **Coste mensual estimado** (€):  ← muestra el cálculo, no solo el número

### D9 · Diseño del schema Pydantic

- **Qué hice**:
- **Por qué enum cerrado de categorías**:
- **Por qué cap 300 chars en `reasoning`**:
- **Qué descarté**:

### D10 · Estrategia de evaluación antes de producción

- **Qué hice / propondría**:
- **Golden set**: cómo lo construirías
- **LLM-as-judge**: cómo lo usarías
- **Métricas clave**:

### D11 · Mitigación cuando el LLM falla (urgencia 5 clasificada como 2)

- **Qué hice**:
- **Por qué**:
- **Qué descarté**:

---

## Parte 5 · Pregunta-trampa (collusion rings)

### D12 · Honestidad técnica

> Si intentaste resolver, documenta tu approach. Si no, **eso vale más que un intento pobre** — responde:

- **Por qué este problema es difícil**:
- **Qué datos pedirías**:
- **Qué algoritmos investigarías**:
- **Tiempo realista necesario**:

---

## Decisiones extra (opcional)

### D13 · ...
