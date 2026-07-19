# PredictCrimeAI — Catalyst Datastore Table Reference

This document describes every datastore table the PredictCrimeAI platform relies on,
grouped into **Existing Tables** (already in the project) and **New Tables** (imported
from the user-supplied CSV files). Every backend function is written to be
backward-compatible: if a new table is absent from your Catalyst project the function
degrades gracefully and returns the original response shape, so you can import the
new tables at your own pace.

---

## 1. Existing Tables (already referenced by the functions)

### 1.1 CaseMaster
The central case ledger. One row = one registered FIR/case.

| Column | Type | Notes |
|---|---|---|
| ROWID | Auto (bigint) | Catalyst auto-generated primary key, used for pagination |
| CaseMasterID | string | Business key; foreign-key target for Accused, Victim, ComplainantDetails, ActSectionAssociation |
| PoliceStationID | string | FK → Unit.ROWID (or Unit.UnitID) — the registering station |
| CrimeMajorHeadID | string | FK → CrimeHead.CrimeHeadID — the broad crime category |
| CrimeRegisteredDate | datetime | Registration timestamp; used for trend / shift / spike analytics |

### 1.2 District
| Column | Type | Notes |
|---|---|---|
| DistrictID | string | Primary key (1–31 for Karnataka) |
| DistrictName | string | e.g. "Bengaluru Urban" |
| StateID | int | FK → State.StateID (1 = Karnataka) |

### 1.3 Unit
Police-station / unit master.
| Column | Type | Notes |
|---|---|---|
| ROWID | Auto | Primary key for joins |
| UnitID | string | Business key |
| UnitName | string | Station name |
| DistrictID | string | FK → District.DistrictID |

### 1.4 Accused
One row per accused person per case.
| Column | Type | Notes |
|---|---|---|
| CaseMasterID | string | FK → CaseMaster |
| AccusedName / AgeYear / GenderID etc. | various | Demographics |

### 1.5 Victim
One row per victim per case.
| Column | Type | Notes |
|---|---|---|
| CaseMasterID | string | FK → CaseMaster |

### 1.6 CrimeHead
Crime category lookup.
| Column | Type | Notes |
|---|---|---|
| CrimeHeadID | string | Primary key (1–10) |
| CrimeGroupName | string | e.g. "Theft & Burglary", "Crimes Against Women/Children" |

---

## 2. New Tables (from user-supplied CSV files)

These tables are imported from the CSV files in the project root. The functions
detect their presence at runtime and only add the new intelligence fields when the
tables exist.

### 2.1 ActSectionAssociation  (source: ActSectionAssociation.csv, ~2.38M rows)
Maps each case to the legal acts and sections charged. One case can carry multiple
act-section pairs (avg ~2.4 charges per case).

| Column | Type | Sample | Notes |
|---|---|---|---|
| CaseMasterID | string | 1, 2, 3 … | FK → CaseMaster.CaseMasterID |
| ActID | string | IPC, BNS, NDPS, CrPC, POCSO, MV Act, IT Act, Excise Act, Arms Act, SC/ST Act | The legal act |
| SectionID | float→string | 37.0, 122.0, 302.0 … | Section number under the act |
| ActOrderID | int | 1, 2, 3 | Order of the act within the case |
| SectionOrderID | int | 1, 2, 3 | Order of the section within the act |

**Observed act distribution:**
IPC 860k, BNS 575k, NDPS 288k, CrPC 215k, POCSO 172k, MV Act 86k,
IT Act 57k, Excise Act 43k, Arms Act 43k, SC/ST Act 43k.

**Use in platform:** `act_section_breakdown` per crime category, `act_profile` per
suspect (MO signature), `act_based_trends` over time, heinous-offence classification.

### 2.2 ComplainantDetails  (source: ComplainantDetails.csv, ~1.1M rows)
Demographic profile of complainants. Some cases have multiple complainants.

| Column | Type | Sample | Notes |
|---|---|---|---|
| ComplainantID | int | 1, 2, 3 … | Primary key |
| CaseMasterID | string | 1, 2, 3 … | FK → CaseMaster |
| ComplainantName | string | "Ravi Sharma" | |
| AgeYear | int | 18–79 | Uniform distribution in the sample data |
| OccupationID | int | 1–20 | Occupation lookup (id-only; names not provided) |
| ReligionID | int | 1–7 | FK → ReligionMaster |
| CasteID | int | 1–8 | Caste category (id-only) |
| GenderID | int | 1=Male, 2=Female, 3=Other | |

**Observed:** Male 682k, Female 406k, Other 11k.
Religion: Hindu 858k, Muslim 143k, Christian 44k, Sikh 22k, Jain 11k, Buddhist 11k, Other 11k.

**Use in platform:** `complainant_demographics` overlay (gender ratio, religion mix,
age bands) in the socio-economic correlation function.

