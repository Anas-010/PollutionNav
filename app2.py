"""
app.py
======
Mumbai AQI Real-Time Route Planner — Streamlit Entry Point (v2)

Tab layout:
  Tab 1 — 📐 Interpolation Evaluation  : LOSO CV for all methods, comparison charts
  Tab 2 — 🌫️ AQI Surface               : Pollution surface (updates with chosen method)
  Tab 3 — 🛣️ Route Planner             : Transport + method selection, Photon search,
                                          route results (cleanest = lowest total dose)

Key changes from v1:
  - Cleanest route now uses total_dose (mean_aqi × distance_km) not mean_aqi
  - Method & transport selectors moved from sidebar → Tab 3 (main page)
  - Evaluation metrics moved from sidebar → Tab 1, covers ALL methods with charts
  - Photon (OSM) geocoding with Mumbai-bounded dropdown for location selection
  - Surface rebuilds automatically when interpolation method changes
"""

import math
import requests as _requests
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium

from config import (
    AQI_EMOJI,
    AQI_PALETTE,
    MIN_LIVE_STATIONS,
    ORS_PROFILES,
    STATIONS_CSV_PATH,
    MUMBAI_BBOX,
    MUMBAI_CENTER,
)
from modules.geocoding import geocode
from modules.interpolation import (
    INTERPOLATION_METHODS as _INTERPOLATION_METHODS_RAW,
    build_road_kdtree,
    evaluate_model,
    sample_surface_at_roads,
)

# RBF (multiquadric) is excluded from this project — removed here at import time
# so it never appears in the sidebar selector, eval tab, or surface builder.
INTERPOLATION_METHODS = {
    k: v for k, v in _INTERPOLATION_METHODS_RAW.items()
    if "multiquadric" not in k.lower()
}
from modules.map_builder import (
    aqi_category,
    aqi_color,
    empty_map,
    route_map,
    surface_only_map,
)
from modules.openaq_client import (
    fetch_live_aqi,
    load_historical_surface,
    should_refresh_cache,
)
from modules.routing import (
    compute_exposure,
    fetch_routes,
    haversine,
    tag_fastest_and_cleanest,
)

# ── Try importing format_duration (may not be exported in all versions) ────────
try:
    from modules.routing import format_duration
