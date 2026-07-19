import zcatalyst_sdk
import json
import hashlib
import re
from collections import defaultdict

def normalize_id(val):
    if val is None: return ""
    try: return str(int(float(val)))
    except: return str(val).strip()

def normalize_name(name):
    if not name: return ""
    return " ".join(str(name).strip().split()).lower()

def name_to_node_id(name_key):
    return "s_" + hashlib.md5(name_key.encode("utf-8")).hexdigest()[:12]

def escape_sql(val):
    return str(val).replace("'", "''")

KSP_CRIME_HEADS = {
    "1":"Crimes Against Body","2":"Theft & Burglary",
    "3":"Crimes Against Women/Children","4":"Cybercrime",
    "5":"White Collar Crime","6":"Narcotics & Drugs",
    "7":"Arson & Property Damage","8":"Economic Offenses",
    "9":"Other IPC Crimes","10":"Special & Local Laws"
}

ALIASES = ["'The Hand'","'The Chemist'","'The Muscle'","'The Pen'",
           "'The Shadow'","'The Actor'","'The Phantom'","'The Cipher'"]

def compute_risk(case_count, mo_div, dist_count, assoc_count):
    return round(
        min(1.0, case_count/30)*40 +
        min(1.0, mo_div/5)*20 +
        min(1.0, dist_count/4)*20 +
        min(1.0, assoc_count/10)*20
    )

def risk_label(score):
    return "CRITICAL" if score>=75 else "HIGH" if score>=55 else "MODERATE" if score>=35 else "LOW"

