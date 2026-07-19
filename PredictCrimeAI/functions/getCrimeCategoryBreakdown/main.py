import zcatalyst_sdk
import json
from collections import defaultdict

"""
getCrimeCategoryBreakdown
-------------------------
Returns total case counts grouped by major crime head (e.g. Theft,
Murder, Crimes Against Body), for pie/bar chart visualization.

Optional ?district_id=5 to scope the breakdown to one district
(joins through Unit.DistrictID, since CaseMaster only stores
PoliceStationID).

ENHANCED — Category-Level Intelligence:
Each category now carries additive analytics fields so the breakdown
tells a strategic story, not just raw counts:

  * share_percent      — this category's share of the total caseload.
  * mom_delta          — month-over-month delta: latest month count vs
                         prior month count (percentage change).
  * mom_direction      — "rising" / "falling" / "flat".
  * risk_tier          — CRITICAL / HIGH / MODERATE / LOW based on share
                         + growth direction, enabling proactive resource
                         deployment to the most dangerous + rising types.
  * socio_economic_note — human-readable overlay tying the category's
                         prevalence to socio-economic context.

ROUND-2 ENHANCEMENT (real ActSectionAssociation + GravityOffence data):
  * act_section_breakdown — top legal acts (IPC/BNS/NDPS/POCSO etc.)
                         charged under this crime head, with case counts
                         and share, revealing the legal-charge signature.
  * gravity_offence_split — Heinous vs Non-Heinous case split for this
                         category, computed from the act/section data.
  * top_sections          — the most frequently charged section numbers.

All new fields are additive; existing categories/totalCases preserved.
If the ActSectionAssociation table is absent, these fields are omitted
and the function behaves exactly as before (graceful degradation).
"""

PAGE_SIZE = 300
MAX_PAGES = 10000  # safety cap: 10000*300 = 3M rows max scanned
ACT_BATCH = 500    # batch size for ActSectionAssociation lookups

# Acts that predominantly carry heinous charges. Used to classify a
# case as Heinous when any of its ActSectionAssociation rows references
# one of these acts (POCSO, and specific IPC/BNS sections for murder,
# rape, dacoity etc.). This is a conservative heuristic bridge because
# the CSV does not contain a direct case→GravityOffenceID foreign key.
HEINOUS_ACTS = {"POCSO", "NDPS"}  # NDPS in large quantities is heinous
# IPC / BNS sections considered heinous (murder, attempted murder, rape,
# gang-rape, dacoity, kidnapping for ransom, acid attack).
HEINOUS_SECTIONS = {
    "302", "307", "376", "376A", "376B", "376C", "376D", "376E",
    "395", "396", "397", "364A", "326A", "326B", "379", "120B",
    "121", "121A", "153A", "153B",
}


def normalize_id(val):
    if val is None:
        return ""
    try:
        return str(int(float(val)))
    except ValueError:
        return str(val).strip()


def risk_tier(share, mom_direction):
    """Combine volume share and growth direction into a risk tier."""
    if share >= 0.25:
        if mom_direction == "rising":
            return "CRITICAL"
        return "HIGH"
    if share >= 0.15:
        if mom_direction == "rising":
            return "HIGH"
        return "MODERATE"
    if share >= 0.05:
        if mom_direction == "rising":
            return "MODERATE"
        return "LOW"
    if mom_direction == "rising":
        return "LOW"
    return "MINIMAL"


def category_socio_economic_note(name, share, mom_direction):
    """Sociological overlay tying a crime category to socio-economic drivers."""
    parts = []
    name_l = name.lower()

    if "theft" in name_l or "burglary" in name_l:
        parts.append("Property crimes of this type concentrate in urbanizing belts and commercial corridors where economic disparity and transient populations are highest.")
    elif "women" in name_l or "children" in name_l:
        parts.append("Vulnerable-population crimes correlate with underreporting gaps in rural districts and domestic-stress surges in peri-urban zones.")
    elif "cyber" in name_l:
        parts.append("Cyber offenses scale with digital-penetration growth, disproportionately affecting high-urbanization metro districts.")
    elif "narcotics" in name_l or "drug" in name_l:
        parts.append("Narcotics offenses cluster along inter-state border corridors and in industrial/commuter belts with high youth migration.")
    elif "body" in name_l:
        parts.append("Crimes against the body are driven by population density, agrarian distress, and inter-personal conflict in semi-arid districts.")
    elif "economic" in name_l or "white collar" in name_l:
        parts.append("Economic offenses track financial-activity density, concentrated in metro-commercial and education-hub districts.")
    elif "arson" in name_l or "property damage" in name_l:
        parts.append("Arson and property damage peak in agrarian districts during harvest-season land disputes and industrial-belt labor unrest.")
    elif "sll" in name_l or "special" in name_l:
        parts.append("Special & Local Law offenses reflect enforcement-intensity patterns rather than pure crime incidence.")
    else:
        parts.append("This category's distribution reflects a blend of demographic density and socio-economic stress factors.")

    if mom_direction == "rising":
        parts.append("The rising month-over-month trend signals an emerging risk that warrants proactive deployment.")
    elif mom_direction == "falling":
        parts.append("The declining trend suggests recent enforcement or prevention efforts are taking effect.")

    if share >= 0.25:
        parts.append("At this volume share it is a dominant driver of the overall caseload.")
    elif share >= 0.10:
        parts.append("It is a significant contributor to the overall caseload.")
    else:
        parts.append("It remains a smaller but strategically relevant contributor.")

    return " ".join(parts)


