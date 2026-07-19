import zcatalyst_sdk
import json
import math
from datetime import datetime, timedelta
from collections import defaultdict
from ml_core import fit_seasonal_trend_model

"""
predictCrimeRisk
-----------------
Predicts next-month crime risk for a district + crime category using a real
fitted regression model (OLS: intercept + linear trend + sin/cos seasonal
terms), instead of a hand-tuned recent-vs-prior-months ratio. The model's
coefficients are learned fresh from that district's own historical monthly
counts on every request.

ENHANCED — AI/ML-Driven Intelligence:
  * contributing_factors  — decomposition of the predicted value into its
    model components: baseline (intercept), trend contribution, seasonal
    contribution. Tells the analyst WHY the prediction is what it is.
  * socio_economic_note   — overlay tying the district's socio-economic
    profile to the predicted risk (the "why behind the where").
  * emerging_typology     — forecast of which crime types are trending up
    in this district (emerging crime risk prediction), computed by fitting
    mini-trends per crime head.
  * trend_acceleration    — whether the slope itself is accelerating or
    decelerating (second derivative of the trend).
  * seasonal_peak_month   — which calendar month the seasonal component
    peaks in, for proactive seasonal deployment.
  * recommendation        — human-readable deployment recommendation derived
    from all the above signals.

Query params: ?district_id=5&crime_head_id=1

Response shape is strictly additive — existing fields preserved.
"""

PAGE_SIZE = 300
MAX_PAGES = 10000

# Socio-economic profiles (shared with getDistrictCrimeStats)
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


