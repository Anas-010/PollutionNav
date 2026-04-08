"""
config.py
=========
Central configuration for the Mumbai AQI Real-Time Route Planner.

Every magic number, colour code, API setting, and physical constant
lives here. Nothing else in the codebase should hardcode these values.
Changing a setting here propagates everywhere automatically.
"""

# ── Mumbai geographic bounding box (WGS84 lat/lon) ───────────────────────────
# Used to restrict OpenAQ queries, geocoding bias, and OSM downloads.
MUMBAI_BBOX = {
    "north": 19.30,
    "south": 18.85,
    "east":  73.05,
    "west":  72.75,
}
MUMBAI_CENTER = [19.076, 72.877]  # Default map centre

# ── Coordinate reference systems ──────────────────────────────────────────────
# EPSG:32643 = WGS84 / UTM Zone 43N — the correct metric CRS for Mumbai.
# All buffer and distance operations must use this CRS, not lat/lon degrees,
# because degrees are not uniform in metres at this latitude.
PROJECTED_CRS  = "EPSG:32643"
GEOGRAPHIC_CRS = "EPSG:4326"

# ── OpenAQ v3 API settings ────────────────────────────────────────────────────
# IMPORTANT: v1 and v2 were permanently retired on 31 January 2025.
# Always use the v3 base URL. Get a free key at explore.openaq.org/register.
OPENAQ_BASE_URL   = "https://api.openaq.org/v3"
OPENAQ_TIMEOUT_S  = 15    # Per-request timeout in seconds
OPENAQ_PAGE_LIMIT = 1000  # Max results per API page

# How long to cache a fetched AQI surface before re-fetching from OpenAQ.
# 30 minutes is appropriate — CPCB publishes hourly averages and the pipeline
# adds ~1 hour delay, so fetching more often gives identical data.
CACHE_TTL_MINUTES = 30

# Minimum number of stations that must have live readings for interpolation
# to be considered reliable. Below this, the app falls back to the
# pre-computed historical CSV and shows a clear warning to the user.
MIN_LIVE_STATIONS = 5

# ── OpenRouteService routing settings ────────────────────────────────────────
ORS_BASE_URL       = "https://api.openrouteservice.org/v2/directions"
ORS_TIMEOUT_S      = 20
ORS_N_ALTERNATIVES = 2   # Additional routes beyond the fastest

ORS_PROFILES = {
    "🚗 Driving":  "driving-car",
    "🚶 Walking":  "foot-walking",
    "🚴 Cycling":  "cycling-regular",
}

# ── CPCB AQI breakpoint tables ────────────────────────────────────────────────
# Source: CPCB National Air Quality Index (2014), Table 1.
# Each tuple: (C_low, C_high, I_low, I_high)
# Sub-index formula: I = ((I_hi - I_lo) / (C_hi - C_lo)) × (C - C_lo) + I_lo
# Units: PM in µg/m³ (24-hour avg), CO in mg/m³ (8-hour avg),
#        NO2/SO2 in µg/m³ (1-hour avg)
CPCB_BREAKPOINTS = {
    "pm25": [
        (0, 30, 0, 50), (30, 60, 51, 100), (60, 90, 101, 200),
        (90, 120, 201, 300), (120, 250, 301, 400), (250, 380, 401, 500),
    ],
    "pm10": [
        (0, 50, 0, 50), (50, 100, 51, 100), (100, 250, 101, 200),
        (250, 350, 201, 300), (350, 430, 301, 400), (430, 600, 401, 500),
    ],
    "no2": [
        (0, 40, 0, 50), (40, 80, 51, 100), (80, 180, 101, 200),
        (180, 280, 201, 300), (280, 400, 301, 400), (400, 800, 401, 500),
    ],
    "co": [
        (0, 1, 0, 50), (1, 2, 51, 100), (2, 10, 101, 200),
        (10, 17, 201, 300), (17, 34, 301, 400), (34, 50, 401, 500),
    ],
    "so2": [
        (0, 40, 0, 50), (40, 80, 51, 100), (80, 380, 101, 200),
        (380, 800, 201, 300), (800, 1600, 301, 400), (1600, 2620, 401, 500),
    ],
}

# ── AQI category thresholds, colours, and emoji ───────────────────────────────
# Order matters — evaluated from lowest to highest.
AQI_CATEGORIES = [
    (50,  "Good"),
    (100, "Satisfactory"),
    (200, "Moderate"),
    (300, "Poor"),
    (400, "Very Poor"),
    (500, "Severe"),
]

# Official CPCB colour codes used on India's AQI dashboard
AQI_PALETTE = {
    "Good":         "#00c400",
    "Satisfactory": "#92d14f",
    "Moderate":     "#e0c800",
    "Poor":         "#ff7e00",
    "Very Poor":    "#e00000",
    "Severe":       "#7e0023",
    "Unknown":      "#aaaaaa",
}

AQI_EMOJI = {
    "Good": "🟢", "Satisfactory": "🟡", "Moderate": "🟠",
    "Poor": "🔴", "Very Poor": "🟣", "Severe": "⚫", "Unknown": "⚪",
}

# ── Month mapping for historical fallback ─────────────────────────────────────
# The historical CSV covers Jul–Dec only. When the current month is outside
# that range, we remap to the nearest climatologically similar month.
#
# Mapping rationale (Mumbai climate zones):
#   Jan–Feb  → December  : Dry winter, low AQI variability, similar wind patterns
#   Mar–Apr  → October   : Post-monsoon dry, moderate pollution
#   May–Jun  → July      : Pre-monsoon / early monsoon, high humidity
#   Jul–Dec  → exact match (data exists)
HISTORICAL_MONTH_MAP = {
    1: 12, 2: 12,
    3: 10, 4: 10,
    5:  7, 6:  7,
    7: 7, 8: 8, 9: 9, 10: 10, 11: 11, 12: 12,
}

# ── Interpolation settings ────────────────────────────────────────────────────
GRID_RESOLUTION_M = 200    # Raster grid resolution for the pollution surface
AQI_DISPLAY_MAX   = 500    # Cap for map colouring

# ── File paths (relative to project root, same directory as app.py) ──────────
FALLBACK_CSV_PATH = "data/road_aqi_output.csv"
STATIONS_CSV_PATH = "data/cleaned_aqi_mumbai_imputed.csv"
