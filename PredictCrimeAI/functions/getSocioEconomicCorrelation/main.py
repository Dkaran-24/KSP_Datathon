import zcatalyst_sdk
import json
import math
from collections import defaultdict

"""
getSocioEconomicCorrelation
----------------------------
Sociological & AI-Driven Predictive Dashboards backend.

This function overlays crime data with socio-economic proxy indicators
(urbanization %, literacy %, population density band) and correlates them
with crime volume and crime-type composition across all Karnataka districts,
returning "why behind the where" insights.

Outputs:
  * indicator_correlations — Pearson correlation between each socio-economic
    indicator and total crime volume, with human-readable interpretation.
  * district_socio_economic_overlay — per-district overlay showing crime
    volume, urbanization, literacy, density, and a sociological profile tag.
  * urbanization_gradient — districts grouped into urbanization tiers
    (metro / urbanizing / transitional / agrarian) with aggregate crime stats,
    showing how crime composition shifts across the urbanization spectrum.
  * crime_type_by_urbanization — which crime types dominate at each
    urbanization tier (e.g. cybercrime in metros, agrarian offenses in
    rural districts).
  * literacy_crime_type_correlations — how literacy correlates with specific
    crime types (e.g. negative correlation with violent crime, positive with
    cybercrime reporting).
  * key_insights — machine-generated bullet-point insights synthesizing the
    correlations into actionable sociological narratives.

Query params: none (analyzes all districts statewide)

Response shape:
{
  "success": true,
  "indicator_correlations": [...],
  "district_socio_economic_overlay": [...],
  "urbanization_gradient": [...],
  "crime_type_by_urbanization": {...},
  "literacy_crime_type_correlations": [...],
  "key_insights": [...],
  "districtsAnalyzed": 31
}
"""

# Socio-economic profiles keyed by district name (derived from public
# Karnataka census / NCRB-style indicators)
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

DENSITY_SCORE = {"very high": 4, "high": 3, "medium": 2, "low": 1}

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


def normalize_id(val):
    if val is None:
        return ""
    try:
        return str(int(float(val)))
    except ValueError:
        return str(val).strip()


def pearson_correlation(x, y):
    n = len(x)
    if n < 3:
        return 0.0
    mean_x, mean_y = sum(x) / n, sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if std_x < 1e-9 or std_y < 1e-9:
        return 0.0
    return cov / (std_x * std_y)


def urbanization_tier(urb):
    if urb >= 70:
        return "metro"
    if urb >= 45:
        return "urbanizing"
    if urb >= 32:
        return "transitional"
    return "agrarian"


