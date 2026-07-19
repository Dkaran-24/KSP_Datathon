import zcatalyst_sdk
import json
import math
from collections import defaultdict

"""
getCrimeTrends
--------------
Returns monthly case-count trends, either overall or filtered to a
single CrimeMajorHeadID (?crime_head_id=3), for the last N months
(?months=24, default 24).

ENHANCED — Pattern & Trend Discovery:
In addition to the raw months/counts arrays, this function now returns
statistical analytics that turn the time-series into actionable intelligence:

  * trend_slope_per_month   — OLS regression slope (cases/month), tells whether
                              crime is rising, falling, or flat over the window.
  * trend_direction          — "rising" / "falling" / "stable" derived from slope.
  * moving_average           — rolling 3-month moving average aligned to each
                              month, smoothing noise to reveal the underlying
                              trend curve.
  * yoy_delta                — year-over-year change (latest month vs same month
                              last year) as a percentage, surfacing long-term
                              shifts vs seasonal repeats.
  * yoy_direction            — "up" / "down" / "flat" / "insufficient_data".
  * anomaly_months           — list of months whose count deviates >= 2 std from
                              the series mean (statistical spike/valley flags).
  * peak_month / trough_month — the highest and lowest months in the window.
  * volatility               — coefficient of variation (std/mean) indicating
                              how erratic the series is.
  * forecast_next            — simple next-month projection from the OLS model.

Response shape is strictly additive — existing months/counts fields are
preserved so all current frontend code keeps working.
"""

PAGE_SIZE = 300
MAX_PAGES = 10000  # safety cap: 10000*300 = 3M rows max scanned


def _ols_slope(values):
    """Simple linear regression slope (y vs index t) using least squares.
    Returns (slope, r_squared)."""
    n = len(values)
    if n < 2:
        return 0.0, 0.0
    t_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    num = sum((t - t_mean) * (y - y_mean) for t, y in enumerate(values))
    den_t = sum((t - t_mean) ** 2 for t in range(n))
    if den_t == 0:
        return 0.0, 0.0
    slope = num / den_t
    # R-squared
    intercept = y_mean - slope * t_mean
    y_hat = [slope * t + intercept for t in range(n)]
    ss_res = sum((y - yh) ** 2 for y, yh in zip(values, y_hat))
    ss_tot = sum((y - y_mean) ** 2 for y in values) or 1e-9
    r_sq = max(0.0, 1.0 - ss_res / ss_tot)
    return slope, r_sq


def _std(values):
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return math.sqrt(var)


def _moving_average(values, window=3):
    """Rolling window moving average; padded at the start with available data."""
    result = []
    for i in range(len(values)):
        start = max(0, i - window + 1)
        chunk = values[start:i + 1]
        result.append(round(sum(chunk) / len(chunk), 1))
    return result


