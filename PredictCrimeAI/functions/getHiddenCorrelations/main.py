import zcatalyst_sdk
import json
from collections import defaultdict
from ml_core import pearson_correlation, kmeans

"""
getHiddenCorrelations
-----------------------
Two real unsupervised-learning outputs a human wouldn't get from a manual
cross-tab query:

1. Crime-type co-occurrence correlation: builds a per-district count vector
   for each crime type, then computes the Pearson correlation between every
   pair of crime types across all districts.

2. District clustering (k-means): groups districts by their normalized
   crime-type profile (the MIX of crime, not the raw volume).

ENHANCED — Sociological & AI-Driven Correlation:
  * socio_economic_correlations — Pearson correlation between each district's
    socio-economic indicators (urbanization %, literacy %) and its total crime
    volume, revealing the "why behind the where" at a statistical level.
  * socio_economic_profile_per_district — attaches urbanization/literacy/
    density to each district profile for cross-reference.
  * inter_district_similarity — top-N most similar district pairs by their
    normalized crime profile (Euclidean distance), surfacing districts that
    behave alike even if geographically far apart — useful for sharing
    policing strategies across similar crime ecosystems.
  * urbanization_crime_ranking — districts ranked by urbanization index with
    their crime volume, showing the socio-economic gradient.

Query params: ?k=4  (number of district clusters, default 4)

Response shape is strictly additive — existing fields preserved.
"""

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

# Socio-economic profiles keyed by district name
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


def normalize_id(val):
    if val is None:
        return ""
    try:
        return str(int(float(val)))
    except ValueError:
        return str(val).strip()