except ImportError:
    def format_duration(seconds: float) -> str:
        m = int(seconds // 60)
        return f"{m // 60}h {m % 60}min" if m >= 60 else f"{m} min"


# ══════════════════════════════════════════════════════════════════════════════
# PAGE CONFIG
# ══════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Mumbai AQI Route Planner",
    page_icon="🗺️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# PHOTON GEOCODING (OSM) — Mumbai-bounded dropdown search
# ══════════════════════════════════════════════════════════════════════════════

def search_photon(query: str, limit: int = 8) -> list:
    """
    Query Photon (photon.komoot.io) geocoder, biased toward Mumbai.

    Uses lat/lon centre bias instead of a hard bbox filter so that nearby
    results just outside the strict bounding box (e.g. Navi Mumbai) are
    still returned.  The results are post-filtered to keep only entries
    within a generous ±0.6° of Mumbai's centre.

    Returns a list of dicts with 'display', 'lat', 'lon'.
    """
    if not query or len(query.strip()) < 3:
        return []
    try:
        resp = _requests.get(
            "https://photon.komoot.io/api/",
            params={
                "q":     f"{query.strip()} Mumbai",
                "limit": limit,
                "lat":   MUMBAI_CENTER[0],
                "lon":   MUMBAI_CENTER[1],
                "lang":  "en",
            },
            timeout=6,
        )
        resp.raise_for_status()
        results = []
        seen    = set()
        lat_min = MUMBAI_BBOX["south"] - 0.15
        lat_max = MUMBAI_BBOX["north"] + 0.15
        lon_min = MUMBAI_BBOX["west"]  - 0.15
        lon_max = MUMBAI_BBOX["east"]  + 0.15
        for feat in resp.json().get("features", []):
            props  = feat.get("properties", {})
            coords = feat.get("geometry", {}).get("coordinates", [None, None])
            if None in coords:
                continue
            lat, lon = float(coords[1]), float(coords[0])
            # Soft geographic filter — accept results near Mumbai
            if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
                continue
            parts = [
                props.get("name"),
                props.get("street"),
                props.get("suburb") or props.get("district") or props.get("neighbourhood"),
                props.get("city") or props.get("locality") or "Mumbai",
            ]
            display = ", ".join(p for p in parts if p)
            if not display or display in seen:
                continue
            seen.add(display)
            results.append({"display": display, "lat": lat, "lon": lon})
        return results
    except Exception:
        return []


def _nominatim_fallback(query: str) -> list:
    """
    Fallback geocoder using Nominatim (OpenStreetMap) when Photon returns nothing.
    Wraps the shared geocode() utility and returns a single-item list so the
    dropdown UI stays consistent.
    """
    from modules.geocoding import geocode as _geo
    try:
        result = _geo(query.strip())
        if result is None:
            result = _geo(f"{query.strip()}, Mumbai, India")
        if result:
            lat, lon = result
            return [{"display": f"{query.strip()}, Mumbai", "lat": lat, "lon": lon}]
    except Exception:
        pass
    return []


def location_search_widget(label: str, key_prefix: str) -> tuple:
    """
    Two-step Photon search widget:
      1. User types >= 3 chars  -> Photon query fires
      2. Results shown as selectbox  -> user picks one
      3. Coordinates stored in session state

    Returns ((lat, lon) or None, display_name string).
    """
    st.markdown(f"**{label}**")
    query = st.text_input(
        label,
        key=f"{key_prefix}_query_input",
        placeholder="Type at least 3 characters to search…",
        label_visibility="collapsed",
    )

    ll_key      = f"{key_prefix}_ll"
    name_key    = f"{key_prefix}_name"
    prev_key    = f"{key_prefix}_prev_query"
    results_key = f"{key_prefix}_results"

    # Invalidate stored selection when query changes
    if query != st.session_state.get(prev_key, ""):
        st.session_state[ll_key]      = None
        st.session_state[name_key]    = ""
        st.session_state[results_key] = []
        st.session_state[prev_key]    = query

    # Fire Photon search when query is long enough and no selection yet
    if query and len(query.strip()) >= 3 and not st.session_state.get(ll_key):
        with st.spinner("🔍 Searching…"):
            photon_results = search_photon(query)
            # If Photon found nothing, silently try Nominatim as fallback
            if not photon_results:
                photon_results = _nominatim_fallback(query)
        st.session_state[results_key] = photon_results

    photon_results = st.session_state.get(results_key, [])

    if photon_results and not st.session_state.get(ll_key):
        options = ["— select a location —"] + [r["display"] for r in photon_results]
        choice = st.selectbox(
            "Select from results",
            options=options,
            key=f"{key_prefix}_selectbox",
            label_visibility="collapsed",
        )
        if choice != "— select a location —":
            idx = options.index(choice) - 1
            st.session_state[ll_key]   = (photon_results[idx]["lat"], photon_results[idx]["lon"])
            st.session_state[name_key] = photon_results[idx]["display"]
            st.rerun()
    elif query and len(query.strip()) >= 3 and not photon_results:
        st.caption("⚠️ No results found. Try a different search term.")

    selected_ll   = st.session_state.get(ll_key)
    selected_name = st.session_state.get(name_key, query)

    if selected_ll:
        st.caption(f"📍 **{selected_name}**")

    return selected_ll, selected_name


# ══════════════════════════════════════════════════════════════════════════════
# CLEANEST ROUTE — TOTAL DOSE LOGIC
# ══════════════════════════════════════════════════════════════════════════════

def retag_cleanest_by_dose(routes: list) -> list:
    """
    Override the cleanest-route tag to use total_dose instead of mean_aqi.

    Why total dose matters:
      A short 400-AQI segment may contribute less total pollution than a long
      200-AQI route. Dose = mean_aqi × distance_km captures cumulative
      exposure — the actual amount of polluted air inhaled over the journey.
      Mean AQI alone ignores route length and can mislead the recommendation.

    Falls back to mean_aqi if total_dose is unavailable for all routes.
    """
    valid_dose = [
        (i, r) for i, r in enumerate(routes)
        if not math.isnan(r.get("total_dose", float("nan")))
    ]
    if valid_dose:
        cleanest_i = min(valid_dose, key=lambda x: x[1]["total_dose"])[0]
    else:
        valid_aqi = [
            (i, r) for i, r in enumerate(routes)
            if not math.isnan(r.get("mean_aqi", float("nan")))
        ]
        cleanest_i = min(valid_aqi, key=lambda x: x[1]["mean_aqi"])[0] if valid_aqi else 0

    for i, r in enumerate(routes):
        r["is_cleanest"] = (i == cleanest_i)
    return routes


# ══════════════════════════════════════════════════════════════════════════════
# SESSION STATE INITIALISATION
# ══════════════════════════════════════════════════════════════════════════════

_defaults = {
    "live_stations_df":   None,
    "road_aqi_df":        None,
    "road_geo":           None,
    "kd_tree":            None,
    "last_fetch_time":    None,
    "data_source":        "none",
    "fetch_error":        None,
    "surface_method":     None,
    "n_fallback":         0,
    "routes":             None,
    "route_origin_ll":    None,
    "route_dest_ll":      None,
    "route_origin_name":  "",
    "route_dest_name":    "",
    "selected_idx":       0,
    "selected_method":    list(INTERPOLATION_METHODS.keys())[0],
    "selected_transport": list(ORS_PROFILES.keys())[0],
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ══════════════════════════════════════════════════════════════════════════════
# DATA LOADING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def load_fallback_geo() -> pd.DataFrame:
    """Road midpoint geometry from historical CSV — cached for the session."""
    try:
        df = pd.read_csv(
            "data/road_aqi_output.csv",
            usecols=["edge_idx", "mid_lat", "mid_lon"],
        )
        return (
            df.dropna(subset=["mid_lat", "mid_lon"])
            .drop_duplicates("edge_idx")
            .reset_index(drop=True)
        )
    except FileNotFoundError:
        return pd.DataFrame(columns=["edge_idx", "mid_lat", "mid_lon"])


def load_aqi_surface(openaq_key: str, method: str) -> None:
    """
    Fetch live AQI and build road surface, caching in session_state.

    Triggers a rebuild when:
      - Cache TTL has expired (default 30 min), OR
      - The chosen interpolation method changed.

    Both conditions matter: changing the method changes how station readings
    are spatially interpolated across the road network, producing a different
    pollution surface even for the same underlying station data.
    """
    cache_fresh = not should_refresh_cache(st.session_state.get("last_fetch_time"))
    method_same = st.session_state.get("surface_method") == method

    if cache_fresh and method_same:
        return

    st.session_state["fetch_error"] = None

    # ── Attempt live fetch ─────────────────────────────────────────────────────
    if openaq_key:
        try:
            _result = fetch_live_aqi(openaq_key)
            if len(_result) == 4:
                live_df, valid_count, fetched_at, n_fallback = _result
            else:
                live_df, valid_count, fetched_at = _result
                n_fallback = 0
            if valid_count >= MIN_LIVE_STATIONS:
                road_aqi = sample_surface_at_roads(load_fallback_geo(), live_df, method)
                road_geo, kd_tree = build_road_kdtree(road_aqi)
                st.session_state.update({
                    "live_stations_df": live_df,
                    "road_aqi_df":      road_aqi,
                    "road_geo":         road_geo,
                    "kd_tree":          kd_tree,
                    "last_fetch_time":  fetched_at,
                    "data_source":      "live",
                    "n_fallback":       n_fallback,
                    "surface_method":   method,
                })
                return
            st.session_state["fetch_error"] = (
                f"Only {valid_count}/{MIN_LIVE_STATIONS} stations returned valid data. "
                "Using historical fallback."
            )
        except Exception as exc:
            st.session_state["fetch_error"] = str(exc)

    # ── Historical fallback ────────────────────────────────────────────────────
    try:
        road_aqi          = load_historical_surface(now=datetime.now())
        road_geo, kd_tree = build_road_kdtree(road_aqi)
        try:
            stn_df = (
                pd.read_csv(STATIONS_CSV_PATH,
                            usecols=["station_id", "station_name", "lat", "lon"])
                .drop_duplicates("station_id")
            )
            stn_df["aqi"] = float("nan")
        except FileNotFoundError:
            stn_df = pd.DataFrame(
                columns=["station_id", "station_name", "lat", "lon", "aqi"]
            )
        st.session_state.update({
            "live_stations_df": stn_df,
            "road_aqi_df":      road_aqi,
            "road_geo":         road_geo,
            "kd_tree":          kd_tree,
            "last_fetch_time":  datetime.now(timezone.utc),
            "data_source":      "historical",
            "surface_method":   method,
        })
    except FileNotFoundError as exc:
        st.session_state["fetch_error"] = str(exc)
        st.session_state["data_source"]  = "none"


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR — API keys + data status only
# ══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.title("🗺️ Mumbai AQI\nRoute Planner")
    st.caption("Real-time pollution-aware navigation")
    st.divider()

    st.subheader("🔑 API Keys")
    openaq_key = st.text_input(
        "OpenAQ API Key",
        type="password",
        placeholder="explore.openaq.org/register",
        help=(
            "Free key required for live AQI. "
            "Without it the app uses historical Jul–Dec 2025 data."
        ),
    )
    ors_key = st.text_input(
        "OpenRouteService Key",
        type="password",
        placeholder="openrouteservice.org/dev",
        help="Free key required for routing. 2,000 requests/day on free tier.",
    )

    st.divider()
    st.subheader("⚙️ Interpolation Method")
    st.selectbox(
        "📐 Method",
        options=list(INTERPOLATION_METHODS.keys()),
        key="selected_method",
        help=(
            "Spatial algorithm used to build the AQI surface from station data. "
            "IDW (power=2) is the recommended default. "
            "Compare all methods in the Interpolation Evaluation tab."
        ),
    )

    st.divider()
    st.subheader("🌫️ Data Status")

    data_src   = st.session_state.get("data_source", "none")
    last_fetch = st.session_state.get("last_fetch_time")

    if data_src == "live" and last_fetch:
        age_min = (
            datetime.now(timezone.utc) -
            last_fetch.replace(tzinfo=timezone.utc)
        ).seconds // 60
        st.success(f"✅ Live data — updated {age_min} min ago")
        n_fb = st.session_state.get("n_fallback", 0)
        if n_fb:
            st.warning(f"⚠️ {n_fb} station(s) used instantaneous snapshot fallback.")
        else:
            st.caption("✔️ All stations: CPCB-correct time-averages")
        st.caption("PM2.5/PM10: 24h · CO: 8h · NO₂/SO₂: 1h")
    elif data_src == "historical":
        st.warning("📂 Historical data (Jul–Dec 2025)")
    else:
        st.info("⏳ No data loaded yet")

    surf_method = st.session_state.get("surface_method")
    if surf_method:
        st.caption(f"Surface built with: **{surf_method}**")

    if st.button("🔄 Refresh AQI Data", use_container_width=True):
        st.session_state["last_fetch_time"] = None
        st.session_state["surface_method"]  = None
        st.rerun()

    st.divider()
    with st.expander("ℹ️ About"):
        st.markdown("""
**Data sources**
- Live AQI: OpenAQ v3 (CPCB/MPCB)
- Routing: OpenRouteService
- Geocoding: Photon (OpenStreetMap)
- Fallback: Pre-computed CSV

**AQI standard:** CPCB National AQI (2014)

**Cleanest route** = minimum **total dose**
(mean AQI × route distance in km).
This better represents actual pollution inhaled
than mean AQI alone.

**Coverage:** ~27 stations across Mumbai
        """)


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-LOAD AQI SURFACE (uses method from session state, set in Tab 3)
# ══════════════════════════════════════════════════════════════════════════════

load_aqi_surface(openaq_key, st.session_state["selected_method"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE HEADER
# ══════════════════════════════════════════════════════════════════════════════

st.title("🗺️ Mumbai AQI-Aware Route Planner")
st.caption(
    "Find the **fastest** and **cleanest** (lowest total pollution dose) route "
    "through Mumbai, powered by real-time CPCB air quality data."
)

if st.session_state.get("fetch_error"):
    st.warning(f"⚠️ {st.session_state['fetch_error']}")


# ══════════════════════════════════════════════════════════════════════════════
# THREE MAIN TABS
# ══════════════════════════════════════════════════════════════════════════════

tab_eval, tab_surface, tab_route, tab_lur = st.tabs([
    "📐 Interpolation Evaluation",
    "🌫️ AQI Surface",
    "🛣️ Route Planner",
    "🤖 LUR Model Comparison",
])


# ╔══════════════════════════════════════════════════════════════════════════════
# ║ TAB 1 — INTERPOLATION EVALUATION
# ╚══════════════════════════════════════════════════════════════════════════════

with tab_eval:
    st.subheader("📐 Interpolation Method Comparison — LOSO Cross-Validation")

    st.markdown("""
**Leave-One-Station-Out Cross-Validation (LOSO CV)** measures how accurately each
interpolation method reconstructs station readings it was not trained on.

**Algorithm:** For each station *i* in turn — hold it out, interpolate from the
remaining stations, predict AQI at station *i*, record the error. Repeat for
every station and aggregate across all folds.

| Metric | What it measures | Better when |
|--------|-----------------|-------------|
| **RMSE** | Root Mean Squared Error — penalises large errors more | ↓ lower |
| **MAE** | Mean Absolute Error — average deviation in AQI units | ↓ lower |
| **Bias** | Mean signed error (+= over-predicts, −= under-predicts) | → 0 |

> Results are **pre-computed** from the historical dataset (27 stations, Jul–Dec 2025, 100k hourly readings)
> via `Historical_LOSO_Evaluation.ipynb`. No live computation needed.
    """)

    # ── Load pre-computed results ──────────────────────────────────────────────
    import json as _json

    _CV_PATH = "data/loso_cv_results.json"
    try:
        with open(_CV_PATH) as _f:
            _cv_data = _json.load(_f)
        _cv_slices = _cv_data["slices"]
        _cv_meta   = _cv_data["_meta"]
    except FileNotFoundError:
        st.error(
            f"Pre-computed results file not found at `{_CV_PATH}`.  \n"
            "Please place `loso_cv_results.json` in the `data/` folder "
            "alongside `road_aqi_output.csv`."
        )
        st.stop()

    # ── Helper ─────────────────────────────────────────────────────────────────
    def _short(m: str) -> str:
        if "power=2"      in m: return "IDW (p=2)"
        if "power=1"      in m: return "IDW (p=1)"
        if "thin plate"   in m: return "RBF-TPS"
        if "multiquadric" in m: return "RBF-MQ"
        if "Kriging"      in m: return "Kriging"
        return m

    _all_methods   = list(_cv_slices["Overall"].keys())
    # Exclude RBF multiquadric — not part of this project
    _all_methods   = [m for m in _all_methods if "multiquadric" not in m.lower()]
    _valid_methods = [m for m in _all_methods if _cv_slices["Overall"][m] is not None]

    # ── Dataset provenance banner ───────────────────────────────────────────────
    st.info(
        f"📊 **Dataset:** {_cv_meta['period']} · {_cv_meta['n_stations']} stations · "
        f"100,464 hourly readings  \n"
        f"🔬 **Method:** {_cv_meta['method']}  \n"
        f"⚠️ {_cv_meta['note']}"
    )

    # ── Slice selector ─────────────────────────────────────────────────────────
    _slice_names = list(_cv_slices.keys())
    _month_slices = ["Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    _tod_slices   = ["Night (0–6h)", "Morning rush (7–10h)", "Midday (11–16h)", "Evening rush (17–22h)"]
    _other_slices = ["Overall", "Weekday", "Weekend"]

    _slice_groups = {
        "Overall & Day-type": [s for s in _slice_names if s in _other_slices],
        "Monthly":            [s for s in _slice_names if s in _month_slices],
        "Time of Day":        [s for s in _slice_names if s in _tod_slices],
    }

    _sc1, _sc2 = st.columns([1, 2])
    with _sc1:
        _group_sel = st.selectbox(
            "View by",
            options=list(_slice_groups.keys()),
            key="eval_group_sel",
        )
    with _sc2:
        _slice_opts = _slice_groups[_group_sel]
        _slice_sel  = st.selectbox(
            "Time slice",
            options=_slice_opts,
            key="eval_slice_sel",
        )

    _res = _cv_slices[_slice_sel]

    # ── Recommendation banner (always from Overall) ────────────────────────────
    _overall_valid = {m: _cv_slices["Overall"][m] for m in _valid_methods}
    _best_m  = min(_overall_valid, key=lambda m: _overall_valid[m]["rmse"])
    _best_r  = _overall_valid[_best_m]
    st.success(
        f"🏆 **Recommended method (across all time slices): {_short(_best_m)}**  \n"
        f"Overall RMSE: **{_best_r['rmse']:.1f}** AQI units · "
        f"MAE: **{_best_r['mae']:.1f}** · "
        f"Bias: **{_best_r['bias']:+.1f}**"
    )

    st.divider()

    # ── Metric cards for selected slice ───────────────────────────────────────
    st.subheader(f"📋 Results — {_slice_sel}")

    _tbl_rows = []
    for m in _all_methods:
        r = _res[m]
        if r is None:
            _tbl_rows.append({
                "Method": _short(m),
                "RMSE":   "—",
                "MAE":    "—",
                "Bias":   "—",
                "N folds": "—",
                "Note":   "Failed to converge",
            })
        else:
            _best_rmse_slice = min(
                _res[mm]["rmse"] for mm in _all_methods if _res[mm] is not None
            )
            _tbl_rows.append({
                "Method":  ("🏆 " if r["rmse"] == _best_rmse_slice else "    ") + _short(m),
                "RMSE":    f"{r['rmse']:.1f}",
                "MAE":     f"{r['mae']:.1f}",
                "Bias":    f"{r['bias']:+.1f}",
                "N folds": str(r["n"]),
                "Note":    "",
            })

    st.dataframe(
        pd.DataFrame(_tbl_rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "RMSE":  st.column_config.TextColumn("RMSE ↓",  help="Lower is better"),
            "MAE":   st.column_config.TextColumn("MAE ↓",   help="Lower is better"),
            "Bias":  st.column_config.TextColumn("Bias → 0", help="Closer to zero is better"),
        },
    )

    st.divider()

    # ── Bar charts: RMSE, MAE, Bias for selected slice ────────────────────────
    st.subheader("📊 Visual Comparison")

    _vlbls     = [_short(m) for m in _valid_methods]
    _rmse_vals = [_res[m]["rmse"] for m in _valid_methods]
    _mae_vals  = [_res[m]["mae"]  for m in _valid_methods]
    _bias_vals = [_res[m]["bias"] for m in _valid_methods]

    _best_rmse = min(_rmse_vals)
    _best_mae  = min(_mae_vals)
    _min_bias  = min(abs(v) for v in _bias_vals)

    def _bar_col(vals, best_val, invert=False):
        return ["#2ecc71" if (v == best_val) else "#1a73e8" for v in vals]

    _cc1, _cc2, _cc3 = st.columns(3)

    with _cc1:
        _fig_rmse = go.Figure(go.Bar(
            x=_vlbls, y=_rmse_vals,
            marker_color=_bar_col(_rmse_vals, _best_rmse),
            text=[f"{v:.1f}" for v in _rmse_vals],
            textposition="outside",
            hovertemplate="%{x}<br>RMSE: %{y:.1f} AQI units<extra></extra>",
        ))
        _fig_rmse.update_layout(
            title=f"RMSE — {_slice_sel}<br><sup>🟢 best · lower is better</sup>",
            yaxis_title="AQI units",
            height=340,
            margin=dict(t=60, b=20, l=50, r=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(_fig_rmse, use_container_width=True)

    with _cc2:
        _fig_mae = go.Figure(go.Bar(
            x=_vlbls, y=_mae_vals,
            marker_color=_bar_col(_mae_vals, _best_mae),
            text=[f"{v:.1f}" for v in _mae_vals],
            textposition="outside",
            hovertemplate="%{x}<br>MAE: %{y:.1f} AQI units<extra></extra>",
        ))
        _fig_mae.update_layout(
            title=f"MAE — {_slice_sel}<br><sup>🟢 best · lower is better</sup>",
            yaxis_title="AQI units",
            height=340,
            margin=dict(t=60, b=20, l=50, r=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(_fig_mae, use_container_width=True)

    with _cc3:
        _bias_colors = [
            "#2ecc71" if abs(v) == _min_bias
            else ("#ff7e00" if v > 0 else "#4e9af1")
            for v in _bias_vals
        ]
        _fig_bias = go.Figure(go.Bar(
            x=_vlbls, y=_bias_vals,
            marker_color=_bias_colors,
            text=[f"{v:+.1f}" for v in _bias_vals],
            textposition="outside",
            hovertemplate="%{x}<br>Bias: %{y:+.1f} AQI units<extra></extra>",
        ))
        _fig_bias.add_hline(
            y=0, line_dash="dash", line_color="black", line_width=1.5,
            annotation_text="Zero bias",
            annotation_position="top right",
        )
        _fig_bias.update_layout(
            title=f"Bias — {_slice_sel}<br><sup>🟢 best · orange = over-predicts · blue = under-predicts</sup>",
            yaxis_title="AQI units",
            height=340,
            margin=dict(t=60, b=20, l=50, r=10),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(_fig_bias, use_container_width=True)

    st.divider()

    # ── Cross-slice RMSE trend for currently selected method group ─────────────
    st.subheader("📈 RMSE Across All Time Slices")
    st.caption("How each method's error varies across months and time-of-day conditions.")

    _method_colors = {
        "IDW (power=2)":           "#1a73e8",
        "IDW (power=1)":           "#ff7e00",
        "RBF (thin plate spline)": "#e74c3c",
        "Kriging (spherical)":     "#2ecc71",
    }

    _fig_trend = go.Figure()
    for m in _valid_methods:
        _rmse_by_slice = [
            _cv_slices[sl][m]["rmse"] if _cv_slices[sl][m] else None
            for sl in _slice_names
        ]
        _fig_trend.add_trace(go.Scatter(
            x=_slice_names,
            y=_rmse_by_slice,
            mode="lines+markers",
            name=_short(m),
            line=dict(color=_method_colors.get(m, "#888"), width=2),
            marker=dict(size=7),
            hovertemplate=f"{_short(m)}<br>%{{x}}<br>RMSE: %{{y:.1f}}<extra></extra>",
        ))
    _fig_trend.update_layout(
        xaxis_tickangle=-35,
        yaxis_title="RMSE (AQI units)",
        height=360,
        margin=dict(t=20, b=80, l=55, r=20),
        legend=dict(orientation="h", y=1.08, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    st.plotly_chart(_fig_trend, use_container_width=True)

    st.divider()

    # ── Grouped RMSE vs MAE for Overall ───────────────────────────────────────
    st.subheader("📊 RMSE vs MAE — Overall (all months)")
    _ov_rmse = [_cv_slices["Overall"][m]["rmse"] for m in _valid_methods]
    _ov_mae  = [_cv_slices["Overall"][m]["mae"]  for m in _valid_methods]
    _fig_cmp = go.Figure()
    _fig_cmp.add_trace(go.Bar(
        name="RMSE", x=_vlbls, y=_ov_rmse,
        marker_color="#1a73e8", opacity=0.88,
        text=[f"{v:.1f}" for v in _ov_rmse], textposition="outside",
    ))
    _fig_cmp.add_trace(go.Bar(
        name="MAE", x=_vlbls, y=_ov_mae,
        marker_color="#ff7e00", opacity=0.88,
        text=[f"{v:.1f}" for v in _ov_mae], textposition="outside",
    ))
    _fig_cmp.update_layout(
        barmode="group",
        yaxis_title="AQI units (lower = better)",
        height=320,
        margin=dict(t=20, b=20, l=55, r=20),
        legend=dict(orientation="h", y=1.08, x=0),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(_fig_cmp, use_container_width=True)

    with st.expander("📝 Interpretation notes"):
        st.markdown("""
- **RMSE is higher in Jul** (~50 AQI units) because Mumbai's monsoon months have extreme
  spatial heterogeneity — stations a few km apart can differ by 80+ AQI. This is a data
  challenge, not a model failure.
- **Kriging wins most monthly slices** but IDW (p=1) is nearly as good overall (RMSE 24.3
  vs 26.5) and is far more robust — Kriging can fail on unusual station configurations.
- **Bias near zero** for Kriging in most slices — it neither systematically over- nor
  under-predicts. IDW (p=1) and IDW (p=2) both show a small positive bias (+1.7 to +7.8).
- **Recommended default: IDW (p=1)** — best overall RMSE, no convergence failures and fastest.
        """)





# ╔══════════════════════════════════════════════════════════════════════════════
# ║ TAB 2 — AQI SURFACE EXPLORER
# ╚══════════════════════════════════════════════════════════════════════════════

with tab_surface:
    st.subheader("🌫️ Mumbai AQI Pollution Surface")

    road_aqi_df = st.session_state.get("road_aqi_df")
    stations_df = st.session_state.get("live_stations_df")
    data_src    = st.session_state.get("data_source", "none")
    surf_method = st.session_state.get("surface_method", "—")

    st.info(
        f"**Current surface:** built with **{surf_method}**.  \n"
        "The pollution surface changes with your choice of interpolation method — "
        "different algorithms produce different spatial distributions from the same "
        "station data. Change the method in the **Route Planner** tab and return here "
        "to compare."
    )

    if data_src == "none":
        st.warning(
            "No AQI data loaded yet. Add your OpenAQ key in the sidebar "
            "or the app will automatically use historical data."
        )
    else:
        now = datetime.now()

        if data_src == "live":
            lf    = st.session_state.get("last_fetch_time")
            age_m = (
                (datetime.now(timezone.utc) - lf.replace(tzinfo=timezone.utc)).seconds // 60
                if lf else 0
            )
            label = (
                f"📡 Live data from OpenAQ  |  Updated {age_m} min ago  |  "
                f"Reflects conditions ~1–2 h before {now.strftime('%H:%M')}  |  "
                f"Method: {surf_method}"
            )
        else:
            from config import HISTORICAL_MONTH_MAP
            data_month = HISTORICAL_MONTH_MAP[now.month]
            month_name = pd.Timestamp(2025, data_month, 1).strftime("%B")
            label = (
                f"📂 Historical — {month_name} | Hour {now.hour:02d}:00 | "
                f"{'Weekend' if now.weekday() >= 5 else 'Weekday'} | Method: {surf_method}"
            )

        # Summary statistics
        if road_aqi_df is not None and "aqi_pred" in road_aqi_df.columns:
            valid_aqi = road_aqi_df["aqi_pred"].dropna()
            if len(valid_aqi) > 0:
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("City Mean AQI",   f"{valid_aqi.mean():.0f}")
                c2.metric("City Median AQI", f"{valid_aqi.median():.0f}")
                c3.metric("Max AQI",         f"{valid_aqi.max():.0f}")
                c4.metric("Road Segments",   f"{len(valid_aqi):,}")
                c5.metric("Method",          surf_method.split(" (")[0] if surf_method else "—")

        # AQI category breakdown
        if road_aqi_df is not None:
            road_copy = road_aqi_df.copy()
            road_copy["category"] = road_copy["aqi_pred"].apply(aqi_category)
            cat_counts = road_copy["category"].value_counts()
            cat_order  = ["Good", "Satisfactory", "Moderate", "Poor", "Very Poor", "Severe"]
            cat_counts = cat_counts.reindex([c for c in cat_order if c in cat_counts.index])

            fig_cats = go.Figure(go.Bar(
                x=cat_counts.index,
                y=cat_counts.values,
                marker_color=[AQI_PALETTE[c] for c in cat_counts.index],
                text=[f"{v:,}" for v in cat_counts.values],
                textposition="outside",
                hovertemplate="%{x}: %{y:,} segments<extra></extra>",
            ))
            fig_cats.update_layout(
                title=f"Road segments by AQI category  ({surf_method})",
                xaxis_title="",
                yaxis_title="Number of road segments",
                height=260,
                margin=dict(t=50, b=20, l=60, r=20),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_cats, use_container_width=True)

        # Surface map
        with st.spinner("Rendering AQI surface map…"):
            m2 = surface_only_map(
                road_aqi_df=road_aqi_df,
                stations_df=stations_df,
                data_label=label,
            )
        st_folium(m2, width=None, height=600, returned_objects=[])
        st.caption(label)


# ╔══════════════════════════════════════════════════════════════════════════════
# ║ TAB 3 — ROUTE PLANNER
# ╚══════════════════════════════════════════════════════════════════════════════

with tab_route:

    # ── Location Search — Photon (OSM) ───────────────────────────────────────
    st.subheader("📍 Choose Locations")
    st.caption(
        "Type at least 3 characters. Suggestions are fetched live from "
        "Photon (OpenStreetMap) and bounded to the Mumbai metropolitan area."
    )

    loc_col1, loc_col2 = st.columns(2)
    with loc_col1:
        origin_ll, origin_name = location_search_widget("📍 Origin", "origin")
    with loc_col2:
        dest_ll, dest_name = location_search_widget("🏁 Destination", "dest")

    st.divider()

    # ── Settings ─────────────────────────────────────────────────────────────
    st.subheader("⚙️ Travel Settings")

    transport_label = st.selectbox(
        "🚗 Mode of Transport",
        options=list(ORS_PROFILES.keys()),
        key="selected_transport",
        help="Determines which road network and speeds ORS uses for routing.",
    )
    method_name = st.session_state["selected_method"]
    ors_profile = ORS_PROFILES[transport_label]

    # Trigger surface rebuild if method changed
    if st.session_state.get("surface_method") != method_name:
        with st.spinner(f"Rebuilding AQI surface with **{method_name}**…"):
            load_aqi_surface(openaq_key, method_name)
        st.rerun()

    # ── Find Routes ───────────────────────────────────────────────────────────
    can_route = origin_ll is not None and dest_ll is not None and bool(ors_key)
    find_btn  = st.button(
        "🔍 Find Routes",
        type="primary",
        use_container_width=True,
        disabled=not can_route,
    )

    if not ors_key:
        st.warning("⚠️ Add your OpenRouteService API key in the sidebar to enable routing.")
    elif origin_ll is None and dest_ll is None:
        st.info("Search and select both an origin and a destination above.")
    elif origin_ll is None:
        st.info("Please select an **Origin** from the search results above.")
    elif dest_ll is None:
        st.info("Please select a **Destination** from the search results above.")

    # ── Route computation ─────────────────────────────────────────────────────
    if find_btn and can_route:

        crow_km = haversine(*origin_ll, *dest_ll) / 1000
        if crow_km < 0.05:
            st.warning("⚠️ Origin and destination appear to be the same location.")
        if crow_km > 80:
            st.warning(f"⚠️ Locations are {crow_km:.0f} km apart — are both in Mumbai?")

        # Step 1 — Fetch routes
        with st.spinner(f"🛣️ Fetching routes ({transport_label})…"):
            try:
                routes = fetch_routes(origin_ll, dest_ll, ors_profile, ors_key)
            except (ValueError, RuntimeError) as exc:
                st.error(str(exc))
                st.stop()

        # Patch zero durations/distances if routing module didn't populate them
        if routes and all(r.get("duration_s", 0) == 0 for r in routes):
            try:
                _url = (
                    f"https://api.openrouteservice.org/v2/directions/"
                    f"{ors_profile}/geojson"
                )
                _payload = {
                    "coordinates": [
                        [origin_ll[1], origin_ll[0]],
                        [dest_ll[1],   dest_ll[0]],
                    ],
                    "alternative_routes": {"share_factor": 0.6, "target_count": 3},
                    "instructions": False,
                }
                _resp = _requests.post(
                    _url, json=_payload,
                    headers={"Authorization": ors_key, "Content-Type": "application/json"},
                    timeout=15,
                )
                _resp.raise_for_status()
                for _r, _f in zip(routes, _resp.json().get("features", [])):
                    _s   = _f.get("properties", {}).get("summary", {})
                    _dur  = _s.get("duration", 0)
                    _dist = _s.get("distance", 0)
                    _r["duration_s"]   = _dur
                    _r["distance_m"]   = _dist
                    _r["duration_str"] = format_duration(_dur)
                    _dk = _dist / 1000
                    _r["distance_str"] = f"{_dk:.1f} km" if _dk >= 1 else f"{_dist:.0f} m"
            except Exception as _pe:
                st.warning(f"Could not patch route durations: {_pe}")

        if not routes:
            st.error(
                "No routes found. Verify both locations are within a routable area "
                "and your ORS API key is valid."
            )
            st.stop()

        # Step 2 — Compute pollution exposure
        road_aqi_df = st.session_state.get("road_aqi_df")
        road_geo    = st.session_state.get("road_geo")
        kd_tree     = st.session_state.get("kd_tree")

        if road_aqi_df is not None and road_geo is not None and kd_tree is not None:
            for i, route in enumerate(routes):
                with st.spinner(f"🧮 Computing exposure — route {i+1}/{len(routes)}…"):
                    routes[i] = compute_exposure(route, road_geo, kd_tree, road_aqi_df)
        else:
            st.warning("AQI surface not available — routes shown without exposure data.")

        # Step 3 — Tag fastest; then retag cleanest by total_dose
        routes = tag_fastest_and_cleanest(routes)
        routes = retag_cleanest_by_dose(routes)

        st.session_state.update({
            "routes":           routes,
            "route_origin_ll":  origin_ll,
            "route_dest_ll":    dest_ll,
            "route_origin_name": origin_name,
            "route_dest_name":   dest_name,
            "selected_idx":     next(i for i, r in enumerate(routes) if r["is_fastest"]),
        })

    # ── Results display ───────────────────────────────────────────────────────
    if st.session_state.get("routes"):
        routes      = st.session_state["routes"]
        origin_ll   = st.session_state["route_origin_ll"]
        dest_ll     = st.session_state["route_dest_ll"]
        origin_name = st.session_state["route_origin_name"]
        dest_name   = st.session_state["route_dest_name"]
        sel_idx     = st.session_state.get("selected_idx", 0)

        fastest_i  = next(i for i, r in enumerate(routes) if r["is_fastest"])
        cleanest_i = next(i for i, r in enumerate(routes) if r["is_cleanest"])
        same_route = (fastest_i == cleanest_i)

        st.divider()

        # Why total dose?
        with st.expander("ℹ️ Why is the cleanest route based on Total Dose, not Mean AQI?"):
            st.markdown("""
**Mean AQI** tells you the average pollution level, but ignores how long you spend in it.

**Total Dose = Mean AQI × Distance (km)** captures cumulative exposure —
the total amount of polluted air actually inhaled over the entire journey.

> *Example:* Route A through 300-AQI roads for 1 km (dose = 300) is **better**
> than Route B through 180-AQI roads for 5 km (dose = 900), even though
> Route B has a much lower mean AQI.

The cleanest recommendation minimises **total dose** — what you actually breathe in.
            """)

        # ── Route comparison cards ─────────────────────────────────────────────
        st.subheader("📊 Route Comparison")
        if same_route:
            st.success("✅ The fastest route is also the cleanest for current conditions.")

        def render_card(route, label, hex_color, btn_key, route_idx):
            with st.container(border=True):
                st.markdown(
                    f"<h4 style='color:{hex_color};margin:0 0 8px 0'>{label}</h4>",
                    unsafe_allow_html=True,
                )
                m1, m2, m3 = st.columns(3)
                m1.metric("⏱ Time",     route.get("duration_str", "—"))
                m2.metric("📏 Distance", route.get("distance_str", "—"))
                mean_aqi = route.get("mean_aqi", float("nan"))
                m3.metric(
                    "🌫️ Mean AQI",
                    f"{mean_aqi:.0f}" if not math.isnan(mean_aqi) else "N/A",
                )
                if not math.isnan(mean_aqi):
                    cat = aqi_category(mean_aqi)
                    st.markdown(
                        f"<div style='background:{AQI_PALETTE[cat]};padding:6px 10px;"
                        f"border-radius:6px;color:white;font-weight:bold;"
                        f"text-align:center;margin:6px 0'>"
                        f"{AQI_EMOJI[cat]} {cat}</div>",
                        unsafe_allow_html=True,
                    )
                total_dose = route.get("total_dose", float("nan"))
                if not math.isnan(total_dose):
                    st.metric(
                        "💨 Total Dose",
                        f"{total_dose / 1000:.2f} µg/m³·km",
                        help=(
                            "Mean AQI × route length in km. "
                            "This is the cleanest-route criterion — lower means "
                            "less total pollution inhaled."
                        ),
                    )
                if st.button("Select this route", key=btn_key, use_container_width=True):
                    st.session_state["selected_idx"] = route_idx
                    st.rerun()

        col_f, col_c = st.columns(2)
        with col_f:
            render_card(
                routes[fastest_i], "🔵 Fastest Route", "#1a73e8",
                "sel_fastest", fastest_i,
            )
        with col_c:
            clbl = "🟢 Cleanest Route  *(= fastest)*" if same_route else "🟢 Cleanest Route"
            render_card(
                routes[cleanest_i], clbl, "#2ecc71",
                "sel_cleanest", cleanest_i,
            )

        # ── Trade-off summary ──────────────────────────────────────────────────
        if not same_route:
            f = routes[fastest_i]
            c = routes[cleanest_i]
            f_dose = f.get("total_dose", float("nan"))
            c_dose = c.get("total_dose", float("nan"))
            if not (math.isnan(f_dose) or math.isnan(c_dose)):
                dt       = c["duration_s"] - f["duration_s"]
                dose_win = (f_dose - c_dose) / 1000
                aqi_win  = f.get("mean_aqi", 0) - c.get("mean_aqi", 0)
                dir_str  = "longer" if dt > 0 else "shorter"
                st.info(
                    f"**Trade-off:** The cleanest route reduces total dose by "
                    f"**{dose_win:.2f} µg/m³·km** and mean AQI by "
                    f"**{aqi_win:.0f} units**, but takes "
                    f"**{format_duration(abs(dt))} {dir_str}**."
                )

        # ── All routes table ───────────────────────────────────────────────────
        with st.expander("📋 All Routes"):
            tbl = []
            for i, r in enumerate(routes):
                tags = (
                    ("🔵 Fastest " if r["is_fastest"] else "") +
                    ("🟢 Cleanest" if r["is_cleanest"] else "")
                ).strip() or f"Route {i+1}"
                dose = r.get("total_dose", float("nan"))
                tbl.append({
                    "":           tags,
                    "Time":       r.get("duration_str", "—"),
                    "Distance":   r.get("distance_str", "—"),
                    "Mean AQI":   f"{r['mean_aqi']:.1f}" if not math.isnan(r.get("mean_aqi", float("nan"))) else "—",
                    "Category":   aqi_category(r.get("mean_aqi", float("nan"))),
                    "Total Dose": f"{dose/1000:.2f} µg/m³·km" if not math.isnan(dose) else "—",
                })
            st.dataframe(pd.DataFrame(tbl), use_container_width=True, hide_index=True)

        st.divider()

        # ── Map ────────────────────────────────────────────────────────────────
        st.subheader("🗺️ Route Map")
        st.caption(
            "🔵 Blue = Fastest  ·  🟢 Green = Cleanest (lowest total dose)  ·  "
            "Coloured dots = road-level AQI  ·  Bold line = selected route"
        )
        with st.spinner("Rendering map…"):
            m = route_map(
                origin_ll, dest_ll, routes,
                road_aqi_df=st.session_state.get("road_aqi_df"),
                stations_df=st.session_state.get("live_stations_df"),
                selected_idx=sel_idx,
                origin_label=origin_name,
                dest_label=dest_name,
            )
        st_folium(m, width=None, height=620, returned_objects=[])

        # ── AQI Profile Chart ──────────────────────────────────────────────────
        sel_route = routes[sel_idx]
        seg_aqis  = sel_route.get("seg_aqis", [])

        if len(seg_aqis) > 1:
            st.divider()
            r_type = (
                "Fastest" if sel_idx == fastest_i else
                "Cleanest" if sel_idx == cleanest_i else
                f"Route {sel_idx + 1}"
            )
            st.subheader(f"📈 AQI Profile — {r_type} Route")
            st.caption(
                "Each bar represents one road segment, coloured by its CPCB AQI level. "
                "The dashed line shows mean AQI for the route."
            )
            fig_prof = go.Figure()
            fig_prof.add_trace(go.Bar(
                x=list(range(len(seg_aqis))),
                y=seg_aqis,
                marker_color=[aqi_color(v) for v in seg_aqis],
                showlegend=False,
                hovertemplate="Segment %{x}<br>AQI: %{y:.0f}<extra></extra>",
            ))
            fig_prof.add_hline(
                y=sel_route["mean_aqi"],
                line_dash="dash", line_color="black", line_width=1.5,
                annotation_text=f"Mean AQI: {sel_route['mean_aqi']:.0f}",
                annotation_position="top right",
            )
            fig_prof.update_layout(
                xaxis_title="Route segment (origin → destination)",
                yaxis_title="AQI",
                yaxis_range=[0, min(500, max(seg_aqis) * 1.15)],
                height=260,
                margin=dict(t=20, b=40, l=55, r=20),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_prof, use_container_width=True)

        # ── Footer ─────────────────────────────────────────────────────────────
        now = datetime.now()
        st.divider()
        src_lbl = (
            "OpenAQ live"
            if st.session_state.get("data_source") == "live"
            else "Historical (Jul–Dec 2025)"
        )
        st.caption(
            f"📅 AQI source: **{src_lbl}**  "
            f"| Hour: **{now.hour:02d}:00**  "
            f"| Day: **{'Weekend' if now.weekday() >= 5 else 'Weekday'}**  "
            f"| Mode: **{transport_label}**  "
            f"| Method: **{method_name}**  "
            f"| Cleanest criterion: **Total Dose (mean AQI × km)**"
        )

    else:
        # ── Empty state — show station overview ────────────────────────────────
        st.divider()
        st.subheader("🗺️ Mumbai AQI Monitoring Stations")
        st.caption(
            "Search and select an origin and destination above to plan a route.  \n"
            "The map below shows all monitoring stations used to build the pollution surface."
        )
        stations_df = st.session_state.get("live_stations_df")
        show_aqi    = (
            stations_df is not None
            and not stations_df.empty
            and st.session_state.get("data_source") == "live"
        )
        with st.spinner("Loading station map…"):
            m0 = empty_map(stations_df=stations_df, show_aqi=show_aqi)
        st_folium(m0, width=None, height=520, returned_objects=[])


# ╔══════════════════════════════════════════════════════════════════════════════
# ║ TAB 4 — LUR MODEL COMPARISON
# ╚══════════════════════════════════════════════════════════════════════════════

with tab_lur:
    st.subheader("🤖 Land Use Regression (LUR) — Method Exploration & Comparison")

    st.markdown("""
We explored **Land Use Regression (LUR)** as an alternative approach to building the
Mumbai pollution surface, alongside the spatial interpolation methods shown in the
Interpolation Evaluation tab. This tab documents those findings for comparison.

**What is LUR?**
LUR predicts pollutant concentrations at arbitrary locations using spatial predictors
— distance to major roads, building coverage, green space fraction, intersection density
— combined with temporal features (hour, month, day-of-week). Models are trained on
historical station data and can in principle predict at any grid point,
not just near existing monitoring stations.

**How it differs from our interpolation approach:**
""")

    _diff_col1, _diff_col2 = st.columns(2)
    with _diff_col1:
        st.info("""
**📐 Spatial Interpolation (our main approach)**
- Estimates AQI directly from live station readings
- Weighted by distance / spatial correlation to known points
- Requires at least 2 live stations to operate
- No land-use data needed
- Generalises poorly beyond station coverage area
        """)
    with _diff_col2:
        st.info("""
**🤖 Land Use Regression (explored alternative)**
- Predicts pollutant concentrations from spatial features
- Trained on historical station readings + GIS layers
- Works without live readings at inference time
- Requires rich land-use feature data per grid cell
- Can predict at locations far from any station
        """)

    st.markdown("""
> **Decision:** We retained spatial interpolation as the primary method because it
> operates on live data without requiring GIS feature extraction, and achieves
> comparable accuracy. LUR is documented here as a methodological comparison.
    """)

    st.divider()

    # ── LUR pipeline results (hardcoded from lur_pipeline.py run) ─────────────
    st.subheader("📋 LUR Pipeline Results — Hold-out Performance")
    st.caption(
        "Results from `lur_pipeline.py` trained on the cleaned Mumbai station dataset "
        "(100,000 hourly readings, 27 stations, Jul–Dec 2025). "
        "Hold-out split is 20% of stations, grouped by station ID to prevent spatial leakage."
    )

    # Raw results from pipeline log (all models, all pollutants)
    _lur_raw = {
        "pm25": {
            "Ridge":             {"rmse": 24.90, "mae": 18.49},
            "ElasticNet":        {"rmse": 24.05, "mae": 16.97},   # ← best
            "RandomForest":      {"rmse": 26.68, "mae": 19.93},
            "GradientBoosting":  {"rmse": 37.26, "mae": 27.82},
            "XGBoost":           {"rmse": 24.13, "mae": 16.86},
        },
        "pm10": {
            "Ridge":             {"rmse": 54.27, "mae": 41.30},
            "ElasticNet":        {"rmse": 52.01, "mae": 37.55},
            "RandomForest":      {"rmse": 42.88, "mae": 33.48},
            "GradientBoosting":  {"rmse": 48.78, "mae": 38.30},
            "XGBoost":           {"rmse": 41.89, "mae": 32.45},   # ← best
        },
        "no2": {
            "Ridge":             {"rmse": 20.39, "mae": 15.48},
            "ElasticNet":        {"rmse": 18.45, "mae": 14.67},
            "RandomForest":      {"rmse": 10.45, "mae": 7.90},    # ← best
            "GradientBoosting":  {"rmse": 15.52, "mae": 11.89},
            "XGBoost":           {"rmse": 12.78, "mae": 9.97},
        },
        "so2": {
            "Ridge":             {"rmse": 11.2325, "mae": 8.4019},
            "ElasticNet":        {"rmse": 12.1758, "mae": 8.5394},
            "RandomForest":      {"rmse": 10.4528, "mae": 6.8125},  # ← best
            "GradientBoosting":  {"rmse": 14.7894, "mae": 9.6541},
            "XGBoost":           {"rmse": 13.8547, "mae": 9.5033},
        },
        "co": {
            "Ridge":             {"rmse": 0.4190, "mae": 0.3082},
            "ElasticNet":        {"rmse": 0.4186, "mae": 0.2857},   # ← best
            "RandomForest":      {"rmse": 0.4274, "mae": 0.2992},
            "GradientBoosting":  {"rmse": 0.4559, "mae": 0.3205},
            "XGBoost":           {"rmse": 0.4288, "mae": 0.2934},
        },
    }

    # Best model per pollutant
    _lur_best = {
        pol: min(models, key=lambda m: models[m]["rmse"])
        for pol, models in _lur_raw.items()
    }

    # ── Best-model summary table ───────────────────────────────────────────────
    st.markdown("#### Best Model per Pollutant")
    _best_rows = []
    _pol_labels = {"pm25": "PM₂.₅ (µg/m³)", "pm10": "PM₁₀ (µg/m³)", "no2": "NO₂ (µg/m³)", "so2": "SO₂ (µg/m³)", "co": "CO (mg/m³)"}
    for pol, best_model in _lur_best.items():
        r = _lur_raw[pol][best_model]
        _best_rows.append({
            "Pollutant":   _pol_labels[pol],
            "Best Model":  best_model,
            "RMSE ↓":      f"{r['rmse']:.2f}",
            "MAE ↓":       f"{r['mae']:.2f}",
            "Units":       "µg/m³",
        })
    st.dataframe(
        pd.DataFrame(_best_rows),
        use_container_width=True,
        hide_index=True,
    )

    st.divider()

    # ── Full model comparison charts ───────────────────────────────────────────
    st.subheader("📊 All Models — RMSE & MAE by Pollutant")
    _lur_models = ["Ridge", "ElasticNet", "RandomForest", "GradientBoosting", "XGBoost"]
    _lur_colors = {
        "Ridge":            "#aaaaaa",
        "ElasticNet":       "#4e9af1",
        "RandomForest":     "#2ecc71",
        "GradientBoosting": "#ff7e00",
        "XGBoost":          "#e74c3c",
    }

    for pol in ["pm25", "pm10", "no2", "so2", "co"]:
        st.markdown(f"**{_pol_labels[pol]}**")
        _c1, _c2 = st.columns(2)
        _rmse_vals_lur = [_lur_raw[pol][m]["rmse"] for m in _lur_models]
        _mae_vals_lur  = [_lur_raw[pol][m]["mae"]  for m in _lur_models]
        _best_lur_m    = _lur_best[pol]
        _bar_cols_lur  = [
            "#2ecc71" if m == _best_lur_m else _lur_colors[m]
            for m in _lur_models
        ]

        with _c1:
            _fig_lur_rmse = go.Figure(go.Bar(
                x=_lur_models,
                y=_rmse_vals_lur,
                marker_color=_bar_cols_lur,
                text=[f"{v:.1f}" for v in _rmse_vals_lur],
                textposition="outside",
                hovertemplate="%{x}<br>RMSE: %{y:.2f} µg/m³<extra></extra>",
            ))
            _fig_lur_rmse.update_layout(
                title=f"RMSE — {_pol_labels[pol]}<br><sup>🟢 best · lower is better</sup>",
                yaxis_title="µg/m³",
                height=300,
                margin=dict(t=55, b=20, l=50, r=10),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(_fig_lur_rmse, use_container_width=True)

        with _c2:
            _fig_lur_mae = go.Figure(go.Bar(
                x=_lur_models,
                y=_mae_vals_lur,
                marker_color=_bar_cols_lur,
                text=[f"{v:.1f}" for v in _mae_vals_lur],
                textposition="outside",
                hovertemplate="%{x}<br>MAE: %{y:.2f} µg/m³<extra></extra>",
            ))
            _fig_lur_mae.update_layout(
                title=f"MAE — {_pol_labels[pol]}<br><sup>🟢 best · lower is better</sup>",
                yaxis_title="µg/m³",
                height=300,
                margin=dict(t=55, b=20, l=50, r=10),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(_fig_lur_mae, use_container_width=True)

    st.divider()

    # ── Cross-method RMSE comparison (best LUR vs best interpolation) ──────────
    st.subheader("⚖️ LUR vs Interpolation — Best-of-Each Comparison")
    st.caption(
        "The two approaches solve related but different problems, so this is an "
        "approximate comparison. Interpolation predicts **AQI** (0–500 index); "
        "LUR predicts **raw pollutant concentrations** (µg/m³). "
        "The table below compares best-achieved RMSE for each method on their respective tasks."
    )

    _cmp_rows = [
        {
            "Method":        "Spatial Interpolation (best: IDW p=1)",
            "Task":          "AQI prediction at held-out stations",
            "Metric":        "LOSO CV RMSE",
            "Best RMSE":     "~24.3 AQI units",
            "Data needed":   "Live station readings only",
            "Can extrapolate?": "❌ No (degrades far from stations)",
        },
        {
            "Method":        "LUR — PM₂.₅ (ElasticNet)",
            "Task":          "PM₂.₅ concentration prediction",
            "Metric":        "Hold-out RMSE",
            "Best RMSE":     "24.1 µg/m³",
            "Data needed":   "GIS land-use + temporal features",
            "Can extrapolate?": "✅ Yes (any location with GIS data)",
        },
        {
            "Method":        "LUR — PM₁₀ (XGBoost)",
            "Task":          "PM₁₀ concentration prediction",
            "Metric":        "Hold-out RMSE",
            "Best RMSE":     "41.9 µg/m³",
            "Data needed":   "GIS land-use + temporal features",
            "Can extrapolate?": "✅ Yes (any location with GIS data)",
        },
        {
            "Method":        "LUR — NO₂ (RandomForest)",
            "Task":          "NO₂ concentration prediction",
            "Metric":        "Hold-out RMSE",
            "Best RMSE":     "10.5 µg/m³",
            "Data needed":   "GIS land-use + temporal features",
            "Can extrapolate?": "✅ Yes (any location with GIS data)",
        },
        {
            "Method":        "LUR — SO₂ (RandomForest)",
            "Task":          "SO₂ concentration prediction",
            "Metric":        "Hold-out RMSE",
            "Best RMSE":     "10.45 µg/m³",
            "Data needed":   "GIS land-use + temporal features",
            "Can extrapolate?": "✅ Yes (any location with GIS data)",
        },
        {
            "Method":        "LUR — CO (ElasticNet)",
            "Task":          "CO concentration prediction",
            "Metric":        "Hold-out RMSE",
            "Best RMSE":     "0.42 mg/m³",
            "Data needed":   "GIS land-use + temporal features",
            "Can extrapolate?": "✅ Yes (any location with GIS data)",
        },
    ]
    st.dataframe(
        pd.DataFrame(_cmp_rows),
        use_container_width=True,
        hide_index=True,
    )

    with st.expander("📝 Key Takeaways"):
        st.markdown("""
- **NO₂** is the best-modelled LUR target (RMSE = 10.5 µg/m³, R² = 0.39 on hold-out),
  consistent with the literature — NO₂ has strong spatial structure tied to road proximity
  that land-use predictors capture well.
- **PM₂.₅** posed the greatest challenge (near-zero R² on hold-out). PM₂.₅ in Mumbai is
  heavily influenced by mesoscale meteorology (sea breeze, monsoon) and fugitive dust
  sources that a static land-use model cannot capture without real-time weather inputs.
- **PM₁₀** sits in the middle. XGBoost achieved an R² of 0.50 on hold-out, suggesting
  proximity-to-road and building density explain roughly half of PM₁₀ variance.
- **Why we kept interpolation as the primary method:** LUR requires pre-computed GIS rasters
  (road buffers, building coverage, NDVI-derived green fraction) for every 200 m grid cell
  across Mumbai. Generating and maintaining those layers is a significant infrastructure cost
  for a demonstrator application. Interpolation achieves comparable accuracy on AQI with
  zero GIS overhead, using only the live station readings already fetched from OpenAQ.
- **Future work:** A hybrid approach — using LUR predictions as a background field and
  interpolation residuals as a correction layer — is a well-established technique in
  regulatory air quality modelling (e.g. UK DEFRA, EU EEA methods). This would combine
  LUR's spatial extrapolation ability with interpolation's ability to track live events.
        """)

