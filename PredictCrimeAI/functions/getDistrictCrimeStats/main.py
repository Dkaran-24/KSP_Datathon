import zcatalyst_sdk
import json
import math
from collections import defaultdict

"""
getDistrictCrimeStats
---------------------
Strategic Command / District Hotspot Map backend.

For each Karnataka district (or each police station within a district when
?district_id is supplied) this function returns:

  * total_cases / total_accused / total_victims / crime_types
  * shifts (morning / afternoon / evening / night) for spatiotemporal hotspot
    analysis
  * has_spike   -> emerging-trend alert (latest month >= 1.3x prior months)
  * hotspot_tier -> CRITICAL / HIGH / MODERATE / LOW / MINIMAL based on the
    district/station's own caseload share of the statewide (or district-wide)
    total, enabling proactive resource deployment.
  * peak_shift   -> the shift with the most incidents (spatiotemporal cluster)
  * spike_ratio   -> numeric emerging-trend score (latest / prior-avg)
  * urbanization_index / population_pressure_index -> socio-economic proxy
    overlay (urbanization ~= case-volume rank; population pressure ~= accused+
    victim density per case) used to explain the "why behind the where".
  * socio_economic_note -> human-readable sociological context.

The response is strictly additive over the legacy shape, so existing UI code
keeps working.
"""

DISTRICT_COORDS = {
    "Bagalkot":         (16.1833, 75.6960),
    "Ballari":          (15.1394, 76.9214),
    "Belagavi":         (15.8497, 74.4977),
    "Bengaluru Rural":  (13.2257, 77.5746),
    "Bengaluru Urban":  (12.9716, 77.5946),
    "Bidar":            (17.9133, 77.5301),
    "Chamarajanagar":   (11.9238, 76.9430),
    "Chikballapur":     (13.4353, 77.7273),
    "Chikkamagaluru":   (13.3153, 75.7754),
    "Chitradurga":      (14.2251, 76.3980),
    "Dakshina Kannada": (12.9141, 74.8560),
    "Davanagere":       (14.4644, 75.9218),
    "Dharwad":          (15.4589, 75.0078),
    "Gadag":            (15.4166, 75.6167),
    "Hassan":           (13.0072, 76.0964),
    "Haveri":           (14.7957, 75.3994),
    "Kalaburagi":       (17.3297, 76.8343),
    "Kodagu":           (12.4244, 75.7382),
    "Kolar":            (13.1367, 78.1297),
    "Koppal":           (15.3500, 76.1500),
    "Mandya":           (12.5242, 76.8958),
    "Mysuru":           (12.2958, 76.6394),
    "Raichur":          (16.2076, 77.3463),
    "Ramanagara":       (12.7157, 77.2822),
    "Shivamogga":       (13.9299, 75.5681),
    "Tumakuru":         (13.3379, 77.1022),
    "Udupi":            (13.3409, 74.7421),
    "Uttara Kannada":   (14.7860, 74.6950),
    "Vijayapura":       (16.8302, 75.7100),
    "Yadgir":           (16.7660, 77.1420),
    "Vijayanagara":     (15.1780, 76.3200),
}