def handler(context, basicio):
    app  = zcatalyst_sdk.initialize()
    zcql = app.zcql()

    min_cases          = int(basicio.get_argument('min_cases') or 2)
    limit              = int(basicio.get_argument('limit') or 50)
    district_filter_id = basicio.get_argument('district_id') or None
    search_name        = (basicio.get_argument('search_name') or '').strip()
    crime_head_filter  = basicio.get_argument('crime_head_id') or None

    try:
        # 1. District map
        dist_rows = zcql.execute_query(
            "SELECT DistrictID, DistrictName FROM District WHERE StateID = 1 LIMIT 300"
        )
        district_map = {
            normalize_id(r['District']['DistrictID']): r['District']['DistrictName']
            for r in dist_rows
        }

        # 2. Unit → District (page with ROWID)
        unit_to_district = {}
        unit_names = {}
        last_uid = 0
        while True:
            u_rows = zcql.execute_query(
                f"SELECT UnitID, UnitName, DistrictID, ROWID FROM Unit "
                f"WHERE ROWID > {last_uid} ORDER BY ROWID LIMIT 300"
            )
            if not u_rows: break
            for r in u_rows:
                u = r.get('Unit', r)
                uid = normalize_id(u.get('UnitID'))
                unit_to_district[uid] = normalize_id(u.get('DistrictID'))
                unit_names[uid] = u.get('UnitName', f'PS {uid}')
            last_uid = int(normalize_id(u_rows[-1].get('Unit', u_rows[-1]).get('ROWID', 0)))
            if len(u_rows) < 300: break

        # 3. Crime heads
        try:
            head_rows = zcql.execute_query("SELECT CrimeHeadID, CrimeGroupName FROM CrimeHead LIMIT 300")
            head_map  = {normalize_id(r['CrimeHead']['CrimeHeadID']): r['CrimeHead']['CrimeGroupName'] for r in head_rows}
        except:
            head_map = {}
        for k, v in KSP_CRIME_HEADS.items():
            head_map.setdefault(k, v)

        # ── DECIDE PATH: search / district-filter / state-wide ──
        if search_name:
            # Search by name using LIKE
            safe = escape_sql(search_name)
            acc_rows = zcql.execute_query(
                f"SELECT AccusedName, COUNT(AccusedMasterID) FROM Accused "
                f"WHERE AccusedName LIKE '*{safe}*' GROUP BY AccusedName LIMIT 300"
            )
            min_cases = 1
            repeats = []
            for r in acc_rows:
                a    = r.get('Accused', r)
                name = a.get('AccusedName', '')
                cnt  = 0
                for k, v in a.items():
                    if 'count' in str(k).lower():
                        try: cnt = int(v)
                        except: pass
                if name and cnt >= min_cases:
                    repeats.append((name, cnt))

        elif district_filter_id:
            # ── DISTRICT FILTER PATH: find suspects with cases here ──
            norm_filter = normalize_id(district_filter_id)
            filtered_unit_ids = [
                uid for uid, did in unit_to_district.items()
                if did == norm_filter
            ]
            if not filtered_unit_ids:
                repeats = []
            else:
                # Get CaseMasterIDs for those units (paginated)
                all_case_ids = set()
                last_rowid = 0
                unit_cond = " OR ".join(
                    f"PoliceStationID = '{escape_sql(uid)}'"
                    for uid in filtered_unit_ids[:50]
                )
                while True:
                    try:
                        c_rows = zcql.execute_query(
                            f"SELECT ROWID, CaseMasterID FROM CaseMaster "
                            f"WHERE ({unit_cond}) AND ROWID > {last_rowid} "
                            f"ORDER BY ROWID LIMIT 500"
                        )
                        if not c_rows: break
                        for r in c_rows:
                            c   = r.get('CaseMaster', r)
                            cid = normalize_id(c.get('CaseMasterID'))
                            if cid:
                                all_case_ids.add(cid)
                        last_rowid = int(normalize_id(
                            c_rows[-1].get('CaseMaster', c_rows[-1]).get('ROWID', 0)
                        ))
                        if len(c_rows) < 500: break
                    except:
                        break

                # Get Accused names for those cases (batched)
                accused_counts = {}
                case_id_list = list(all_case_ids)
                for i in range(0, len(case_id_list), 100):
                    batch = case_id_list[i:i+100]
                    ids_str = ",".join(f"'{c}'" for c in batch)
                    try:
                        a_rows = zcql.execute_query(
                            f"SELECT AccusedName, COUNT(AccusedMasterID) FROM Accused "
                            f"WHERE CaseMasterID IN ({ids_str}) "
                            f"GROUP BY AccusedName LIMIT 500"
                        )
                        for r in a_rows:
                            a    = r.get('Accused', r)
                            name = a.get('AccusedName', '')
                            cnt  = 0
                            for k, v in a.items():
                                if 'count' in str(k).lower():
                                    try: cnt = int(v)
                                    except: pass
                            if name:
                                accused_counts[name] = accused_counts.get(name, 0) + cnt
                    except:
                        pass

                repeats = [
                    (name, cnt) for name, cnt in accused_counts.items()
                    if cnt >= min_cases
                ]
            repeats.sort(key=lambda x: -x[1])
            repeats = repeats[:limit]

        else:
            # ── DEFAULT PATH: top repeat offenders state-wide ──
            acc_rows = zcql.execute_query(
                "SELECT AccusedName, COUNT(AccusedMasterID) FROM Accused "
                "GROUP BY AccusedName LIMIT 300"
            )
            repeats = []
            for r in acc_rows:
                a    = r.get('Accused', r)
                name = a.get('AccusedName', '')
                cnt  = 0
                for k, v in a.items():
                    if 'count' in str(k).lower():
                        try: cnt = int(v)
                        except: pass
                if name and cnt >= min_cases:
                    repeats.append((name, cnt))
            repeats.sort(key=lambda x: -x[1])
            repeats = repeats[:limit]

        if not repeats:
            basicio.write(json.dumps({
                "success": True, "nodes": [], "edges": [],
                "message": f"No suspects with {min_cases}+ cases found.",
                "districts": [{"id":k,"name":v} for k,v in sorted(district_map.items(), key=lambda x:x[1])],
                "crime_heads": [{"id":k,"name":v} for k,v in sorted(head_map.items(), key=lambda x:int(x[0]) if x[0].isdigit() else 999)]
            }))
            context.close()
            return

        # 4. For each repeat offender, fetch their cases (max 5 per suspect)
        nodes, edges = [], []
        seen_districts, seen_edges = set(), set()

        for display_name, case_count in repeats:
            safe_name = escape_sql(display_name)
            name_key  = normalize_name(display_name)
            node_id   = name_to_node_id(name_key)

            # Fetch case IDs — increase when district filter is active for better coverage
            try:
                acc_limit = 50 if district_filter_id else 5
                acc_detail = zcql.execute_query(
                    f"SELECT CaseMasterID FROM Accused "
                    f"WHERE AccusedName = '{safe_name}' LIMIT {acc_limit}"
                )
                case_ids = [
                    normalize_id(r.get('Accused', r).get('CaseMasterID'))
                    for r in acc_detail if r.get('Accused', r).get('CaseMasterID')
                ]
            except:
                case_ids = []

            # Fetch CaseMaster details for these cases
            mo_set   = set()
            dist_set = set()
            history  = []

            if case_ids:
                # Increase per-suspect sample when filtering for better district coverage
                case_sample_limit = 50 if district_filter_id else 5
                ids_str = ",".join(f"'{c}'" for c in case_ids[:case_sample_limit])
                try:
                    c_rows = zcql.execute_query(
                        f"SELECT CaseMasterID, PoliceStationID, CrimeMajorHeadID, CrimeRegisteredDate "
                        f"FROM CaseMaster WHERE CaseMasterID IN ({ids_str}) "
                        f"ORDER BY CrimeRegisteredDate DESC LIMIT {case_sample_limit}"
                    )
                    for cr in c_rows:
                        c   = cr.get('CaseMaster', cr)
                        cid = normalize_id(c.get('CaseMasterID'))
                        sid = normalize_id(c.get('PoliceStationID'))
                        hid = normalize_id(c.get('CrimeMajorHeadID'))
                        did = unit_to_district.get(sid)
                        mo  = head_map.get(hid, KSP_CRIME_HEADS.get(hid, "Unknown"))
                        date_str = str(c.get('CrimeRegisteredDate', '') or '').split(' ')[0]

                        # Apply filters FIRST before adding to sets
                        if district_filter_id and did != normalize_id(district_filter_id):
                            continue
                        if crime_head_filter and hid != normalize_id(crime_head_filter):
                            continue

                        # Now add to stats (only filter-passing cases contribute)
                        mo_set.add(mo)
                        if did and did in district_map:
                            dist_set.add(did)

                        history.append({
                            "id":   f"INC-{cid}",
                            "type": mo,
                            "date": date_str,
                            "loc":  unit_names.get(sid, f"PS {sid}")
                        })
                except:
                    pass

            # Apply district filter at suspect level
            if district_filter_id:
                if normalize_id(district_filter_id) not in dist_set:
                    continue

            alias_idx = int(hashlib.md5(name_key.encode()).hexdigest(), 16) % len(ALIASES)
            risk      = compute_risk(case_count, len(mo_set), len(dist_set), 0)

            nodes.append({
                "id":         node_id,
                "type":       "suspect",
                "label":      display_name,
                "alias":      ALIASES[alias_idx],
                "uid":        f"8871-KSP-{name_key[:6].upper().replace(' ','')}",
                "case_count": case_count,
                "mo":         sorted(mo_set),
                "history":    history,
                "summary":    f"Repeat offender with {case_count} cases across {len(dist_set)} district(s). MO: {', '.join(sorted(mo_set)) or 'Various'}.",
                "suspect_risk_score": risk,
                "risk_level": risk_label(risk),
                "cross_district": len(dist_set) > 1,
                "operating_districts": [district_map[d] for d in dist_set if d in district_map]
            })

            for did in dist_set:
                seen_districts.add(did)
                ekey = (node_id, f"d_{did}")
                if ekey not in seen_edges:
                    seen_edges.add(ekey)
                    edges.append({"from": node_id, "to": f"d_{did}", "type": "location"})

        # Add district nodes
        for did in seen_districts:
            nodes.append({"id": f"d_{did}", "type": "district", "label": district_map[did]})

        basicio.write(json.dumps({
            "success": True,
            "nodes":   nodes,
            "edges":   edges,
            "network_stats": {
                "total_suspects":    sum(1 for n in nodes if n["type"]=="suspect"),
                "cross_district_suspects": sum(1 for n in nodes if n.get("cross_district")),
                "high_risk_suspects": sum(1 for n in nodes if n.get("risk_level") in ("CRITICAL","HIGH")),
                "total_firs": sum(n.get("case_count",0) for n in nodes if n["type"]=="suspect")
            },
            "districts":   [{"id":k,"name":v} for k,v in sorted(district_map.items(), key=lambda x:x[1])],
            "crime_heads": [{"id":k,"name":v} for k,v in sorted(head_map.items(), key=lambda x:int(x[0]) if x[0].isdigit() else 999)]
        }))
    except Exception as e:
        basicio.write(json.dumps({"success": False, "error": str(e)}))
    context.close()