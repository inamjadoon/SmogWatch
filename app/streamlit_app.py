"""SmogWatch Punjab: Streamlit app. Reads only small files from data/processed/ (built by scripts/build_all.py)."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from smogwatch import config  # noqa: E402

P = ROOT / "data" / "processed"
# Okabe-Ito colour-blind-safe palette
C_MODIS, C_VIIRS, C_BOTH = "#E69F00", "#0072B2", "#CC79A7"
RGB = {"MODIS": [230, 159, 0], "VIIRS": [0, 114, 178], "both": [204, 121, 167]}

st.set_page_config(page_title="SmogWatch Punjab", page_icon="🔥", layout="wide")


@st.cache_data
def load():
    return {
        "recent": pd.read_parquet(P / "recent_events.parquet"),
        "daily": pd.read_parquet(P / "daily_counts.parquet"),
        "stats": pd.read_parquet(P / "season_stats.parquet"),
        "eps": pd.read_parquet(P / "eps_sensitivity.parquet"),
        "lsei": pd.read_parquet(P / "lsei.parquet"),
        "trends": json.loads((P / "trends.json").read_text()),
        "meta": json.loads((P / "meta.json").read_text()),
    }


def doy_label(doy: int) -> str:
    return (pd.Timestamp("2025-01-01") + pd.Timedelta(days=int(doy) - 1)).strftime("%d %b")


#: Smoke index bands, set from the 2012-2026 history of season days (share of days in brackets).
LSEI_BANDS = [(5, "Low", "#009E73", "little fire smoke is blowing toward Lahore (about 3 in 4 season days)"),
              (25, "Moderate", "#E69F00", "some fire smoke is reaching Lahore (about 1 in 5 days)"),
              (50, "High", "#D55E00", "a lot of fire smoke is blowing toward Lahore (about 1 in 50 days)"),
              (101, "Very high", "#8B0000", "among the smokiest days on record (about 3 in 100 days)")]


def lsei_band(v: float) -> tuple[str, str, str]:
    v = round(v)  # band must match the rounded number shown to the user
    for upper, name, color, text in LSEI_BANDS:
        if v < upper:
            return name, color, text
    return LSEI_BANDS[-1][1:]


def how_to_read(text: str) -> None:
    with st.expander("ℹ️ How to read this"):
        st.markdown(text)


D = load()
meta = D["meta"]
latest = pd.Timestamp(meta["fire_data_latest_date"])

st.title("🔥 SmogWatch Punjab")
st.markdown(
    "#### Where are farm fires burning, and is their smoke heading to Lahore?\n"
    "Every autumn, farmers in **Punjab (Pakistan and India) and Haryana** burn leftover rice straw to clear their "
    "fields. That smoke is a big part of Lahore's winter **smog**. This app uses **NASA satellites** that spot fires "
    "from space to show **where** fires are burning, **when** the burning season peaks, and **how much smoke is "
    "blowing toward Lahore**.")
st.caption(f"Satellite fire data up to **{latest:%d %B %Y}** · updated {meta['built_utc']} · "
           "all times are UTC (Pakistan time = UTC + 5 h)")
with st.expander("👋 New here? What each tab shows"):
    st.markdown(
        "- **📍 Today**: a map of fires spotted in the last 2 days, and today's smoke level for Lahore.\n"
        "- **📅 Burning Calendar**: every year since 2012 at a glance. When does burning start, peak and end? "
        "Is it getting better or worse?\n"
        "- **🛰️ Why Combine Satellites?**: why we merge two kinds of NASA satellite instead of trusting one.\n"
        "- **🌫️ Smog Link**: day by day, how much fire smoke the wind carried toward Lahore.\n"
        "- **ℹ️ About**: how it works, the data, the limits, and a glossary of terms.")

tab_today, tab_cal, tab_why, tab_smog, tab_about = st.tabs(
    ["📍 Today", "📅 Burning Calendar", "🛰️ Why Combine Satellites?", "🌫️ Smog Link", "ℹ️ About"])

# ---------------------------------------------------------------- Today
with tab_today:
    rec = D["recent"].copy()
    rec["date"] = pd.to_datetime(rec["date"])
    last48 = rec[rec["date"] > latest - pd.Timedelta(days=2)]
    today_n = int((rec["date"] == latest).sum())
    cmp_ = meta["same_date_vs_last_year"]
    lsei = D["lsei"].dropna(subset=["lsei"]).sort_values("date")
    lsei_last = lsei.iloc[-1] if len(lsei) else None

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Fires spotted on {latest:%d %b}", f"{today_n:,}",
              help="Fires seen by satellites so far today (UTC). The day may not be over yet, so this can rise.")
    c2.metric("Fires spotted, last 2 days", f"{len(last48):,}",
              help="Each fire is counted once, even if several satellites saw it.")
    c3.metric("Fires this season so far", f"{cmp_['this_year']:,}",
              f"{cmp_['change_pct']:+.0f}% vs same date last year" if cmp_["change_pct"] is not None else None,
              delta_color="inverse",
              help="Total fires since 15 September, compared with the same date last year. Green = fewer fires.")
    if lsei_last is not None:
        band, band_color, band_text = lsei_band(lsei_last["lsei"])
        c4.metric(f"Smoke toward Lahore ({pd.Timestamp(lsei_last['date']):%d %b})",
                  f"{lsei_last['lsei']:.0f} / 100 · {band}",
                  help="Lahore Smoke Exposure Index: 0 = no fire smoke blowing toward Lahore, 100 = as bad as the "
                       "worst days on record. Based on fires upwind of Lahore and the wind that day.")

    # Plain-language summary of the current situation
    summary = (f"**In short:** satellites spotted **{len(last48):,} farm fires** in the last two days. This season "
               f"has had **{cmp_['this_year']:,} fires so far**, ")
    if cmp_["change_pct"] is not None:
        summary += (f"**{abs(cmp_['change_pct']):.0f}% {'fewer' if cmp_['change_pct'] < 0 else 'more'}** than by "
                    "this date last year. ")
    if lsei_last is not None:
        summary += (f"On {pd.Timestamp(lsei_last['date']):%d %B} the smoke level for Lahore was "
                    f"<span style='color:{band_color};font-weight:700'>{band}</span>: {band_text}.")
    st.markdown(summary, unsafe_allow_html=True)
    st.caption("The smoke level uses NASA weather data, which arrives about 3 days late, so it shows the most "
               "recent day with wind data. The 'vs last year' figure uses all satellites; with only the satellites "
               "that have flown since 2012 the change is "
               f"{meta['same_date_vs_last_year_core']['change_pct']:+.0f}%.")
    st.markdown("##### 🗺️ Fires spotted in the last 2 days")

    show = last48.assign(color=last48["seen_by"].map(RGB),
                         when=last48["first_time_utc"].astype(str).str[:16])
    layers = [
        pdk.Layer("ScatterplotLayer", show, get_position="[lon, lat]", get_fill_color="color",
                  get_radius=1500, radius_min_pixels=2, opacity=0.8, pickable=True),
        pdk.Layer("ScatterplotLayer", pd.DataFrame([{"lat": config.LAHORE[0], "lon": config.LAHORE[1]}]),
                  get_position="[lon, lat]", get_fill_color=[0, 0, 0], get_line_color=[255, 255, 255],
                  stroked=True, line_width_min_pixels=2, radius_min_pixels=8),
        pdk.Layer("TextLayer", pd.DataFrame([{"lat": config.LAHORE[0] + 0.12, "lon": config.LAHORE[1],
                                              "t": "Lahore"}]),
                  get_position="[lon, lat]", get_text="t", get_size=16, get_color=[0, 0, 0]),
    ]
    st.pydeck_chart(pdk.Deck(layers=layers, map_provider="carto", map_style="light",
                             initial_view_state=pdk.ViewState(latitude=30.6, longitude=74.5, zoom=6),
                             tooltip={"text": "Seen by: {seen_by} · {country}\nFirst seen {when} UTC\n"
                                              "Fire intensity: {max_frp_mw} MW"}))
    counts = last48["seen_by"].value_counts()
    st.markdown(f"<span style='color:{C_VIIRS}'>●</span> seen by VIIRS satellites only ({counts.get('VIIRS', 0):,}) "
                f"&nbsp; <span style='color:{C_MODIS}'>●</span> seen by MODIS satellites only "
                f"({counts.get('MODIS', 0):,}) &nbsp; <span style='color:{C_BOTH}'>●</span> seen by both "
                f"({counts.get('both', 0):,}) &nbsp; ⚫ Lahore", unsafe_allow_html=True)
    how_to_read(
        "- **Each dot is one fire** spotted from space by its heat. Zoom with the mouse wheel or pinch; "
        "hover over a dot for details.\n"
        "- **Colour shows which satellites saw it.** NASA has two kinds of fire-spotting instruments, MODIS and "
        "VIIRS. If both saw the same fire, we count it **once** (purple).\n"
        "- **Fire intensity (MW)** is how much heat the fire gave off, in megawatts. Bigger numbers mean bigger or "
        "hotter fires.\n"
        "- Clusters of dots are farming areas where straw is being burned. Most are in **Indian Punjab and "
        "Haryana** (east and south-east of Lahore) and in **Pakistani Punjab** around and south of Lahore.\n"
        "- Satellites can miss fires under clouds or thick smoke, and a dot is not proof of a farm fire: a few "
        "may be other hot spots.")

# ---------------------------------------------------------------- Calendar
with tab_cal:
    st.markdown("#### When does the burning happen, and is it getting better or worse?")
    a, b, c = st.columns(3)
    REGIONS = {"Both countries": "ALL", "Pakistan only": "PK", "India only": "IN"}
    SATS = {"Fair comparison: same 3 satellites every year": "core",
            "Maximum detail: all 5 satellites (newer ones from 2018 & 2023)": "all"}
    METRICS = {"Number of fires": "n_events", "Total fire intensity (MW)": "sum_frp_mw"}
    country = REGIONS[a.selectbox("Region", list(REGIONS))]
    constellation = SATS[b.selectbox(
        "Satellites", list(SATS),
        help="Newer satellites spot extra fires, so counting with them makes recent years look worse than they "
             "are. Use 'Fair comparison' to compare years.")]
    metric = METRICS[c.selectbox("Show", list(METRICS))]
    daily = D["daily"]
    sub = daily[(daily["country"] == country) & (daily["constellation"] == constellation)]
    m = sub.pivot_table(index="year", columns="doy", values=metric, aggfunc="sum")
    crit = sub[sub["critical"]]
    fig = go.Figure(go.Heatmap(z=m.values, x=[doy_label(d) for d in m.columns], y=m.index.astype(str),
                               colorscale="YlOrRd", colorbar_title="events" if metric == "n_events" else "MW",
                               hovertemplate="%{y} %{x}: %{z:,.0f}<extra></extra>"))
    fig.add_trace(go.Scatter(x=[doy_label(d) for d in crit["doy"]], y=crit["year"].astype(str), mode="markers",
                             marker=dict(symbol="x", size=5, color="black"), name="critical day",
                             hovertemplate="critical day %{y} %{x}<extra></extra>"))
    fig.update_layout(height=520, margin=dict(l=10, r=10, t=30, b=10), xaxis_nticks=14,
                      title="Burning activity, 15 Sep – 15 Dec, by year", legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig, width="stretch")
    how_to_read(
        "- **Each row is one year**, and each small box is one day from 15 September to 15 December.\n"
        "- **Darker red = more fires that day.** Pale yellow = few or none.\n"
        "- **✕ marks a 'critical day'**: unusually many fires for that time of year compared with earlier years. "
        "These are the days to watch for smog.\n"
        "- What to notice: the dark band in **early-to-mid November** appears every year. That is the peak of "
        "rice-straw burning, just before wheat is sown.")

    stats = D["stats"]
    s = stats[(stats["country"] == country) & (stats["constellation"] == constellation)]
    s_plot = s.assign(season=np.where(s["complete"], "Full season", "Season still in progress"))
    fig2 = px.bar(s_plot, x="year", y="total_events", color="season",
                  color_discrete_map={"Full season": "#D55E00", "Season still in progress": "#BBBBBB"},
                  labels={"total_events": "Fires in the season", "season": "", "year": "Year"})
    fig2.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                       title="Total fires each burning season (15 Sep – 15 Dec)")
    st.plotly_chart(fig2, width="stretch")

    tr = D["trends"].get(country, {})
    if tr:
        te, p50 = tr["total_events"], tr["p50_doy"]
        where = {"ALL": "Across both countries", "PK": "In Pakistan", "IN": "In India"}[country]
        if te["trend"] == "no trend":
            verdict = "there is **no clear rise or fall** in the number of fires"
        else:
            word = "falling" if te["trend"] == "decreasing" else "rising"
            icon = "📉" if word == "falling" else "📈"
            verdict = (f"{icon} burning is **{word}**, by about **{abs(te['sen_slope_per_year']):,.0f} fires per "
                       "year**")
        timing = ""
        if p50["trend"] != "no trend":
            timing = (f" The season is also shifting **{'later' if p50['trend'] == 'increasing' else 'earlier'}** "
                      f"by about {abs(p50['sen_slope_per_year']):.1f} days per year.")
        conf = "This trend is statistically reliable" if te["p_value"] < 0.05 else "This is not statistically reliable"
        st.success(f"**What the data says ({tr['years'][0]}–{tr['years'][1]}):** {where}, {verdict}.{timing} "
                   f"_{conf} (Mann-Kendall test, p = {te['p_value']:.3f}; uses the same 3 satellites every year "
                   "for a fair comparison)._")
    st.markdown("**Season details by year**")
    table = s[["year", "start", "peak_date", "end", "peak_count", "total_events", "complete"]].rename(columns={
        "year": "Year", "start": "Burning started", "peak_date": "Worst day", "end": "Burning ended",
        "peak_count": "Fires on worst day", "total_events": "Total fires", "complete": "Full season?"})
    st.dataframe(table, hide_index=True, width="stretch")
    st.caption("'Started' and 'ended' are the first and last days of a run of 3+ days with notable burning.")

# ---------------------------------------------------------------- Why harmonize
with tab_why:
    st.markdown("#### Why we combine two kinds of NASA satellite instead of trusting one")
    st.markdown(
        "NASA spots fires from space with two instruments: **MODIS** (older, since 2000, sees in blocks about 1 km "
        "wide) and **VIIRS** (newer, since 2012, sharper at about 375 m, better at small farm fires). They fly over "
        "at **different times of day**, and a straw fire can burn out in under an hour, so **each one catches fires "
        "the other never sees**. They also often see the *same* fire, which would be counted twice if we just added "
        "them up. SmogWatch merges them into **one clean list where each fire counts once**.")
    d = daily[(daily["country"] == "ALL") & (daily["constellation"] == "all")]
    by = d.groupby("year")[["n_viirs_only", "n_modis_only", "n_both", "n_events", "n_detections"]].sum()
    long = by[["n_viirs_only", "n_modis_only", "n_both"]].reset_index().melt("year", var_name="seen_by",
                                                                              value_name="events")
    long["seen_by"] = long["seen_by"].map({"n_viirs_only": "VIIRS only", "n_modis_only": "MODIS only",
                                           "n_both": "both"})
    fig = px.bar(long, x="year", y="events", color="seen_by",
                 color_discrete_map={"VIIRS only": C_VIIRS, "MODIS only": C_MODIS, "both": C_BOTH})
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10),
                      title="Fires each year, by which satellite saw them", legend_title_text="Seen by")
    st.plotly_chart(fig, width="stretch")
    tot = by.sum()
    st.markdown(
        f"Across 2012–2026, **{tot['n_modis_only'] / tot['n_events']:.0%} of fire events were seen only by MODIS** "
        f"and {tot['n_viirs_only'] / tot['n_events']:.0%} only by VIIRS; just {tot['n_both'] / tot['n_events']:.0%} "
        "by both. The sensors pass at different times (Aqua MODIS now crosses about 2 h after VIIRS because its orbit "
        "has drifted), and short stubble fires often burn out in between. Using either sensor alone misses fires.")

    c1, c2 = st.columns(2)
    raw = by[["n_detections", "n_events"]].reset_index().melt("year", var_name="kind", value_name="count")
    raw["kind"] = raw["kind"].map({"n_detections": "raw detections", "n_events": "merged fire events"})
    f2 = px.line(raw, x="year", y="count", color="kind", markers=True,
                 color_discrete_map={"raw detections": "#999999", "merged fire events": "#D55E00"})
    f2.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10),
                     title="Raw counts overstate fires (same fire seen many times)")
    c1.plotly_chart(f2, width="stretch")
    c1.caption(f"{tot['n_detections']:,.0f} raw detections → {tot['n_events']:,.0f} fire events "
               f"({1 - tot['n_events'] / tot['n_detections']:.0%} were duplicates).")
    c2.markdown("**De-duplication radius sensitivity**")
    c2.dataframe(D["eps"].rename(columns={"eps_km": "radius (km)", "n_events": "fire events",
                                          "n_MODIS": "MODIS only", "n_VIIRS": "VIIRS only", "n_both": "both"}),
                 hide_index=True, width="stretch")
    c2.caption("We use 1 km: MODIS pixels are about 1 km and VIIRS pixels 375 m, so the same fire lands at slightly "
               "different positions. Larger radii start merging neighbouring fields.")
    how_to_read(
        "- **Top chart:** each bar is one year's fires, split by who saw them. The orange slice is fires that "
        "**only MODIS** caught. Without combining, those fires would simply be missing.\n"
        "- **Left chart:** the grey line counts every satellite sighting; the red line counts real fires after "
        "removing repeat sightings of the same fire. The gap shows how much raw counts overstate burning.\n"
        "- **Table:** how the result changes if we treat sightings closer than 0.5, 1 or 2 km as the same fire. "
        "We use 1 km, about the size of one MODIS block.")
    st.info("**A trap we avoided:** newer satellites joined in 2018 (NOAA-20) and 2023 (NOAA-21). More satellites "
            "spot more fires, so raw totals jumped 63% in 2018 even though farmers didn't burn 63% more. To compare "
            "years fairly we use only the 3 satellites flying since 2012. Done that way, burning is **falling in "
            "India** and **rising in Pakistan**. With all satellites mixed in, that signal disappears.")

# ---------------------------------------------------------------- Smog link
with tab_smog:
    st.markdown("#### How much fire smoke did the wind carry toward Lahore each day?")
    st.markdown(
        "A fire only matters for Lahore if **the wind is blowing its smoke toward the city**. The **Lahore Smoke "
        "Exposure Index** combines where the fires are, how big they are, how far away they are, and which way the "
        "wind blew that day into **one score from 0 to 100**.")
    st.markdown(" &nbsp; ".join(f"<span style='background:{col};color:white;padding:2px 8px;border-radius:4px'>"
                                f"{name}</span> {lo}–{min(up, 100) if up <= 100 else 100}"
                                for (up, name, col, _), lo in zip(LSEI_BANDS, [0, 5, 25, 50])),
                unsafe_allow_html=True)
    L = D["lsei"].dropna(subset=["lsei"]).copy()
    L["date"] = pd.to_datetime(L["date"])
    years = sorted(L["date"].dt.year.unique(), reverse=True)
    yr = st.selectbox("Burning season (year)", years)
    Ly = L[L["date"].dt.year == yr].copy()
    Ly["band"] = Ly["lsei"].map(lambda v: lsei_band(v)[0])
    fig = go.Figure()
    fig.add_trace(go.Bar(x=Ly["date"], y=Ly["lsei"], name="Smoke toward Lahore (0–100)",
                         marker_color=[lsei_band(v)[1] for v in Ly["lsei"]], customdata=Ly["band"],
                         hovertemplate="%{x|%d %b}: smoke score %{y:.0f} (%{customdata})<extra></extra>"))
    fig.add_trace(go.Scatter(x=Ly["date"], y=Ly["lsei_upwind_events"], name="Fires upwind of Lahore", yaxis="y2",
                             line=dict(color="#555555"),
                             hovertemplate="%{x|%d %b}: %{y:,.0f} fires upwind<extra></extra>"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=-0.15),
                      yaxis=dict(title="Smoke score (0–100)", range=[0, 100]),
                      yaxis2=dict(title="Fires upwind", overlaying="y", side="right", showgrid=False))
    st.plotly_chart(fig, width="stretch")
    worst = Ly.nlargest(3, "lsei")
    if len(worst) and worst["lsei"].iloc[0] > 0:
        st.markdown("**Smokiest days this season:** " + ", ".join(
            f"{d:%d %b} ({v:.0f}, {lsei_band(v)[0]})" for d, v in zip(worst["date"], worst["lsei"])))
    how_to_read(
        "- **Each bar is one day.** Taller and redder bars mean more fire smoke was blowing toward Lahore.\n"
        "- **The grey line** is the number of fires that were *upwind* (in the direction the wind came from).\n"
        "- A day can have many fires but a **low score** if the wind blew the smoke away from Lahore. That is the "
        "point of this index: not every fire affects the city.\n"
        "- Calm, humid days score higher, because smoke lingers instead of blowing away.")
    pk = Ly["lsei_pk_share"].dropna()
    w = (Ly["lsei_upwind_frp_mw"] * Ly["lsei_pk_share"]).sum() / Ly["lsei_upwind_frp_mw"].sum() \
        if Ly["lsei_upwind_frp_mw"].sum() else np.nan
    if not np.isnan(w):
        st.markdown(f"🇵🇰 🇮🇳 **Where the smoke came from in {yr}:** of the fire heat upwind of Lahore, "
                    f"**{w:.0%} was from fires in Pakistan** and **{1 - w:.0%} from fires in India** "
                    f"({len(pk)} days had upwind fires). Smog is a shared, cross-border problem.")
    with st.expander("🔬 How the score is calculated (technical)"):
        st.markdown(
            "For each day: take fires within **300 km** of Lahore that lie within **±45°** of the direction the "
            "wind blows *from* (NASA POWER 10 m wind at Lahore). Add up each fire's intensity divided by its "
            "distance (closer fires count more). Multiply by 1.5 on calm days (wind < 2 m/s) and by a further "
            "0.25 on humid days (> 70%). Scale to 0–100 so that 100 equals the smokiest 1% of all past season days.")
    st.caption("⚠️ This is an estimate of smoke **heading toward** Lahore, not a measurement of the air people "
               "breathe (PM2.5) and not an official air-quality forecast. Traffic, industry and weather also cause "
               "smog. Values from 2018 and 2023 onward include newer satellites, so they are not directly "
               "comparable with earlier years.")

# ---------------------------------------------------------------- About
with tab_about:
    st.markdown(f"""
