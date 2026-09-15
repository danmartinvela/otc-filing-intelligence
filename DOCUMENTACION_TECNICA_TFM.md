# Documentación Técnica — OTC Filing Intelligence

> Documento generado mediante auditoría exhaustiva del repositorio tal y como
> existe en el momento de redacción. Describe **exclusivamente lo que el
> código implementa hoy**, distinguiendo explícitamente lo implementado de lo
> legado/inconsistente. Cuando un dato no puede determinarse a partir del
> repositorio, se indica literalmente `No determinado a partir del código`.
> Todas las referencias de archivo son rutas relativas a la raíz del
> repositorio.

---

## 1. Objetivo y visión general

**Problema que resuelve.** Las empresas que cotizan en OTC Markets (y en
general cualquier emisor que reporta ante la SEC) publican un volumen alto de
filings (formularios) en EDGAR cada día. La inmensa mayoría no representa un
evento corporativo relevante para un inversor (informes periódicos
rutinarios, exhibits, actualizaciones administrativas). Leer manualmente cada
filing para detectar los pocos que sí importan (una fusión, una OPA, una
quiebra, un cambio de control, una financiación relevante) no escala. El
proyecto automatiza ese triage.

**Objetivo de la plataforma.** Construir un pipeline que:
1. Descarga automáticamente el índice diario de EDGAR.
2. Clasifica cada filing en `EVENT`, `CONTEXT` o `IGNORED` según su tipo de
   formulario (`src/filing_routing/routing.py`).
3. Descarga y limpia únicamente el **documento principal** de cada filing
   EVENT/CONTEXT (no los exhibits).
