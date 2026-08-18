# OTC Filing Intelligence — Dashboard

Interfaz web de solo lectura sobre `data/filings.db`. No modifica el backend ni
la base de datos (la conexión se abre en modo `mode=ro`); su único trabajo es
consultar y presentar lo que el pipeline (`src/`) ya generó, para que un
analista pueda revisar y priorizar los eventos corporativos detectados.

## Instalación y ejecución

El dashboard usa su **propio entorno virtual**, separado del `.venv` del
backend (`src/`), con Python 3.12 instalado vía Homebrew:

```bash
# Una sola vez:
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv dashboard/.venv

# Cada vez que quieras correrlo, desde la raíz del proyecto:
source dashboard/.venv/bin/activate
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

**¿Por qué un venv aparte?** El `.venv` del backend usa el Python 3.9 que
viene con las Command Line Tools de Apple
(`/Library/Developer/CommandLineTools/usr/bin/python3`), enlazado contra una
LibreSSL muy antigua. Los wheels binarios modernos de Streamlit (numpy,
pandas, pyarrow...) no son compatibles con ese Python en macOS reciente y
pueden hacer crashear el proceso (`segmentation fault`,
`malloc: pointer being freed was not allocated`). Un Python 3.12 "de verdad"
evita el problema sin tocar el entorno del backend.

La app necesita que `data/filings.db` ya exista (créala primero con el
pipeline, ver el README principal del proyecto).

## Páginas

- **Resumen Ejecutivo** — KPIs generales, eventos por categoría, evolución
  diaria y distribución de importance score.
- **Explorador de Eventos** — tabla completa de filings EVENT con filtros
  (fecha, ticker, empresa, formulario, categoría del evento, score mínimo,
  deep research, OTC tier), orden y paginación. Filtrado, orden y paginación
  se resuelven en SQL, no en pandas, para escalar a miles de filas.
- **Detalle del Filing** — toda la información de un filing: datos generales,
  análisis IA, extracción estructurada (Document Intelligence), evidencias y
  texto completo. Se llega aquí seleccionando una fila del Explorador o
  buscando directamente por empresa/ticker.
- **Estadísticas** — eventos por categoría/formulario, evolución temporal,
  empresas con más eventos, distribución de importance score y % de Deep
  Research.

## Estructura

```
dashboard/
├── app.py                # Entry point: tema, CSS, navegación (st.navigation)
├── config.py              # Rutas, umbrales, paleta de color
├── .streamlit/config.toml # Tema nativo de Streamlit (oscuro) para que los
│                          # widgets nativos (sliders, checkboxes, barras de
│                          # progreso) usen la misma paleta que el CSS
├── database/              # Acceso a datos — la única capa que conoce SQL
├── services/               # Filtros (UI -> WHERE/params) y cálculo de KPIs
├── components/             # Piezas de UI reutilizables (headers, tarjetas,
│                          # tabla, filtros, secciones de detalle, meter)
├── charts/                 # Figuras Plotly + tema compartido
├── screens/                # Una página por pantalla (orquestan datos + UI)
├── styles/main.css         # Tema oscuro tipo terminal financiero
└── utils/                  # Formateo y parseo seguro de columnas *_json
```

## Notas de diseño

- **Solo lectura de verdad**: la conexión SQLite se abre con la URI
  `file:...?mode=ro`, así que cualquier intento de escritura falla a nivel de
  driver, no solo por convención de código.
- **Tema oscuro fijo**, no light/dark toggle — así se ve consistentemente
  como una terminal financiera (Bloomberg/PitchBook/AlphaSense), que es lo
  que se pidió.
- Los filings con `clean_text` extremadamente largo (algún exhibit sin
  depurar puede superar varios MB) se truncan en el detalle
  (`config.MAX_FULL_TEXT_CHARS`) con un enlace al documento completo en
  EDGAR — mostrar varios MB de texto en un único nodo HTML congela el navegador.