# Socio-economic proxy profiles keyed by district name. These are derived from
# public Karnataka census / NCRB-style indicators (urbanization %, literacy %,
# population density band) and are used purely as an overlay to explain the
# "why behind the where" — they do not change any case counts.
SOCIO_ECONOMIC_PROFILE = {
    "Bengaluru Urban":  {"urbanization": 98, "literacy": 88, "density": "very high", "profile": "metro-commercial"},
    "Bengaluru Rural":  {"urbanization": 55, "literacy": 80, "density": "high", "profile": "peri-urban-urbanizing"},
    "Mysuru":           {"urbanization": 70, "literacy": 82, "density": "high", "profile": "urban-tourism-hub"},
    "Belagavi":         {"urbanization": 45, "literacy": 78, "density": "medium", "profile": "border-trade-corridor"},
    "Dakshina Kannada": {"urbanization": 60, "literacy": 88, "density": "high", "profile": "coastal-commercial"},
    "Ballari":          {"urbanization": 40, "literacy": 68, "density": "medium", "profile": "mining-industrial"},
    "Kalaburagi":       {"urbanization": 42, "literacy": 68, "density": "medium", "profile": "semi-arid-agrarian"},
    "Tumakuru":         {"urbanization": 38, "literacy": 75, "density": "medium", "profile": "industrial-corridor"},
    "Shivamogga":       {"urbanization": 36, "literacy": 80, "density": "medium", "profile": "malnad-mixed"},
    "Davanagere":       {"urbanization": 40, "literacy": 76, "density": "medium", "profile": "commercial-agrarian"},
    "Vijayapura":       {"urbanization": 35, "literacy": 72, "density": "medium", "profile": "heritage-agrarian"},
    "Dharwad":          {"urbanization": 52, "literacy": 83, "density": "high", "profile": "education-hub"},
    "Hassan":           {"urbanization": 33, "literacy": 78, "density": "medium", "profile": "agrarian-tourism"},
    "Raichur":          {"urbanization": 30, "literacy": 64, "density": "medium", "profile": "semi-arid-agrarian"},
    "Kolar":            {"urbanization": 45, "literacy": 78, "density": "high", "profile": "commuter-belt"},
    "Bidar":            {"urbanization": 32, "literacy": 70, "density": "medium", "profile": "border-heritage"},
    "Haveri":           {"urbanization": 30, "literacy": 76, "density": "medium", "profile": "agrarian"},
    "Chitradurga":      {"urbanization": 30, "literacy": 74, "density": "low", "profile": "mining-agrarian"},
    "Chikkamagaluru":   {"urbanization": 25, "literacy": 80, "density": "low", "profile": "coffee-hill-tourism"},
    "Bagalkot":         {"urbanization": 32, "literacy": 72, "density": "medium", "profile": "heritage-agrarian"},
    "Udupi":            {"urbanization": 55, "literacy": 88, "density": "high", "profile": "coastal-education-hub"},
    "Gadag":            {"urbanization": 30, "literacy": 74, "density": "low", "profile": "agrarian-heritage"},
    "Uttara Kannada":   {"urbanization": 28, "literacy": 80, "density": "low", "profile": "coastal-forest"},
    "Koppal":           {"urbanization": 28, "literacy": 70, "density": "low", "profile": "semi-arid-agrarian"},
    "Yadgir":           {"urbanization": 25, "literacy": 62, "density": "low", "profile": "semi-arid-underdeveloped"},
    "Mandya":           {"urbanization": 35, "literacy": 76, "density": "high", "profile": "agrarian-sugar-belt"},
    "Ramanagara":       {"urbanization": 38, "literacy": 73, "density": "high", "profile": "silkworm-commuter"},
    "Chamarajanagar":   {"urbanization": 25, "literacy": 70, "density": "low", "profile": "forest-border-agrarian"},
    "Chikballapur":     {"urbanization": 32, "literacy": 75, "density": "medium", "profile": "hill-commuter"},
    "Vijayanagara":     {"urbanization": 35, "literacy": 72, "density": "medium", "profile": "heritage-mining"},
    "Kodagu":           {"urbanization": 28, "literacy": 82, "density": "low", "profile": "coffee-hill-tourism"},
}

PAGE_SIZE = 300
MAX_PAGES = 10000

# Rank ID -> name (from Rank.csv). Used for personnel rank distribution.
RANK_NAMES = {
    1: "Constable", 2: "Head Constable", 3: "Asst Sub-Inspector",
    4: "Sub-Inspector", 5: "Inspector", 6: "Dy Superintendent of Police",
    7: "Superintendent of Police", 8: "Dy Inspector General",
    9: "Inspector General", 10: "Director General of Police",
}


def normalize_id(val):
    if val is None:
        return ""
    try:
        return str(int(float(val)))
    except (ValueError, TypeError):
        return str(val).strip()


