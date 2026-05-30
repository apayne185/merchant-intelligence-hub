# Take-Home Technical Test — Getnet AI Lab Graduate Program 2026

**Bienvenido/a.** Esto es el `README.md` que acompaña al `.zip` con tu prueba técnica. Léelo entero antes de tocar código (10 min).

> **TL;DR:** tienes **72 h**. Implementa 4 partes (pandas, SQL, ML, API con Agno). Documenta tus decisiones con rigor. Entrega un `.zip` por el link que te hemos enviado. **Aceptamos que uses LLMs** — el test mide tu criterio, no tu sintaxis. Si pasas, hay **entrevista técnica** donde defiendes en directo lo que has entregado.

---

## 1. ¿Qué es esto?

El **AI Lab Getnet** (Data & AI del adquirente global de Santander) opera un portafolio de modelos predictivos, causales y agentic workflows en BR, MX, CL, AR y EU.

Buscamos personas para un **Graduate Program de 12 meses**. Este test mide cómo piensas y comunicas decisiones técnicas, no cuánto código produces.

---

## 2. Mapa del `.zip`

```
merchant-intelligence-hub/
├── README.md                       ← este documento
├── STATEMENT.md                    ← enunciado completo y oficial (LEE ANTES DE CODEAR)
├── Makefile                        ← targets de conveniencia (setup, test, run)
├── .gitignore
├── pyproject.toml                  ← packaging 
├── data/
│   ├── README.md                   ← descripción del dataset
│   ├── transactions_sample.csv     ← 200k filas · ~80 MB · OJO: contiene trampas a propósito
│   └── merchants_context.json      ← ~500 merchants para la tool de Parte 4
├── src/
│   ├── parte1_pandas.py
│   ├── parte2_sql.sql
│   ├── parte3_modeling.ipynb
│   └── parte4_api/
│       ├── __init__.py
│       ├── main.py
│       ├── agent.py
│       ├── schemas.py
│       └── README.md
├── tests/
│   ├── __init__.py
│   ├── test_solution.py
│   └── test_api.py
├── outputs/
│   └── .gitkeep
└── templates/
    ├── DECISIONS.md
    ├── ASSUMPTIONS.md
    ├── SELF_REVIEW.md
    └── TOOLS_USED.md
```

---

## 3. Setup en 4 pasos (≤ 5 min)