def simple_slope(values):
    """OLS slope of values vs index."""
    n = len(values)
    if n < 2:
        return 0.0
    t_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((t - t_mean) * (y - y_mean) for t, y in enumerate(values))
    den = sum((t - t_mean) ** 2 for t in range(n))
    return num / den if den != 0 else 0.0


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    district_id = str(basicio.get_argument('district_id') or '5')
    crime_head_id = str(basicio.get_argument('crime_head_id') or '1')

    try:
        # ---- 1. District name + police stations ----
        district_rows = zcql.execute_query(
            "SELECT DistrictID, DistrictName FROM District WHERE StateID = 1"
        )
        district_name = "Unknown"
        for r in district_rows:
            d = r.get("District", r)
            if str(d["DistrictID"]) == district_id:
                district_name = d["DistrictName"]
                break

        unit_rows = zcql.execute_query(
            f"SELECT ROWID, UnitID FROM Unit WHERE DistrictID = '{district_id}'"
        )
        station_ids = set()
        for r in unit_rows:
            u = r['Unit']
            if u.get('ROWID'):
                station_ids.add(normalize_id(u['ROWID']))
            if u.get('UnitID'):
                station_ids.add(normalize_id(u['UnitID']))

        if not station_ids:
            basicio.write(json.dumps({
                "success": True,
                "status": "no_units_found",
                "district_id": district_id,
                "risk_score": 0,
                "risk_level": "LOW",
                "confidence": "low"
            }))
            return

        # ---- 2. Page through CaseMaster (last 3 years only), build monthly counts ----
        monthly_counts = {}
        monthly_by_head = defaultdict(lambda: defaultdict(int))  # head_id -> {month -> count}

        # Find the starting ROWID near the cutoff date so we don't scan old records
        cutoff_date = (datetime.now() - timedelta(days=3*365)).strftime("%Y-%m-%d")
        try:
            start_result = zcql.execute_query(
                f"SELECT ROWID FROM CaseMaster WHERE CrimeRegisteredDate >= '{cutoff_date}' ORDER BY ROWID LIMIT 1"
            )
            last_rowid = max(0, int(start_result[0]['CaseMaster']['ROWID']) - 1) if start_result else 0
        except Exception:
            last_rowid = 0

        pages = 0
        while pages < MAX_PAGES:
            query = (
                "SELECT ROWID, CrimeRegisteredDate, CaseMasterID, PoliceStationID, CrimeMajorHeadID "
                f"FROM CaseMaster WHERE ROWID > {last_rowid} "
                f"ORDER BY ROWID LIMIT {PAGE_SIZE}"
            )
            page = zcql.execute_query(query)
            if not page:
                break

            for row in page:
                c = row['CaseMaster']
                if normalize_id(c.get('PoliceStationID')) not in station_ids:
                    continue
                date_str = c.get('CrimeRegisteredDate')
                if not date_str:
                    continue
                date_part = date_str.split(' ')[0]
                parts = date_part.split('-')
                if len(parts) < 2:
                    continue
                key = f"{parts[0]}-{parts[1]}"

                hid = normalize_id(c.get('CrimeMajorHeadID'))
                monthly_by_head[hid][key] += 1

                if hid == crime_head_id:
                    monthly_counts[key] = monthly_counts.get(key, 0) + 1

            last_rowid = int(page[-1]['CaseMaster']['ROWID'])
            pages += 1
            if len(page) < PAGE_SIZE:
                break

        sorted_months = sorted(monthly_counts.keys())[-24:]
        counts = [float(monthly_counts[m]) for m in sorted_months]

        # Need enough points to fit 4 parameters
        if len(counts) < 8:
            basicio.write(json.dumps({
                "success": True,
                "status": "insufficient_data",
                "district_id": district_id,
                "district_name": district_name,
                "crime_head_id": crime_head_id,
                "risk_score": 50,
                "risk_level": "MEDIUM",
                "months_analyzed": len(counts),
                "confidence": "low",
                "note": "Fewer than 8 months of history -- not enough data to fit a reliable seasonal trend model."
            }))
            return

        # ---- 3. Fit the model and predict next month ----
        model = fit_seasonal_trend_model(counts)
        next_t = len(counts)
        predicted_next_month = model["predict"](next_t)
        predicted_next_month = max(0.0, predicted_next_month)

        historical_avg = sum(counts) / len(counts)
        max_count = max(counts) or 1.0

        raw_position = predicted_next_month / max_count
        risk_score = max(0.0, min(100.0, raw_position * 100))

        if risk_score >= 80:
            risk_level = "CRITICAL"
        elif risk_score >= 60:
            risk_level = "HIGH"
        elif risk_score >= 40:
            risk_level = "MEDIUM"
        else:
            risk_level = "LOW"

        r_squared = model["r_squared"]
        if r_squared >= 0.6 and len(counts) >= 12:
            confidence = "high"
        elif r_squared >= 0.35:
            confidence = "medium"
        else:
            confidence = "low"

        # ---- ENHANCED: Contributing factors decomposition ----
        beta = model["beta"]  # [intercept, trend_slope, seasonal_sin, seasonal_cos]
        intercept = beta[0]
        trend_contribution = beta[1] * next_t
        seasonal_angle = 2 * math.pi * next_t / 12.0
        seasonal_contribution = beta[2] * math.sin(seasonal_angle) + beta[3] * math.cos(seasonal_angle)

        contributing_factors = {
            "baseline": round(intercept, 1),
            "trend_contribution": round(trend_contribution, 1),
            "seasonal_contribution": round(seasonal_contribution, 1),
            "trend_direction": "rising" if beta[1] > 0.5 else ("falling" if beta[1] < -0.5 else "stable"),
            "seasonal_strength": round(math.sqrt(beta[2]**2 + beta[3]**2), 2)
        }

        # ---- ENHANCED: Trend acceleration (second derivative) ----
        # Compare slope of first half vs second half
        mid = len(counts) // 2
        if mid >= 4:
            slope_first = simple_slope(counts[:mid])
            slope_second = simple_slope(counts[mid:])
            trend_acceleration = "accelerating" if slope_second > slope_first * 1.2 else \
                                 "decelerating" if slope_second < slope_first * 0.8 else "steady"
        else:
            trend_acceleration = "steady"

        # ---- ENHANCED: Seasonal peak month ----
        # Find which month (0-11) the seasonal component peaks
        seasonal_values = []
        for m in range(12):
            angle = 2 * math.pi * m / 12.0
            seasonal_values.append(beta[2] * math.sin(angle) + beta[3] * math.cos(angle))
        peak_month_idx = seasonal_values.index(max(seasonal_values))
        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        seasonal_peak_month = month_names[peak_month_idx]

        # ---- ENHANCED: Emerging typology forecast ----
        # Fit mini-trends per crime head, flag those rising
        head_rows = zcql.execute_query("SELECT CrimeHeadID, CrimeGroupName FROM CrimeHead")
        head_map = {
            normalize_id(r['CrimeHead']['CrimeHeadID']): r['CrimeHead']['CrimeGroupName']
            for r in head_rows
        }
        for hid, name in KSP_CRIME_HEADS.items():
            head_map.setdefault(hid, name)

        emerging_typology = []
        for hid, monthly in monthly_by_head.items():
            sorted_m = sorted(monthly.keys())[-12:]
            vals = [float(monthly[m]) for m in sorted_m]
            if len(vals) >= 6:
                slope = simple_slope(vals)
                latest = vals[-1]
                if slope > 0.3 and latest >= 3:
                    head_name = head_map.get(hid, KSP_CRIME_HEADS.get(hid, f"Head {hid}"))
                    emerging_typology.append({
                        "crime_head_id": hid,
                        "crime_head_name": head_name,
                        "trend_slope": round(slope, 3),
                        "latest_month_count": int(latest),
                        "direction": "rising"
                    })
        emerging_typology.sort(key=lambda x: x["trend_slope"], reverse=True)
        emerging_typology = emerging_typology[:5]  # top 5 emerging types

        # ---- ENHANCED: Socio-economic note ----
        profile = SOCIO_ECONOMIC_PROFILE.get(district_name)
        if profile:
            urb = profile["urbanization"]
            ptype = profile["profile"]
            target_name = head_map.get(crime_head_id, KSP_CRIME_HEADS.get(crime_head_id, "this crime type"))
            if urb >= 70:
                se_note = f"{district_name} is a highly urbanized commercial centre ({ptype}). High population density and economic activity in such metros correlate with elevated {target_name} risk. The model's predicted {round(predicted_next_month)} cases for next month align with this urbanization-driven baseline."
            elif urb >= 45:
                se_note = f"{district_name} is a rapidly urbanizing peri-urban belt ({ptype}). Transitional demographics and commuter inflow correlate with moderate-to-high {target_name} incidence. The forecast reflects this socio-economic pressure profile."
            else:
                se_note = f"{district_name} is a predominantly agrarian/rural district ({ptype}). Lower urbanization typically suppresses volume but agrarian distress and low literacy can elevate certain crime types. The predicted {round(predicted_next_month)} cases should be read in this rural-context frame."
        else:
            se_note = f"Socio-economic overlay unavailable for {district_name}. The prediction is based purely on the fitted temporal trend model."

        # ---- ENHANCED: Recommendation ----
        rec_parts = []
        if risk_level in ("CRITICAL", "HIGH"):
            rec_parts.append(f"Deploy additional patrols and investigative resources to {district_name}")
            if contributing_factors["trend_direction"] == "rising":
                rec_parts.append("as the upward trend shows no sign of reversal")
            if seasonal_peak_month:
                rec_parts.append(f"with peak seasonal incidence expected in {seasonal_peak_month}")
        elif risk_level == "MEDIUM":
            rec_parts.append(f"Maintain current deployment levels in {district_name}")
            if emerging_typology:
                rec_parts.append(f"but monitor the rising {emerging_typology[0]['crime_head_name']} trend")
        else:
            rec_parts.append(f"Current risk in {district_name} is manageable with standard deployment")
        if trend_acceleration == "accelerating":
            rec_parts.append("The trend is accelerating — consider pre-emptive community policing initiatives")
        elif trend_acceleration == "decelerating":
            rec_parts.append("The trend is decelerating — existing interventions appear effective")
        recommendation = ". ".join(rec_parts) + "."

        # ---- ROUND-2: Resource gap analysis from Employee table ----
        # Graceful: if Employee table absent, skip.
        resource_gap = None
        try:
            emp_rows = zcql.execute_query(
                f"SELECT COUNT(*) AS cnt FROM Employee WHERE DistrictID = '{district_id}'"
            )
            if emp_rows:
                rec = emp_rows[0].get("Employee", emp_rows[0])
                personnel = int(rec.get("cnt", 0) or 0)
                if personnel > 0:
                    officer_ratio = round(personnel / max(predicted_next_month, 1), 1)
                    # Heuristic: need roughly 1 officer per 3 predicted cases for adequate coverage
                    adequate_threshold = predicted_next_month * 3
                    if officer_ratio < 3:
                        gap_status = "understaffed"
                        gap_severity = "critical" if officer_ratio < 1.5 else "moderate"
                    else:
                        gap_status = "adequate"
                        gap_severity = "none"
                    resource_gap = {
                        "district_id": district_id,
                        "police_strength": personnel,
                        "predicted_cases_next_month": round(predicted_next_month),
                        "officer_to_case_ratio": officer_ratio,
                        "adequate_threshold": round(adequate_threshold),
                        "gap_status": gap_status,
                        "gap_severity": gap_severity,
                        "shortfall": max(0, round(adequate_threshold - personnel)),
                    }
                    # Enhance recommendation with resource context
                    if gap_status == "understaffed":
                        recommendation += (f" Resource alert: {district_name} currently has {personnel} "
                                           f"police personnel against a forecast of {round(predicted_next_month)} "
                                           f"cases next month (ratio {officer_ratio}:1). "
                                           f"An estimated shortfall of {resource_gap['shortfall']} personnel "
                                           f"should be addressed through inter-district redeployment or recruitment.")
        except Exception as emp_err:
            print(f"Employee resource lookup skipped: {emp_err}")

        response = {
            "success": True,
            "status": "success",
            "district_id": district_id,
            "district_name": district_name,
            "crime_head_id": crime_head_id,
            "risk_score": round(risk_score),
            "risk_level": risk_level,
            "predicted_next_month": round(predicted_next_month),
            "historical_avg": round(historical_avg, 1),
            "trend_slope_per_month": round(model["beta"][1], 3),
            "r_squared": round(r_squared, 3),
            "residual_std": round(model["residual_std"], 2),
            "months_analyzed": len(counts),
            "confidence": confidence,
            "model": "ols_seasonal_trend",
            # --- Enhanced fields ---
            "contributing_factors": contributing_factors,
            "trend_acceleration": trend_acceleration,
            "seasonal_peak_month": seasonal_peak_month,
            "emerging_typology": emerging_typology,
            "socio_economic_note": se_note,
            "recommendation": recommendation
        }
        if resource_gap:
            response["resource_gap_analysis"] = resource_gap

        basicio.write(json.dumps(response))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()