### The problem
Every October–December, farmers across Punjab (Pakistan and India) and Haryana burn rice stubble to clear fields
for wheat. With cold, still air, that smoke helps push Lahore's air quality to some of the worst levels in the world.

### Method
1. **Fetch:** NASA FIRMS MODIS (Terra, Aqua) and VIIRS 375 m (Suomi-NPP, NOAA-20, NOAA-21) detections for
   69.0–77.8° E, 27.5–33.0° N; archive (SP) where available, near-real-time (NRT) for recent weeks.
2. **Harmonize:** one schema; confidence mapped to low/nominal/high (MODIS < 30 / 30–79 / ≥ 80; VIIRS l/n/h);
   low confidence and known static/industrial sources removed.
3. **Merge:** per day, detections within 1 km (DBSCAN, haversine) become one fire event, tagged MODIS / VIIRS / both.
4. **Calendar:** daily counts, season start/peak/end, critical days, Mann-Kendall trends on a constant satellite set.
5. **Smoke index:** upwind fire power ÷ distance with NASA POWER wind, scaled 0–100.

**Scale:** {meta['n_detections_total']:,} detections (confidence ≥ nominal) → {meta['n_events_total']:,} fire events,
main seasons 2012–2026.

### NASA data
- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/): MODIS Collection 6.1 and VIIRS 375 m active fire products (LANCE / EOSDIS)
- [NASA POWER](https://power.larc.nasa.gov/): daily wind, humidity, temperature, precipitation (Lahore)
- Country boundaries: Natural Earth (public domain)

### Limitations
- Detections are **thermal anomalies**, not confirmed crop fires or burned area; some may be other heat sources.
- Clouds, thick smoke and overpass timing hide fires; all sensors are polar-orbiting (a few looks per day).
- Suomi-NPP near-real-time data is unavailable after 2 Nov 2026, so the 2026 core series is incomplete.
- The smoke index is a transport proxy, not a PM2.5 forecast; it uses wind at a single point (Lahore).
- The 7-day fire forecast is on the roadmap and not included in this version.

### Glossary
| Term | Plain meaning |
|---|---|
| **Fire / fire event** | One burning spot seen from space by its heat. If several satellites saw the same spot on the same day, it counts once. |
| **Detection** | A single satellite sighting. One fire can have several detections. |
| **MODIS** | NASA's older fire-spotting instrument (on the Terra and Aqua satellites). Sees in ~1 km blocks. |
| **VIIRS** | Newer, sharper instrument (on Suomi-NPP, NOAA-20, NOAA-21). Sees in ~375 m blocks; better at small fires. |
| **Fire intensity (FRP, MW)** | Fire Radiative Power: the heat a fire gives off, in megawatts. Bigger = bigger or hotter fire. |
| **Upwind** | In the direction the wind is coming from. Smoke from upwind fires is carried toward the city. |
| **Smoke score (LSEI)** | Lahore Smoke Exposure Index, 0–100: how much fire smoke the wind was carrying toward Lahore. |
| **Critical day** | A day with unusually many fires for that time of year, compared with earlier years. |
| **Stubble burning** | Burning leftover rice straw after harvest to clear fields quickly for wheat. |
| **Trend (Mann-Kendall)** | A standard statistical test of whether values rise or fall over the years; p < 0.05 means the trend is reliable. |

### Team & tools
SmogWatch, NASA Space Apps Challenge 2026. AI tools (Claude) were used to help write code; outputs were reviewed by
the team. Every number in this app is produced by code in the
[GitHub repository](https://github.com/inamjadoon/SmogWatch). *Not endorsed by NASA.*
""")