### 2.3 Employee  (source: Employee.csv, ~50K rows)
Karnataka police personnel roster — the **resource deployment** data set.

| Column | Type | Sample | Notes |
|---|---|---|---|
| EmployeeID | int | 1, 2, 3 … | Primary key |
| DistrictID | int | 1–31 | FK → District |
| UnitID | int | 190, 1608 … | FK → Unit (2000 unique units) |
| RankID | int | 1–10 | FK → Rank |
| DesignationID | int | 1–8 | Designation lookup (id-only) |
| KGID | string | "KGID00000001" | Karnataka Government ID |
| FirstName | string | "Farhan Pillai" | |
| EmployeeDOB | date | 1992-03-09 | |
| GenderID | int | 1=Male, 2=Female, 3=Other | |
| BloodGroupID | int | 1–8 | |
| PhysicallyChallenged | int | 0 / 1 | 48.5k = 0, 1.5k = 1 |
| AppointmentDate | date | 1985-01-01 … 2023-12-31 | Used for tenure/experience |

**Observed:** 31 districts (each ~1600–1700 personnel), 2000 units, 10 ranks evenly
distributed (~5000 each), Male 37.5k / Female 11.9k / Other 551.

**Use in platform:** `police_strength`, `officer_to_case_ratio`,
`personnel_rank_distribution`, `resource_gap_analysis`, deployment recommendations.

### 2.4 GravityOffence  (source: GravityOffence.csv, 2 rows)
Heinous vs Non-Heinous classification.

| Column | Type | Notes |
|---|---|---|
| GravityOffenceID | int | 1, 2 |
| LookupValue | string | "Heinous", "Non-Heinous" |

**Use in platform:** `gravity_offence_split` per crime category, `heinous_case_ratio`
per district in anomaly detection. (Note: the link from a case to its gravity
classification flows through ActSectionAssociation → specific act/section → gravity
mapping. The functions use a built-in heinous-act map for IPC/BNS/POCSO sections as
the bridge, since the CSV does not contain a direct case→gravity foreign key.)

### 2.5 Rank  (source: Rank.csv, 10 rows)
Police rank hierarchy.

| RankID | RankName | Hierarchy | Active |
|---|---|---|---|
| 1 | Constable | 10 | TRUE |
| 2 | Head Constable | 9 | TRUE |
| 3 | Asst Sub-Inspector | 8 | TRUE |
| 4 | Sub-Inspector | 7 | TRUE |
| 5 | Inspector | 6 | TRUE |
| 6 | Dy Superintendent of Police | 5 | TRUE |
| 7 | Superintendent of Police | 4 | TRUE |
| 8 | Dy Inspector General | 3 | TRUE |
| 9 | Inspector General | 2 | TRUE |
| 10 | Director General of Police | 1 | TRUE |

### 2.6 ReligionMaster  (source: ReligionMaster.csv, 7 rows)
| ReligionID | ReligionName |
|---|---|
| 1 | Hindu |
| 2 | Muslim |
| 3 | Christian |
| 4 | Sikh |
| 5 | Buddhist |
| 6 | Jain |
| 7 | Other |

### 2.7 State  (source: State.csv, 1 row)
| StateID | StateName | NationalityID | Active |
|---|---|---|---|
| 1 | Karnataka | 1 | 1 |

---

## 3. Entity-Relationship Summary

```
State (1) ──< District (31) ──< Unit (2000) ──< Employee (50K) >── Rank (10)
                  │                  │
                  │                  └──< CaseMaster >── Accused
                  │                         │      └── Victim
                  │                         ├──< ActSectionAssociation (2.38M)
                  │                         ├──< ComplainantDetails (1.1M)
                  │                         └──> CrimeHead (10)
                  │
                  └──> GravityOffence (2)  [via act/section heinous-map]
                  └──> ReligionMaster (7)  [via ComplainantDetails.ReligionID]
```

**Key join paths used by the functions:**
- CaseMaster.PoliceStationID → Unit.ROWID → Unit.DistrictID → District.DistrictName
- CaseMaster.CaseMajorHeadID → CrimeHead.CrimeHeadID → CrimeGroupName
- CaseMaster.CaseMasterID → ActSectionAssociation.CaseMasterID → ActID / SectionID
- CaseMaster.CaseMasterID → ComplainantDetails.CaseMasterID → demographics
- CaseMaster.CaseMasterID → Accused / Victim (counts)
- Employee.DistrictID → District.DistrictID (police strength per district)
- Employee.RankID → Rank.RankName (rank distribution)

---

## 4. Graceful Degradation

Every backend function wraps new-table ZCQL queries in try/except blocks. If a new
table has not yet been imported into the Catalyst project, the function logs a
warning and continues with the original (pre-Round-2) response. This means:

- You can deploy the updated functions **before** importing the new CSV tables.
- Once the tables are imported, the new intelligence fields appear automatically.
- No function ever fails because of a missing optional table.