4. Envía el texto de cada filing `EVENT` a un LLM que actúa como analista
   de research event-driven, y que devuelve una clasificación estructurada
   (tipo de evento, importancia, impacto de mercado, si merece "deep
   research", resumen, evidencias).
5. Persiste todo en una base de datos SQLite única.
6. Expone un dashboard Streamlit de solo lectura para que un analista
   humano explore, filtre, ordene y revise los resultados.

**Documentos que procesa.** Formularios SEC EDGAR de dos grupos:
- **EVENT** (formularios candidatos a evento corporativo relevante): `8-K`,
  `8-K/A`, `SC TO-I`, `SC TO-T`, `SC TO-C`, `SC 13D`, `SC 13D/A`, `SC 13E3`,
  `S-1`, `S-1/A`, `424B3`, `424B5`, `DEF 14A`, `DEFM14A`, `PREM14A`.
- **CONTEXT** (informes periódicos, almacenados pero no analizados por sí
  solos): `10-K`, `10-Q`, `20-F`, `6-K`.
- Todo lo demás es **IGNORED** y se descarta antes de llegar a la base de
  datos.

(Lista exacta en `src/filing_routing/routing.py:15-38` — ver sección 7.)

**Fuentes de información.**
- **SEC EDGAR** — fuente primaria y única de filings: el índice diario
  (`master.idx`) y el contenido de cada "complete submission text file".
- **`company_tickers.json` de la SEC** — mapeo CIK → ticker → nombre de
  empresa, para enriquecer los filings con su ticker
  (`src/company_enrichment/sec_company_tickers.py`).
- **CSV del OTC Markets Stock Screener** (importación manual, fuera de
  línea) — datos de mercado OTC (tier, precio, volumen, tipo, país) para
  enriquecer filings vía ticker (`src/otcmarkets/otc_screener_importer.py`).

**Papel de SEC EDGAR.** Es la única fuente de los propios filings
(metadatos + contenido). No hay ningún otro proveedor de datos de mercado en
tiempo real ni de precios; el screener OTC es un archivo estático importado
manualmente, no una API en vivo.

**Papel de la IA/LLM.** El LLM no interviene en la ingesta ni en el
enrutamiento EVENT/CONTEXT (eso es una regla determinista por tipo de
formulario). Su único papel es un **"first pass" de clasificación semántica**
sobre el texto ya extraído de cada filing `EVENT`: decide de qué trata
realmente el filing, si es material, qué impacto de mercado podría tener, si
merece profundizar, y produce un resumen y evidencias. No hay una segunda
pasada de "deep research" implementada en el código — el campo
`deep_research`/`next_step="RESEARCH"` es una señal para un analista humano,
no un proceso automático posterior (ver sección 20, "funcionalidades
parciales").

**Qué obtiene finalmente el usuario.** Un dashboard web (Streamlit) donde
puede:
- Ver KPIs agregados del pipeline (Resumen Ejecutivo).
- Explorar, filtrar, ordenar y paginar la lista de filings `EVENT` con su
  clasificación IA (Explorador de Eventos).
- Ver el detalle completo de un filing: metadatos, análisis IA completo,
  evidencias y texto íntegro del documento (Detalle del Filing).
- Ver estadísticas agregadas (Estadísticas).
- Disparar manualmente, desde el navegador, tanto la ingesta SEC como el
  análisis LLM, sin usar la CLI (Procesar filings).

**Flujo general (alto nivel).**
```
Aparece un filing en el índice diario de EDGAR
    → se descarga y parsea el índice (master.idx)
    → se filtra por tipo de formulario (EVENT/CONTEXT se guardan, el resto se descarta)
    → se inserta en SQLite (deduplicado por filename)
    → (opcional, mismo comando/acción) se descarga el "complete submission"
    → se extrae solo el documento principal (SEQUENCE=1) y se limpia su texto
    → se guarda raw_text/clean_text en la misma fila
    → (para EVENT) el filing queda "pendiente de análisis LLM"
    → se selecciona para el LLM first pass (manual o programado)
    → se llama al LLM con el texto del documento
    → se parsea la respuesta JSON y se guarda en llm_filing_analysis
    → el filing aparece en el Explorador de Eventos del dashboard con su análisis
    → el usuario abre el Detalle del Filing para revisar el resultado completo
```
No existe ningún paso de "mantener o descartar" el resultado en el código
actual (ver sección 13): esa funcionalidad fue implementada y posteriormente
revertida en su totalidad durante el desarrollo — el estado actual del
repositorio no contiene descarte manual.

---

## 2. Arquitectura completa

El sistema se compone de dos aplicaciones Python **completamente
independientes en tiempo de ejecución**, que comparten únicamente el fichero
SQLite y (en el caso del dashboard) importan directamente el paquete `src/`
como librería para dos acciones puntuales de escritura:

1. **Backend / pipeline** (`src/`) — CLI (`src/main.py`), sin dependencia de
   Streamlit. Corre en el `.venv` raíz del proyecto (Python 3.9, el de las
   Command Line Tools de Apple en macOS, ver `dashboard/README.md`).
2. **Dashboard** (`dashboard/`) — aplicación Streamlit, con su **propio
   entorno virtual** (`dashboard/.venv`, Python 3.12), documentado como
   necesario por incompatibilidades binarias de Streamlit/pandas/pyarrow con
   el Python 3.9 del backend (`dashboard/README.md:24-31`). El dashboard abre
   su propia conexión SQLite en modo **estrictamente de solo lectura**
   (`dashboard/database/connection.py`), y solo dos módulos concretos
   (`dashboard/screens/process_filings.py`) importan y ejecutan funciones del
   backend (`src.pipeline`) para lanzar ingesta/análisis desde el navegador —
   la escritura real ocurre siempre por la conexión read-write del backend
   (`src/database/db.py:get_connection`), nunca por la conexión de solo
   lectura del dashboard.

### Diagrama textual real (según el código)

```
                         SEC EDGAR
                             │
                 ┌───────────┴────────────┐
                 │                         │
        master.idx (índice diario)   complete submission
        src/sec_ingestion/            (.txt por accession)
        daily_index.py                src/sec_ingestion/downloader.py
                 │                         │
        src/sec_ingestion/parser.py        │
        (parse_master_idx)                 │
                 │                         │
                 ▼                         │
   src/filing_routing/routing.py           │
   get_filing_category(form_type)          │
   → EVENT | CONTEXT | IGNORED             │
   (IGNORED se descarta aquí mismo)        │
                 │                         │
                 ▼                         │
     src/database/db.py                    │
     insert_filings()                      │
     → tabla `filings` (SQLite,            │
       data/filings.db)                    │
     dedup por filename (UNIQUE)           │
                 │                         │
                 └──────────┬──────────────┘
                            ▼
          extract_primary_document() (SGML → <DOCUMENT SEQUENCE=1> → <TEXT>)
          clean_filing_text() (BeautifulSoup / regex fallback)
          src/sec_ingestion/downloader.py::fetch_and_clean
                            │
                            ▼
          update_filing_content() → filings.raw_text / filings.clean_text
                            │
                            ▼
          (solo filings filing_category = 'EVENT', con clean_text no vacío,
           no presentes aún en llm_filing_analysis)
                            │
                            ▼
     src/llm_analysis/first_pass.py::run_first_pass
     ThreadPoolExecutor (N workers) → LLMClient.chat_completion (por hilo)
     parse_llm_response() (JSON estricto o PARSE_ERROR)
     insert_llm_filing_analysis() (un solo hilo, el llamante)
                            │
                            ▼
          tabla `llm_filing_analysis` (SQLite)
                            │
                            ▼
      ┌─────────────────────────────────────────────────────────┐
      │                     dashboard/ (Streamlit)               │
      │  database/connection.py — conexión SQLite mode=ro         │
      │  database/queries.py — todo el SQL, cacheado (st.cache_data)│
      │                            │                              │
      │   ┌────────────┬───────────┼───────────┬───────────────┐  │
      │   ▼            ▼           ▼           ▼               │  │
      │ Resumen    Explorador   Detalle    Estadísticas   Procesar│
      │ Ejecutivo  de Eventos   del Filing               filings │
      │                                          (llama a src.pipeline│
      │                                           para ingesta/LLM   │
      │                                           bajo demanda)      │
      └─────────────────────────────────────────────────────────┘
```

### Capas y responsabilidades

| Capa | Paquete | Responsabilidad |
|---|---|---|
| Ingesta SEC | `src/sec_ingestion/` | Descarga del índice diario, parseo, descarga y limpieza del documento principal |
| Enriquecimiento | `src/company_enrichment/`, `src/otcmarkets/` | Ticker por CIK, datos OTC por ticker |
| Enrutamiento | `src/filing_routing/` | Clasificación determinista EVENT/CONTEXT/IGNORED |
| Persistencia | `src/database/` | Esquema SQLite, todas las operaciones de lectura/escritura del backend |
| Análisis IA | `src/llm_analysis/` | Prompt, cliente HTTP, orquestación concurrente, parsing de respuesta |
| Orquestación | `src/pipeline.py` | Funciones compartidas por CLI y dashboard (`run_daily_pipeline`, `run_llm_pipeline`) |
| CLI | `src/main.py` | Punto de entrada por línea de comandos |
| Acceso a datos (dashboard) | `dashboard/database/` | Conexión de solo lectura + todas las consultas SQL cacheadas |
| Lógica de presentación (dashboard) | `dashboard/services/` | Traducción de filtros UI → SQL, cálculo de KPIs derivados |
| Componentes UI | `dashboard/components/` | Tabla de eventos, secciones de detalle, tarjetas KPI, filtros de sidebar, header, meter |
| Gráficas | `dashboard/charts/` | Figuras Plotly + tema visual compartido |
| Pantallas | `dashboard/screens/` | Una por pantalla, orquestan datos + componentes |

No existe capa de mensajería, cola de tareas ni servicio en segundo plano:
todo es invocación síncrona (CLI o clic en el dashboard) sobre SQLite.

---

## 3. Estructura del repositorio

### `src/` (backend)

| Archivo | Responsabilidad | Elementos principales | Relación |
|---|---|---|---|
| `src/main.py` | CLI: parseo de argumentos, orquesta todas las acciones | `main()`, `_build_arg_parser()`, `_run_daily_pipeline()`, `_run_llm_first_pass()`, `_daily_pipeline_cli_progress()`, `_make_llm_cli_progress()` | Llama a `src/pipeline.py` y a `src/database/db.py` |
| `src/pipeline.py` | Orquestación reutilizable (CLI + dashboard), con callback de progreso | `run_daily_pipeline()`, `run_llm_pipeline()`, `DailyPipelineResult`, `LLMPipelineResult`, `get_user_agent()` | Usa `sec_ingestion`, `database.db`, `llm_analysis.first_pass` |
| `src/database/db.py` | Todo el esquema SQLite y las operaciones CRUD del backend | `init_db()`, `insert_filings()`, `update_filing_content()`, `get_filenames_with_content()`, `get_event_filings_needing_llm_analysis()`, `_llm_selection_where()`, `insert_llm_filing_analysis()`, `enrich_filings_with_tickers()`, `enrich_filings_with_otc()` | Es el único módulo que conoce el esquema; todo lo demás pasa por aquí |
| `src/database/models.py` | Dataclass `Filing` | `Filing` (cik, company_name, form_type, date_filed, filename, filing_url, raw_text, clean_text, created_at) | Usado por `parser.py` y `pipeline.py` |
| `src/sec_ingestion/daily_index.py` | Descarga y filtra el índice diario | `build_index_url()`, `fetch_daily_index()`, `get_filtered_filings()`, `get_quarter()` | Usa `parser.py` y `filing_routing.routing` |
| `src/sec_ingestion/parser.py` | Parseo de `master.idx` (formato pipe-delimited) | `parse_line()`, `parse_master_idx()` | Produce objetos `Filing` |
| `src/sec_ingestion/downloader.py` | Descarga, extracción del documento principal, limpieza de texto | `build_session()`, `fetch_raw()`, `extract_primary_document()`, `clean_filing_text()`, `fetch_and_clean()`, `PrimaryDocumentResult` | Usado por `pipeline.py` |
| `src/filing_routing/routing.py` | Clasificación EVENT/CONTEXT/IGNORED | `EVENT_FORMS`, `CONTEXT_FORMS`, `get_filing_category()` | Consumido por `daily_index.py`, `db.py`, `first_pass.py` |
| `src/llm_analysis/client.py` | Cliente HTTP hacia una API compatible OpenAI | `LLMClient`, `LLMResponse`, `LLMConfigError` | Usado por `first_pass.py` |
| `src/llm_analysis/prompts.py` | Definición del prompt del sistema y construcción del mensaje de usuario | `EVENT_TYPES`, `SYSTEM_PROMPT`, `build_user_message()` | Usado por `first_pass.py` |
| `src/llm_analysis/first_pass.py` | Orquestación concurrente del análisis LLM | `run_first_pass()`, `build_filing_input()`, `parse_llm_response()`, `_call_llm()`, constantes `MAX_CLEAN_TEXT_CHARS`, `PARSE_ERROR`, `DEFAULT_WORKERS` | Usa `client.py`, `prompts.py`, `database.db` |
| `src/company_enrichment/sec_company_tickers.py` | Descarga/parseo del mapeo CIK↔ticker de la SEC | `download_sec_company_tickers()`, `parse_sec_company_tickers()`, `normalize_cik()` | Alimenta `db.upsert_companies` |
| `src/company_enrichment/enrich_filings.py` | Wrapper fino sobre `db.enrich_filings_with_tickers` | `enrich_existing_filings_with_tickers()` | — |
| `src/otcmarkets/otc_screener_importer.py` | Parseo del CSV del OTC Markets Screener | `import_otc_screener_csv()`, `_parse_row()`, `_to_float()`, `_to_int()` | Alimenta `db.upsert_otc_securities` |

**Nota sobre código legado detectado:** `src/document_intelligence/` (con
`extractor.py`, `models.py`, `patterns.py`) y su test
`tests/test_document_intelligence.py` **existían en el historial pero están
eliminados en el estado actual del repositorio** (aparecen como `D` —
deleted— en `git status`, y `grep` no encuentra ninguna referencia viva a
`document_intelligence` en el código Python actual). El README del
dashboard (`dashboard/README.md:45-47`) todavía menciona "extracción
estructurada (Document Intelligence)" en la pantalla Detalle del Filing —
esto es una **inconsistencia entre README y código real** (ver sección 20).

### `dashboard/`

| Archivo | Responsabilidad | Elementos principales |
|---|---|---|
| `dashboard/app.py` | Punto de entrada: `st.set_page_config`, inyección de CSS, `st.navigation` | `main()`, `_inject_css()`, `_render_topbar()` |
| `dashboard/config.py` | Constantes centrales: rutas, umbrales de negocio, paleta de color | `DB_PATH`, `IMPORTANCE_THRESHOLD=70`, `DEFAULT_PAGE_SIZE=50`, `TOP_COMPANIES_LIMIT=15`, `MAX_FULL_TEXT_CHARS=50_000`, `CACHE_TTL_SECONDS=300`, `THEME`, `MARKET_IMPACT_COLORS` |
| `dashboard/database/connection.py` | Conexión SQLite de solo lectura (`mode=ro`), cacheada como recurso | `get_connection()` |
| `dashboard/database/queries.py` | **Todo** el SQL del dashboard; cada función cacheada con `st.cache_data(ttl=300)` | Ver detalle en sección 4/10/11 |
| `dashboard/services/filters.py` | Traduce el estado de filtros de la UI en `WHERE`/parámetros SQL parametrizados | `EventFilters`, `build_where_clause()`, `resolve_order_by()`, `SORT_OPTIONS` |
| `dashboard/services/metrics.py` | Convierte resultados de queries en tarjetas KPI listas para renderizar | `build_kpi_cards()` |
| `dashboard/components/data_table.py` | Tabla de eventos (Explorador) y tabla de resultados de búsqueda (Detalle) + paginación | `render_events_table()`, `render_search_results_table()`, `render_pagination()` |
| `dashboard/components/sidebar_filters.py` | Panel de filtros del Explorador (sidebar), con persistencia en `st.session_state` | `render_sidebar_filters()`, `_widget_key()`, `resolve_last_downloaded_date()` |
| `dashboard/components/detail_sections.py` | Secciones de la pantalla Detalle del Filing | `render_general_info()`, `render_ai_analysis()`, `render_evidence()`, `render_full_text()` |
| `dashboard/components/kpi_card.py` | Tarjetas KPI reutilizables | `render_kpi_row()` |
| `dashboard/components/meter.py` | Medidor de "ratio contra un límite" (usado para % Deep Research) | `render_meter()` |
| `dashboard/components/header.py` | Cabecera de página (título + subtítulo + última actualización) | `render_page_header()` |
| `dashboard/components/section.py` | Contenedor tipo "tarjeta" para envolver gráficas/contenido | `section_card()` (context manager) |
| `dashboard/charts/overview_charts.py` | Gráficas de Resumen Ejecutivo | `category_breakdown_chart()`, `daily_evolution_chart()`, `score_distribution_chart()` |
| `dashboard/charts/stats_charts.py` | Gráficas de Estadísticas | `form_type_chart()`, `monthly_evolution_chart()`, `top_companies_chart()` |
| `dashboard/charts/theme.py` | Layout Plotly compartido | `themed_figure()`, `render_chart()`, `LAYOUT_DEFAULTS` |
| `dashboard/screens/executive_summary.py` | Pantalla "Resumen Ejecutivo" | `render()` |
| `dashboard/screens/event_explorer.py` | Pantalla "Explorador de Eventos" | `render()` |
| `dashboard/screens/filing_detail.py` | Pantalla "Detalle del Filing" (con búsqueda on-demand) | `render()`, `_render_search()` |
| `dashboard/screens/statistics.py` | Pantalla "Estadísticas" | `render()` |
| `dashboard/screens/process_filings.py` | Pantalla "Procesar filings" (ingesta + LLM manuales) | `render()`, `_run_ingest()`, `_render_llm_section()`, `_run_llm_analysis()` |
| `dashboard/utils/formatting.py` | Formateo de fechas/números | `format_iso_timestamp()`, `format_yyyymmdd()`, `compact_number()`, `format_score()`, `yyyymmdd_to_date()`, `date_to_yyyymmdd()` |
| `dashboard/utils/json_helpers.py` | Decodificación segura de columnas `*_json` | `safe_json_list()` |
| `dashboard/styles/main.css` | Tema visual (terminal financiera oscura) | — |
| `dashboard/.streamlit/config.toml` | Tema nativo de Streamlit (widgets oscuros) | — |

### `scripts/`

| Archivo | Responsabilidad |
|---|---|
| `scripts/add_performance_indexes.sql` | Script SQL independiente, ejecutable manualmente con `sqlite3 data/filings.db < ...`, que añade índices de rendimiento a una base de datos ya existente (ver sección 14) |
| `scripts/benchmark_llm_concurrency.py` | Script manual (no forma parte de la suite de tests ni de la CLI) que mide throughput real del LLM a distintos niveles de concurrencia (ver sección 9) |

### `tests/` (backend) y `dashboard/tests/` (dashboard)

Ver sección 15.

---

## 4. Base de datos

Motor: **SQLite**, un único fichero en `data/filings.db` (ruta calculada en
`src/database/db.py:13`: `DB_PATH = <raíz>/data/filings.db`). El fichero está
excluido de git (`*.db` en `.gitignore`), por lo que no se distribuye con el
repositorio y se crea/regenera con `init_db()`. No existe en este checkout
auditado (`data/` no existe) — este documento se basa en el **esquema
definido en el código**, no en una inspección de datos reales.

### Tabla `filings`

Definida en `src/database/db.py:15-34` (`_CREATE_FILINGS_SQL`), con columnas
añadidas incrementalmente vía `_FILINGS_OPTIONAL_COLUMNS`
(`src/database/db.py:96-103`) y aplicadas por `_add_missing_columns()` desde
`init_db()`, de forma que una base de datos ya existente se migra sin
recrearla (`ALTER TABLE ... ADD COLUMN`).

| Columna | Tipo | Origen / cuándo se rellena |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | Automático |
| `cik` | `TEXT NOT NULL` | Del índice SEC (`master.idx`) |
| `company_name` | `TEXT NOT NULL` | Del índice SEC |
| `form_type` | `TEXT NOT NULL` | Del índice SEC (p. ej. `8-K`, `10-K`) |
| `date_filed` | `TEXT NOT NULL` | Del índice SEC, formato `YYYYMMDD` (string, no `DATE`) |
| `filename` | `TEXT NOT NULL UNIQUE` | Ruta relativa del "complete submission" en EDGAR (viene de `master.idx`); es la **clave de deduplicación** |
| `filing_url` | `TEXT NOT NULL` | `https://www.sec.gov/Archives/<filename>`, construida en `src/sec_ingestion/parser.py:26` |
| `raw_text` | `TEXT` (nullable) | Texto crudo del **documento principal únicamente** (no el submission completo), rellenado por `update_filing_content()` cuando se pide `--download-content` |
| `clean_text` | `TEXT` (nullable) | `raw_text` normalizado (HTML strippeado, espacios colapsados) |
| `ticker` | `TEXT` (nullable) | Vía `--enrich-filings` / auto-enriquecido al insertar si `companies` ya tiene datos |
| `exchange` | `TEXT` (nullable) | Columna reservada; **no se rellena en ningún flujo del código actual** (ver sección 20) |
| `otc_tier` | `TEXT` (nullable) | Vía `--enrich-filings-with-otc`, cruce por `ticker` = `otc_securities.symbol` |
| `sec_type` | `TEXT` (nullable) | Igual que `otc_tier` |
| `country` | `TEXT` (nullable) | Igual que `otc_tier` |
| `filing_category` | `TEXT` (nullable) | `EVENT` / `CONTEXT` / `IGNORED`, calculado en el momento de insertar (`insert_filings()`) vía `get_filing_category(form_type)`; para bases de datos antiguas se rellena por lotes en `_backfill_filing_categories()` |
| `created_at` | `TEXT NOT NULL` | ISO-8601 UTC, generado en el `Filing` dataclass |

**Cómo se identifica un filing.** Por `filename` — es el único campo
`UNIQUE` de la tabla, y es el valor de la columna `Filename` del propio
`master.idx` de la SEC (una ruta como
`edgar/data/<cik>/<accession>.txt`), no un ID generado por la aplicación.

**Cómo se evita procesar dos veces el mismo filing.**
- **A nivel de inserción de metadatos:** `insert_filings()`
  (`src/database/db.py:205-248`) usa `INSERT OR IGNORE ... filename` —
  reinsertar el mismo filing (misma fecha reprocesada) es un no-op.
- **A nivel de descarga de contenido:** `get_filenames_with_content()`
  (`src/database/db.py:275-291`) devuelve el conjunto de `filename` que ya
  tienen `clean_text` no vacío; `run_daily_pipeline()`
  (`src/pipeline.py:116-132`) usa ese conjunto para saltarse la descarga de
  los que ya lo tienen, en lugar de volver a pedirlos a EDGAR.
- **A nivel de análisis LLM:** `_llm_selection_where()`
  (`src/database/db.py:423-466`), con `status="pending"`, añade
  `f.filename NOT IN (SELECT filing_filename FROM llm_filing_analysis)` —
  un filing ya analizado no vuelve a enviarse salvo que se pida
  explícitamente `status="analyzed"` o `"all"`.
- **A nivel de escritura del análisis:** `insert_llm_filing_analysis()`
  (`src/database/db.py:554-593`) usa `INSERT OR IGNORE ... filing_filename`
  (columna `UNIQUE`) — un resultado ya existente no se sobrescribe.

### Tabla `companies`

Definida en `src/database/db.py:48-57`.

| Columna | Tipo | Notas |
|---|---|---|
| `cik` | `TEXT PRIMARY KEY` | Zero-padded a 10 dígitos (`normalize_cik()`) |
| `ticker` | `TEXT` | |
| `company_name` | `TEXT` | |
| `exchange` | `TEXT` | Reservada, no se rellena en el flujo actual |
| `source` | `TEXT` | p. ej. `"SEC company_tickers.json"` |
| `updated_at` | `TEXT NOT NULL` | ISO-8601 UTC |

Se puebla con `upsert_companies()` (`INSERT OR REPLACE`) a partir de
`--import-sec-company-tickers`. Cuando esta tabla tiene datos,
`insert_filings()` auto-enriquece con ticker cada filing nuevo
(`_auto_enrich_tickers()`, `src/database/db.py:152-167`), cruzando
`companies.cik` (zero-padded) con `filings.cik` (formateado con
`printf('%010d', CAST(filings.cik AS INTEGER))`).

### Tabla `otc_securities`

Definida en `src/database/db.py:60-72`.

| Columna | Tipo | Notas |
|---|---|---|
| `symbol` | `TEXT PRIMARY KEY` | Cruza con `filings.ticker` |
| `security_name` | `TEXT` | |
| `tier` | `TEXT` | p. ej. Expert Market, OTCQB, OTCQX |
| `price` | `REAL` | |
| `volume` | `INTEGER` | |
| `sec_type` | `TEXT` | p. ej. "Common Stock" |
| `country` | `TEXT` | |
| `source` | `TEXT` | `"OTC Markets Stock Screener"` |
| `updated_at` | `TEXT NOT NULL` | |

**Discrepancia detectada:** el `README.md` principal (sección "Database
schema") documenta también columnas `change_percent` y `state` en esta
tabla; **no existen en el esquema real** (`_CREATE_OTC_SECURITIES_SQL`,
`src/database/db.py:60-71`, ni en `_parse_row()` de
`src/otcmarkets/otc_screener_importer.py:29-39`). Es documentación
desactualizada, no código legado (ver sección 20).

Se puebla con `upsert_otc_securities()` (`INSERT OR REPLACE`) desde
`--import-otc-screener-csv <path>`. `enrich_filings_with_otc()`
(`src/database/db.py:387-418`) actualiza `filings.otc_tier/sec_type/country`
cruzando por `ticker = symbol`.

### Tabla `llm_filing_analysis`

Definida en `src/database/db.py:74-93`, con columnas opcionales añadidas
vía `_LLM_FILING_ANALYSIS_OPTIONAL_COLUMNS`
(`src/database/db.py:105-109`).

| Columna | Tipo | Origen |
|---|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | |
| `filing_filename` | `TEXT NOT NULL UNIQUE` | FK lógica hacia `filings.filename` (no hay `FOREIGN KEY` declarada en SQL, es una relación por convención) |
| `provider` | `TEXT` | `LLMClient.provider`, de `LLM_PROVIDER` en `.env` |
| `model` | `TEXT` | `LLMClient.model`, de `LLM_MODEL` en `.env` |
| `primary_event_type` | `TEXT` | Del JSON del LLM, o `PARSE_ERROR` |
| `secondary_event_types_json` | `TEXT` | JSON-encoded list |
| `is_material` | `INTEGER` (0/1) | |
| `importance_score` | `INTEGER` (0-100) | |
| `market_impact` | `TEXT` | `LOW`\|`MEDIUM`\|`HIGH`\|`VERY_HIGH` |
| `deep_research` | `INTEGER` (0/1) | |
| `summary` | `TEXT` | |
| `key_entities_json` | `TEXT` | JSON-encoded list |
| `evidence_json` | `TEXT` | JSON-encoded list |
| `reason_for_score` | `TEXT` | |
| `next_step` | `TEXT` | `IGNORE`\|`WATCH`\|`RESEARCH` |
| `raw_response` | `TEXT` | Respuesta cruda del LLM, **se guarda incluso si el parseo falla** |
| `created_at` | `TEXT NOT NULL` | ISO-8601 UTC |

**Relación `filings` ↔ `llm_filing_analysis`:** 0-o-1 análisis por filing
(no hay reintentos automáticos ni historial de versiones — un filing
analizado con éxito no vuelve a analizarse salvo selección explícita
`status="analyzed"`/`"all"`). El join se hace siempre por
`l.filing_filename = f.filename` (p. ej.
`dashboard/database/queries.py:20-24`, `src/database/db.py:542`).

**Estados booleanos/manuales existentes.** En el estado actual del
esquema **no existe ningún campo de revisión/descarte manual** (columnas
tipo `is_material`/`deep_research` son generadas por el LLM, no por un
humano). Una funcionalidad de descarte manual (`manual_discarded`) fue
diseñada e implementada durante el desarrollo de este proyecto y
**posteriormente revertida por completo** — no queda ni rastro en el
esquema, en las queries ni en la UI (ver sección 13, que documenta esto
como "no implementado actualmente").

### Índices

Definidos en dos sitios:

1. **En `init_db()`** (siempre, cualquier base de datos, nueva o existente),
   `src/database/db.py:194-195`:
   - `idx_filings_ticker_nocase` — `filings(ticker COLLATE NOCASE)`
   - `idx_filings_company_name_nocase` — `filings(company_name COLLATE NOCASE)`

   Motivo documentado en el propio código (`src/database/db.py:36-41`): la
   búsqueda on-demand de "Detalle del Filing" (`search_event_filings`) hace
   `LIKE 'prefijo%'` case-insensitive sobre `ticker`/`company_name`; sin
   estos índices `NOCASE`, SQLite no puede usar un índice para ese patrón y
   hace escaneo completo (medido: ~2.4s–6.5s por búsqueda; con el índice,
   ~10ms).

2. **En `scripts/add_performance_indexes.sql`** (script manual, opcional,
   **no se ejecuta automáticamente** desde `init_db()`):
   - `idx_filings_category_date` — `filings(filing_category, date_filed)`
   - `idx_filings_created_at` — `filings(created_at)`
   - Repite los dos índices `NOCASE` de arriba (con `IF NOT EXISTS`, así que
     ejecutarlo no duplica nada si `init_db()` ya los creó).

   Motivo documentado en el propio script (comentario, líneas 1-20): con
   `filings` creciendo hasta 16 GB, cualquier consulta que filtrase por
   `filing_category` y/o ordenase por `date_filed` (prácticamente todas las
   del dashboard: `get_category_counts`, `get_summary_kpis`,
   `get_daily_filing_counts`, `get_form_type_counts`,
   `get_monthly_event_counts`, `get_top_companies`, `get_event_date_bounds`,
   `get_filter_options`, `get_events_count`, `get_events_page`) hacía
   escaneo completo (~7.3s medidos); igualmente `get_last_updated`
   (`SELECT MAX(created_at)`).

No hay índice explícito sobre `llm_filing_analysis.filing_filename` más
allá del implícito que crea `UNIQUE`.

---

## 5. Ingesta desde SEC EDGAR

Módulo: `src/sec_ingestion/`.

**1. Obtención del índice.** `fetch_daily_index()`
(`src/sec_ingestion/daily_index.py:29-42`) hace un `GET` HTTP simple
(`requests.get`, sin sesión persistente — cada llamada de índice es una
petición aislada, no forma parte del batch de descargas de contenido) a:

```
https://www.sec.gov/Archives/edgar/daily-index/<año>/QTR<trimestre>/master.<YYYYMMDD>.idx
```

construida por `build_index_url()` (`daily_index.py:20-26`), con el
trimestre calculado por `get_quarter(month) = (month-1)//3 + 1`. Si SEC
devuelve `404`, se lanza `FileNotFoundError` con un mensaje explícito
("puede ser fin de semana/festivo/fecha futura"); cualquier otro error HTTP
se propaga vía `response.raise_for_status()`.

**2. Contenido del índice.** `master.idx` es un fichero de texto
pipe-delimited (`CIK|Company Name|Form Type|Date Filed|Filename`), con una
cabecera y una línea de guiones antes de los datos.

**3. Parseo.** `parse_master_idx()` (`src/sec_ingestion/parser.py:30-50`)
detecta el inicio de la sección de datos buscando la línea que empieza por
`"CIK|"`, salta la línea de guiones, y delega cada línea a `parse_line()`
(`parser.py:11-27`), que:
- Descompone por `|` y exige exactamente 5 campos.
- Descarta la línea si `cik` no es numérico (esto también descarta de forma
  natural la propia cabecera).
- Construye un `Filing` con `filing_url = f"{SEC_ARCHIVES_BASE}/{filename}"`
  (`SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives"`).

**4. Qué filings interesan / filtrado por formulario.**
`get_filtered_filings()` (`daily_index.py:45-57`) descarga el índice,
parsea todo, y se queda solo con los filings cuya `get_filing_category(form_type)
!= IGNORED` — es decir, **los `IGNORED` se descartan aquí, antes de tocar la
base de datos**; nunca llegan a `filings`. Esto ocurre antes de cualquier
inserción en SQLite (a diferencia de `filing_category`, que sí se guarda en
la fila para EVENT/CONTEXT).

**5. EVENT/CONTEXT.** Ver sección 7 completa; resumen: `EVENT_FORMS` y
`CONTEXT_FORMS` son dos `set` fijos en `src/filing_routing/routing.py`.

**6. Construcción de la URL de descarga del contenido.** No hay una URL
distinta para "el documento principal": `filing_url` (la misma que ya se
guardó al insertar el filing) **siempre apunta al "complete submission text
file"** completo de la SEC (el `.txt` que agrupa todos los documentos de la
accession). Esto está documentado explícitamente en el código
(`src/sec_ingestion/downloader.py:131-148`) y es la base de la extracción
del documento principal descrita en la sección 6.

**7. Descarga.** `fetch_raw()` (`downloader.py:40-70`) hace el `GET`
propiamente dicho. Puede recibir una `requests.Session` persistente
(ver siguiente punto) o, si no se pasa ninguna, cae a `requests.get()` con
cabecera `User-Agent` explícita.

**8. Fair Access / rate limiting.** SEC EDGAR exige quedarse por debajo de
10 req/s (comentario en `downloader.py:16-19`, con enlace a
`https://www.sec.gov/os/accessing-edgar-data`). El código aplica dos
mecanismos:
- `DEFAULT_DELAY = 0.2` s entre descargas de contenido (~5 req/s, margen
  cómodo).
- `MIN_SAFE_DELAY = 0.11` s (~9.1 req/s) como **suelo obligatorio**:
  `fetch_and_clean()` siempre hace `time.sleep(max(delay, MIN_SAFE_DELAY))`
  (`downloader.py:255`), de modo que ningún llamante (CLI, dashboard, un
  futuro caller) puede forzar un ritmo por encima del límite de Fair
  Access, pase lo que pase como argumento `delay`.

Este delay se aplica **solo a la descarga del contenido del filing**
(`fetch_and_clean`), no a la descarga del índice diario ni a la descarga del
mapeo de tickers (una única petición cada una).

**9. Sesión HTTP persistente.** `build_session(user_agent)`
(`downloader.py:29-37`) crea una `requests.Session()` con la cabecera
`User-Agent` ya fijada, reutilizada para **todo un lote de descargas**
(una sesión por llamada a `run_daily_pipeline`, nunca una por filing). El
propio código documenta la motivación y el resultado medido: evitar un
nuevo handshake TCP+TLS por filing, ~2.9x más rápido que `requests.get()`
suelto para las mismas URLs.

**10. Retries/backoff.**
- **Descarga de contenido** (`fetch_raw`, `downloader.py:40-70`): reintenta
  hasta `_MAX_RETRIES = 2` veces, solo ante un `5xx` de SEC, con backoff
  lineal `_RETRY_BACKOFF_SECONDS * (intento+1)` (2s, 4s). Un `4xx` u otro
  `RequestException` **no se reintenta** — se asume que fallaría igual.
- **Índice diario** (`fetch_daily_index`): sin reintentos; un `404` se
  traduce en `FileNotFoundError`, cualquier otro error se propaga.

**11. Cómo se evitan descargas repetidas.** Ver sección 4 ("Cómo se evita
procesar dos veces el mismo filing"): `get_filenames_with_content()` +
comprobación previa al bucle de descarga en `run_daily_pipeline`
(`src/pipeline.py:116-132`) — el filing se cuenta como
`content_skipped_existing` y se emite igualmente un evento de progreso
(`skipped=True`) para que la barra de progreso del dashboard/CLI avance sin
parecer colgada.

**12. Registro de errores.** Cada fallo de descarga individual se captura
(`try/except Exception`, `src/pipeline.py:137-143`), se cuenta en
`result.content_failed`, y se añade un mensaje textual a
`DailyPipelineResult.errors` (lista de strings). El pipeline **nunca aborta
el lote entero** por un filing fallido — sigue con el siguiente. El CLI
(`src/main.py:190-205`) resume el resultado final (descargados, MB
transferidos/almacenados, omitidos, fallidos) y avisa con `logger.warning`
si hubo fallos; el dashboard (`process_filings.py:161-165`) muestra los
errores en un `st.expander`.

---

## 6. Extracción del documento principal

Esta es la pieza más específica del proyecto y está implementada en
`src/sec_ingestion/downloader.py:131-257`.

### El problema de origen

`filing_url` (calculado desde `master.idx`) siempre apunta al **"complete
submission text file"**: un envoltorio SGML que concatena, en un único
`.txt`, el formulario principal **más todos los exhibits, instancias XBRL,
cartas de consentimiento, gráficos, etc.** de esa accession. El propio
código documenta un caso real auditado (comentario,
`downloader.py:131-148`): un 8-K de HyOrc Corp
(`edgar/data/1070789/0001493152-26-036791.txt`) con **162 documentos**
dentro de un único submission de ~129 MB, donde el 8-K real es solo el
primero. Guardar el `.txt` completo habría inflado el almacenamiento (y el
coste de limpieza de texto) en función del número de exhibits de cada
filing, no del contenido real del formulario.

### El proceso: SGML → bloques `<DOCUMENT>` → `SEQUENCE=1` → `<TEXT>`

`extract_primary_document(raw_submission)` (`downloader.py:168-205`):
1. Localiza el **primer** `<DOCUMENT>` con `str.find()` (no una regex sobre
   todo el fichero) — como solo interesa el primero, la búsqueda nunca
   necesita recorrer los exhibits que vienen después, aunque sean el 99%
   del fichero.
2. Dentro de ese bloque, localiza `<TEXT>` y comprueba que no aparece antes
   un `</DOCUMENT>` (lo que indicaría un bloque malformado sin `<TEXT>`).
3. Extrae del encabezado (todo lo que hay entre `<DOCUMENT>` y `<TEXT>`) los
   metadatos `<TYPE>` y `<SEQUENCE>` vía regex (`_TYPE_RE`, `_SEQUENCE_RE`).
4. Extrae el contenido entre `<TEXT>` y `</TEXT>`, recortando saltos de
   línea sobrantes al principio/final.
5. Devuelve un `PrimaryDocumentResult(text, doc_type, sequence, found)`.

**Por qué SEQUENCE=1 es fiable.** El comentario del módulo documenta que
esto se confirmó contra filings reales de **todos los tipos de formulario
EVENT** que enruta este proyecto (`8-K`, `8-K/A`, `DEF 14A`, `DEFM14A`,
`PREM14A`, `S-1`, `S-1/A`, `SC 13D`, `SC 13D/A`, `SC 13E3`, `SC TO-C`,
`SC TO-I`, `SC TO-T`, `424B3`, `424B5`): el primer `<DOCUMENT>` (SEQUENCE=1)
es siempre el documento principal/sustantivo.

**Validación de `TYPE` — solo advertencia, nunca rechazo.** Se encontró un
caso real (un `SC TO-T/A` que EDGAR también referenciaba como
`SC 13D/A`, donde `master.idx` decía "SC 13D/A" pero el propio
`<DOCUMENT><TYPE>` del submission decía "SC TO-T/A") en el que
`SEQUENCE=1` seguía siendo el documento correcto pese al desacuerdo de tipo.
Por eso, en `fetch_and_clean()` (`downloader.py:242-246`), un
`expected_type` (el `form_type` de `master.idx`) que no coincide con el
`doc_type` real del documento **solo genera un `logger.warning`**, nunca
descarta ni rechaza el documento.

**Fallback si no hay estructura `<DOCUMENT>`/`<TEXT>`.** Si
`extract_primary_document` devuelve `found=False` (formato inesperado, p.
ej. un filing muy antiguo en texto plano), `fetch_and_clean()`
(`downloader.py:247-252`) usa **el submission completo** como `raw_text`, y
registra un `logger.warning` pidiendo auditar ese filing.

### Qué se guarda

- **`raw_text`** — únicamente el contenido de `<TEXT>...</TEXT>` del bloque
  `SEQUENCE=1` (o el submission completo en el caso de fallback anterior).
  **Nunca** se guardan los exhibits ni el resto de bloques `<DOCUMENT>`.
- **`clean_text`** — `raw_text` pasado por `clean_filing_text()`
  (`downloader.py:113-128`): si el texto parece HTML (detectado por regex
  sobre etiquetas comunes `_HTML_TAG_RE`), se extrae el texto con
  BeautifulSoup (`html.parser`, con fallback a `lxml` si está instalado, y
  a un `strip_tags_regex` de última instancia si ambos parsers fallan);
  después se normaliza (`_normalize`: colapsa espacios/tabs repetidos,
  limita líneas en blanco consecutivas a una sola). `clean_filing_text`
  **nunca lanza excepción**.

### Qué ocurre con el complete submission después

**No se persiste en ningún sitio.** `fetch_and_clean()` lo mantiene solo en
memoria (`raw_submission`, variable local) mientras dura la llamada; una
vez extraído `raw_text`, la variable queda fuera de alcance y se descarta al
salir de la función. La base de datos nunca almacena el submission
completo, solo el documento principal ya recortado.

### Bytes descargados vs. bytes almacenados

`fetch_and_clean()` devuelve una tupla de 4 elementos:
`(raw_text, clean_text, downloaded_bytes, stored_bytes)`
(`downloader.py:208-257`):
- `downloaded_bytes = len(raw_submission.encode("utf-8"))` — lo realmente
  transferido desde SEC (para contabilidad de velocidad/Fair Access).
- `stored_bytes = len(raw_text.encode("utf-8"))` — el tamaño de lo que
  finalmente se guarda en `raw_text`.

Estas dos métricas se acumulan en `DailyPipelineResult.content_bytes_downloaded`
/ `content_bytes_stored` (`src/pipeline.py:70-71,148-149`) y se muestran
tanto en el resumen de la CLI (`src/main.py:190-201`, con el % de reducción
calculado como `(1 - stored/downloaded) * 100`) como en las tarjetas KPI del
dashboard (`dashboard/screens/process_filings.py:130-153`).

### Motivación técnica y efecto

- **Almacenamiento:** el tamaño guardado depende del tamaño real del
  formulario, no del número de exhibits adjuntos — evita que un submission
  de 129 MB (162 documentos) infle la base de datos con contenido que nunca
  se usa (los exhibits no se analizan ni se muestran).
- **Rendimiento del LLM:** el texto que se envía al modelo
  (`MAX_CLEAN_TEXT_CHARS = 20000`, ver sección 8) es mucho más probable que
  contenga contenido relevante desde el principio, en vez de estar dominado
  por anexos legales o XBRL.
- **Rendimiento de extracción:** `str.find()` nunca recorre más allá del
  final del primer bloque `<TEXT>` — el coste de extraer el documento
  principal es independiente del tamaño de los exhibits que le siguen.

---

## 7. Clasificación EVENT / CONTEXT

Definida íntegramente en `src/filing_routing/routing.py`.

```python
EVENT = "EVENT"
CONTEXT = "CONTEXT"
IGNORED = "IGNORED"

EVENT_FORMS = {
    "8-K", "8-K/A", "SC TO-I", "SC TO-T", "SC TO-C", "SC 13D", "SC 13D/A",
    "SC 13E3", "S-1", "S-1/A", "424B3", "424B5", "DEF 14A", "DEFM14A",
    "PREM14A",
}

CONTEXT_FORMS = {
    "10-K", "10-Q", "20-F", "6-K",
}
```

- **`EVENT`** — formularios que pueden representar un evento corporativo
  material (fusiones/adquisiciones, ofertas públicas de adquisición
  (`SC TO-*`), cambios de control (`SC 13D*`, `SC 13E3`), registro de
  valores (`S-1*`), folletos (`424B3`/`424B5`), poderes de voto/juntas
  (`DEF 14A`, `DEFM14A`, `PREM14A`), y el genérico `8-K`/`8-K/A` de eventos
  materiales). Estos filings reciben "el tratamiento completo": se
  descargan, se limpian y se envían al LLM.
- **`CONTEXT`** — informes periódicos (`10-K`, `10-Q`, `20-F`, `6-K`). Se
  almacenan (metadatos + opcionalmente contenido si `--download-content`
  está activo, ya que el filtro de descarga de contenido no distingue
  EVENT/CONTEXT) pero **nunca se envían al LLM por sí solos** — la
  selección para el LLM (`_llm_selection_where`) siempre filtra
  `f.filing_category = ?` con `category=EVENT` por defecto. Sirven, según
  la intención documentada (README, comentarios de `routing.py`), como
  contexto histórico para un filing EVENT relacionado, aunque **no existe
  en el código actual ningún mecanismo automático que vincule un CONTEXT
  con un EVENT** — la recuperación "bajo demanda" mencionada en el README
  no tiene una implementación de "recuperar el 10-K más reciente de esta
  empresa" en la base de código auditada (`No determinado a partir del
  código` más allá de que la fila queda disponible en `filings` y es
  consultable por `company_name`/`ticker` desde "Detalle del Filing").
- **`IGNORED`** — cualquier otro `form_type`. Se descarta en
  `get_filtered_filings()` (sección 5) **antes** de llegar a la base de
  datos — no se guarda ni una fila para ellos.

`get_filing_category(form_type)` (`routing.py:41-48`) normaliza
(`strip().upper()`) antes de comparar, así que la clasificación es
insensible a mayúsculas/espacios.

**Dónde se aplica y cuándo:**
1. En la ingesta, para decidir qué filings guardar (`daily_index.py:52`).
2. Al insertar cada fila, para rellenar `filings.filing_category`
   (`insert_filings()`, `src/database/db.py:233`).
3. En una migración retroactiva (`_backfill_filing_categories()`,
   `db.py:135-149`) para bases de datos creadas antes de que existiera esta
   columna — agrupa por `form_type` distinto (no fila a fila) para no
   escanear toda la tabla por cada actualización.
4. Como filtro obligatorio en toda selección de candidatos al LLM
   (`_llm_selection_where`).

**Consecuencias posteriores:** solo los `EVENT` entran en el pipeline de
LLM, en el Explorador de Eventos, y en las estadísticas del dashboard (todas
las queries de `dashboard/database/queries.py` que tocan
`llm_filing_analysis` o alimentan el Explorador filtran
`filing_category = 'EVENT'`).

---

## 8. Pipeline de análisis mediante LLM

Módulo: `src/llm_analysis/` (`client.py`, `prompts.py`, `first_pass.py`).

### Selección de filings

La única fuente de verdad para "qué filings son candidatos al LLM" es
`_llm_selection_where()` (`src/database/db.py:423-466`), reutilizada tanto
por la ejecución real (`get_event_filings_needing_llm_analysis`) como por
los contadores de previsualización (`get_llm_selection_counts`) y la tabla
de previsualización (`get_llm_selection_preview`), de forma que la CLI, el
preview del dashboard y la ejecución real nunca puedan discrepar sobre qué
filings entrarían.

Cláusulas siempre presentes:
- `f.filing_category = ?` (por defecto `EVENT`, parametrizable vía
  `category` — aunque en la práctica solo se usa `EVENT`).
- `f.clean_text IS NOT NULL AND f.clean_text != ''` — solo filings con
  contenido ya descargado.

Cláusulas condicionales:
- `f.date_filed >= ?` / `<= ?` si se pasan `date_from`/`date_to`.
- `f.form_type IN (...)` si se pasa `form_types`.
- Según `status`: `"pending"` (por defecto) excluye los ya presentes en
  `llm_filing_analysis`; `"analyzed"` exige lo contrario; `"all"` no añade
  cláusula.

### Texto enviado

`build_filing_input()` (`src/llm_analysis/first_pass.py:31-46`) construye,
por filing:
```python
{
    "company_name": ..., "ticker": ..., "form_type": ..., "filing_url": ...,
    "items": _load_json_list(filing.get("items_json")),      # ver nota legado
    "keywords": _load_json_list(filing.get("keywords_json")), # ver nota legado
    "clean_text": (filing.get("clean_text") or "")[:MAX_CLEAN_TEXT_CHARS],
}
```
**Límite de caracteres:** `MAX_CLEAN_TEXT_CHARS = 20000`
(`first_pass.py:17`) — un simple slicing de Python (`[:20000]`), no un
tokenizador; no hay límite de tokens explícito en el código, solo este
límite de caracteres.

**Nota de código legado:** `items_json`/`keywords_json` no existen como
columnas en la tabla `filings` actual (ver sección 4) ni los produce
ninguna query del pipeline (`get_event_filings_needing_llm_analysis`
selecciona solo `filename, company_name, ticker, form_type, filing_url,
clean_text`). En la práctica `filing.get("items_json")` siempre devuelve
`None` → `_load_json_list(None)` devuelve `[]`. Es un resto de una etapa de
extracción estructurada anterior (el módulo `document_intelligence`, ya
eliminado — ver sección 3/20) que nunca se limpió de `first_pass.py`. No
afecta al resultado (las listas quedan vacías), pero es código muerto/legado
a señalar en la memoria.

### Construcción del prompt

`src/llm_analysis/prompts.py`:
- **`SYSTEM_PROMPT`** (`prompts.py:43-118`) — prompt extenso, fijo, con:
  - Rol: "analista senior de research event-driven en un hedge fund".
  - Instrucciones de clasificación: evento principal, eventos secundarios,
    materialidad, impacto de mercado potencial, si merece deep research.
  - Esquema de salida JSON estricto (ver más abajo).
  - Lista cerrada de 32 valores válidos para `primary_event_type`/
    `secondary_event_types` (`EVENT_TYPES`, `prompts.py:5-39`): `ROUTINE`,
    `MANAGEMENT_CHANGE`, `BOARD_CHANGE`, `AUDITOR_CHANGE`,
    `MATERIAL_AGREEMENT`, `ASSET_SALE`, `ASSET_ACQUISITION`, `MERGER`,
    `REVERSE_MERGER`, `SPIN_OFF`, `TENDER_OFFER`, `GOING_PRIVATE`,
    `CHANGE_OF_CONTROL`, `ACTIVIST_INVESTOR`, `PROXY_CONTEST`,
    `DEBT_FINANCING`, `CONVERTIBLE_FINANCING`, `EQUITY_OFFERING`,
    `IPO_REGISTRATION`, `SPAC_TRANSACTION`, `BANKRUPTCY_DISTRESS`,
    `DELISTING_RISK`, `SHARE_REPURCHASE`, `STOCK_SPLIT`, `DIVIDEND`,
    `LITIGATION`, `REGULATORY_INVESTIGATION`, `REGULATORY_APPROVAL`,
    `CONTRACT_AWARD`, `IMPAIRMENT_OR_RESTATEMENT`, `CREDIT_RATING_CHANGE`,
    `GUIDANCE_UPDATE`, `OTHER_MATERIAL_EVENT`.
  - Reglas explícitas anti-sesgo: no clasificar por frecuencia de palabras
    clave, ignorar boilerplate (Risk Factors, Forward-Looking Statements,
    exhibits, disclaimers legales).
  - Puntos de referencia fijos de calibración de `importance_score` (p. ej.
    cambio de auditor rutinario ≈15, retiro de CEO ordenado ≈40, nuevo CFO
    ≈55, emisión de deuda de $500M ≈80, tender offer ≈95, reverse merger
    ≈98, quiebra ≈99).
  - Instrucción explícita de que `deep_research` es "un recurso escaso":
    debe activarse en ~10-20% de los filings.
  - `next_step` como señal para la siguiente etapa: `IGNORE`/`WATCH`/
    `RESEARCH`.
- **`build_user_message(filing_input)`** (`prompts.py:121-140`) — arma el
  mensaje de usuario en texto plano: Company, Ticker, Form type, Filing
  URL, Items referenced (vacío en la práctica, ver nota legado), Keywords
  (solo si existen, con la advertencia explícita de que son "context only,
  not a classification signal"), y finalmente el `clean_text` (posiblemente
  truncado).

### Modelo/proveedor configurables

`LLMClient` (`src/llm_analysis/client.py:33-67`) lee de variables de
entorno (`.env`, cargado por `python-dotenv`):
- `LLM_API_KEY` (obligatoria)
- `LLM_BASE_URL` (obligatoria) — cualquier API compatible con el formato de
  chat completions de OpenAI (el propio docstring cita OpenAI, Grok/x.ai, o
  un proxy local; el README menciona también OpenRouter como proveedor
  observado en la práctica, ver comentario sobre Gemini vía OpenRouter en
  `first_pass.py:69-76`).
- `LLM_MODEL` (obligatoria)
- `LLM_PROVIDER` (opcional, por defecto `"openai-compatible"`; se guarda tal
  cual junto a cada resultado)

Si falta cualquiera de las tres obligatorias, el constructor lanza
`LLMConfigError` inmediatamente (fail-fast, antes de intentar ninguna
llamada).

### Llamada HTTP

`LLMClient.chat_completion(system_prompt, user_message)`
(`client.py:68-121`):
```python
POST {base_url}/chat/completions
{
  "model": ..., "temperature": 0,
  "messages": [{"role": "system", ...}, {"role": "user", ...}]
}
```
`temperature=0` para determinismo. Timeout por defecto 60s
(`DEFAULT_TIMEOUT`). Sin estado compartido entre llamadas — cada llamada
abre su propia conexión, por lo que es segura desde múltiples hilos a la
vez (importante para la concurrencia, ver sección 9).

### Estructura esperada de la respuesta

```json
{
  "primary_event_type": "<uno de EVENT_TYPES>",
  "secondary_event_types": ["..."],
  "is_material": true/false,
  "importance_score": 0-100,
  "market_impact": "LOW|MEDIUM|HIGH|VERY_HIGH",
  "deep_research": true/false,
  "summary": "máx. 4 frases",
  "key_entities": ["..."],
  "evidence": ["..."],
  "reason_for_score": "...",
  "next_step": "IGNORE|WATCH|RESEARCH"
}
```

### Parsing de la respuesta

`parse_llm_response()` (`first_pass.py:82-96`):
1. `_strip_markdown_fence()` (`first_pass.py:68-79`) — quita un posible
   ` ```json ... ``` ` que envuelva el JSON (observado en la práctica con
   Gemini vía OpenRouter, aunque el prompt pide JSON puro).
2. `json.loads(...)`.
3. Si falla el parseo, o el resultado no es un `dict` (p. ej. es una lista
   JSON válida pero no un objeto), se devuelve `_parse_error_result(reason)`.
4. Si tiene éxito, se devuelven **todos los campos tal cual los devolvió el
   modelo** (no hay validación de esquema estricta más allá de esto — un
   campo faltante o de tipo inesperado no se corrige aquí, se guarda tal
   cual).

### `PARSE_ERROR`

`_parse_error_result(reason)` (`first_pass.py:49-62`) produce un dict con
`primary_event_type = "PARSE_ERROR"`, `importance_score = 0`,
`market_impact = None`, `deep_research = False`, listas vacías, y
`reason_for_score` con el motivo textual del fallo (p. ej. `"JSON parse
error: ..."`). **Este resultado se guarda igualmente** en
`llm_filing_analysis` (incluyendo `raw_response` con la respuesta cruda del
modelo) — el filing no se pierde, queda marcado y auditable.

### Almacenamiento de resultados

`insert_llm_filing_analysis()` (`src/database/db.py:554-593`) — `INSERT OR
IGNORE`, un registro por `filing_filename`. Persiste todos los campos
descritos en la sección 4 (tabla `llm_filing_analysis`).

### Todos los campos del análisis (nombres reales)

`primary_event_type`, `secondary_event_types` (↔ `secondary_event_types_json`
en BD), `is_material`, `importance_score`, `market_impact`, `deep_research`,
`summary`, `key_entities` (↔ `key_entities_json`), `evidence` (↔
`evidence_json`), `reason_for_score`, `next_step`. Además, a nivel de fila
(no del JSON del modelo): `provider`, `model`, `raw_response`, `created_at`.

---

## 9. Paralelización del LLM

Implementado en `src/llm_analysis/first_pass.py::run_first_pass`
(líneas 115-215).

**Mecanismo:** `ThreadPoolExecutor(max_workers=workers)`
(`workers` configurable, por defecto `DEFAULT_WORKERS = 5`,
`first_pass.py:19`). Se lanza una tarea por filing pendiente:
```python
futures = [executor.submit(_call_llm, client, filing) for filing in pending]
for done, future in enumerate(as_completed(futures), start=1):
    ...
```
`as_completed` procesa los resultados **en orden de finalización**, no en
el orden original de selección — el propio docstring lo documenta
explícitamente como una diferencia inherente frente a un futuro modo
secuencial.

**Por qué hilos y no async/multiprocessing:** el trabajo es I/O-bound
(esperar la red), justificación explícita en el docstring de
`run_first_pass` (`first_pass.py:135-138`).

**`_call_llm(client, filing)`** (`first_pass.py:99-112`) — se ejecuta en el
hilo worker: construye el input y el mensaje de usuario, hace la llamada
HTTP, y **nunca lanza excepción** — captura cualquier error y devuelve
`(filing, None, str(exc))`. Esto garantiza que un `future.result()` nunca
propague una excepción y que un filing con error no tumbe el
`ThreadPoolExecutor` ni deje un future sin resolver.

**Por qué SQLite sigue escribiendo desde un único hilo.** El parseo de la
respuesta (`parse_llm_response`) y la escritura
(`insert_llm_filing_analysis`) ocurren **siempre en el hilo llamante**
(el que ejecuta el bucle `for done, future in enumerate(as_completed(...))`),
nunca dentro de `_call_llm`. El docstring lo explicita: nunca hay más de
una escritura SQLite en vuelo a la vez, así que no hace falta ningún
lock/cola para evitar `"database is locked"` — sencillamente nunca hay un
segundo escritor con quien contender.

**Gestión individual de errores.** Si `_call_llm` devuelve `error is not
None`: se cuenta en `errors`, se loggea, y se notifica vía `on_progress`
con `ok=False` — el bucle continúa con el siguiente resultado sin abortar
el resto del lote (`first_pass.py:189-194`).

**Reintentos, 429, 5xx, timeout, backoff** — implementados en
`LLMClient.chat_completion` (`src/llm_analysis/client.py:68-136`), no en
`first_pass.py`:
- `_MAX_RETRIES = 3`, `_RETRY_BACKOFF_SECONDS = 2.0`.
- `_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}`.
- En un `429`, se respeta la cabecera `Retry-After` si está presente,
  acotada a `_MAX_RETRY_AFTER_SECONDS = 30.0` (para que un proveedor mal
  comportado no bloquee un lote indefinidamente); si no hay cabecera, o el
  código no es 429, backoff exponencial: `2.0 * (2 ** intento)`.
- `Timeout` y `ConnectionError` de `requests` también se reintentan con la
  misma política de backoff.
- Cualquier otro `4xx` (distinto de 429) o error no contemplado se propaga
  de inmediato — no se reintenta.
- El comentario del módulo (`client.py:13-16`) documenta explícitamente que
  esta política de reintentos es más necesaria aún al correr en paralelo,
  ya que varios workers compartiendo el rate limit del mismo proveedor
  hacen los 429 más probables que en el bucle secuencial original.

**Cálculo de velocidad y ETA.** Dentro de `run_first_pass`,
`_timing_fields(done)` (`first_pass.py:172-181`):
```python
elapsed = time.monotonic() - started
speed_per_min = (done / elapsed) * 60
avg_seconds_per_item = elapsed / done
eta_seconds = avg_seconds_per_item * (total - done)
```
Se recalcula tras cada filing completado (no una estimación previa) y se
expone en el diccionario `info` pasado a `on_progress`, junto con
`workers`. La CLI (`src/main.py:228-254`) y el dashboard
(`dashboard/screens/process_filings.py:317-333`) consumen esta misma
información para mostrar "Velocidad: X filings/min" y "ETA: Xm Ys".

### Benchmarks existentes en el repositorio

`scripts/benchmark_llm_concurrency.py` — script manual (no forma parte de
`pytest`, no se ejecuta en CI), documentado como el método usado para
elegir el valor por defecto de `workers`. Metodología:
- Toma una muestra fija de `SAMPLE_SIZE = 16` filings EVENT recientes
  (`status="all"`, para que resultados ya analizados también cuenten como
  "trabajo hecho" y no haya diferencia de qué se procesa entre
  configuraciones).
- Ejecuta `run_first_pass` real (llamadas de pago reales al proveedor
  configurado en `.env`) con `WORKER_COUNTS = [1, 2, 4, 6]`.
- Instrumenta `LLMClient.chat_completion` (monkeypatch temporal) para medir
  la duración de cada llamada individual, y añade un
  `logging.Handler` (`_StatusCounter`) que cuenta cuántos 429/5xx se
  observaron (incluso los que un reintento absorbió sin llegar a fallar el
  batch).
- Imprime, por configuración: tiempo total, filings/min, errores, número de
  429, tiempo medio por llamada, y una tabla resumen final.

**No se han encontrado resultados numéricos de este benchmark persistidos
en el repositorio** (ni fichero de salida, ni resultados pegados en un
README/comentario) — `No determinado a partir del código` en cuanto a las
cifras concretas que llevaron a fijar `DEFAULT_WORKERS = 5`; solo se puede
documentar la metodología del script, no sus resultados históricos.

---

## 10. Dashboard

Aplicación Streamlit multipágina (`st.navigation`/`st.Page`, definida en
`dashboard/app.py:57-73`), tema oscuro fijo tipo terminal financiera
(`dashboard/styles/main.css`, `dashboard/.streamlit/config.toml`).
Conexión SQLite de solo lectura (`dashboard/database/connection.py`,
`mode=ro`) para **todas** las pantallas salvo "Procesar filings", que
escribe indirectamente vía `src.pipeline` (ver sección 12).

Las 5 pantallas reales (nombres tal como aparecen en la navegación,
`dashboard/app.py:62-66`):

### Resumen Ejecutivo (`dashboard/screens/executive_summary.py`)

- **Objetivo:** vista general del estado del pipeline al abrir la app
  (página por defecto, sin `url_path` propio — solo resuelve en `/`).
- **Información mostrada:** fila de KPIs (`build_kpi_cards`, ver sección
  14), gráfica de eventos por categoría (barra horizontal EVENT vs
  CONTEXT), gráfica de evolución diaria (línea, EVENT vs CONTEXT por
  `date_filed`), histograma de distribución del `importance_score`.
- **Consultas:** `get_summary_kpis`, `get_category_counts`,
  `get_daily_filing_counts`, `get_importance_score_distribution`,
  `get_last_updated` (todas en `dashboard/database/queries.py`, cacheadas
  con `st.cache_data(ttl=300)`).
- **Filtros:** ninguno — vista agregada fija.
- **Interacción:** ninguna más allá de las gráficas Plotly (hover); no hay
  navegación a otras pantallas desde aquí.
- **Estado de sesión:** no usa `st.session_state`.
- **Paginación:** no aplica.

### Explorador de Eventos (`dashboard/screens/event_explorer.py`)

Ver detalle completo en la sección 11.

### Detalle del Filing (`dashboard/screens/filing_detail.py`)

- **Objetivo:** mostrar el registro completo de un filing concreto.
- **Cómo se llega:** (a) seleccionando una fila en el Explorador (fija
  `st.session_state["selected_filing"]` y hace `st.switch_page`, abre
  directo sin re-buscar) o (b) buscando directamente en esta pantalla.
- **Búsqueda on-demand:** no hay ninguna lista precargada de filings ni
  desplegable — mientras el campo de texto está vacío, no se ejecuta
  ninguna consulta (`_render_search`, líneas 58-60). Cada pulsación de
  tecla que cambia el término ejecuta `search_event_filings(conn, term,
  limit=51)` (límite `_RESULTS_LIMIT=50` + 1 para poder detectar
  truncamiento sin una segunda consulta `COUNT(*)`). Es un `LIKE
  'término%'` (prefijo, no substring) sobre `ticker`/`company_name`, que
  aprovecha los índices `NOCASE` (sección 4); ordenado por `date_filed
  DESC`; devuelve solo columnas ligeras (`filename, date_filed, ticker,
  company_name, form_type, filing_category`), nunca `clean_text`/
  `raw_text`/`raw_response`.
- **Persistencia del término de búsqueda:** clave durable
  `filing_detail_search_term` en `st.session_state`, deliberadamente
  **distinta** de la `key=` propia del widget (`_SEARCH_WIDGET_KEY`) —
  documentado en el código (`filing_detail.py:31-40`): Streamlit purga la
  entrada de `session_state` ligada a la `key=` de un widget en cuanto ese
  widget deja de instanciarse en un render (lo que ocurre siempre al
  navegar a otra página), así que la clave durable se siembra manualmente
  como `value=` y se reescribe manualmente tras cada render.
- **Información mostrada (una vez seleccionado un filing):**
  `render_general_info` (empresa, ticker, formulario, fecha, enlace a
  EDGAR), `render_ai_analysis` (evento principal, eventos secundarios,
  entidades clave, importance score, market impact con badge de color,
  next step con badge, deep research, resumen, razón de la puntuación),
  `render_evidence` (lista de evidencias), `render_full_text` (texto
  completo de `clean_text`, truncado a `MAX_FULL_TEXT_CHARS = 50 000`
  caracteres con enlace a EDGAR para el resto, para no congelar el
  navegador con documentos de varios MB).
- **Consulta:** `get_filing_detail(conn, filename)` — un único `SELECT` con
  `LEFT JOIN` entre `filings` y `llm_filing_analysis`.
- **Interacción:** expander "Buscar otro filing" siempre disponible aunque
  ya haya un filing cargado.
- **Filtros:** solo el término de búsqueda (no hay filtros adicionales en
  esta pantalla).
- **Paginación:** no aplica (un único registro a la vez); los resultados de
  búsqueda se acotan a 50 sin paginación (mensaje "Mostrando los 50 más
  recientes" si se trunca).

### Estadísticas (`dashboard/screens/statistics.py`)

- **Objetivo:** análisis agregado de todos los filings EVENT.
- **Información mostrada:** eventos por categoría (barra), medidor (%) de
  Deep Research sobre analizados, eventos por formulario (barra
  horizontal, ranking), evolución mensual (línea), empresas con más
  eventos (barra horizontal, top `TOP_COMPANIES_LIMIT=15`), distribución
  de importance score (histograma).
- **Consultas:** `get_category_counts`, `get_deep_research_ratio`,
  `get_form_type_counts`, `get_monthly_event_counts`, `get_top_companies`,
  `get_importance_score_distribution`.
- **Filtros:** ninguno.
- **Interacción:** solo hover en las gráficas Plotly.

### Procesar filings (`dashboard/screens/process_filings.py`)

Ver detalle completo en la sección 12.

---

## 11. Explorador de Eventos

`dashboard/screens/event_explorer.py` + `dashboard/components/sidebar_filters.py`
+ `dashboard/components/data_table.py` + `dashboard/services/filters.py`.

**Filtrado, orden y paginación se resuelven en SQL**, no en pandas — solo la
página actual se materializa en memoria (`get_events_page`, con
`LIMIT/OFFSET`), lo que mantiene la pantalla rápida con miles de filas
(comentario explícito en `event_explorer.py:1-6`).

### Selección de fecha

Dos modos, radio `"Día"` / `"Rango"` (`DATE_MODE_DAY`/`DATE_MODE_RANGE`,
`sidebar_filters.py:50-52`):
- **Día:** un único `st.date_input`; `date_from = date_to` = esa fecha.
- **Rango:** un `st.date_input` de rango; si el usuario solo marca un
  extremo, ambos colapsan a esa misma fecha
  (`sidebar_filters.py:165`).

**Fecha por defecto:** `resolve_last_downloaded_date(date_bounds)`
(`sidebar_filters.py:103-111`) — la fecha `date_filed` **máxima entre
filings EVENT** (no un "último dato descargado" de toda la tabla), para no
arrancar en un día en el que todo lo descargado ese día fuese CONTEXT y la
tabla apareciera vacía. Si no hay ningún filing EVENT, cae a `date.today()`.
Al cambiar a modo Rango, el rango por defecto es ese mismo día repetido, no
todo el histórico (`test_range_mode_defaults_to_the_last_day_not_the_full_history`,
`dashboard/tests/test_sidebar_filters_state.py:160-169`, confirma este
comportamiento).

### Ticker

`st.sidebar.text_input`, traducido en `build_where_clause`
(`dashboard/services/filters.py:47-49`) a
`f.ticker LIKE ?` con patrón `%TICKER%` (substring, mayúsculas forzadas —
no es un prefijo como en la búsqueda de Detalle del Filing).

### Formulario

`st.sidebar.multiselect` sobre los `form_type` distintos entre filings
EVENT (`get_filter_options`); si se seleccionan varios, se traduce a
`f.form_type IN (?, ?, ...)`.

### Ordenación

`SORT_OPTIONS` (`services/filters.py:13-18`): `"Fecha"` (`f.date_filed`,
por defecto), `"Empresa"` (`f.company_name`), `"Ticker"` (`f.ticker`),
`"Importance score"` (`l.importance_score`). Un toggle `"Ascendente"`
controla la dirección. Si la columna de orden viene de
`llm_filing_analysis` (empieza por `l.`), se añade `NULLS LAST` para que
los filings aún no analizados no dominen un orden descendente por score
(`resolve_order_by`, `services/filters.py:63-67`).

### Paginación

`DEFAULT_PAGE_SIZE = 50` (`dashboard/config.py:23`). `render_pagination`
(`components/data_table.py`) muestra "Mostrando X–Y de N" y botones
←/→; el número total de páginas se calcula con división entera hacia
arriba. Cualquier cambio en el filtro/orden reinicia la página a 1
(comparando una "firma" `(where_extra, params, order_by)` guardada en
`st.session_state["explorer_filters_signature"]`,
`event_explorer.py:45-49`).

### Columnas mostradas (estado actual)

`_DISPLAY_ORDER` (`dashboard/components/data_table.py`):
**Fecha, Empresa, Ticker, Formulario, Evento principal, Importance Score,
Deep Research.**

`Importance Score` se renderiza con `st.column_config.ProgressColumn`
(barra 0-100); `Deep Research` con `st.column_config.CheckboxColumn`. No
hay columna de filtro por `otc_tier` ni por "categoría del evento"/"score
mínimo"/"solo deep research" en la sidebar (fueron eliminadas
deliberadamente durante el desarrollo — siguen existiendo las columnas de
datos subyacentes en la base de datos, solo se quitó su exposición como
filtro/columna en el dashboard; ver sección 20).

**Nota histórica relevante:** en una iteración anterior del desarrollo la
tabla incluyó también una columna "Mercado" (`market_impact`) y, en otra
iteración, un mecanismo de descarte manual con columnas "Ver"/"Descartar"
implementado sobre `st.data_editor`. **Ambas fueron revertidas** y el
estado actual usa `st.dataframe` con selección de fila simple
(`selection_mode="single-row", on_select="rerun"`) sin columna de mercado
ni de descarte (ver sección 13).

### Navegación al detalle

`render_events_table` devuelve el `filename` de la fila seleccionada (por
posición, vía `event.selection.rows`); `event_explorer.py:57-60` fija
`st.session_state["selected_filing"]` y hace
`st.switch_page(st.session_state["nav_pages"]["detalle"])`.

### Persistencia de filtros durante la sesión

Cada filtro vive en una clave "durable" de `st.session_state`
(`_DATE_MODE_KEY`, `_TICKER_KEY`, etc., `sidebar_filters.py:63-70`),
**deliberadamente distinta** de la `key=` propia del widget. Documentado en
el módulo (`sidebar_filters.py:1-34`): confirmado contra un cambio de
página real (no solo un rerun de la misma página) que Streamlit purga la
entrada de `session_state` ligada a la `key=` de un widget en cuanto ese
widget no se instancia en un render — lo que un cambio de página siempre
provoca. Por eso cada widget usa una `key=` interna desechable
(`_widget_key`), y la clave durable se siembra como `value=`/`index=`/
`default=` y se reescribe con el valor devuelto tras cada render.

`_widget_key` también incorpora un contador de reset (`RESET_COUNTER_KEY`):
confirmado en navegador real que algunos widgets (`st.text_input` en
particular) no refrescan visualmente un nuevo `value=` si su `key=` no
cambia, aunque el `session_state` subyacente sí haya cambiado — el botón
"Limpiar filtros" incrementa este contador para forzar un remount completo
de todos los widgets a la vez.

### Comportamiento al cambiar de página/pestaña

Como los filtros viven en claves durables de `session_state` (no en las
`key=` de los widgets), sobreviven a la navegación a otra pantalla y vuelta
— confirmado mediante pruebas manuales con Playwright contra
`streamlit run app.py` (documentado en el docstring de
`dashboard/tests/test_sidebar_filters_state.py:1-28`, que también aclara
que `AppTest` —usado en los tests automatizados— no puede ejercitar un
salto real de página, solo reruns de la misma página).

### Funcionalidad de descarte manual

**No existe en el estado actual del código** (ver sección 13 para el
detalle completo de por qué se documenta explícitamente esta ausencia).

---

## 12. Procesar filings

`dashboard/screens/process_filings.py` — la única pantalla del dashboard
que produce escritura en `filings.db`, y solo indirectamente: llama a las
mismas funciones de `src/pipeline.py` que usa la CLI (`src/main.py`), nunca
reimplementa lógica de selección o escritura propia.

### A. Ingesta SEC

**Controles:**
- `st.date_input("Fecha a procesar", value=date.today())`
- `st.checkbox("Descargar índice SEC", value=True)`
- `st.checkbox("Descargar contenido de los documentos", value=True,
  disabled=not download_index)`
- Botón `"Ejecutar ingesta"` (deshabilitado implícitamente si no se marca
  "Descargar índice SEC" — en ese caso se muestra `st.error`).

**Llamada de backend:** `_run_ingest()` →
`src.pipeline.run_daily_pipeline(target_date, user_agent,
download_content=download_content, on_progress=_daily_progress)` — la
**misma función** que ejecuta `src/main.py --date ... [--download-content]`.

**Progreso en vivo:** `_daily_progress` actualiza un `st.status` con:
mensaje al completar la descarga del índice (documentos encontrados,
EVENT/CONTEXT), mensaje al completar el guardado en SQLite (insertados/ya
existentes), y una barra `st.progress` + caption por cada filing durante la
descarga de contenido (bytes descargados/guardados, velocidad, ETA, o
"ya tenía contenido, omitido" si se saltó).

**Resumen final:** tarjetas KPI (`_render_ingest_summary`) — documentos
encontrados, insertados, contenido descargado (MB transferidos), "Almacenado
(solo doc. principal)" con el % de reducción frente a lo transferido,
omitidos, fallos de descarga (si los hay, con detalle expandible), y tiempo
total. Al terminar, `st.cache_data.clear()` invalida toda la caché de
`dashboard/database/queries.py` para que el resto de pantallas reflejen los
datos nuevos de inmediato.

### B. Análisis mediante IA

**Controles:**
- Periodo: radio `"Día concreto"` / `"Rango de fechas"` (`_resolve_period`).
- `st.multiselect` de tipos de formulario (limitado a los `form_types` de
  filings EVENT existentes).
- `st.number_input("Máximo de filings a analizar", value=25)` — `0` =
  sin límite.
- `st.selectbox("Estado de análisis")`: "Solo pendientes de análisis"
  (`pending`) / "Ya analizados" (`analyzed`) / "Todos" (`all`).
- `st.selectbox("Priorizar por")`: "Más recientes primero" (`recent`) /
  "Más antiguos primero" (`oldest`).
- `st.number_input("Workers", min_value=1, max_value=16,
  value=DEFAULT_LLM_WORKERS)`.

**Previsualización antes de gastar cuota ("antes de gastar dinero"):**
`_render_llm_preview` llama a `get_llm_selection_counts` (totales +
pendientes, independiente del filtro de estado elegido) y muestra tarjetas
KPI (Periodo, Filings encontrados, Pendientes de IA, Límite seleccionado,
Se enviarán al LLM) más una tabla de vista previa
(`get_llm_selection_preview`, acotada a `_PREVIEW_DISPLAY_CAP = 200` filas
**solo en pantalla** — nunca limita lo que realmente se enviaría).

**Botón:** `"Analizar {N} filings con IA"` (deshabilitado si `to_send == 0`).

**Llamada de backend:** `_run_llm_analysis()` →
`src.pipeline.run_llm_pipeline(limit, date_from, date_to, form_types,
status, order, workers, on_progress=_on_progress)` → delega íntegramente en
`src.llm_analysis.first_pass.run_first_pass` (misma función que usa
`--llm-first-pass` de la CLI).

**Progreso en vivo:** barra de progreso, caption con Workers/Velocidad/ETA,
y una tabla HTML de las últimas 20 filas procesadas (`_llm_progress_row_html`,
columnas Progreso/Empresa/Evento/Score en proporción fija 10/40/40/10% vía
CSS Grid con unidades `fr`), con filas en rojo (`is-error`) para errores.
Si `LLMConfigError` (variables `.env` faltantes), se muestra
`st.warning` y no se lanza nada.

**Resumen final:** tarjetas KPI — Procesados, Correctos, Errores, Deep
Research (contados por el propio callback de progreso, sumando cada
`data.get("deep_research")` de las respuestas exitosas), Tiempo total.
También limpia la caché al terminar.

Ambos bloques (A y B) son completamente independientes: eligen su propio
estado de `st.session_state` (claves con prefijo `ingest_*` y `llm_*`
respectivamente) y no comparten filtros — elegir una fecha en A nunca
afecta lo que B enviaría al modelo.

---

## 13. Sistema de descarte/revisión manual

**Estado actual: no implementado.** Tras una auditoría exhaustiva del
código (`grep` de `manual_discarded`, `discard`, `descartar` sobre `src/`,
`dashboard/` y `tests/`), no existe ningún campo, columna, endpoint, botón
ni lógica de descarte o revisión manual en el estado actual del
repositorio.

**Contexto (relevante para la memoria, no para el código actual):** a lo
largo del desarrollo de este proyecto se llegó a **diseñar e implementar
por completo** una funcionalidad de este tipo — una columna
`manual_discarded BOOLEAN NOT NULL DEFAULT 0` en `filings`, un checkbox
"Descartar" independiente de la selección de fila en el Explorador (que
obligó a introducir una columna "Ver" para poder seguir navegando al
detalle, ya que `st.data_editor` —necesario para editar celdas— no admite
`selection_mode` en la versión de Streamlit instalada), un control
"Mostrar: Activos/Descartados/Todos", un botón Descartar/Restaurar en
Detalle del Filing, exclusión de los filings descartados de las
estadísticas agregadas y de la selección automática para el LLM, y una
suite de tests dedicada (12 tests de backend + 10 de dashboard). Esta
funcionalidad **fue solicitada explícitamente por el usuario, completada,
verificada, y a continuación revertida en su totalidad también a petición
explícita del usuario**, restaurando cada archivo tocado a su estado previo
y borrando los tests dedicados. El único resto físico de ese trabajo es que
la columna `manual_discarded` pudo llegar a añadirse a una base de datos
real (`data/filings.db`) de un despliegue concreto durante las pruebas —
una columna así, de existir en una base de datos ya migrada, quedaría
**inerte** (ningún código actual la lee ni la escribe), igual que
`exchange` (ver sección 4) o el módulo `document_intelligence` (ver
secciones 3 y 20).

Por tanto, para esta sección, la respuesta a cada punto solicitado es:
- `manual_discarded`, significado, cómo se marca, cómo se restaura, dónde
  aparece, efecto sobre Explorador/estadísticas/selección LLM,
  persistencia: **no implementado en el código actual** —
  `No determinado a partir del código` en cuanto a un comportamiento vivo,
  más allá de la reconstrucción histórica anterior.
- Por qué un filing no se elimina físicamente: principio de diseño general
  observable en el resto del código actual (nunca hay un `DELETE` sobre
  `filings` ni sobre `llm_filing_analysis` en ningún módulo auditado —
  todas las mutaciones son `INSERT OR IGNORE`, `INSERT OR REPLACE`,
  `UPDATE` de columnas concretas, o `ALTER TABLE ADD COLUMN`); el borrado
  físico de un filing habría sido irreversible y habría roto la
  trazabilidad de qué se decidió ignorar y por qué, algo consistente con
  cómo el proyecto trata en general las columnas obsoletas (dejarlas
  inertes, nunca un `DROP COLUMN`/`DELETE`, ver sección 20).

---

## 14. Rendimiento y optimizaciones

Optimizaciones reales verificadas en el código, con el problema que
resuelve cada una:

| Optimización | Dónde | Problema que resuelve |
|---|---|---|
| Índices `NOCASE` en `ticker`/`company_name` | `src/database/db.py:42-46` (siempre) | Búsqueda de "Detalle del Filing" pasaba de ~10ms a ~2.4-6.5s sin índice utilizable para `LIKE` case-insensitive |
| Índices `filing_category+date_filed` y `created_at` | `scripts/add_performance_indexes.sql` (manual) | Casi todas las queries del dashboard filtran/ordenan por esos campos; sin índice, escaneo completo de una tabla de hasta 16GB (~7.3s medidos) |
| Sesión HTTP persistente (`requests.Session`) | `src/sec_ingestion/downloader.py:29-37` | Evita un nuevo handshake TCP+TLS por cada filing descargado — ~2.9x más rápido medido |
| Omitir contenido ya descargado | `get_filenames_with_content` + `run_daily_pipeline` (`src/database/db.py:275-291`, `src/pipeline.py:116-132`) | Reprocesar la misma fecha no vuelve a pedir a EDGAR filings que ya tienen `clean_text` |
| Extracción del documento principal (solo SEQUENCE=1) | `src/sec_ingestion/downloader.py:168-257` | Evita almacenar exhibits irrelevantes; reduce tamaño en BD y el texto que llega al LLM; `str.find()` nunca recorre el resto del submission |
| Límite de caracteres al LLM (`MAX_CLEAN_TEXT_CHARS=20000`) | `src/llm_analysis/first_pass.py:17,37` | Acota coste/latencia por llamada, evita enviar documentos completos de varios MB |
| Concurrencia LLM (`ThreadPoolExecutor`) | `src/llm_analysis/first_pass.py:183-212` | El first pass sobre N filings ya no es estrictamente secuencial — varias llamadas HTTP en vuelo a la vez |
| Paginación SQL (`LIMIT/OFFSET`) | `dashboard/database/queries.py::get_events_page` | Solo la página visible se materializa en un DataFrame — el Explorador escala a miles de filas |
| Filtrado/orden en SQL, no en pandas | `dashboard/services/filters.py`, `dashboard/database/queries.py` | Evita cargar la tabla completa en memoria del proceso Streamlit para filtrar client-side |
| Consultas ligeras para búsqueda (sin `clean_text`/`raw_text`) | `search_event_filings` (`dashboard/database/queries.py`) | Cada tecla escrita en la búsqueda no mueve texto potencialmente enorme, solo columnas cortas |
| Caché de queries (`st.cache_data(ttl=300)`) | `dashboard/database/queries.py:14` | Evita repetir el mismo `SELECT` en cada rerun de Streamlit (que ocurre en cada interacción de widget); TTL de 5 min porque el backend solo actualiza como mucho una vez al día |
| Caché de la conexión (`st.cache_resource`) | `dashboard/database/connection.py:14` | Una única conexión SQLite reutilizada entre reruns, no una nueva por interacción |
| Conexión de solo lectura a nivel de driver (`mode=ro`) | `dashboard/database/connection.py:21-22` | No es una optimización de velocidad, pero sí de seguridad/robustez: cualquier escritura accidental del dashboard falla en el driver, no solo por convención |
| Reintentos acotados con backoff (SEC y LLM) | `src/sec_ingestion/downloader.py:40-70`, `src/llm_analysis/client.py:68-136` | Evita que un fallo transitorio (503 de SEC, 429/5xx del LLM) tumbe todo el lote |
| `Retry-After` acotado (`_MAX_RETRY_AFTER_SECONDS=30`) | `src/llm_analysis/client.py:20,123-136` | Evita que un proveedor LLM mal comportado bloquee un lote entero con una espera arbitrariamente larga |
| Backfill por lotes de `filing_category` (por `form_type` distinto, no fila a fila) | `src/database/db.py:135-149` | Migrar una tabla existente sin recorrer todas las filas una a una |

No hay caché de resultados del LLM más allá de la propia deduplicación por
`filing_filename` en `llm_filing_analysis` (evitar volver a pedir un
análisis ya hecho, no una caché de respuestas en memoria).

---

## 15. Tests

Suite dividida en dos entornos independientes (backend y dashboard, cada
uno con su propio `.venv`).

### Backend (`tests/`, ejecutado con `pytest tests/`)

**169 tests recogidos** (`pytest --collect-only`, ejecución real en esta
auditoría), repartidos en 7 ficheros:

| Fichero | Qué cubre |
|---|---|
| `tests/test_daily_index.py` | Cálculo de trimestre, construcción de la URL del índice diario |
| `tests/test_parser.py` | Parseo de `master.idx`: líneas válidas/inválidas, CIK no numérico, cabecera, separador de guiones, fichero vacío |
| `tests/test_filing_routing.py` | Clasificación EVENT/CONTEXT/IGNORED (parametrizado sobre cada formulario real de las listas), insensibilidad a mayúsculas/espacios, y que `EVENT_FORMS`/`CONTEXT_FORMS` no se solapan |
| `tests/test_company_enrichment.py` | Normalización de CIK, parseo de `company_tickers.json`, migraciones de esquema (`init_db` añadiendo columnas a una tabla existente), enriquecimiento de `ticker` (con match, sin match, CIK sin padding), auto-enriquecido al insertar, asignación de `filing_category` al insertar y backfill retroactivo |
| `tests/test_otc_importer.py` | Parseo de valores del CSV (float/int con `$`, `%`, comas, vacíos), parseo de filas, import completo (inserción, reimportación/actualización, fichero no encontrado, volumen nulo), `upsert_otc_securities`, enriquecimiento OTC (con y sin match), migración de columnas OTC en `filings` |
| `tests/test_downloader.py` | Detección/limpieza de HTML (incl. fallback de parsers y de regex), reintentos de `fetch_raw` (5xx sí, 4xx no), **extracción del documento principal**: un solo documento, múltiples documentos (se queda solo con SEQUENCE=1), coincidencia/discordancia de TYPE, casos reales documentados (SC TO-T, S-1, SC 13E3, el caso dual SC 13D/A vs SC TO-T/A), fallback sin estructura `<DOCUMENT>`, y `fetch_and_clean` de extremo a extremo (solo se guarda el documento principal, warnings de tipo, fallo de descarga → ceros) |
| `tests/test_llm_analysis.py` | Prompt (contiene todos los `EVENT_TYPES`, pide JSON estricto), construcción del mensaje de usuario, parseo de respuesta (JSON válido/ inválido/ vacío/ `None`/ envuelto en fences con y sin `json`), truncado de `clean_text`, `LLMClient` (variables de entorno faltantes/explícitas, strip de barra final en la URL, parseo de una respuesta simulada), migraciones del esquema de `llm_filing_analysis`, selección de candidatos (excluye CONTEXT/IGNORED, excluye ya analizados, respeta `limit`), **concurrencia**: llamadas realmente concurrentes, nunca se excede el número de workers configurado, la escritura en SQLite ocurre solo desde el hilo llamante, un fallo individual no detiene el lote, reintentos ante 429 (incl. `Retry-After` y su tope), 5xx, timeout, error de conexión, no reintento de otros 4xx, agotamiento de reintentos, información de progreso (timing/workers) presente tanto en éxito como en fallo |

### Dashboard (`dashboard/tests/`, ejecutado con `pytest dashboard/tests/`)

**23 tests recogidos**, en 2 ficheros, usando `streamlit.testing.v1.AppTest`
contra pequeños scripts "harness" (`dashboard/tests/apps/`):

| Fichero | Qué cubre |
|---|---|
| `dashboard/tests/test_sidebar_filters_state.py` | `resolve_last_downloaded_date` (cálculo puro), valores por defecto al primer render, que cada widget lee/escribe la clave durable (no la `key=` propia del widget) y sobrevive a un rerun no relacionado, el cambio a modo Rango, y que "Limpiar filtros" resetea todo a los valores por defecto y vuelve a la página 1 |
| `dashboard/tests/test_filing_detail_search.py` | `search_event_filings` contra una conexión SQLite real (prefijo, case-insensitivity, orden por fecha, límite, que nunca selecciona `raw_text`/`clean_text`), y el comportamiento on-demand de la pantalla (sin campo = sin consulta/sin resultados), más la persistencia del término de búsqueda a través de un salto de página simulado |

El propio docstring de `test_sidebar_filters_state.py` documenta una
limitación real de `AppTest`: no puede ejercer un salto de página genuino
en una app multipágina, solo reruns de la misma página — dos bugs reales
del proyecto (purga de `session_state` ligado a `key=` en un cambio de
página; un `st.text_input` que no refresca visualmente aunque el
`session_state` sí cambie) solo se pudieron reproducir con Playwright
contra `streamlit run app.py`, no con estos tests automatizados.

### Casos límite cubiertos de forma transversal

- Respuestas del LLM no-JSON, JSON no-objeto, vacías, `None`, envueltas en
  fences de markdown.
- Filings sin `<DOCUMENT>`/`<TEXT>` reconocible (formato inesperado).
- CIK sin padding, CSV con campos vacíos/no numéricos, símbolo vacío.
- Descargas fallidas (`fetch_raw`, `fetch_and_clean`) devolviendo ceros de
  forma consistente en vez de lanzar.
- Reintento vs. no-reintento según código de estado HTTP, en dos módulos
  distintos con políticas parecidas pero no idénticas (SEC: solo 5xx, sin
  429 porque SEC no aplica ese código aquí; LLM: 429+5xx+timeout+conexión).
- Migraciones de esquema sobre una base de datos ya poblada (no solo
  creación desde cero).

**No se han encontrado tests de UI end-to-end automatizados con Playwright
en el repositorio** — las verificaciones con navegador real mencionadas en
comentarios/docstrings fueron manuales durante el desarrollo, no forman
parte de la suite de `pytest`.

---

## 16. Tecnologías utilizadas

Extraídas de `requirements.txt` (raíz) y `dashboard/requirements.txt`, y
confirmadas contra los `import` reales del código.

### Backend (`requirements.txt`)

| Tecnología | Para qué se usa |
|---|---|
| **Python** (3.9 en el entorno documentado, ver `dashboard/README.md`) | Lenguaje de todo el backend |
| **`requests`** (>=2.31.0) | Todas las peticiones HTTP: índice diario SEC, descarga de filings, `company_tickers.json`, llamadas al LLM |
| **`beautifulsoup4`** (>=4.12.0) | Extracción de texto desde HTML en `clean_filing_text` (con `lxml` como parser alternativo si está instalado, detectado dinámicamente vía `importlib.util.find_spec`) |
| **`python-dotenv`** | Carga de variables desde `.env` (`SEC_USER_AGENT`, `LLM_*`) |
| **`pytest`** (>=8.0.0) | Suite de tests del backend |
| **`sqlite3`** (stdlib) | Persistencia — no es una dependencia externa, pero es la base de datos del proyecto |
| **`concurrent.futures`** (stdlib) | `ThreadPoolExecutor`/`as_completed` para la concurrencia del LLM |
| **`argparse`** (stdlib) | CLI (`src/main.py`) |

### Dashboard (`dashboard/requirements.txt`)

| Tecnología | Para qué se usa |
|---|---|
| **`streamlit`** (>=1.38.0) | Framework de la aplicación web completa (navegación, widgets, caché, tema) |
| **`plotly`** (>=5.22.0, vía `plotly.graph_objects`) | Todas las gráficas del dashboard |
| **`pandas`** (>=2.2.0) | DataFrames de resultados de consultas SQL (`pd.read_sql_query`) |
| **`watchdog`** (>=4.0.0) | File-watcher nativo de Streamlit para autorecarga rápida en desarrollo (comentario explícito: sin él, Streamlit cae a polling, más lento) |
| **`requests`, `beautifulsoup4`, `python-dotenv`** | Repetidas porque `dashboard/.venv` es un entorno separado del backend, y `process_filings.py` importa `src/` directamente |
| **`pytest`** | Suite de tests del dashboard (`streamlit.testing.v1.AppTest`) — dependencia de desarrollo, no necesaria para ejecutar la app |

### Proveedor LLM

**No es una dependencia de código** — `LLMClient` es un cliente HTTP
genérico contra cualquier API compatible con el formato de chat completions
de OpenAI, configurado enteramente vía variables de entorno
(`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_PROVIDER`). El código no
importa ningún SDK oficial de OpenAI ni de ningún proveedor concreto — todo
pasa por `requests.post`. El README principal documenta como ejemplos
OpenAI (`api.openai.com`) y Grok/x.ai (`api.x.ai`); un comentario en
`first_pass.py:69-76` documenta haber observado en la práctica respuestas
de **Gemini servido a través de OpenRouter** (que envuelve el JSON en un
code fence de markdown, motivo por el que existe `_strip_markdown_fence`).
No se ha encontrado en el código ninguna referencia a un modelo o proveedor
fijo por defecto — `LLM_MODEL` no tiene valor por defecto, es obligatorio.

---

## 17. Configuración y ejecución

### Variables de entorno (`.env`, cargado con `python-dotenv`)

| Variable | Obligatoria | Usada por | Efecto si falta |
|---|---|---|---|
| `SEC_USER_AGENT` | Recomendada (no bloqueante) | `src/pipeline.py::get_user_agent()` | Si no está definida, se usa un placeholder (`"OTCFilingIntelligence contact@example.com"`) y se loggea un `warning`; SEC exige un User-Agent identificable, así que no configurarla arriesga bloqueo de IP (documentado en el README) |
| `LLM_API_KEY` | Sí, para cualquier acción LLM | `LLMClient.__init__` | `LLMConfigError` inmediato |
| `LLM_BASE_URL` | Sí | `LLMClient.__init__` | `LLMConfigError` inmediato |
| `LLM_MODEL` | Sí | `LLMClient.__init__` | `LLMConfigError` inmediato |
| `LLM_PROVIDER` | No (por defecto `"openai-compatible"`) | `LLMClient.__init__` | Se usa el valor por defecto; se guarda igualmente junto a cada resultado |

No se ha localizado en el repositorio auditado un fichero `.env.example`
vivo (aparece como eliminado, `D`, en `git status`) — el README documenta
su existencia y contenido esperado, pero el fichero de plantilla en sí no
está presente en este checkout. **No se copian aquí valores reales** del
`.env` local (nunca se han impreso ni se imprimirán secretos).

### Ejecución del backend (CLI)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m src.main --date 2024-05-15                       # solo metadatos
python -m src.main --date 2024-05-15 --download-content    # + contenido
python -m src.main --import-sec-company-tickers             # mapeo CIK↔ticker
python -m src.main --enrich-filings                          # ticker en filings existentes
python -m src.main --import-otc-screener-csv <path.csv>     # datos OTC
python -m src.main --enrich-filings-with-otc                 # cruce OTC en filings
python -m src.main --llm-first-pass [--limit N] [--from-date YYYY-MM-DD] \
                    [--to-date YYYY-MM-DD] [--workers N]      # análisis LLM
```

`init_db()` se ejecuta siempre al arrancar `main()` (`src/main.py:291`),
independientemente de qué acción se pida — garantiza que el esquema/índices
existen y están migrados antes de cualquier operación.

Parámetros relevantes: `--limit` (tope de filings a analizar),
`--from-date`/`--to-date` (rango para `--llm-first-pass`), `--workers`
(concurrencia del LLM, por defecto `DEFAULT_WORKERS=5`, validado
`>=1` por `_positive_int`).

### Ejecución del dashboard

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv dashboard/.venv
source dashboard/.venv/bin/activate
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

Requiere que `data/filings.db` ya exista (creado por el backend); si no
existe, `dashboard/database/connection.py::get_connection()` lanza
`FileNotFoundError` con un mensaje explícito.

### Estructura mínima para ejecutar el proyecto

```
otc-filing-intelligence/
├── .env                 # SEC_USER_AGENT, LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
├── data/filings.db       # se crea con init_db() al primer uso de la CLI
├── .venv/                # entorno del backend
└── dashboard/.venv/      # entorno separado del dashboard
```

---

## 18. Flujo end-to-end (ejemplo completo)

**Día X — el usuario ejecuta `python -m src.main --date X --download-content`
(o pulsa "Ejecutar ingesta" en Procesar filings):**

1. `main()` (`src/main.py:272`) llama a `init_db()`
   (`src/database/db.py:188`) — asegura esquema e índices.
2. `_run_daily_pipeline` → `run_daily_pipeline(target_date, user_agent,
   download_content=True)` (`src/pipeline.py:75`).
3. `get_filtered_filings(target_date, user_agent)`
   (`src/sec_ingestion/daily_index.py:45`) → `fetch_daily_index` descarga
   `master.<X>.idx`; `parse_master_idx` produce N `Filing` objetos;
   `get_filing_category` descarta los `IGNORED`. Supongamos que aparecen
   **N filings**, de los cuales `E` son EVENT y `C` son CONTEXT.
4. **Clasificación:** ya ocurrió en el paso anterior (`get_filing_category`
   por `form_type`, sección 7) — es una decisión determinista, no hay LLM
   implicado todavía.
5. **Inserción:** `insert_filings(filings)` (`src/database/db.py:205`) —
   `INSERT OR IGNORE` por `filename`; se cuentan `inserted`/`skipped`.
6. **Descarga del contenido (uno por uno, secuencial, con
   `build_session`/`fetch_and_clean`):** para cada uno de los `N` filings
   (EVENT y CONTEXT, la descarga de contenido no distingue categoría):
   - Si ya tiene `clean_text` (de una ejecución previa de esa fecha), se
     cuenta como `content_skipped_existing` y no se pide a EDGAR.
   - Si no, `fetch_and_clean(filing.filing_url, user_agent, session=...,
     expected_type=filing.form_type)` (`src/sec_ingestion/downloader.py:208`):
     descarga el "complete submission" completo, extrae solo el bloque
     `SEQUENCE=1` (**extracción del documento principal**, sección 6),
     limpia el texto, y hace `time.sleep(max(delay, MIN_SAFE_DELAY))` antes
     de devolver el resultado (respetando Fair Access).
   - `update_filing_content(filename, raw, cleaned)`
     (`src/database/db.py:251`) — `UPDATE` de esa fila concreta.
   - Se emite `on_progress` con velocidad/ETA calculados en el momento.
7. Al terminar, la CLI resume: filings encontrados, insertados, MB
   transferidos vs. almacenados (con el % de reducción propio de guardar
   solo el documento principal), omitidos, fallidos.

**Selección para IA (manual, cuando el usuario lo decide — CLI
`--llm-first-pass` o botón "Analizar N filings con IA" en el dashboard):**

8. `run_llm_pipeline(...)` (`src/pipeline.py:187`) → `run_first_pass(...)`
   (`src/llm_analysis/first_pass.py:115`).
9. `get_event_filings_needing_llm_analysis(...)`
   (`src/database/db.py:469`), construida sobre `_llm_selection_where`
   (`filing_category='EVENT'`, `clean_text` no vacío, no analizado aún,
   filtros de fecha/formulario si los hay) — de los `E` filings EVENT de
   ese día, supongamos que `P` están pendientes.
10. `ThreadPoolExecutor(max_workers=workers)` lanza una llamada por cada
    uno de los `P` filings; cada hilo ejecuta `_call_llm` →
    `build_filing_input` (recorta `clean_text` a 20 000 caracteres) →
    `build_user_message` → `LLMClient.chat_completion` (`temperature=0`,
    con reintentos ante 429/5xx/timeout/conexión).
11. En el hilo principal, a medida que cada llamada termina
    (`as_completed`): `parse_llm_response(response.content)` — JSON
    estricto o `PARSE_ERROR` si falla — y
    `insert_llm_filing_analysis(filing_filename, provider, model, parsed,
    raw_response)` (`INSERT OR IGNORE`, así que un resultado ya guardado no
    se duplica).
12. Progreso reportado con velocidad (filings/min) y ETA recalculados tras
    cada resultado.

**Aparición en el dashboard:**

13. El usuario abre el **Explorador de Eventos** — `get_events_page`
    (`dashboard/database/queries.py`) hace un `LEFT JOIN` entre `filings`
    y `llm_filing_analysis`; el filing recién analizado aparece con su
    `primary_event_type`, `importance_score` (barra de progreso) y
    `deep_research` (checkbox) ya rellenos (o vacíos, si aún no se
    seleccionó para el LLM, o si el análisis fue un `PARSE_ERROR`).
14. El usuario hace clic en la fila → `st.session_state["selected_filing"]
    = filename` → `st.switch_page(...)` → **Detalle del Filing**.
15. `get_filing_detail(conn, filename)` trae el registro completo; se
    renderizan metadatos, análisis IA completo (evento, score, market
    impact, next step, deep research, resumen, razón), evidencias, y el
    texto completo (`clean_text`, truncado a 50 000 caracteres si hace
    falta).

**"El usuario mantiene o descarta el resultado":** en el estado actual del
código **no existe ningún paso de mantener/descartar** — el usuario solo
puede leer el análisis; no hay ninguna acción de escritura sobre el
resultado del LLM ni sobre el filing desde esta pantalla (ver sección 13).
Cualquier decisión de "descartar" un filing es puramente una lectura humana
del dashboard, sin efecto persistido en la base de datos.

---

## 19. Decisiones de diseño y justificación

Para cada punto se distingue explícitamente **Hecho observable** (lo que el
código realmente hace) de **Justificación** (argumento defendible para la
memoria, no necesariamente escrito literalmente en el código, salvo que se
cite).

**SQLite como única base de datos.**
- *Hecho observable:* todo el proyecto (backend y dashboard) usa un único
  fichero SQLite (`data/filings.db`), sin servidor de base de datos
  separado, sin ORM (SQL crudo parametrizado en todas partes).
- *Justificación:* para un pipeline de un solo escritor (el backend) y
  lectores que toleran una latencia de "hasta un día" (la caché del
  dashboard es de 5 minutos, y el propio comentario del código dice "data
  is refreshed by the backend at most once a day",
  `dashboard/config.py:33`), un fichero SQLite evita la complejidad
  operativa de un servidor de base de datos separado, sin sacrificar SQL
  completo ni índices. El propio diseño de solo-lectura del dashboard
  (`mode=ro` a nivel de driver) es coherente con "hay un único escritor".

**Procesamiento incremental (deduplicación por `filename`, `INSERT OR
IGNORE`/`INSERT OR IGNORE`, omisión de contenido ya descargado).**
- *Hecho observable:* sección 4/5 — ningún paso vuelve a repetir trabajo ya
  hecho (metadatos, contenido, análisis LLM) salvo que se pida
  explícitamente.
- *Justificación:* la ingesta diaria se puede re-ejecutar de forma segura
  (idempotente) sin duplicar filas ni volver a pagar descargas o llamadas
  LLM — importante tanto por coste (llamadas LLM de pago) como por Fair
  Access de SEC (menos peticiones repetidas).

**Clasificación previa EVENT/CONTEXT (determinista, antes del LLM).**
- *Hecho observable:* `get_filing_category` es un simple `set` lookup, sin
  ningún componente de IA (sección 7); se aplica antes de cualquier llamada
  al LLM y antes incluso de guardar filings `IGNORED`.
- *Justificación (explícita en `README.md:45-48` y en el docstring de
  `routing.py`):* los informes periódicos (10-K/10-Q) raramente contienen
  un evento inmediato; enviarlos al LLM sería gasto de cómputo/dinero sin
  valor añadido. Filtrar por tipo de formulario es instantáneo y gratuito
  frente a clasificar semánticamente con un LLM.

**Almacenamiento exclusivo del documento principal (no el complete
submission).**
- *Hecho observable:* sección 6 — solo se persiste el bloque
  `SEQUENCE=1`.
- *Justificación:* evita que el tamaño almacenado dependa del número de
  exhibits (caso real documentado: 129MB/162 documentos por un solo 8-K);
  reduce el texto que llega al LLM a lo sustantivo, mejorando la señal
  frente al ruido de anexos legales/XBRL sin necesidad de un extractor más
  sofisticado.

**LLM como clasificación semántica (no como extractor de datos
estructurados de bajo nivel).**
- *Hecho observable:* el LLM recibe texto ya limpio y devuelve una
  clasificación de alto nivel (tipo de evento, score, impacto), no hace
  extracción de entidades de forma exhaustiva ni parsing de tablas
  financieras.
- *Justificación:* el propio `SYSTEM_PROMPT` (`prompts.py:43-49`) plantea
  el problema como "triage rápido", no como resumen/analítica exhaustiva —
  coherente con el objetivo de detectar señal entre un alto volumen diario
  de filings, no con producir un informe de análisis fundamental completo.

**Concurrencia con hilos (no async/multiprocessing) para el LLM.**
- *Hecho observable:* `ThreadPoolExecutor`, justificado explícitamente en
  el propio docstring como I/O-bound (sección 9).
- *Justificación:* evita la complejidad de reescribir todo el pipeline en
  `asyncio` para un cuello de botella que es, en la práctica, tiempo de
  espera de red; los hilos de Python liberan el GIL durante la espera de
  I/O, suficiente para el paralelismo que se busca (decenas de llamadas
  concurrentes, no miles).

**Revisión humana / dashboard de solo lectura + acciones puntuales.**
- *Hecho observable:* el dashboard es de solo lectura salvo "Procesar
  filings" (sección 10/12); no hay ninguna decisión automática que actúe
  sobre el resultado del LLM sin intervención humana (ni siquiera hay un
  paso de "aceptar/descartar" en el estado actual, sección 13).
- *Justificación:* el sistema se presenta como una herramienta de "triage"
  para un analista, no como un sistema de decisión autónoma — el LLM
  puntúa y prioriza, pero la interpretación final queda fuera del código
  (en la cabeza del analista que usa el dashboard).

**Streamlit para el dashboard.**
- *Hecho observable:* toda la UI es Streamlit puro (sin framework JS
  aparte, componentes nativos + HTML/CSS inyectado para theming).
- *Justificación:* permite construir una interfaz interactiva sobre datos
  tabulares/gráficas con muy poco código de UI dedicado, coherente con un
  proyecto de alcance de TFM donde el foco es el pipeline de datos/IA, no
  el desarrollo de un frontend a medida.

**Separación ingesta / análisis / visualización en paquetes y entornos
distintos.**
- *Hecho observable:* `src/` (backend, Python 3.9) y `dashboard/`
  (Python 3.12, venv propio) son ejecutables independientes; el dashboard
  importa `src/` como librería solo para dos acciones concretas
  (`process_filings.py`), nunca al revés.
- *Justificación (parcialmente explícita en `dashboard/README.md:24-31`):*
  motivo técnico documentado (incompatibilidad de wheels binarios de
  Streamlit/pandas/pyarrow con el Python 3.9 de macOS) más un motivo de
  diseño (mantener el backend ejecutable de forma completamente
  independiente del dashboard, p. ej. en un cron/CI sin ninguna
  dependencia de Streamlit).

---

## 20. Estado actual y limitaciones

### Funcionalidades completamente implementadas

- Descarga y parseo del índice diario SEC EDGAR.
- Clasificación determinista EVENT/CONTEXT/IGNORED.
- Extracción del documento principal desde el complete submission
  (SEQUENCE=1), con fallback documentado.
- Limpieza de texto HTML→texto plano con fallback multinivel.
- Persistencia SQLite con migración incremental de esquema
  (`_add_missing_columns`).
- Enriquecimiento por ticker (SEC `company_tickers.json`) y por datos OTC
  (CSV del Stock Screener).
- Pipeline LLM completo: selección de candidatos, prompt, llamada HTTP con
  reintentos, parseo robusto (incl. `PARSE_ERROR` y fences de markdown),
  concurrencia con `ThreadPoolExecutor`, escritura desde un único hilo.
- CLI completa (`src/main.py`) con todas las acciones documentadas.
- Dashboard con 5 pantallas funcionales, filtros persistentes en sesión,
  búsqueda on-demand indexada, paginación SQL, disparo manual de ingesta y
  análisis LLM desde el navegador con progreso en vivo.
- Suite de tests amplia (169 backend + 23 dashboard, sección 15).

### Funcionalidades parciales o con alcance limitado

- **"Deep research"** — el LLM marca `deep_research=true`/`next_step=
  "RESEARCH"`, pero **no existe ningún proceso automático posterior** que
  ejecute una segunda pasada de investigación más profunda; es una señal
  para que un humano priorice, no una funcionalidad activa por sí misma.
- **CONTEXT como "contexto histórico para un EVENT"** — los filings
  CONTEXT se almacenan y son consultables (por empresa/ticker desde la
  búsqueda de Detalle del Filing), pero no se ha encontrado ningún
  mecanismo automático en el código que vincule explícitamente un filing
  EVENT con sus CONTEXT relacionados o los incluya en el prompt del LLM —
  la "recuperación bajo demanda" mencionada en el README es, en la
  práctica, "un humano puede buscarlo manualmente en el dashboard".
- **`exchange`** — columna presente en `filings` y en `companies`, pero
  **ningún flujo del código actual la rellena** (ni la ingesta, ni el
  enriquecimiento de tickers, ni el de OTC). Reservada para uso futuro,
  documentado así en el propio README.
- **Enriquecimiento OTC** (`otc_tier`, `sec_type`, `country`) — funcional
  en el backend, pero la columna `otc_tier` **ya no se expone como filtro
  ni columna en el dashboard** (se eliminó de la UI del Explorador durante
  el desarrollo, manteniendo la columna y el enriquecimiento intactos en la
  base de datos/backend).

### Código legado / no utilizado

- **`src/document_intelligence/`** — módulo completo (`extractor.py`,
  `models.py`, `patterns.py`) y su test (`tests/test_document_intelligence.py`)
  fueron eliminados del árbol de trabajo (aparecen como `D` en
  `git status`); no queda ninguna referencia activa a
  `document_intelligence` en el código Python actual. El README del
  dashboard (`dashboard/README.md:45-47`) sigue mencionando "extracción
  estructurada (Document Intelligence)" como parte de la pantalla Detalle
  del Filing — **desactualizado respecto al código real**.
- **`items_json`/`keywords_json`** en `build_filing_input`
  (`src/llm_analysis/first_pass.py:43-44`) — referencian columnas que no
  existen en el esquema actual de `filings`; siempre se resuelven a listas
  vacías. Resto muerto de la etapa de extracción estructurada ya eliminada.
  No afecta a la corrección del resultado, pero es código sin efecto que
  podría limpiarse.
- **`screener.csv`** (raíz del repo) — aparece eliminado en `git status`;
  era, presumiblemente, un fichero de datos de ejemplo/entrada manual para
  `--import-otc-screener-csv`, no código.
- **`.env.example`** — aparece eliminado en `git status`; el README lo
  referencia como parte del flujo de configuración, pero el fichero de
  plantilla no está presente en este checkout.
- **Funcionalidad de descarte manual (`manual_discarded`)** — implementada
  y completamente revertida durante el desarrollo (ver sección 13); no
  queda código vivo, solo la posibilidad de que la columna exista de forma
  inerte en una base de datos real ya migrada durante las pruebas.
- **Columna "Mercado" en el Explorador de Eventos** — existió en una
  iteración intermedia del dashboard y fue eliminada explícitamente de la
  tabla (aunque `market_impact` se sigue calculando, guardando y mostrando
  en Detalle del Filing con normalidad).

### TODOs

No se han encontrado comentarios `TODO`/`FIXME`/`XXX` en el código fuente
Python auditado (`src/`, `dashboard/`) — `No determinado a partir del
código` más allá de esta ausencia constatada.

### Limitaciones observadas

- **Sin backfill automático de `market_impact`/`next_step` para filings ya
  analizados antes de que esos campos existieran** más allá de lo que el
  propio `ALTER TABLE` permite (columnas nuevas quedan `NULL` en filas
  antiguas; no hay un paso que vuelva a llamar al LLM para rellenarlas).
- **Sin límite de tokens real, solo de caracteres** (`MAX_CLEAN_TEXT_CHARS
  = 20000`) — para modelos con tokenización muy distinta del recuento de
  caracteres, el presupuesto real de contexto podría no ajustarse con
  precisión.
- **Sin reintento automático de un `PARSE_ERROR`** — un filing cuya
  respuesta no se pudo parsear queda registrado como tal permanentemente
  salvo que se vuelva a seleccionar manualmente con `status="all"` (el
  filtro `status="pending"` no distingue "nunca analizado" de "analizado
  pero con PARSE_ERROR": ambos casos, una vez hay fila en
  `llm_filing_analysis`, cuentan como "ya analizado").
- **Ningún mecanismo de autenticación/autorización en el dashboard** — es
  una aplicación Streamlit sin capa de usuarios/roles; cualquiera con
  acceso a la URL puede disparar ingesta o análisis LLM (gasto real) desde
  "Procesar filings".
- **Dependencia de dos entornos virtuales distintos** (backend Python 3.9,
  dashboard Python 3.12) — documentado como necesario por
  incompatibilidades binarias en macOS, pero añade complejidad operativa
  (dos `pip install` distintos, dos `requirements.txt`).
- **No hay pruebas de integración automatizadas de extremo a extremo**
  contra SEC EDGAR real o contra un proveedor LLM real — los benchmarks
  (`scripts/benchmark_llm_concurrency.py`) hacen llamadas reales pero son
  manuales, no CI.

### Funcionalidades mencionadas en README pero ausentes/desactualizadas en el código

- Extracción estructurada "Document Intelligence" en Detalle del Filing
  (`dashboard/README.md`) — módulo eliminado.
- Filtro por "empresa" en el Explorador de Eventos
  (`dashboard/README.md:43`, "filtros (fecha, ticker, empresa,
  formulario)") — la sidebar actual (`sidebar_filters.py`) no tiene ningún
  filtro de texto por nombre de empresa, solo Ticker.
- Columnas `change_percent` y `state` en `otc_securities`
  (`README.md`, sección "Database schema") — no existen en
  `_CREATE_OTC_SECURITIES_SQL` ni en el parseo del CSV.
- El propio `dashboard/README.md:44` afirma que la tabla "sigue mostrando
  evento principal, importance score y deep research por fila, aunque ya
  no se pueda filtrar por ellos" — esto es coherente con el código actual
  (esas tres columnas sí siguen en `_DISPLAY_ORDER`), pero el mismo README
  no refleja que también se retiró la columna "Mercado" en una iteración
  posterior a cuando se escribió ese párrafo.

### Posibles mejoras futuras (identificadas por contraste entre lo implementado y lo mencionado/insinuado en el propio proyecto, no una lista de deseos externa)

- Vincular automáticamente un EVENT con sus CONTEXT relacionados (mismo
  `cik`) para enriquecer el prompt del LLM con contexto histórico real.
- Reprocesar automáticamente los `PARSE_ERROR` (hoy requiere selección
  manual explícita).
- Backfill del análisis LLM para filings antiguos cuando cambie el
  esquema de campos devueltos por el modelo.
- Persistir los resultados de `scripts/benchmark_llm_concurrency.py` para
  poder documentar con cifras reales la elección de `DEFAULT_WORKERS`.
- Limpiar el código muerto de `items_json`/`keywords_json` en
  `build_filing_input`.
- Actualizar `README.md` y `dashboard/README.md` para reflejar el estado
  actual (eliminación de Document Intelligence, del filtro de empresa, de
  la columna Mercado, y las columnas reales de `otc_securities`).