Prerrequisitos: **Python 3.10/3.11/3.12** · **Git** · **[uv](https://docs.astral.sh/uv/)** (gestor moderno de dependencias y venv, el que usamos en el lab) · ~2 GB libres.

Si no tienes uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # Linux/Mac
# powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows
```

Después:

```bash
# 1. Descomprime y entra al directorio
unzip takehome_candidate_starter.zip
cd takehome_candidate_starter/

# 2. Inicializa git INMEDIATAMENTE (el historial se evalúa)
git init && git add . && git commit -m "chore: initial starter from Getnet AI Lab"

# 3. Resolver y crear venv en 1 comando (uv lee pyproject.toml + bloquea versiones en uv.lock)
uv sync --extra dev

# 4. Sanity check + arranque mínimo viable de la API (debe responder 200 en /health)
uv run python -c "import pandas, sklearn, fastapi, uvicorn, agno, pydantic; print('OK · environment ready')"
MOCK_LLM=1 uv run uvicorn src.parte4_api.main:app --port 8000 &
sleep 2 && curl -s http://localhost:8000/health && kill %1
```

> **Atajo:** `make setup` ejecuta el paso 3 y `make run` el arranque de la API.

### Fallback con `pip` (sin uv)

Si por política de tu máquina no puedes instalar uv, hay alternativa equivalente:

```bash
python -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -e ".[dev]"
```

Nos sirve igual, pero **recomendamos uv** porque es lo que corre el evaluador y porque hace `pip install` 10–100× más rápido.

Si el sanity check falla, abre incidencia con tu reclutador antes de empezar — no descuenta tiempo.

---

## 4. Copia las plantillas a la raíz

Las plantillas en `templates/` deben copiarse a la **raíz del proyecto** y rellenarse. Son entregables obligatorios.

```bash
cp templates/DECISIONS.md    ./DECISIONS.md
cp templates/ASSUMPTIONS.md  ./ASSUMPTIONS.md
cp templates/SELF_REVIEW.md  ./SELF_REVIEW.md
cp templates/TOOLS_USED.md   ./TOOLS_USED.md
```

---

## 5. Las 4 partes — resumen ejecutivo

> Detalle completo en `STATEMENT.md`. **No empieces a codear sin leerlo.**

| Parte | Entregable | Pts | Foco |
|---|---|---:|---|
| 1 · Pandas + EDA | `src/parte1_pandas.py` | 17 | Calidad de datos, vectorización |
| 2 · SQL | `src/parte2_sql.sql` | 11 | Window functions, partition pruning |
| 3 · ML | `src/parte3_modeling.ipynb` | 18 | Anti-leakage, métricas, interpretabilidad |
| 4 · FastAPI + Agno | `src/parte4_api/` (debe arrancar con `uvicorn src.parte4_api.main:app`) | 18 | uvicorn + ASGI, Pydantic v2, agente Agno + tools, guardrails |

Más: `DECISIONS.md` (**22**) · `SELF_REVIEW.md`+`ASSUMPTIONS.md` (**14**).
**Total 100 · Aprobado ≥ 60 · Excelencia ≥ 80.**
---

## 6. Reglas del juego (lo esencial)

✅ **Permitido**: cualquier LLM (ChatGPT, Claude, Copilot, Cursor, Windsurf), Stack Overflow, docs oficiales. Declara qué usaste en `TOOLS_USED.md` y para qué.

🚫 **No permitido**: que otra persona haga el test por ti (lo detectamos en la entrevista posterior); copiar de otro candidato; manipular fechas/commits.

⚠️ **Lo que medimos** es si entiendes lo que has escrito. Los documentos de criterio (`DECISIONS.md`, `SELF_REVIEW.md`, `ASSUMPTIONS.md`) y el commit history son la prueba escrita.

**LLM provider para Parte 4** — 3 opciones válidas:
1. Tu propia clave OpenAI (≤ 0,50 € coste real).
2. `export MOCK_LLM=1` — agente determinístico, no penaliza.

## 7. Entrega

### Estructura final del `.zip`

Cuando termines, tu `.zip` debe contener exactamente:

```
Apellido_Nombre_TakeHome.zip
├── README.md              ← reemplaza este: cómo arrancar TU solución
├── DECISIONS.md           ← obligatorio
├── ASSUMPTIONS.md         ← obligatorio
├── SELF_REVIEW.md         ← obligatorio
├── TOOLS_USED.md          ← obligatorio
├── .git/                  ← historial completo · NO BORRAR
├── pyproject.toml         ← actualizado si añadiste deps (con versión fijada)
├── src/                   ← tu código
├── tests/                 ← tus tests
└── outputs/               ← CSVs/JSONs generados por tu código
```

**Archivo obligatorio faltante:** −5 pts cada uno.

### Comando recomendado para empaquetar

```bash
# Excluye .venv, cache, el CSV original (ya lo tenemos) y el generador interno
zip -r Apellido_Nombre_TakeHome.zip . \
  -x ".venv/*" "**/__pycache__/*" "*.pyc" \
     "data/transactions_sample.csv" "data/_generator.py" "templates/*" \
     "build/*" "dist/*" "*.egg-info/*"
```

> **Nota:** mantén la estructura root (`src/`, `tests/`, `pyproject.toml`). Copia las plantillas de `templates/` a la raíz **antes** de comprimir (ver §4).



## 8. Rúbrica resumida

| Bloque | Pts |
|---|---:|
| `DECISIONS.md` | 22 |
| `SELF_REVIEW.md` + `ASSUMPTIONS.md` | 14 |
| Parte 1 · Pandas | 17 |
| Parte 2 · SQL | 11 |
| Parte 3 · ML | 18 |
| Parte 4 · API + Agno | 18 |
---

## 9. FAQ rápida

- **¿Cuánto tiempo real?** 6–8 h efectivas. Si te lleva 20 h, estás sobre-ingenierizando.
- **¿Si no termino?** Mejor 3 partes bien que 5 a medias. Prioriza Parte 1 y Parte 4.
- **¿Otra librería en vez de Agno?** Está permitido pero tienes que justificar el motivo y modificar el código.
- **¿Deploy en cloud?** No. Tiene que arrancar local con `uvicorn`.

--

## 10. Una última cosa

No buscamos código perfecto. Buscamos a alguien que:

- **Lee los datos antes de modelarlos.**
- **Pregunta lo que no entiende** (en `ASSUMPTIONS.md`).
- **Defiende lo que escribió** (por escrito ahora, en directo en la entrevista después).
- **Reconoce lo que no sabe** sin inventar respuestas.
- **Documenta sus decisiones** como si otra persona fuera a mantener el código mañana.

**Mucha suerte.** Estamos del otro lado leyéndote con curiosidad genuina.

— El equipo del Getnet AI Lab

---

*Versión 1.0 · Mayo 2026 · Distribución restringida a candidatos del Graduate Program AI Lab Getnet.*
