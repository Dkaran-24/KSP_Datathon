import zcatalyst_sdk
import json
from collections import defaultdict
from datetime import datetime
from ml_core import zscore_anomaly

"""
detectAnomalies
-----------------
Flags districts whose latest-month case count deviates sharply from THAT
DISTRICT'S OWN historical norm, using an adaptive z-score (mean/std learned
per district) instead of one global fixed multiplier applied to everyone.
A quiet district and a busy district get judged against their own baseline,
not the same yardstick.

ENHANCED — Behavioral Context for Linking Complex Cases:
Each anomaly record now carries additive behavioral context so analysts
can link anomalies to underlying crime patterns, not just see a number:

  * peak_shift            — the time-of-day shift (morning/afternoon/evening/
                            night) with the most incidents in this district,
                            enabling spatiotemporal resource deployment.
  * dominant_crime_head    — the crime category with the highest count in this
                            district, revealing what is actually driving the
                            anomaly.
  * dominant_crime_share   — that category's share of the district's caseload.
  * repeat_offender_hotspot — boolean flag: true if this district has an
                            above-median count of cases with multiple accused
                            (a proxy for organized/repeat-offender activity).
  * accused_per_case       — average accused persons per case in this district
                            (higher = more organized, multi-actor offending).
  * behavioral_note        — human-readable summary tying the anomaly to its
                            behavioral context.

Response shape is strictly additive — existing anomalies/allDistricts fields
preserved.
"""

def normalize_id(val):
    if val is None:
        return ""
    try:
        return str(int(float(val)))
    except ValueError:
        return str(val).strip()


KSP_CRIME_HEADS = {
    "1": "Crimes Against Body",
    "2": "Theft & Burglary",
    "3": "Crimes Against Women/Children",
    "4": "Cybercrime",
    "5": "White Collar Crime",
    "6": "Narcotics & Drugs",
    "7": "Arson & Property Damage",
    "8": "Economic Offenses",
    "9": "Other IPC Crimes",
    "10": "Special & Local Laws (SLL)"
}