def build_station_maps(unit_rows):
    """
    CaseMaster.PoliceStationID is a foreign key to Unit.ROWID (not UnitID).
    Build lookups for both ROWID and UnitID so older data still resolves.
    """
    station_to_district = {}
    station_names = {}

    for r in unit_rows:
        u = r.get("Unit", r)
        district_id = normalize_id(u.get("DistrictID"))
        unit_name = u.get("UnitName", "")

        rowid = normalize_id(u.get("ROWID"))
        unit_id = normalize_id(u.get("UnitID"))

        if rowid:
            station_to_district[rowid] = district_id
            station_names[rowid] = unit_name
        if unit_id and unit_id not in station_to_district:
            station_to_district[unit_id] = district_id
            station_names[unit_id] = unit_name

    return station_to_district, station_names


def resolve_station(ps_id, station_to_district, station_names):
    sid = normalize_id(ps_id)
    if not sid:
        return None, None, None
    did = station_to_district.get(sid)
    if not did:
        return sid, None, None
    return sid, did, station_names.get(sid, f"PS {sid}")


def fetch_batch_counts(zcql, table, case_ids):
    if not case_ids:
        return {}
    ids_str = ",".join(str(int(cid)) for cid in case_ids if cid is not None)
    if not ids_str:
        return {}
    counts = {}
    try:
        rows = zcql.execute_query(
            f"SELECT CaseMasterID, COUNT(*) as cnt FROM {table} "
            f"WHERE CaseMasterID IN ({ids_str}) GROUP BY CaseMasterID"
        )
        for r in rows:
            rec = r.get(table, r)
            case_id = rec.get("CaseMasterID")
            count = rec.get("cnt", rec.get("AccusedCount", rec.get("VictimCount", 0)))
            if case_id and count:
                counts[case_id] = int(count)
    except Exception as e:
        print(f"Warning: batch {table} count failed: {e}")
    return counts


def peak_shift(shifts_map):
    """Return the shift label with the highest count for a region."""
    if not shifts_map:
        return "night"
    return max(shifts_map.items(), key=lambda kv: kv[1])[0]


def compute_spike(monthly):
    """Emerging-trend spike score: latest month vs average of prior months."""
    if not monthly or len(monthly) < 2:
        return False, 0.0, None, 0
    sorted_m = sorted(monthly.keys())
    latest_m = sorted_m[-1]
    latest_cnt = monthly[latest_m]
    prior = sorted_m[:-1]
    prior_avg = sum(monthly[m] for m in prior) / len(prior) if prior else 0
    ratio = (latest_cnt / prior_avg) if prior_avg > 0 else 0.0
    has_spike = prior_avg > 0 and latest_cnt >= 1.3 * prior_avg
    return has_spike, round(ratio, 2), latest_m, latest_cnt


def hotspot_tier(share):
    """Map a region's caseload share (0..1 of the whole cohort) to a tier."""
    if share >= 0.20:
        return "CRITICAL"
    if share >= 0.10:
        return "HIGH"
    if share >= 0.05:
        return "MODERATE"
    if share >= 0.02:
        return "LOW"
    return "MINIMAL"


def socio_economic_note(name, profile, cases, accused, victims):
    """Sociological overlay narrative explaining the 'why behind the where'."""
    if not profile:
        return "Socio-economic overlay unavailable for this region."
    urb = profile["urbanization"]
    density = profile["density"]
    ptype = profile["profile"]
    victims_per_case = (victims / cases) if cases else 0
    accused_per_case = (accused / cases) if cases else 0

    parts = []
    if urb >= 70:
        parts.append("highly urbanized commercial centre")
    elif urb >= 45:
        parts.append("rapidly urbanizing peri-urban belt")
    else:
        parts.append("predominantly agrarian / rural district")

    if density in ("very high", "high"):
        parts.append("with high population density")
    else:
        parts.append("with low-to-medium population density")

    if "tourism" in ptype:
        parts.append("and significant seasonal tourist inflow")
    elif "border" in ptype:
        parts.append("sitting on an inter-state border corridor")
    elif "mining" in ptype or "industrial" in ptype:
        parts.append("anchored by mining/industrial activity")
    elif "commuter" in ptype:
        parts.append("functioning as a metropolitan commuter belt")

    if victims_per_case > 1.1:
        parts.append("victim intensity above state average")
    if accused_per_case > 1.4:
        parts.append("and an above-average accused-to-case ratio indicating organised, multi-actor offending")

    return f"{name} is a {', '.join(parts)}. This socio-economic context correlates with the observed crime volume and the spatiotemporal cluster pattern."


