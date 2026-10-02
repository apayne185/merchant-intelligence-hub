# Data

**English** | [Español](#datos)

Everything here is real and public, refreshed with `make fixtures` (`scripts/fetch_fixtures.py`, which requires `SEC_USER_AGENT`).

| Path | Contents | Source |
|---|---|---|
| `universe.json` | The 10 covered issuers: ticker, CIK, SEC name, display name, sector, fiscal year end | SEC submissions |
| `xbrl/<TICKER>.json` | Every filed version (FY2019 onward) of 12 canonical metrics, with concept, period, accession number and filing date | SEC XBRL companyfacts, normalized by `src/filings/xbrl.py` |
| `filings/<TICKER>_risk_factors.json` | Item 1A passages of the latest 10-K, about 1,200 characters each, with content-addressed ids | SEC Archives, extracted by `src/filings/sections.py` |
| `filings/filing_index.json` | Recent 10-K/10-Q filings with acceptance times; the replay source for the ingestion stream | SEC submissions |
| `prices/daily_adjclose.csv` | Five years of daily adjusted closes | Public chart endpoint, demonstration only |
| `golden_set.json` | Evaluation cases; expected figures taken from companyfacts by concept and period end date | Hand-built, see DECISIONS.md D14 |
| `risk_limits.json` | Illustrative pre-trade limits | Hand-written |

---

# Datos

[English](#data) | **Español**

Todo es real y público, y se actualiza con `make fixtures` (`scripts/fetch_fixtures.py`, que requiere `SEC_USER_AGENT`).

| Ruta | Contenido | Fuente |
|---|---|---|
| `universe.json` | Los 10 emisores cubiertos: ticker, CIK, nombre en la SEC, nombre corto, sector, cierre del año fiscal | *Submissions* de la SEC |
| `xbrl/<TICKER>.json` | Todas las versiones presentadas (desde FY2019) de 12 métricas canónicas, con concepto, periodo, número de registro y fecha de presentación | *Companyfacts* XBRL de la SEC, normalizados por `src/filings/xbrl.py` |
| `filings/<TICKER>_risk_factors.json` | Pasajes del Item 1A del último 10-K, de unos 1.200 caracteres, con identificadores derivados del contenido | Archivo de la SEC, extraídos por `src/filings/sections.py` |
| `filings/filing_index.json` | 10-K/10-Q recientes con su hora de aceptación; la fuente de repetición del *stream* de ingesta | *Submissions* de la SEC |
| `prices/daily_adjclose.csv` | Cinco años de cierres diarios ajustados | Endpoint público de gráficos, solo para demostración |
| `golden_set.json` | Casos de evaluación; cifras esperadas tomadas de *companyfacts* por concepto y fecha de cierre | Hechos a mano, ver DECISIONS.md D14 |
| `risk_limits.json` | Límites pre-trade ilustrativos | Escritos a mano |