def _detect_anomaly_months(months, counts, threshold=2.0):
    """Flag months whose count is >= threshold std deviations from the mean."""
    if len(counts) < 4:
        return []
    mean = sum(counts) / len(counts)
    sd = _std(counts)
    if sd < 1e-9:
        return []
    anomalies = []
    for m, c in zip(months, counts):
        z = (c - mean) / sd
        if abs(z) >= threshold:
            anomalies.append({
                "month": m,
                "count": c,
                "z_score": round(z, 2),
                "type": "spike" if z > 0 else "valley"
            })
    return anomalies


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    crime_head_id = basicio.get_argument('crime_head_id')  # None = all
    months_limit = int(basicio.get_argument('months') or 24)

    try:
        monthly_counts = defaultdict(int)
        case_ids_by_month = defaultdict(list)  # month_key -> [CaseMasterID, ...]
        last_rowid = 0
        pages = 0
        total_cases = 0

        while pages < MAX_PAGES:
            if crime_head_id:
                query = (
                    "SELECT ROWID, CrimeRegisteredDate, CaseMasterID FROM CaseMaster "
                    f"WHERE ROWID > {last_rowid} "
                    f"AND CrimeMajorHeadID = '{crime_head_id}' "
                    f"ORDER BY ROWID LIMIT {PAGE_SIZE}"
                )
            else:
                query = (
                    "SELECT ROWID, CrimeRegisteredDate, CaseMasterID FROM CaseMaster "
                    f"WHERE ROWID > {last_rowid} "
                    f"ORDER BY ROWID LIMIT {PAGE_SIZE}"
                )

            page = zcql.execute_query(query)
            if not page:
                break

            for row in page:
                c = row['CaseMaster']
                date_str = c.get('CrimeRegisteredDate')
                if not date_str:
                    continue
                date_part = date_str.split(' ')[0]
                parts = date_part.split('-')
                if len(parts) < 2:
                    continue
                key = f"{parts[0]}-{parts[1]}"
                monthly_counts[key] += 1
                total_cases += 1
                cm_id = c.get('CaseMasterID')
                if cm_id:
                    case_ids_by_month[key].append(str(cm_id))

            last_rowid = int(page[-1]['CaseMaster']['ROWID'])
            pages += 1
            if len(page) < PAGE_SIZE:
                break

        sorted_months = sorted(monthly_counts.keys())[-months_limit:]
        counts = [monthly_counts[m] for m in sorted_months]

        # ---- ENHANCED: Statistical trend analytics ----
        analytics = {
            "trend_slope_per_month": 0.0,
            "trend_direction": "stable",
            "trend_r_squared": 0.0,
            "moving_average": [],
            "yoy_delta": None,
            "yoy_direction": "insufficient_data",
            "anomaly_months": [],
            "peak_month": None,
            "peak_count": 0,
            "trough_month": None,
            "trough_count": 0,
            "volatility": 0.0,
            "forecast_next": 0,
            "mean": 0.0,
            "std": 0.0
        }

        if len(counts) >= 2:
            slope, r_sq = _ols_slope(counts)
            analytics["trend_slope_per_month"] = round(slope, 3)
            analytics["trend_r_squared"] = round(r_sq, 3)
            if slope > 0.5:
                analytics["trend_direction"] = "rising"
            elif slope < -0.5:
                analytics["trend_direction"] = "falling"
            else:
                analytics["trend_direction"] = "stable"

            analytics["moving_average"] = _moving_average(counts, window=3)

            mean_val = sum(counts) / len(counts)
            sd_val = _std(counts)
            analytics["mean"] = round(mean_val, 1)
            analytics["std"] = round(sd_val, 1)
            analytics["volatility"] = round(sd_val / mean_val, 3) if mean_val > 0 else 0.0

            # Peak / trough
            peak_idx = counts.index(max(counts))
            trough_idx = counts.index(min(counts))
            analytics["peak_month"] = sorted_months[peak_idx]
            analytics["peak_count"] = counts[peak_idx]
            analytics["trough_month"] = sorted_months[trough_idx]
            analytics["trough_count"] = counts[trough_idx]

            # Anomaly months
            analytics["anomaly_months"] = _detect_anomaly_months(sorted_months, counts, threshold=2.0)

            # YoY delta: compare latest month to same month one year prior
            if len(sorted_months) >= 13:
                latest_m = sorted_months[-1]
                yr, mo = latest_m.split("-")
                yoy_m = f"{int(yr) - 1}-{mo}"
                if yoy_m in monthly_counts:
                    yoy_current = counts[-1]
                    yoy_prior = monthly_counts[yoy_m]
                    if yoy_prior > 0:
                        delta_pct = round(((yoy_current - yoy_prior) / yoy_prior) * 100, 1)
                        analytics["yoy_delta"] = delta_pct
                        if delta_pct > 5:
                            analytics["yoy_direction"] = "up"
                        elif delta_pct < -5:
                            analytics["yoy_direction"] = "down"
                        else:
                            analytics["yoy_direction"] = "flat"
                    else:
                        analytics["yoy_delta"] = None
                        analytics["yoy_direction"] = "insufficient_data"

            # Forecast next month via OLS projection
            n = len(counts)
            t_mean = (n - 1) / 2.0
            y_mean = sum(counts) / n
            intercept = y_mean - slope * t_mean
            forecast = max(0, slope * n + intercept)
            analytics["forecast_next"] = round(forecast)

        # ---- ROUND-2: Act-based trends (legal act breakdown over time) ----
        # Graceful: if ActSectionAssociation absent, skip.
        act_based_trends = None
        try:
            # Sample: use the last N months (capped) to keep query volume manageable
            recent_months = sorted_months[-min(len(sorted_months), 12):]
            act_monthly = defaultdict(lambda: defaultdict(int))  # act -> {month -> count}
            for mkey in recent_months:
                cids = case_ids_by_month.get(mkey, [])
                for i in range(0, len(cids), 200):
                    chunk = cids[i:i + 200]
                    ids_str = ",".join(f"'{c}'" for c in chunk if c)
                    if not ids_str:
                        continue
                    arows = zcql.execute_query(
                        f"SELECT CaseMasterID, ActID FROM ActSectionAssociation "
                        f"WHERE CaseMasterID IN ({ids_str})"
                    )
                    for ar in arows:
                        a = ar.get("ActSectionAssociation", ar)
                        act_id = str(a.get("ActID", "")).strip()
                        if act_id:
                            act_monthly[act_id][mkey] += 1
            if act_monthly:
                act_trends = []
                for act_id, month_map in act_monthly.items():
                    m_sorted = sorted(month_map.keys())
                    vals = [month_map[m] for m in m_sorted]
                    total_act = sum(vals)
                    act_slope = _ols_slope(vals) if len(vals) >= 2 else 0.0
                    direction = "rising" if act_slope > 0.5 else ("falling" if act_slope < -0.5 else "stable")
                    act_trends.append({
                        "act": act_id,
                        "total_charges": total_act,
                        "monthly_counts": vals,
                        "months": m_sorted,
                        "trend_slope": round(act_slope, 2),
                        "direction": direction,
                    })
                act_trends.sort(key=lambda x: x["total_charges"], reverse=True)
                act_based_trends = act_trends[:10]  # top 10 acts
        except Exception as act_err:
            print(f"ActSectionAssociation trend lookup skipped: {act_err}")

        response_data = {
            "months": sorted_months,
            "counts": counts,
            "crimeHeadId": crime_head_id or "all",
            "totalCases": total_cases,
            "analytics": analytics
        }
        if act_based_trends is not None:
            response_data["act_based_trends"] = act_based_trends

        basicio.write(json.dumps({
            "success": True,
            "data": response_data
        }))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()
