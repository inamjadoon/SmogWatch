# SmogWatch Punjab — Product Requirements Document (PRD)

| | |
|---|---|
| **Event** | NASA Space Apps Challenge 2026 (14–15 Nov 2026) |
| **Challenge** | Harmonization of MODIS and VIIRS Hot Spots |
| **Team** | SmogWatch Punjab |
| **Status** | In development |
| **Repo** | github.com/inamjadoon/SmogWatch |

---

## 1. Summary
SmogWatch Punjab merges NASA's MODIS and VIIRS satellite fire detections into one clean, de-duplicated
record of crop-residue burning across Punjab (Pakistan and India) and Haryana. It turns that record into a
**burning activity calendar**, a **7-day burning forecast**, and a **Lahore Smoke Exposure Index** that uses
wind data to show which fires are sending smoke toward Lahore.

## 2. Problem
- Every Oct–Dec, farmers burn rice stubble to clear fields quickly for wheat sowing.
- Smoke, combined with urban emissions, cold air and low wind, makes Lahore's air among the worst in the
  world: school closures, hospital load, flight delays.
- NASA has two fire-detecting instruments, but they **disagree**:
  - **MODIS** (Terra/Aqua, since 2000): 1 km pixels, misses small farm fires, 0–100 confidence.
  - **VIIRS** (Suomi NPP, NOAA-20, NOAA-21, since 2012): 375 m pixels, catches small fires, low/nominal/high confidence.
  - The same fire can appear 2–3 times, or in only one sensor. Raw counts are misleading.
- Existing fire maps show *where* fires are, not *which fires affect a given city today*.

## 3. Goals
| # | Goal | Measure of success |
|---|---|---|
| G1 | One harmonized fire record from MODIS + VIIRS | Unified schema; duplicate detections merged into fire events; % seen by one vs both sensors reported |
| G2 | Burning activity calendar | Day-of-year × year heatmap 2012→2026; season start/peak/end per year; critical days flagged |
| G3 | Link fires to Lahore smog | Daily Lahore Smoke Exposure Index (0–100) using wind direction/speed |
| G4 | Early warning | 7-day forecast of fire activity that beats seasonal-naive and persistence baselines (lower MAE) |
| G5 | Usable by non-experts | Public web app, loads in < 5 s, plain-language explanations |

### Non-goals
- Not an official air-quality forecast (no PM2.5 dispersion modelling).
- Not burned-area mapping (detections ≠ burned area).
- No real-time alerts/SMS in the hackathon version.

## 4. Users
| User | Need | What they use |
|---|---|---|
| **EPA Punjab / PDMA officials** | Where and when to enforce burning bans; when to issue smog advisories | Map, calendar, forecast |
| **Schools / district admin** | Decide closures in advance | "Smoke risk this week" card |
| **Lahore residents** | Know bad-air days ahead | Today page, risk card |
| **Researchers / journalists** | Trends: is burning rising, shifting, India vs Pakistan | Calendar, trend stats, data download |
| **NASA judges** | Proof the harmonization is correct and useful | "Why harmonize?" page, method, limits |

## 5. User stories
1. As an official, I can see all fires from the last 48 h on one map without double-counting.
2. As an official, I can see whether this season is worse than past seasons on the same date.
3. As a resident, I can see a simple risk score for Lahore for the next 7 days.
4. As a researcher, I can filter the calendar by country and year and see when the season peaks.
5. As a judge, I can see how many fires only one sensor caught, which proves why merging matters.
6. As anyone, I can read which NASA data was used and the tool's limitations.

## 6. Features and priority
| ID | Feature | Priority |
|---|---|---|
| F1 | Data fetch (FIRMS MODIS + VIIRS, 2012→now) | **P0 must** |
| F2 | Harmonization: unified schema, confidence mapping, cross-sensor de-duplication | **P0 must** |
| F3 | Today map (last 48 h fire events, colored by sensor) | **P0 must** |
| F4 | Burning calendar heatmap + season stats + critical-day flags | **P0 must** |
| F5 | "Why harmonize?" comparison (MODIS-only / VIIRS-only / both) | **P0 must** |
| F6 | Lahore Smoke Exposure Index (NASA POWER wind) | **P0 must** |
| F7 | 7-day fire-activity forecast + baselines + metrics | P1 should |
| F8 | Trend stats (Mann-Kendall, Sen's slope; peak-date shift) | P1 should |
| F9 | Pakistan vs India split | P1 should |
| F10 | Aerosol (AOD) validation vs smoke index | P2 nice |
| F11 | CSV download of harmonized events | P2 nice |
| F12 | Urdu language toggle | P2 nice |

**Minimum shippable product:** F1–F6.

## 7. Scope (geography and time)
- Bounding box (W, S, E, N): **69.0, 27.5, 77.8, 33.0**
- Lahore: **31.5497 N, 74.3436 E**
- Main season: **15 Sep – 15 Dec** (rice stubble); secondary: **1 Apr – 31 May** (wheat stubble)
- Years: **2012–2026** (VIIRS era); MODIS back to 2000 as a stretch

## 8. Judging alignment
| Criterion | How we score |
|---|---|
| **Impact** | Smog affects ~13 M people in Lahore; tool targets real agencies and decisions |
| **Creativity** | Smoke Exposure Index: "which fires hit Lahore", not just "where are fires" |
| **Validity** | Transparent harmonization rules, unit tests, baselines for the model, honest limitations, optional AOD check |
| **Relevance** | Directly delivers what the challenge asks: merged MODIS + VIIRS record and a burning activity calendar for early warning |
| **Presentation** | Clean app, one-story demo video, clear charts |

## 9. Risks and mitigations
| Risk | Mitigation |
|---|---|
| FIRMS API limits / slow history download | Cache chunks; fetch current season first; use yearly country CSVs for history |
| Deadline | Ship F1–F6 first; submit early; keep improving the live app |
| Model underperforms | Report honestly vs baselines; the calendar + index still deliver value |
| Clouds/haze hide fires | State it as a limitation; show sensor-agreement stats |
| Aerosol data hard to access | P2 only; skip if blocked |

## 10. Deliverables
- Live Streamlit app (public URL)
- GitHub repo with README, docs, tests
- Demo video / slides per the 2026 Project Submission Guide
- Space Apps project page (summary, NASA data list, links)

## 11. Milestones
See `../PLAN.md` for the hour-by-hour build plan (M0 setup → M8 submission).