def interpret_correlation(indicator, r):
    direction = "positively" if r > 0 else "negligibly" if abs(r) < 0.15 else "negatively"
    strength = "strongly" if abs(r) >= 0.7 else "moderately" if abs(r) >= 0.5 else "weakly" if abs(r) >= 0.3 else "negligibly"

    interpretations = {
        "urbanization": {
            "positive": f"Urbanization is {strength} {direction} correlated with crime volume — more urbanized districts experience higher crime, consistent with the urbanization-crime nexus theory: population density, economic disparity, and anonymity-of-the-city drive offense rates.",
            "negative": f"Urbanization is {strength} {direction} correlated with crime — counterintuitively, more urbanized districts show lower crime, possibly due to better policing infrastructure and CCTV surveillance in metros.",
            "neutral": "Urbanization shows no significant correlation with overall crime volume in this dataset."
        },
        "literacy": {
            "positive": f"Literacy is {strength} {direction} correlated with crime — higher literacy districts report more crime, likely due to better reporting rates and legal awareness rather than higher incidence.",
            "negative": f"Literacy is {strength} {direction} correlated with crime — higher literacy districts tend to have lower crime, supporting the education-as-social-deterrent hypothesis.",
            "neutral": "Literacy shows no significant correlation with overall crime volume."
        },
        "population_density": {
            "positive": f"Population density is {strength} {direction} correlated with crime — denser districts experience more crime, reflecting the anonymity-of-the-city effect and higher interaction frequency.",
            "negative": f"Population density is {strength} {direction} correlated with crime — less expected, possibly reflecting urban-suburban migration of crime.",
            "neutral": "Population density shows no significant correlation with crime volume."
        }
    }

    if abs(r) < 0.15:
        return interpretations.get(indicator, {}).get("neutral", f"{indicator} shows no significant correlation.")
    key = "positive" if r > 0 else "negative"
    return interpretations.get(indicator, {}).get(key, f"{indicator} is {strength} {direction} correlated with crime volume.")


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    try:
        # 1. District ID -> Name mapping
        district_rows = zcql.execute_query(
            "SELECT DistrictID, DistrictName FROM District WHERE StateID = 1"
        )
        district_names = {
            normalize_id(r['District']['DistrictID']): r['District']['DistrictName']
            for r in district_rows
        }

        # 2. Police station (Unit.ROWID) -> District ID mapping
        unit_rows = zcql.execute_query(
            "SELECT ROWID, UnitID, DistrictID FROM Unit WHERE StateID = 1"
        )
        unit_to_district = {}
        for r in unit_rows:
            u = r.get('Unit', r)
            district_id = normalize_id(u['DistrictID'])
            rowid = normalize_id(u.get('ROWID'))
            unit_id = normalize_id(u.get('UnitID'))
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

        # 4. Page through CaseMaster building district x crime-type counts
        district_crime_counts = defaultdict(lambda: defaultdict(int))
        last_rowid = 0
        pages = 0
        PAGE_SIZE = 300
        MAX_PAGES = 10000

        while pages < MAX_PAGES:
            query = (
                "SELECT ROWID, PoliceStationID, CrimeMajorHeadID FROM CaseMaster "
                f"WHERE ROWID > {last_rowid} ORDER BY ROWID LIMIT {PAGE_SIZE}"
            )
            rows = zcql.execute_query(query)
            if not rows:
                break

            for r in rows:
                c = r.get("CaseMaster", r)
                ps_id = normalize_id(c.get("PoliceStationID"))
                head_id = normalize_id(c.get("CrimeMajorHeadID"))
                did = unit_to_district.get(ps_id)
                if not did or not head_id:
                    continue
                district_crime_counts[did][head_id] += 1

            last_rowid = int(rows[-1].get("CaseMaster", rows[-1])["ROWID"])
            pages += 1
            if len(rows) < PAGE_SIZE:
                break

        if not district_crime_counts:
            basicio.write(json.dumps({
                "success": True,
                "indicator_correlations": [],
                "district_socio_economic_overlay": [],
                "urbanization_gradient": [],
                "crime_type_by_urbanization": {},
                "literacy_crime_type_correlations": [],
                "key_insights": ["No crime data available for socio-economic correlation analysis."],
                "districtsAnalyzed": 0
            }))
            return

        active_district_ids = sorted(
            d for d in district_crime_counts.keys() if d in district_names
        )
        all_crime_head_ids = sorted({
            hid for counts in district_crime_counts.values() for hid in counts.keys()
        })

        # 5. Build overlay: per-district socio-economic + crime data
        overlay = []
        urbanization_vals = []
        literacy_vals = []
        density_vals = []
        crime_volume_vals = []
        # For crime-type-specific correlation with literacy
        crime_type_by_district = {hid: [] for hid in all_crime_head_ids}

        for did in active_district_ids:
            dname = district_names.get(did, did)
            se_profile = SOCIO_ECONOMIC_PROFILE.get(dname)
            counts = district_crime_counts[did]
            total = sum(counts.values())

            urb = se_profile["urbanization"] if se_profile else None
            lit = se_profile["literacy"] if se_profile else None
            dens = se_profile["density"] if se_profile else None
            dens_score = DENSITY_SCORE.get(dens, 2) if dens else None
            profile_tag = se_profile["profile"] if se_profile else None
            tier = urbanization_tier(urb) if urb else "unknown"

            overlay.append({
                "districtId": did,
                "districtName": dname,
                "totalCrime": total,
                "urbanization": urb,
                "literacy": lit,
                "population_density": dens,
                "density_score": dens_score,
                "socio_economic_profile": profile_tag,
                "urbanization_tier": tier,
                "crime_composition": {
                    head_map.get(hid, KSP_CRIME_HEADS.get(hid, f"Head {hid}")): counts.get(hid, 0)
                    for hid in all_crime_head_ids if counts.get(hid, 0) > 0
                }
            })

            if se_profile:
                urbanization_vals.append(float(urb))
                literacy_vals.append(float(lit))
                density_vals.append(float(dens_score))
                crime_volume_vals.append(float(total))
                for hid in all_crime_head_ids:
                    crime_type_by_district[hid].append(float(counts.get(hid, 0)))

        overlay.sort(key=lambda x: x["totalCrime"], reverse=True)

        # 6. Indicator correlations with total crime volume
        indicator_correlations = []
        if len(crime_volume_vals) >= 4:
            for ind_name, ind_vals in [("urbanization", urbanization_vals),
                                        ("literacy", literacy_vals),
                                        ("population_density", density_vals)]:
                r = pearson_correlation(ind_vals, crime_volume_vals)
                indicator_correlations.append({
                    "indicator": ind_name,
                    "correlation": round(r, 3),
                    "strength": "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.5 else "weak" if abs(r) >= 0.3 else "negligible",
                    "direction": "positive" if r > 0 else "negative" if r < 0 else "neutral",
                    "interpretation": interpret_correlation(ind_name, r)
                })

        # 7. Urbanization gradient: group districts by tier, aggregate
        tier_stats = defaultdict(lambda: {"districts": [], "total_crime": 0, "count": 0})
        for entry in overlay:
            tier = entry["urbanization_tier"]
            tier_stats[tier]["districts"].append(entry["districtName"])
            tier_stats[tier]["total_crime"] += entry["totalCrime"]
            tier_stats[tier]["count"] += 1

        tier_order = ["metro", "urbanizing", "transitional", "agrarian"]
        urbanization_gradient = []
        for tier in tier_order:
            if tier in tier_stats:
                ts = tier_stats[tier]
                avg_crime = ts["total_crime"] / ts["count"] if ts["count"] else 0
                urbanization_gradient.append({
                    "tier": tier,
                    "districtCount": ts["count"],
                    "totalCrime": ts["total_crime"],
                    "avgCrimePerDistrict": round(avg_crime, 1),
                    "districts": ts["districts"]
                })

        # 8. Crime type by urbanization tier
        crime_type_by_urbanization = {}
        for tier in tier_order:
            if tier not in tier_stats:
                continue
            tier_districts = tier_stats[tier]["districts"]
            tier_dids = [did for did in active_district_ids if district_names.get(did) in tier_districts]
            head_totals = defaultdict(int)
            for did in tier_dids:
                for hid in all_crime_head_ids:
                    head_totals[hid] += district_crime_counts[did].get(hid, 0)
            total = sum(head_totals.values()) or 1
            ranked = sorted(
                [(head_map.get(hid, KSP_CRIME_HEADS.get(hid, f"Head {hid}")), cnt, cnt / total) for hid, cnt in head_totals.items()],
                key=lambda x: x[1], reverse=True
            )
            crime_type_by_urbanization[tier] = [
                {"crimeType": name, "count": cnt, "share": round(share, 3)}
                for name, cnt, share in ranked[:5] if cnt > 0
            ]

        # 9. Literacy vs crime-type correlations
        literacy_crime_type_correlations = []
        if len(literacy_vals) >= 4:
            for hid in all_crime_head_ids:
                type_vals = crime_type_by_district[hid]
                if sum(type_vals) < 3:
                    continue
                r = pearson_correlation(literacy_vals, type_vals)
                if abs(r) >= 0.25:
                    head_name = head_map.get(hid, KSP_CRIME_HEADS.get(hid, f"Head {hid}"))
                    literacy_crime_type_correlations.append({
                        "crimeType": head_name,
                        "correlation_with_literacy": round(r, 3),
                        "direction": "positive" if r > 0 else "negative",
                        "interpretation": (
                            f"Higher literacy districts report MORE {head_name} (r={r:.2f}) — likely better reporting/awareness, not necessarily higher incidence."
                            if r > 0.25 else
                            f"Higher literacy districts have LESS {head_name} (r={r:.2f}) — education acts as a social deterrent for this crime type."
                        )
                    })
            literacy_crime_type_correlations.sort(key=lambda x: abs(x["correlation_with_literacy"]), reverse=True)

        # 10. Key insights (machine-generated synthesis)
        key_insights = []
        if indicator_correlations:
            urb_corr = next((c for c in indicator_correlations if c["indicator"] == "urbanization"), None)
            if urb_corr:
                if urb_corr["correlation"] > 0.3:
                    key_insights.append(f"URBANIZATION-CRIME NEXUS: Urbanization is {urb_corr['strength']} positively correlated with crime volume (r={urb_corr['correlation']}). Metro districts like Bengaluru Urban drive the bulk of statewide crime, validating targeted urban policing strategies.")
                elif urb_corr["correlation"] < -0.3:
                    key_insights.append(f"INVERSE URBANIZATION PATTERN: Counterintuitively, urbanization is negatively correlated with crime (r={urb_corr['correlation']}). This may reflect superior urban policing infrastructure and surveillance.")

            lit_corr = next((c for c in indicator_correlations if c["indicator"] == "literacy"), None)
            if lit_corr:
                if lit_corr["correlation"] < -0.3:
                    key_insights.append(f"EDUCATION DETERRENT EFFECT: Literacy is negatively correlated with crime (r={lit_corr['correlation']}), supporting investment in education as a long-term crime prevention strategy.")
                elif lit_corr["correlation"] > 0.3:
                    key_insights.append(f"REPORTING BIAS DETECTED: Literacy is positively correlated with crime (r={lit_corr['correlation']}) — higher-literacy districts report more crime, suggesting underreporting in low-literacy districts rather than truly lower incidence.")

        if urbanization_gradient:
            metro = next((t for t in urbanization_gradient if t["tier"] == "metro"), None)
            agrarian = next((t for t in urbanization_gradient if t["tier"] == "agrarian"), None)
            if metro and agrarian:
                ratio = metro["avgCrimePerDistrict"] / agrarian["avgCrimePerDistrict"] if agrarian["avgCrimePerDistrict"] > 0 else 0
                if ratio > 1:
                    key_insights.append(f"URBAN-RURAL DIVIDE: Metro districts average {ratio:.1f}x the crime volume of agrarian districts. Resource allocation should weight urban deployment while not neglecting rural underreporting.")

        if literacy_crime_type_correlations:
            top_lit = literacy_crime_type_correlations[0]
            key_insights.append(f"LITERACY-CRIME TYPE LINK: {top_lit['crimeType']} shows the strongest literacy correlation (r={top_lit['correlation_with_literacy']}). {top_lit['interpretation']}")

        if crime_type_by_urbanization.get("metro") and crime_type_by_urbanization.get("agrarian"):
            metro_top = crime_type_by_urbanization["metro"][0]["crimeType"] if crime_type_by_urbanization["metro"] else "N/A"
            agrarian_top = crime_type_by_urbanization["agrarian"][0]["crimeType"] if crime_type_by_urbanization["agrarian"] else "N/A"
            if metro_top != agrarian_top:
                key_insights.append(f"CRIME TYPOLOGY SHIFT: Metro districts are dominated by {metro_top}, while agrarian districts are dominated by {agrarian_top} — the nature of crime fundamentally shifts across the urbanization spectrum, requiring differentiated policing strategies.")

        if not key_insights:
            key_insights.append("Socio-economic correlation analysis complete. Correlations are weak in this dataset, suggesting crime distribution is driven more by local factors than broad socio-economic indicators.")

        # ---- ROUND-2: Complainant demographics from ComplainantDetails ----
        # Graceful: if table absent, skip.
        complainant_demographics = None
        try:
            demo_rows = zcql.execute_query(
                "SELECT GenderID, ReligionID, AgeYear, COUNT(*) AS cnt "
                "FROM ComplainantDetails GROUP BY GenderID, ReligionID, AgeYear"
            )
            if demo_rows:
                gender_counts = defaultdict(int)
                religion_counts = defaultdict(int)
                age_bands = defaultdict(int)
                total_complainants = 0
                for r in demo_rows:
                    d = r.get("ComplainantDetails", r)
                    cnt = d.get("cnt") or d.get("COUNT") or 0
                    try:
                        cnt = int(cnt)
                    except (ValueError, TypeError):
                        cnt = 0
                    total_complainants += cnt
                    gid = d.get("GenderID")
                    if gid is not None:
                        gender_counts[str(int(float(gid)))] += cnt
                    rid = d.get("ReligionID")
                    if rid is not None:
                        religion_counts[str(int(float(rid)))] += cnt
                    age = d.get("AgeYear")
                    if age is not None:
                        try:
                            a = int(float(age))
                            if a < 25:
                                age_bands["18-24"] += cnt
                            elif a < 35:
                                age_bands["25-34"] += cnt
                            elif a < 45:
                                age_bands["35-44"] += cnt
                            elif a < 55:
                                age_bands["45-54"] += cnt
                            elif a < 65:
                                age_bands["55-64"] += cnt
                            else:
                                age_bands["65+"] += cnt
                        except (ValueError, TypeError):
                            pass
                GENDER_NAMES = {"1": "Male", "2": "Female", "3": "Other"}
                RELIGION_NAMES = {
                    "1": "Hindu", "2": "Muslim", "3": "Christian",
                    "4": "Sikh", "5": "Buddhist", "6": "Jain", "7": "Other",
                }
                t = total_complainants or 1
                complainant_demographics = {
                    "total_complainants": total_complainants,
                    "gender_distribution": [
                        {"gender": GENDER_NAMES.get(g, f"Gender {g}"), "count": c,
                         "percent": round(c / t * 100, 1)}
                        for g, c in sorted(gender_counts.items(), key=lambda x: -x[1])
                    ],
                    "religion_distribution": [
                        {"religion": RELIGION_NAMES.get(r, f"Religion {r}"), "count": c,
                         "percent": round(c / t * 100, 1)}
                        for r, c in sorted(religion_counts.items(), key=lambda x: -x[1])
                    ],
                    "age_band_distribution": [
                        {"band": b, "count": c, "percent": round(c / t * 100, 1)}
                        for b, c in sorted(age_bands.items(),
                                           key=lambda x: ["18-24", "25-34", "35-44",
                                                          "45-54", "55-64", "65+"].index(x[0]))
                    ],
                }
                # Add a demographic insight
                if complainant_demographics["gender_distribution"]:
                    top_g = complainant_demographics["gender_distribution"][0]
                    key_insights.append(
                        f"COMPLAINANT DEMOGRAPHICS: Of {total_complainants} complainants, "
                        f"{top_g['percent']}% are {top_g['gender'].lower()}. "
                        f"This gender asymmetry in complaint registration is a key sociological "
                        f"indicator for victim-support resource planning."
                    )
        except Exception as demo_err:
            print(f"ComplainantDetails lookup skipped: {demo_err}")

        response = {
            "success": True,
            "indicator_correlations": indicator_correlations,
            "district_socio_economic_overlay": overlay,
            "urbanization_gradient": urbanization_gradient,
            "crime_type_by_urbanization": crime_type_by_urbanization,
            "literacy_crime_type_correlations": literacy_crime_type_correlations[:10],
            "key_insights": key_insights,
            "districtsAnalyzed": len(active_district_ids)
        }
        if complainant_demographics:
            response["complainant_demographics"] = complainant_demographics

        basicio.write(json.dumps(response))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()