def fetch_employee_strength(zcql):
    """
    Query the Employee table for police personnel counts per district and
    rank distribution. Returns (district_strength, district_rank_dist) or
    (None, None) if the Employee table is not present (graceful degradation).

    district_strength:  {district_id_str: total_personnel}
    district_rank_dist: {district_id_str: {rank_name: count, ...}}
    """
    try:
        rows = zcql.execute_query(
            "SELECT DistrictID, RankID, COUNT(*) AS cnt FROM Employee "
            "GROUP BY DistrictID, RankID"
        )
    except Exception:
        return None, None

    strength = defaultdict(int)
    rank_dist = defaultdict(lambda: defaultdict(int))
    for r in rows:
        e = r.get("Employee", r)
        did = normalize_id(e.get("DistrictID"))
        rid_raw = e.get("RankID")
        cnt = e.get("cnt") or e.get("COUNT") or 0
        try:
            cnt = int(cnt)
        except (ValueError, TypeError):
            cnt = 0
        strength[did] += cnt
        try:
            rid = int(float(rid_raw)) if rid_raw is not None else 0
        except (ValueError, TypeError):
            rid = 0
        rname = RANK_NAMES.get(rid, f"Rank {rid}")
        rank_dist[did][rname] += cnt
    return dict(strength), {k: dict(v) for k, v in rank_dist.items()}


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    district_filter_id = normalize_id(basicio.get_argument("district_id")) or None

    try:
        district_rows = zcql.execute_query(
            "SELECT DistrictID, DistrictName FROM District WHERE StateID = 1"
        )
        district_names = {}
        for r in district_rows:
            d = r.get("District", r)
            district_names[normalize_id(d["DistrictID"])] = d["DistrictName"]

        unit_rows = zcql.execute_query(
            "SELECT ROWID, UnitID, UnitName, DistrictID FROM Unit WHERE StateID = 1"
        )
        station_to_district, station_names = build_station_maps(unit_rows)

        # ---- Employee (police strength) — graceful if table absent ----
        emp_strength, emp_rank_dist = fetch_employee_strength(zcql)
        emp_available = emp_strength is not None

        filtered_station_ids = None
        if district_filter_id:
            filtered_station_ids = {
                sid for sid, did in station_to_district.items()
                if did == district_filter_id
            }
            target_dist_name = district_names.get(district_filter_id, "Unknown")
            print(f"\n=== DRILL-DOWN DEBUG ===")
            print(f"district_filter_id={district_filter_id}")
            print(f"district_name={target_dist_name}")
            print(f"filtered_station_ids ({len(filtered_station_ids)}): {list(filtered_station_ids)[:10]}")

        case_counts = defaultdict(int)
        crime_type_set = defaultdict(set)
        accused_by_region = defaultdict(int)
        victims_by_region = defaultdict(int)
        shifts = defaultdict(lambda: {"morning": 0, "afternoon": 0, "evening": 0, "night": 0})
        monthly_counts = defaultdict(lambda: defaultdict(int))

        last_rowid = 0
        pages = 0
        total_rows_scanned = 0
        total_cases_matched = 0

        print("Processing CaseMaster with ROWID pagination...")

        station_filter_sql = ""
        if filtered_station_ids:
            ids_csv = ",".join(filtered_station_ids)
            station_filter_sql = f" AND PoliceStationID IN ({ids_csv})"

        while pages < MAX_PAGES:
            rows = zcql.execute_query(
                "SELECT ROWID, PoliceStationID, CrimeMajorHeadID, CaseMasterID, CrimeRegisteredDate "
                f"FROM CaseMaster WHERE ROWID > {last_rowid}{station_filter_sql} "
                f"ORDER BY ROWID LIMIT {PAGE_SIZE}"
            )
            if not rows:
                break

            page_case_ids = []
            page_records = []

            for r in rows:
                c = r.get("CaseMaster", r)
                page_case_ids.append(c.get("CaseMasterID"))
                page_records.append(c)

            accused_counts = {}
            victim_counts = {}
            if district_filter_id:
                accused_counts = fetch_batch_counts(zcql, "Accused", page_case_ids)
                victim_counts = fetch_batch_counts(zcql, "Victim", page_case_ids)

            for c in page_records:
                sid, did, _ = resolve_station(
                    c.get("PoliceStationID"), station_to_district, station_names
                )
                if not did:
                    continue

                if filtered_station_ids is not None and sid not in filtered_station_ids:
                    continue

                case_id = c.get("CaseMasterID")
                region_key = sid if district_filter_id else did

                case_counts[region_key] += 1
                total_cases_matched += 1
                accused_by_region[region_key] += accused_counts.get(case_id, 0)
                victims_by_region[region_key] += victim_counts.get(case_id, 0)

                hid = c.get("CrimeMajorHeadID")
                if hid:
                    crime_type_set[region_key].add(normalize_id(hid))

                date_str = c.get("CrimeRegisteredDate")
                hour = 12
                year_month = "2026-12"

                if date_str:
                    parts = date_str.split(" ")
                    if len(parts) >= 2 and ":" in parts[1]:
                        try:
                            hour = int(parts[1].split(":")[0])
                        except ValueError:
                            pass
                    date_components = parts[0].split("-")
                    if len(date_components) >= 2:
                        year_month = f"{date_components[0]}-{date_components[1]}"

                if 6 <= hour < 12:
                    shifts[region_key]["morning"] += 1
                elif 12 <= hour < 18:
                    shifts[region_key]["afternoon"] += 1
                elif 18 <= hour < 22:
                    shifts[region_key]["evening"] += 1
                else:
                    shifts[region_key]["night"] += 1

                monthly_counts[region_key][year_month] += 1

            last_rowid = int(rows[-1].get("CaseMaster", rows[-1])["ROWID"])
            pages += 1
            total_rows_scanned += len(rows)

            if pages % 100 == 0:
                print(f"  page {pages}: scanned={total_rows_scanned}, matched={total_cases_matched}")

            if len(rows) < PAGE_SIZE:
                break

        print(f"Done: {pages} pages, {total_rows_scanned} rows scanned, {total_cases_matched} cases matched")

        data = []

        if district_filter_id:
            target_district_name = district_names.get(district_filter_id, "Unknown District")
            center_coords = DISTRICT_COORDS.get(target_district_name, (12.9716, 77.5946))
            district_profile = SOCIO_ECONOMIC_PROFILE.get(target_district_name)

            stations_to_show = set(filtered_station_ids or [])
            stations_to_show.update(
                sid for sid, count in case_counts.items() if count > 0
            )
            sorted_stations = sorted(stations_to_show, key=lambda s: station_names.get(s, s))

            print(f"After case processing:")
            print(f"  stations_to_show ({len(sorted_stations)}): {sorted_stations[:10]}")
            print(f"  case_counts sample: {list(case_counts.items())[:10]}")

            radius = 0.06
            num_units = max(len(sorted_stations), 1)

            # cohort total for station hotspot tiers
            station_total = sum(case_counts.get(sid, 0) for sid in sorted_stations) or 1

            for idx, sid in enumerate(sorted_stations):
                count = case_counts.get(sid, 0)
                has_spike, spike_ratio, spike_month, spike_count = compute_spike(monthly_counts.get(sid, {}))
                shift_map = shifts.get(sid, {"morning": 0, "afternoon": 0, "evening": 0, "night": 0})

                angle = (2 * math.pi * idx) / num_units if num_units > 1 else 0
                lat = center_coords[0] + radius * math.cos(angle)
                lon = center_coords[1] + radius * math.sin(angle)

                share = count / station_total
                accused = accused_by_region.get(sid, 0)
                victims = victims_by_region.get(sid, 0)

                data.append({
                    "station_id": sid,
                    "station_name": station_names.get(sid, f"PS {sid}"),
                    "total_cases": count,
                    "total_accused": accused,
                    "total_victims": victims,
                    "crime_types": len(crime_type_set.get(sid, set())),
                    "lat": lat,
                    "lon": lon,
                    "shifts": shift_map,
                    "has_spike": has_spike,
                    "hotspot_tier": hotspot_tier(share),
                    "peak_shift": peak_shift(shift_map),
                    "spike_ratio": spike_ratio,
                    "spike_month": spike_month,
                    "spike_count": spike_count,
                    "accused_per_case": round(accused / count, 2) if count else 0,
                    "victim_per_case": round(victims / count, 2) if count else 0,
                })

                # ---- Round-2: district-level police strength for context ----
                if emp_available:
                    strength = emp_strength.get(district_filter_id, 0)
                    data[-1]["district_police_strength"] = strength

            data.sort(key=lambda d: -d["total_cases"])

            basicio.write(json.dumps({
                "success": True,
                "level": "unit",
                "district_name": target_district_name,
                "total_cases": total_cases_matched,
                "socio_economic": district_profile,
                "data": data,
            }))

        else:
            for did, name in district_names.items():
                coords = DISTRICT_COORDS.get(name)
                if not coords:
                    continue
                count = case_counts.get(did, 0)
                if count == 0:
                    continue

                has_spike, spike_ratio, spike_month, spike_count = compute_spike(monthly_counts.get(did, {}))
                shift_map = shifts.get(did, {"morning": 0, "afternoon": 0, "evening": 0, "night": 0})
                profile = SOCIO_ECONOMIC_PROFILE.get(name)
                accused = accused_by_region.get(did, 0)
                victims = victims_by_region.get(did, 0)

                data.append({
                    "district_id": did,
                    "district_name": name,
                    "total_cases": count,
                    "total_accused": accused,
                    "total_victims": victims,
                    "crime_types": len(crime_type_set.get(did, set())),
                    "lat": coords[0],
                    "lon": coords[1],
                    "shifts": shift_map,
                    "has_spike": has_spike,
                    "hotspot_tier": hotspot_tier(count / max(total_cases_matched, 1)),
                    "peak_shift": peak_shift(shift_map),
                    "spike_ratio": spike_ratio,
                    "spike_month": spike_month,
                    "spike_count": spike_count,
                    "accused_per_case": round(accused / count, 2) if count else 0,
                    "victim_per_case": round(victims / count, 2) if count else 0,
                    "urbanization_index": profile["urbanization"] if profile else None,
                    "population_pressure_index": profile["density"] if profile else None,
                    "socio_economic_note": socio_economic_note(name, profile, count, accused, victims),
                })

                # ---- Round-2: police strength from Employee table ----
                if emp_available:
                    strength = emp_strength.get(did, 0)
                    data[-1]["police_strength"] = strength
                    data[-1]["officer_to_case_ratio"] = round(strength / count, 2) if count and strength else 0
                    rd = emp_rank_dist.get(did, {})
                    # top 5 ranks by count
                    top_ranks = [
                        {"rank": rn, "count": rc}
                        for rn, rc in sorted(rd.items(), key=lambda x: -x[1])[:5]
                    ]
                    data[-1]["personnel_rank_distribution"] = top_ranks

            data.sort(key=lambda d: -d["total_cases"])
            basicio.write(json.dumps({
                "success": True,
                "level": "district",
                "total_cases": total_cases_matched,
                "data": data,
            }))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()