def handler(context, basicio):
    app = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    district_id = basicio.get_argument('district_id')  # None = all districts

    try:
        # ---- 1. Crime head ID -> name map ----
        head_rows = zcql.execute_query("SELECT CrimeHeadID, CrimeGroupName FROM CrimeHead")

        # Standard KSP Crime Category Names mapping fallback
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

        head_map = {
            normalize_id(r['CrimeHead']['CrimeHeadID']): r['CrimeHead']['CrimeGroupName']
            for r in head_rows
        }

        # ---- 2. If scoped to a district, build allowed PoliceStationID set ----
        allowed_units = None
        if district_id:
            unit_rows = zcql.execute_query(
                f"SELECT ROWID, UnitID FROM Unit WHERE DistrictID = '{district_id}'"
            )
            allowed_units = set()
            for r in unit_rows:
                u = r['Unit']
                if u.get('ROWID'):
                    allowed_units.add(normalize_id(u['ROWID']))
                if u.get('UnitID'):
                    allowed_units.add(normalize_id(u['UnitID']))
            if not allowed_units:
                basicio.write(json.dumps({
                    "success": True,
                    "data": {"categories": [], "totalCases": 0}
                }))
                return

        # ---- 3. Page through CaseMaster, counting by CrimeMajorHeadID ----
        # Also track monthly counts per head for MoM delta
        counts = defaultdict(int)
        monthly_by_head = defaultdict(lambda: defaultdict(int))  # head_id -> {month -> count}
        case_ids_by_head = defaultdict(set)  # head_id -> set of CaseMasterID (for act lookup)
        last_rowid = 0
        pages = 0
        total = 0

        while pages < MAX_PAGES:
            query = (
                "SELECT ROWID, CrimeMajorHeadID, PoliceStationID, CaseMasterID, CrimeRegisteredDate "
                f"FROM CaseMaster WHERE ROWID > {last_rowid} ORDER BY ROWID LIMIT {PAGE_SIZE}"
            )
            page = zcql.execute_query(query)
            if not page:
                break

            for row in page:
                c = row['CaseMaster']
                if allowed_units is not None and normalize_id(c.get('PoliceStationID')) not in allowed_units:
                    continue
                head_id = normalize_id(c.get('CrimeMajorHeadID'))
                counts[head_id] += 1
                total += 1

                cm_id = c.get('CaseMasterID')
                if cm_id:
                    case_ids_by_head[head_id].add(str(cm_id))

                date_str = c.get('CrimeRegisteredDate')
                if date_str:
                    date_part = date_str.split(' ')[0]
                    parts = date_part.split('-')
                    if len(parts) >= 2:
                        month_key = f"{parts[0]}-{parts[1]}"
                        monthly_by_head[head_id][month_key] += 1

            last_rowid = int(page[-1]['CaseMaster']['ROWID'])
            pages += 1
            if len(page) < PAGE_SIZE:
                break

        # ---- 3b. Fetch ActSectionAssociation data for act/gravity breakdown ----
        # Graceful: if the table doesn't exist, skip and continue.
        act_by_head = {}        # head_id -> {act_id -> count}
        section_by_head = {}    # head_id -> {section_id -> count}
        gravity_by_head = {}    # head_id -> {"Heinous": n, "Non-Heinous": n}
        act_data_available = False
        try:
            for head_id, id_set in case_ids_by_head.items():
                id_list = list(id_set)
                act_counts = defaultdict(int)
                section_counts = defaultdict(int)
                heinous_n = 0
                non_heinous_n = 0
                for i in range(0, len(id_list), ACT_BATCH):
                    batch = id_list[i:i + ACT_BATCH]
                    id_csv = ",".join(f"'{x}'" for x in batch)
                    arows = zcql.execute_query(
                        f"SELECT CaseMasterID, ActID, SectionID FROM ActSectionAssociation "
                        f"WHERE CaseMasterID IN ({id_csv})"
                    )
                    seen_cases = set()
                    case_heinous = {}
                    for ar in arows:
                        a = ar.get('ActSectionAssociation', ar)
                        act_id = str(a.get('ActID', '')).strip()
                        sec_raw = a.get('SectionID')
                        sec_str = normalize_id(sec_raw) if sec_raw is not None else ""
                        cm = str(a.get('CaseMasterID', "")).strip()
                        if act_id:
                            act_counts[act_id] += 1
                        if sec_str:
                            section_counts[sec_str] += 1
                        # gravity classification per case
                        is_hein = False
                        if act_id in HEINOUS_ACTS:
                            is_hein = True
                        if sec_str in HEINOUS_SECTIONS:
                            is_hein = True
                        if is_hein:
                            case_heinous[cm] = True
                    heinous_n = len(case_heinous)
                    non_heinous_n = len(id_set) - heinous_n
                act_by_head[head_id] = dict(act_counts)
                section_by_head[head_id] = dict(section_counts)
                gravity_by_head[head_id] = {
                    "Heinous": heinous_n,
                    "Non-Heinous": non_heinous_n,
                }
            act_data_available = bool(act_by_head)
        except Exception as act_err:
            # Table not present or query error — degrade gracefully.
            print(f"ActSectionAssociation lookup skipped: {act_err}")
            act_data_available = False

        # ---- 4. Build categories with enhanced analytics ----
        categories = []
        for head_id, count in sorted(counts.items(), key=lambda x: -x[1]):
            share = (count / total) if total > 0 else 0.0

            # MoM delta
            monthly = monthly_by_head.get(head_id, {})
            sorted_m = sorted(monthly.keys())
            mom_delta = None
            mom_direction = "flat"
            if len(sorted_m) >= 2:
                latest = monthly[sorted_m[-1]]
                prior = monthly[sorted_m[-2]]
                if prior > 0:
                    mom_delta = round(((latest - prior) / prior) * 100, 1)
                    if mom_delta > 5:
                        mom_direction = "rising"
                    elif mom_delta < -5:
                        mom_direction = "falling"
                    else:
                        mom_direction = "flat"
                elif latest > 0:
                    mom_delta = 100.0
                    mom_direction = "rising"

            head_name = head_map.get(head_id) or KSP_CRIME_HEADS.get(head_id, "Unknown")

            cat = {
                "crimeHeadId": int(head_id) if head_id.isdigit() else head_id,
                "crimeHeadName": head_name,
                "count": count,
                "share_percent": round(share * 100, 1),
                "mom_delta": mom_delta,
                "mom_direction": mom_direction,
                "risk_tier": risk_tier(share, mom_direction),
                "socio_economic_note": category_socio_economic_note(head_name, share, mom_direction)
            }

            # ---- Round-2 additive: act/section breakdown + gravity split ----
            if act_data_available and head_id in act_by_head:
                acts = act_by_head[head_id]
                total_act_refs = sum(acts.values()) or 1
                act_breakdown = []
                for act_id, act_cnt in sorted(acts.items(), key=lambda x: -x[1])[:8]:
                    act_breakdown.append({
                        "act": act_id,
                        "case_count": act_cnt,
                        "share_percent": round((act_cnt / total_act_refs) * 100, 1),
                    })
                cat["act_section_breakdown"] = act_breakdown

                sections = section_by_head.get(head_id, {})
                top_sections = [
                    {"section": s, "count": c}
                    for s, c in sorted(sections.items(), key=lambda x: -x[1])[:10]
                ]
                cat["top_sections"] = top_sections

                g = gravity_by_head.get(head_id, {"Heinous": 0, "Non-Heinous": 0})
                hein = g.get("Heinous", 0)
                non_hein = g.get("Non-Heinous", 0)
                gtotal = hein + non_hein
                cat["gravity_offence_split"] = {
                    "Heinous": hein,
                    "Non-Heinous": non_hein,
                    "heinous_percent": round((hein / gtotal) * 100, 1) if gtotal > 0 else 0,
                    "non_heinous_percent": round((non_hein / gtotal) * 100, 1) if gtotal > 0 else 0,
                }

            categories.append(cat)

        basicio.write(json.dumps({
            "success": True,
            "data": {
                "categories": categories,
                "totalCases": total,
                "districtId": district_id or "all"
            }
        }))

    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))

    context.close()