def behavioral_note(peak_shift_val, dominant_crime, dominant_share, repeat_flag, accused_per_case):
    parts = []
    shift_map = {
        "morning": "morning hours (06:00–12:00)",
        "afternoon": "afternoon hours (12:00–18:00)",
        "evening": "evening hours (18:00–22:00)",
        "night": "night hours (22:00–06:00)"
    }
    parts.append(f"Incidents cluster predominantly in the {shift_map.get(peak_shift_val, peak_shift_val)}")
    if dominant_crime:
        parts.append(f"with {dominant_crime} as the dominant typology ({dominant_share:.0%} of local caseload)")
    if repeat_flag:
        parts.append("and a repeat-offender / organized-activity signature (above-median multi-accused cases)")
    if accused_per_case > 1.3:
        parts.append(f"and an elevated accused-per-case ratio of {accused_per_case:.1f} indicating multi-actor offending")
    parts.append("— this behavioral pattern should guide targeted interdiction and linking of complex cases.")
    return " ".join(parts)


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    crime_head_id = basicio.get_argument('crime_head_id')
    try:
        z_threshold = float(basicio.get_argument('threshold') or 2.0)
    except ValueError:
        z_threshold = 2.0

    try:
        # 1. District ID -> Name mapping
        district_rows = zcql.execute_query(
            "SELECT DistrictID, DistrictName FROM District WHERE StateID = 1"
        )
        district_names = {}
        for r in district_rows:
            d = r.get("District", r)
            district_names[str(d["DistrictID"])] = d["DistrictName"]

        # 2. Police station (Unit.ROWID) -> District ID mapping
        unit_rows = zcql.execute_query(
            "SELECT ROWID, UnitID, DistrictID FROM Unit WHERE StateID = 1"
        )
        unit_to_district = {}
        for r in unit_rows:
            u = r.get("Unit", r)
            district_id = str(u["DistrictID"])
            rowid = normalize_id(u.get("ROWID"))
            unit_id = normalize_id(u.get("UnitID"))
            if rowid:
                unit_to_district[rowid] = district_id
            if unit_id and unit_id not in unit_to_district:
                unit_to_district[unit_id] = district_id

        # 3. Crime head names
        head_rows = zcql.execute_query("SELECT CrimeHeadID, CrimeGroupName FROM CrimeHead")
        head_map = {
            normalize_id(r['CrimeHead']['CrimeHeadID']): r['CrimeHead']['CrimeGroupName']
            for r in head_rows
        }
        for hid, name in KSP_CRIME_HEADS.items():
            head_map.setdefault(hid, name)

        # 4. Page through CaseMaster to build monthly counts per district
        #    PLUS behavioral context: shifts, crime heads, case IDs for accused counts
        case_counts_by_month_and_district = defaultdict(lambda: defaultdict(int))
        shifts_by_district = defaultdict(lambda: {"morning": 0, "afternoon": 0, "evening": 0, "night": 0})
        crime_head_by_district = defaultdict(lambda: defaultdict(int))
        district_case_ids = defaultdict(list)  # did -> [case_id, ...] for accused counting

        last_rowid = 0
        pages = 0
        PAGE_SIZE = 300
        MAX_PAGES = 10000

        while pages < MAX_PAGES:
            if crime_head_id:
                query = (
                    "SELECT ROWID, PoliceStationID, CrimeRegisteredDate, CaseMasterID, CrimeMajorHeadID "
                    f"FROM CaseMaster WHERE ROWID > {last_rowid} AND CrimeMajorHeadID = '{crime_head_id}' "
                    f"ORDER BY ROWID LIMIT {PAGE_SIZE}"
                )
            else:
                query = (
                    "SELECT ROWID, PoliceStationID, CrimeRegisteredDate, CaseMasterID, CrimeMajorHeadID "
                    f"FROM CaseMaster WHERE ROWID > {last_rowid} "
                    f"ORDER BY ROWID LIMIT {PAGE_SIZE}"
                )

            rows = zcql.execute_query(query)
            if not rows:
                break

            for r in rows:
                c = r.get("CaseMaster", r)
                date_str = c.get("CrimeRegisteredDate")
                ps_id = normalize_id(c.get("PoliceStationID"))
                did = unit_to_district.get(ps_id)
                head_id = normalize_id(c.get("CrimeMajorHeadID"))

                if not did or not date_str:
                    continue

                date_part = date_str.split(' ')[0]
                parts = date_part.split('-')
                if len(parts) < 2:
                    continue
                month_key = f"{parts[0]}-{parts[1]}"
                case_counts_by_month_and_district[month_key][did] += 1

                # Shift bucketing
                hour = 12
                time_parts = date_str.split(' ')
                if len(time_parts) >= 2 and ':' in time_parts[1]:
                    try:
                        hour = int(time_parts[1].split(':')[0])
                    except ValueError:
                        pass
                if 6 <= hour < 12:
                    shifts_by_district[did]["morning"] += 1
                elif 12 <= hour < 18:
                    shifts_by_district[did]["afternoon"] += 1
                elif 18 <= hour < 22:
                    shifts_by_district[did]["evening"] += 1
                else:
                    shifts_by_district[did]["night"] += 1

                # Crime head distribution
                if head_id:
                    crime_head_by_district[did][head_id] += 1

                # Collect case IDs for accused counting
                case_id = c.get("CaseMasterID")
                if case_id:
                    district_case_ids[did].append(case_id)

            last_rowid = int(rows[-1].get("CaseMaster", rows[-1])["ROWID"])
            pages += 1
            if len(rows) < PAGE_SIZE:
                break

        # 5. Fetch accused counts per case (batched) to compute accused_per_case
        accused_counts_by_case = {}
        for did, case_ids in district_case_ids.items():
            chunk_size = 200
            for i in range(0, len(case_ids), chunk_size):
                chunk = case_ids[i:i + chunk_size]
                ids_str = ",".join(str(int(cid)) for cid in chunk if cid)
                if not ids_str:
                    continue
                try:
                    arows = zcql.execute_query(
                        f"SELECT CaseMasterID, COUNT(*) as cnt FROM Accused "
                        f"WHERE CaseMasterID IN ({ids_str}) GROUP BY CaseMasterID"
                    )
                    for ar in arows:
                        rec = ar.get("Accused", ar)
                        cid = rec.get("CaseMasterID")
                        cnt = rec.get("cnt", 0)
                        if cid and cnt:
                            accused_counts_by_case[cid] = int(cnt)
                except Exception:
                    pass

        # 6. Process monthly counts to detect anomalies
        sorted_months = sorted(case_counts_by_month_and_district.keys())

        if not sorted_months:
            basicio.write(json.dumps({
                "success": True,
                "data": {
                    "anomalies": [],
                    "allDistricts": [],
                    "generatedAt": datetime.now().isoformat() + "Z",
                    "model": "adaptive_zscore"
                }
            }))
            return

        latest_month = sorted_months[-1]
        historical_months = sorted_months[-13:-1] if len(sorted_months) > 1 else []

        anomalies = []
        all_districts = []

        # Pre-compute accused_per_case and multi-accused-case counts per district
        district_accused_per_case = {}
        district_multi_accused_cases = {}
        for did in district_names:
            cids = district_case_ids.get(did, [])
            total_accused = sum(accused_counts_by_case.get(cid, 0) for cid in cids)
            multi_count = sum(1 for cid in cids if accused_counts_by_case.get(cid, 0) >= 2)
            district_accused_per_case[did] = (total_accused / len(cids)) if cids else 0.0
            district_multi_accused_cases[did] = multi_count

        # Median multi-accused case count for repeat-offender hotspot flag
        multi_counts = list(district_multi_accused_cases.values())
        if multi_counts:
            sorted_mc = sorted(multi_counts)
            median_multi = sorted_mc[len(sorted_mc) // 2]
        else:
            median_multi = 0

        # --- Round-2: heinous case ratio per district via ActSectionAssociation ---
        # Graceful: if table absent, skip.
        HEINOUS_ACTS = {"POCSO", "NDPS"}
        HEINOUS_SECTIONS = {
            "302", "307", "376", "376A", "376B", "376C", "376D", "376E",
            "395", "396", "397", "364A", "326A", "326B", "120B", "121", "121A",
        }
        district_heinous = {}  # did -> {"heinous": n, "total": n, "ratio": float}
        try:
            for did, cids in district_case_ids.items():
                hein_set = set()
                for i in range(0, len(cids), 200):
                    chunk = cids[i:i + 200]
                    ids_str = ",".join(f"'{str(c)}'" for c in chunk if c)
                    if not ids_str:
                        continue
                    arows = zcql.execute_query(
                        f"SELECT CaseMasterID, ActID, SectionID FROM ActSectionAssociation "
                        f"WHERE CaseMasterID IN ({ids_str})"
                    )
                    for ar in arows:
                        a = ar.get("ActSectionAssociation", ar)
                        act_id = str(a.get("ActID", "")).strip()
                        sec_raw = a.get("SectionID")
                        sec_str = ""
                        if sec_raw is not None:
                            try:
                                sec_str = str(int(float(sec_raw)))
                            except (ValueError, TypeError):
                                sec_str = str(sec_raw).strip()
                        if act_id in HEINOUS_ACTS or sec_str in HEINOUS_SECTIONS:
                            hein_set.add(str(a.get("CaseMasterID", "")).strip())
                total_c = len(cids) or 1
                hein_n = len(hein_set)
                district_heinous[did] = {
                    "heinous": hein_n,
                    "total": len(cids),
                    "ratio": round(hein_n / total_c, 3),
                }
        except Exception as hein_err:
            print(f"Heinous classification skipped: {hein_err}")
            district_heinous = {}

        for did, name in district_names.items():
            latest_count = case_counts_by_month_and_district[latest_month].get(did, 0)
            hist_counts = [
                float(case_counts_by_month_and_district[m].get(did, 0))
                for m in historical_months
            ]

            stats = zscore_anomaly(hist_counts, float(latest_count))
            z = stats["z_score"]

            if z >= 3.0 and latest_count >= 3:
                severity = "Critical"
            elif z >= 2.5 and latest_count >= 3:
                severity = "High"
            elif z >= z_threshold and latest_count >= 3:
                severity = "Elevated"
            else:
                severity = "Low"

            # --- Behavioral context ---
            shift_map = shifts_by_district.get(did, {"morning": 0, "afternoon": 0, "evening": 0, "night": 0})
            peak_shift_val = max(shift_map.items(), key=lambda kv: kv[1])[0] if any(shift_map.values()) else "night"

            head_dist = crime_head_by_district.get(did, {})
            if head_dist:
                dominant_head_id = max(head_dist.items(), key=lambda kv: kv[1])[0]
                dominant_head_count = head_dist[dominant_head_id]
                total_head_count = sum(head_dist.values()) or 1
                dominant_crime = head_map.get(dominant_head_id, KSP_CRIME_HEADS.get(dominant_head_id, "Unknown"))
                dominant_share = dominant_head_count / total_head_count
            else:
                dominant_crime = "Unknown"
                dominant_share = 0.0

            apc = district_accused_per_case.get(did, 0.0)
            multi_accused = district_multi_accused_cases.get(did, 0)
            repeat_flag = multi_accused > median_multi and multi_accused > 0

            record = {
                "districtId": did,
                "districtName": name,
                "latestMonth": latest_month,
                "latestCount": latest_count,
                "historicalAvg": round(stats["mean"], 2),
                "historicalStd": round(stats["std"], 2),
                "zScore": z,
                "severity": severity,
                "monthsOfHistory": len(historical_months),
                # --- Enhanced behavioral context ---
                "peak_shift": peak_shift_val,
                "shifts": shift_map,
                "dominant_crime_head": dominant_crime,
                "dominant_crime_share": round(dominant_share, 3),
                "repeat_offender_hotspot": repeat_flag,
                "multi_accused_cases": multi_accused,
                "accused_per_case": round(apc, 2),
                "behavioral_note": behavioral_note(peak_shift_val, dominant_crime, dominant_share, repeat_flag, apc)
            }

            # --- Round-2: heinous case ratio ---
            hein_info = district_heinous.get(did)
            if hein_info:
                record["heinous_cases"] = hein_info["heinous"]
                record["heinous_case_ratio"] = hein_info["ratio"]
                record["heinous_percent"] = round(hein_info["ratio"] * 100, 1)

            all_districts.append(record)
            if severity in ("Critical", "High", "Elevated") and z >= z_threshold:
                anomalies.append(record)

        all_districts.sort(key=lambda x: x["zScore"], reverse=True)
        anomalies.sort(key=lambda x: x["zScore"], reverse=True)

        basicio.write(json.dumps({
            "success": True,
            "data": {
                "anomalies": anomalies,
                "allDistricts": all_districts,
                "generatedAt": datetime.now().isoformat() + "Z",
                "model": "adaptive_zscore",
                "threshold": z_threshold
            }
        }))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()
