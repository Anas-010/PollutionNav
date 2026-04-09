# Appendix — PollutionNav: Spatial AQI Interpolation and Pollution-Optimal Routing in Mumbai

> **Report:** PollutionNav: Spatial AQI Interpolation and Pollution Optimal Routing in Mumbai  
> **Authors:** Pratyaksh Bhutani, Husain Bootwala, Shitiz Kumar Gupta, Keegan Nunes, Anas Shaikh  
> **Institution:** SVKM's NMIMS (Deemed-to-be University) — Nilkamal School of Mathematical, Applied Statistics & Analytics  
> **Supervisor:** Prof. Shraddha Sarode  
> **Date:** April 2026

---

## Contents

- [A1. Complete CPCB AQI Breakpoint Tables](#a1-complete-cpcb-aqi-breakpoint-tables)
- [A2. Mumbai AQI Monitoring Station Register](#a2-mumbai-aqi-monitoring-station-register)
- [A3. Land Use Regression Full Results](#a3-land-use-regression-full-results-all-models--all-pollutants)
- [A4. Complete Technology Stack and Library Versions](#a4-complete-technology-stack-and-library-versions)
- [A5. Data Pipeline and Preprocessing Parameters](#a5-data-pipeline-and-preprocessing-parameters)

---

## A1. Complete CPCB AQI Breakpoint Tables

The following tables reproduce the complete set of CPCB National AQI (2014) breakpoints for all five pollutants used in this project. These are the authoritative reference values underlying all sub-index calculations.

The sub-index for each pollutant is computed by piecewise linear interpolation:

$$I_p = \frac{I_{Hi} - I_{Lo}}{C_{Hi} - C_{Lo}} \cdot (C_p - C_{Lo}) + I_{Lo}$$

where $C_p$ is the observed concentration and the bounds correspond to the applicable breakpoint interval. Sub-indices exceeding the highest defined breakpoint are capped at 500. The composite station AQI is the **maximum sub-index across all available pollutants**.

---

### A1.1 PM2.5 (24-hour average, μg/m³)

| PM2.5 Conc. (μg/m³) | AQI Sub-Index | Category | Health Implication |
|---|---|---|---|
| 0 – 30 | 0 – 50 | Good | Minimal impact |
| 30 – 60 | 51 – 100 | Satisfactory | Minor breathing discomfort for sensitive persons |
| 60 – 90 | 101 – 200 | Moderate | Breathing discomfort for asthma, heart disease patients |
| 90 – 120 | 201 – 300 | Poor | Breathing discomfort for most on prolonged exposure |
| 120 – 250 | 301 – 400 | Very Poor | Respiratory illness on prolonged exposure |
| 250 – 380 | 401 – 500 | Severe | Serious risk; hazardous for sensitive groups |

---

### A1.2 PM10 (24-hour average, μg/m³)

| PM10 Conc. (μg/m³) | AQI Sub-Index | Category | Health Implication |
|---|---|---|---|
| 0 – 50 | 0 – 50 | Good | Minimal impact |
| 50 – 100 | 51 – 100 | Satisfactory | Minor breathing discomfort for sensitive persons |
| 100 – 250 | 101 – 200 | Moderate | Breathing discomfort for asthma, heart disease patients |
| 250 – 350 | 201 – 300 | Poor | Breathing discomfort for most on prolonged exposure |
| 350 – 430 | 301 – 400 | Very Poor | Respiratory illness on prolonged exposure |
| 430 – 600 | 401 – 500 | Severe | Serious risk; hazardous for sensitive groups |

---

### A1.3 NO₂ (1-hour average, μg/m³)

| NO₂ Conc. (μg/m³) | AQI Sub-Index | Category | Health Implication |
|---|---|---|---|
| 0 – 40 | 0 – 50 | Good | Minimal impact |
| 40 – 80 | 51 – 100 | Satisfactory | Minor breathing discomfort for sensitive persons |
| 80 – 180 | 101 – 200 | Moderate | Breathing discomfort for asthma, heart disease patients |
| 180 – 280 | 201 – 300 | Poor | Breathing discomfort on prolonged exposure |
| 280 – 400 | 301 – 400 | Very Poor | Respiratory illness on prolonged exposure |
| 400 – 800 | 401 – 500 | Severe | Serious health effects |

---

### A1.4 CO (8-hour average, mg/m³)

| CO Conc. (mg/m³) | AQI Sub-Index | Category | Health Implication |
|---|---|---|---|
| 0 – 1.0 | 0 – 50 | Good | Minimal impact |
| 1.0 – 2.0 | 51 – 100 | Satisfactory | Minor breathing discomfort for sensitive persons |
| 2.0 – 10 | 101 – 200 | Moderate | Breathing discomfort for asthma, heart disease patients |
| 10 – 17 | 201 – 300 | Poor | Breathing discomfort on prolonged exposure |
| 17 – 34 | 301 – 400 | Very Poor | Serious risk of exposure |
| 34 – 50 | 401 – 500 | Severe | Serious health effects |

> **Note:** API readings reported in μg/m³ are divided by 1,000 before applying these breakpoints. Any CO reading > 50 in the raw API response is automatically converted (see Section 6.2.3 of the report).

---

### A1.5 SO₂ (1-hour average, μg/m³)

| SO₂ Conc. (μg/m³) | AQI Sub-Index | Category | Health Implication |
|---|---|---|---|
| 0 – 40 | 0 – 50 | Good | Minimal impact |
| 40 – 80 | 51 – 100 | Satisfactory | Minor breathing discomfort for sensitive persons |
| 80 – 380 | 101 – 200 | Moderate | Breathing discomfort for asthma, heart disease patients |
| 380 – 800 | 201 – 300 | Poor | Breathing discomfort on prolonged exposure |
| 800 – 1600 | 301 – 400 | Very Poor | Respiratory illness on prolonged exposure |
| 1600 – 2100 | 401 – 500 | Severe | Serious health effects |

---

## A2. Mumbai AQI Monitoring Station Register

All 27 CPCB and MPCB monitoring stations used in this project, as retrieved from the OpenAQ v3 API via the `/v3/locations` endpoint with the Mumbai bounding box (18.89°N–19.32°N, 72.79°E–72.96°E).

**Operator codes:** MPCB = Maharashtra Pollution Control Board; IITM = Indian Institute of Tropical Meteorology; BMC = Brihanmumbai Municipal Corporation.

| # | OpenAQ ID | Station Name | Operator | Lat | Lon |
|---|---|---|---|---|---|
| 1 | 6927 | Colaba | MPCB | 18.9100 | 72.8200 |
| 2 | 6945 | Kurla | MPCB | 19.0863 | 72.8888 |
| 3 | 6948 | CSIA Airport T2 | MPCB | 19.1008 | 72.8746 |
| 4 | 6956 | Powai | MPCB | 19.1375 | 72.9151 |
| 5 | 6959 | Siddharth Nagar–Worli | IITM | 19.0001 | 72.8140 |
| 6 | 6965 | Borivali East (MPCB) | MPCB | 19.2275 | 72.8644 |
| 7 | 6967 | Sion | MPCB | 19.0470 | 72.8746 |
| 8 | 6987 | Vile Parle West | MPCB | 19.1086 | 72.8362 |
| 9 | 11606 | Borivali East (IITM) | IITM | 19.2324 | 72.8690 |
| 10 | 11611 | Malad West | IITM | 19.1971 | 72.8220 |
| 11 | 12024 | Chakala–Andheri East | IITM | 19.1107 | 72.8608 |
| 12 | 12039 | Khindipada–Bhandup West | IITM | 19.1644 | 72.9283 |
| 13 | 12040 | Mulund West | MPCB | 19.1750 | 72.9419 |
| 14 | 12044 | Kandivali East | MPCB | 19.2058 | 72.8682 |
| 15 | 3409323 | Worli | MPCB | 18.9936 | 72.8128 |
| 16 | 3409328 | Bandra Kurla Complex (IITM) | IITM | 19.0627 | 72.8461 |
| 17 | 3409329 | Deonar | IITM | 19.0495 | 72.9230 |
| 18 | 3409478 | Bhayandar West | MPCB | 19.2965 | 72.8409 |
| 19 | 3409479 | Mindspace–Malad West | MPCB | 19.1879 | 72.8304 |
| 20 | 3409482 | Bandra Kurla Complex (MPCB) | MPCB | 19.0659 | 72.8621 |
| 21 | 3409483 | Chembur | MPCB | 19.0365 | 72.8954 |
| 22 | 3409486 | Kherwadi–Bandra East | MPCB | 19.0632 | 72.8456 |
| 23 | 3409510 | Byculla | BMC | 18.9767 | 72.8380 |
| 24 | 3409511 | Shivaji Nagar | BMC | 19.0605 | 72.9234 |
| 25 | 3409512 | Kandivali West | BMC | 19.2159 | 72.8317 |
| 26 | 3409513 | Sewri | BMC | 19.0001 | 72.8567 |
| 27 | 3409514 | Ghatkopar | BMC | 19.0837 | 72.9210 |

All coordinates are in WGS84 (EPSG:4326). Stations are projected to UTM Zone 43N (EPSG:32643) for all spatial operations.

---

## A3. Land Use Regression Full Results (All Models × All Pollutants)

Hold-out RMSE and MAE for all five model architectures across all five pollutant targets, as generated by `lur_pipeline.py`. The train-test split was conducted at the **station level** (~80% training, ~20% test stations) to evaluate generalisation to spatially unseen locations. ★ = best per pollutant.

### A3.1 Station-wise Train-Test Split Sizes

| Target | Total Rows | Train Rows | Test Rows | Units |
|---|---|---|---|---|
| PM2.5 | 94,534 | 76,467 | 18,067 | μg/m³ |
| PM10 | 97,852 | 77,630 | 20,222 | μg/m³ |
| NO₂ | 99,212 | 79,697 | 19,515 | μg/m³ |
| CO | 94,797 | 75,931 | 18,866 | mg/m³ |
| SO₂ | 90,212 | 72,086 | 18,126 | μg/m³ |

### A3.2 Hold-out RMSE by Model and Pollutant

| Model | PM2.5 (μg/m³) | PM10 (μg/m³) | NO₂ (μg/m³) | CO (mg/m³) | SO₂ (μg/m³) |
|---|---|---|---|---|---|
| Ridge | 24.90 | 54.27 | 20.39 | 0.419 | 11.23 |
| ElasticNet | **24.05 ★** | 52.01 | 18.45 | **0.419 ★** | 12.18 |
| Random Forest | 26.68 | 42.88 | **10.45 ★** | 0.427 | **10.45 ★** |
| Gradient Boosting | 37.26 | 48.78 | 15.52 | 0.456 | 14.79 |
| XGBoost | 24.13 | **41.89 ★** | 12.78 | 0.429 | 13.85 |

### A3.3 Hold-out MAE by Model and Pollutant

| Model | PM2.5 (μg/m³) | PM10 (μg/m³) | NO₂ (μg/m³) | CO (mg/m³) | SO₂ (μg/m³) |
|---|---|---|---|---|---|
| Ridge | 18.49 | 41.30 | 15.48 | 0.308 | 8.40 |
| ElasticNet | **16.97 ★** | 37.55 | 14.67 | **0.286 ★** | 8.54 |
| Random Forest | 19.93 | 33.48 | **7.90 ★** | 0.299 | **6.81 ★** |
| Gradient Boosting | 27.82 | 38.30 | 11.89 | 0.321 | 9.65 |
| XGBoost | 16.86 ★ | **32.45 ★** | 9.97 | 0.293 | 9.50 |

> **Key finding:** NO₂ and SO₂ were the most successfully modelled pollutants (RMSE = 10.45 μg/m³ each), consistent with their strong spatial association with road network density — a signal well-represented in the OSM-derived feature set. PM2.5 showed near-zero explained variance on held-out stations, reflecting the dominance of mesoscale meteorological drivers (monsoon sea breeze, nocturnal boundary layer collapse) that static land-use predictors cannot capture.

---

## A4. Complete Technology Stack and Library Versions

| Library / Service | Role | Version Tested |
|---|---|---|
| Python | Core programming language | 3.13.2 |
| Streamlit | Web application framework; session state management | 1.x |
| Pandas | Tabular data manipulation; imputation; time-series operations | ≥ 2.0 |
| NumPy | Numerical arrays; interpolation computations | ≥ 1.24 |
| SciPy (`cKDTree`) | K-Dimensional tree for O(log N) nearest-neighbour road snapping | ≥ 1.11 |
| SciPy (`RBFInterpolator`) | Radial Basis Function interpolation (thin plate spline) | ≥ 1.11 |
| GeoPandas | CRS-aware spatial operations; WGS84 → UTM projection | ≥ 1.0 |
| PyKrige | Ordinary Kriging with variogram fitting | 1.x |
| Folium | Interactive Leaflet.js maps embedded in Streamlit | 0.x |
| Plotly | Interactive bar, line, and scatter charts | 5.x |
| Requests | HTTP client for OpenAQ and ORS API calls | 2.x |
| Geopy (Nominatim) | Fallback geocoding (address → coordinates) | 2.x |
| scikit-learn | LUR pipeline: imputation, scaling, Ridge/ElasticNet/RandomForest/GB | ≥ 1.4 |
| XGBoost | Gradient-boosted trees for LUR modelling | ≥ 2.0 |
| SHAP | Model explainability for LUR feature importance | ≥ 0.44 |
| Matplotlib / Seaborn | Static plots for LUR pipeline diagnostics | 3.7 / 0.12 |
| OSMnx | OpenStreetMap road network and POI download for LUR features | 2.1.0 |
| Shapely | Geometric operations: buffers, intersections, area computation | ≥ 2.0 |
| OpenAQ v3 API | Live pollutant readings from CPCB/MPCB monitoring stations | v3 (2025) |
| OpenRouteService API | Multi-modal routing; alternative route generation | v2 |
| Photon (Komoot) Geocoder | Primary geocoding for Mumbai location search | Public API |
| Nominatim (OSM) | Fallback geocoder when Photon returns no results | Public API |
| CartoDB Positron | Base map tile layer (muted grey background) | Tile CDN |

---

## A5. Data Pipeline and Preprocessing Parameters

Key parameter choices made during data preprocessing, with the rationale for each decision.

| Parameter | Value | Rationale |
|---|---|---|
| Max consecutive NaN gap for time interpolation | 3 hours | Gaps ≥ 4 hours cannot be reliably reconstructed from adjacent readings; set conservatively to preserve data integrity |
| KNN imputation neighbours (meteorological variables) | k = 5 | Balances sensitivity to localised spatial patterns with stability; standard for sparse networks |
| PM2.5 sensor-error rejection threshold | > 1,000 μg/m³ | Physically impossible; CPCB-recorded maximum in any Indian city is ≈ 900 μg/m³ |
| PM10 sensor-error rejection threshold | > 1,500 μg/m³ | Physically impossible |
| NO₂ sensor-error rejection threshold | > 2,000 μg/m³ | Exceeds CPCB maximum breakpoint (800 μg/m³) by a factor of 2.5 |
| CO unit conversion threshold | > 50 | CO > 50 in a μg/m³-scale reading is impossible in mg/m³ terms; sensor reports in μg/m³ — divide by 1,000 |
| IQR Winsorisation multiplier | 3.0× IQR | More conservative than the standard 1.5×; chosen to cap only extreme outliers, not genuine pollution events |
| Min valid sub-indices for composite AQI | 3 | CPCB standard requirement |
| API cache TTL | 30 minutes | CPCB publishes hourly averages with 1–2 h pipeline delay; re-fetching more often returns identical data |
| Min live stations for interpolation | 5 | Below 5 stations, spatial coverage of Mumbai's 603 km² is insufficient for credible interpolation |
| Parallel fetch workers | 10 threads | Reduces sequential ~40 s fetch to 3–4 s; bounded at 10 to respect API rate limits |
| AQI surface grid resolution | 200 m | Balances spatial precision with computation time (~25,000 grid points) |
| Road KD-tree snap threshold | 500 m | Prevents waypoints over water or unroutable areas from receiving erroneous AQI assignments |
| Map marker sample (AQI surface display) | 12,000 | Maximum that renders smoothly in typical browser environments without lag |

---

*This appendix accompanies the full project report. For methodology details, results discussion, and references, see the main report document.*