def euclidean(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    try:
        k = int(basicio.get_argument('k') or 4)
    except ValueError:
        k = 4

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
                "SELECT ROWID, PoliceStationID, CrimeMajorHeadID, CaseMasterID FROM CaseMaster "
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
                "crimeTypes": [],
                "correlations": [],
                "clusters": [],
                "districtProfiles": [],
                "socio_economic_correlations": [],
                "inter_district_similarity": [],
                "urbanization_crime_ranking": []
            }))
            return

        active_district_ids = sorted(
            d for d in district_crime_counts.keys() if d in district_names
        )
        all_crime_head_ids = sorted({
            hid for counts in district_crime_counts.values() for hid in counts.keys()
        })
        crime_type_names = [head_map.get(hid, f"Head {hid}") for hid in all_crime_head_ids]

        # 5. Correlation across crime types (samples = districts)
        crime_type_vectors = {}
        for hid in all_crime_head_ids:
            crime_type_vectors[hid] = [
                float(district_crime_counts[did].get(hid, 0)) for did in active_district_ids
            ]

        correlations = []
        for i in range(len(all_crime_head_ids)):
            for j in range(i + 1, len(all_crime_head_ids)):
                hid_a, hid_b = all_crime_head_ids[i], all_crime_head_ids[j]
                r = pearson_correlation(crime_type_vectors[hid_a], crime_type_vectors[hid_b])
                if abs(r) >= 0.3:
                    correlations.append({
                        "crimeA": head_map.get(hid_a, f"Head {hid_a}"),
                        "crimeB": head_map.get(hid_b, f"Head {hid_b}"),
                        "correlation": round(r, 3),
                        "strength": (
                            "strong" if abs(r) >= 0.7 else
                            "moderate" if abs(r) >= 0.5 else
                            "weak"
                        ),
                        "direction": "positive" if r > 0 else "negative"
                    })

        correlations.sort(key=lambda x: abs(x["correlation"]), reverse=True)

        # 6. K-means clustering of districts by NORMALIZED crime-type profile
        district_profiles = []
        profile_vectors = []
        for did in active_district_ids:
            counts = district_crime_counts[did]
            total = sum(counts.values()) or 1
            profile = {head_map.get(hid, f"Head {hid}"): round(counts.get(hid, 0) / total, 3)
                       for hid in all_crime_head_ids}
            profile_vector = [counts.get(hid, 0) / total for hid in all_crime_head_ids]
            profile_vectors.append(profile_vector)

            # Attach socio-economic profile
            dname = district_names.get(did, did)
            se_profile = SOCIO_ECONOMIC_PROFILE.get(dname)

            district_profiles.append({
                "districtId": did,
                "districtName": dname,
                "totalCases": total,
                "profile": profile,
                "urbanization_index": se_profile["urbanization"] if se_profile else None,
                "literacy_index": se_profile["literacy"] if se_profile else None,
                "density": se_profile["density"] if se_profile else None,
                "socio_economic_profile": se_profile["profile"] if se_profile else None
            })

        clusters_out = []
        if len(profile_vectors) >= 2:
            labels, centroids = kmeans(profile_vectors, k=k, seed=42)
            for i, did in enumerate(active_district_ids):
                district_profiles[i]["clusterId"] = labels[i]

            for c_idx, centroid in enumerate(centroids):
                member_names = [
                    district_profiles[i]["districtName"]
                    for i in range(len(active_district_ids)) if labels[i] == c_idx
                ]
                ranked = sorted(
                    zip(crime_type_names, centroid), key=lambda x: x[1], reverse=True
                )
                dominant = [name for name, share in ranked[:2] if share > 0.05]

                clusters_out.append({
                    "clusterId": c_idx,
                    "districtCount": len(member_names),
                    "districts": member_names,
                    "dominantCrimeTypes": dominant,
                    "profile": {name: round(share, 3) for name, share in ranked}
                })

        # ---- ENHANCED: Socio-economic correlation overlay ----
        # Pearson correlation between urbanization/literacy and total crime volume
        urbanization_values = []
        literacy_values = []
        density_values = []
        crime_volume_values = []
        valid_districts_for_se = []

        for did in active_district_ids:
            dname = district_names.get(did, did)
            se_profile = SOCIO_ECONOMIC_PROFILE.get(dname)
            if se_profile:
                total = sum(district_crime_counts[did].values())
                urbanization_values.append(float(se_profile["urbanization"]))
                literacy_values.append(float(se_profile["literacy"]))
                density_values.append(float(DENSITY_SCORE.get(se_profile["density"], 2)))
                crime_volume_values.append(float(total))
                valid_districts_for_se.append(dname)

        socio_economic_correlations = []
        if len(crime_volume_values) >= 4:
            for indicator_name, indicator_vals in [
                ("urbanization", urbanization_values),
                ("literacy", literacy_values),
                ("population_density", density_values)
            ]:
                r = pearson_correlation(indicator_vals, crime_volume_values)
                socio_economic_correlations.append({
                    "indicator": indicator_name,
                    "correlation_with_crime_volume": round(r, 3),
                    "strength": "strong" if abs(r) >= 0.7 else "moderate" if abs(r) >= 0.5 else "weak" if abs(r) >= 0.3 else "negligible",
                    "direction": "positive" if r > 0 else "negative",
                    "interpretation": _interpret_se_correlation(indicator_name, r)
                })

        # ---- ENHANCED: Urbanization vs crime ranking ----
        urbanization_crime_ranking = []
        for i, dname in enumerate(valid_districts_for_se):
            urbanization_crime_ranking.append({
                "district": dname,
                "urbanization": int(urbanization_values[i]),
                "literacy": int(literacy_values[i]),
                "total_crime": int(crime_volume_values[i])
            })
        urbanization_crime_ranking.sort(key=lambda x: x["urbanization"], reverse=True)

        # ---- ENHANCED: Inter-district similarity ranking ----
        # Compute pairwise Euclidean distance on normalized profiles, return top-N closest
        inter_district_similarity = []
        n = len(active_district_ids)
        for i in range(n):
            for j in range(i + 1, n):
                dist = euclidean(profile_vectors[i], profile_vectors[j])
                inter_district_similarity.append({
                    "districtA": district_names.get(active_district_ids[i], active_district_ids[i]),
                    "districtB": district_names.get(active_district_ids[j], active_district_ids[j]),
                    "similarity_distance": round(dist, 4),
                    "similarity_score": round(max(0, 1 - dist) * 100, 1)  # 0-100, higher = more similar
                })
        inter_district_similarity.sort(key=lambda x: x["similarity_distance"])
        inter_district_similarity = inter_district_similarity[:15]  # top 15 most similar pairs

        # ---- ROUND-2: Personnel vs crime correlation (Employee table) ----
        # Graceful: if Employee table absent, skip.
        personnel_vs_crime = None
        try:
            emp_rows = zcql.execute_query(
                "SELECT DistrictID, COUNT(*) AS cnt FROM Employee GROUP BY DistrictID"
            )
            if emp_rows:
                emp_strength = {}
                for r in emp_rows:
                    e = r.get("Employee", r)
                    did = normalize_id(e.get("DistrictID"))
                    cnt = e.get("cnt") or e.get("COUNT") or 0
                    try:
                        emp_strength[did] = int(cnt)
                    except (ValueError, TypeError):
                        emp_strength[did] = 0
                # Build parallel arrays for districts that have both crime + employee data
                crime_vals = []
                personnel_vals = []
                for did in active_district_ids:
                    if did in emp_strength:
                        total_crime = sum(district_crime_counts.get(did, {}).values())
                        crime_vals.append(float(total_crime))
                        personnel_vals.append(float(emp_strength[did]))
                if len(crime_vals) >= 5:
                    r_val = pearson_correlation(personnel_vals, crime_vals)
                    if r_val is not None:
                        strength_label = ("very strong" if abs(r_val) >= 0.7 else
                                          "strong" if abs(r_val) >= 0.5 else
                                          "moderate" if abs(r_val) >= 0.3 else
                                          "weak")
                        direction = "positive" if r_val >= 0 else "negative"
                        if r_val > 0.3:
                            interpretation = ("Districts with more police personnel also have more crime — "
                                              "this likely reflects deployment proportional to crime volume "
                                              "rather than personnel causing crime. It validates that resource "
                                              "allocation currently tracks demand.")
                        elif r_val < -0.3:
                            interpretation = ("More personnel correlates with less crime — suggesting "
                                              "effective deterrence where deployment is strongest.")
                        else:
                            interpretation = ("Police strength and crime volume show no strong linear "
                                              "relationship, indicating deployment may not be fully "
                                              "optimized to crime demand across districts.")
                        personnel_vs_crime = {
                            "pearson_r": round(r_val, 3),
                            "strength": strength_label,
                            "direction": direction,
                            "interpretation": interpretation,
                            "districts_compared": len(crime_vals),
                        }
        except Exception as emp_err:
            print(f"Employee correlation skipped: {emp_err}")

        response = {
            "success": True,
            "crimeTypes": crime_type_names,
            "correlations": correlations[:20],
            "clusters": clusters_out,
            "districtProfiles": district_profiles,
            "model": "pearson_correlation + kmeans_clustering",
            "districtsAnalyzed": len(active_district_ids),
            # --- Enhanced fields ---
            "socio_economic_correlations": socio_economic_correlations,
            "urbanization_crime_ranking": urbanization_crime_ranking,
            "inter_district_similarity": inter_district_similarity
        }
        if personnel_vs_crime:
            response["personnel_vs_crime_correlation"] = personnel_vs_crime

        basicio.write(json.dumps(response))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()


def _interpret_se_correlation(indicator, r):
    """Human-readable interpretation of a socio-economic correlation."""
    direction = "positively" if r > 0 else "negatively"
    strength = "strongly" if abs(r) >= 0.7 else "moderately" if abs(r) >= 0.5 else "weakly" if abs(r) >= 0.3 else "negligibly"

    if indicator == "urbanization":
        if r > 0.3:
            return f"Urbanization is {strength} {direction} correlated with crime volume — more urbanized districts experience higher crime, consistent with the urbanization-crime nexus theory."
        elif r < -0.3:
            return f"Urbanization is {strength} {direction} correlated with crime volume — counterintuitively, more urbanized districts show lower crime in this dataset."
        return "Urbanization shows no significant correlation with crime volume in this dataset."
    elif indicator == "literacy":
        if r < -0.3:
            return f"Literacy is {strength} {direction} correlated with crime — higher literacy districts tend to have lower crime, supporting the education-as-deterrent hypothesis."
        elif r > 0.3:
            return f"Literacy is {strength} {direction} correlated with crime — higher literacy districts report more crime, likely due to better reporting rates rather than higher incidence."
        return "Literacy shows no significant correlation with crime volume."
    elif indicator == "population_density":
        if r > 0.3:
            return f"Population density is {strength} {direction} correlated with crime — denser districts experience more crime, reflecting anonymity-of-the-city effects."
        return "Population density shows no significant correlation with crime volume."
    return f"{indicator} is {strength} {direction} correlated with crime volume."
