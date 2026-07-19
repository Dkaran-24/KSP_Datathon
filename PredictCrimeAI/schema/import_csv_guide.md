# PredictCrimeAI — CSV Import Guide for Zoho Catalyst Datastore

This guide walks you through importing the seven new CSV files into your Zoho
Catalyst datastore so the enhanced backend functions can use the real Karnataka
police data.

## Prerequisites

1. Install the Catalyst CLI:
   ```bash
   npm install -g catalyst-cli
   ```
2. Log in and link your project:
   ```bash
   catalyst login
   cd PredictCrimeAI
   catalyst link
   ```
3. Place the CSV files in a folder, e.g. `./csv/`:
   ```
   csv/ActSectionAssociation.csv
   csv/ComplainantDetails.csv
   csv/Employee.csv
   csv/GravityOffence.csv
   csv/Rank.csv
   csv/ReligionMaster.csv
   csv/State.csv
   ```

## Option A — Create tables via Catalyst Console (recommended for first import)

1. Go to **Catalyst Console → Data Store → Tables → Create Table**.
2. Create each table with the columns listed in `datastore_tables.md`.
3. Use **Import Data → CSV** to bulk-load each file.
4. Map columns carefully — set `CaseMasterID`, `ActID` etc. as **String** (not int)
   to match the existing CaseMaster business keys.

## Option B — Create tables via CLI / ZCQL DDL

```sql
-- Lookup tables (small)
CREATE TABLE GravityOffence (
    GravityOffenceID INT PRIMARY KEY,
    LookupValue VARCHAR(50)
);

CREATE TABLE Rank (
    RankID INT PRIMARY KEY,
    RankName VARCHAR(100),
    Hierarchy INT,
    Active BOOLEAN
);

CREATE TABLE ReligionMaster (
    ReligionID INT PRIMARY KEY,
    ReligionName VARCHAR(50)
);

CREATE TABLE State (
    StateID INT PRIMARY KEY,
    StateName VARCHAR(50),
    NationalityID INT,
    Active INT
);

-- Large tables
CREATE TABLE ActSectionAssociation (
    CaseMasterID VARCHAR(50),
    ActID VARCHAR(50),
    SectionID VARCHAR(50),
    ActOrderID INT,
    SectionOrderID INT
);

CREATE TABLE ComplainantDetails (
    ComplainantID INT PRIMARY KEY,
    CaseMasterID VARCHAR(50),
    ComplainantName VARCHAR(200),
    AgeYear INT,
    OccupationID INT,
    ReligionID INT,
    CasteID INT,
    GenderID INT
);

CREATE TABLE Employee (
    EmployeeID INT PRIMARY KEY,
    DistrictID INT,
    UnitID INT,
    RankID INT,
    DesignationID INT,
    KGID VARCHAR(50),
    FirstName VARCHAR(100),
    EmployeeDOB DATE,
    GenderID INT,
    BloodGroupID INT,
    PhysicallyChallenged INT,
    AppointmentDate DATE
);
```

## Importing the CSV data

After the tables are created, import each CSV via the console's **Import Data**
feature, or use the CLI bulk-import if available in your Catalyst edition.

> **Important:** The CSV files are large (ActSectionAssociation is ~50 MB / 2.38M
> rows). Import in chunks or use Catalyst's bulk-import endpoint. The console
> import wizard handles up to ~1M rows per job; split the large files if needed.

### CSV pre-processing tips

The CSV files are already clean (quoted fields, standard headers). If you need to
split the large files:

```bash
# Split ActSectionAssociation into 500k-row chunks (keep header)
head -1 ActSectionAssociation.csv > ActSectionAssociation_part1.csv
tail -n +2 ActSectionAssociation.csv | head -500000 >> ActSectionAssociation_part1.csv
# repeat for subsequent parts with offset
```

## Verifying the import

Run these ZCQL queries from the console query editor:

```sql
-- Row counts
SELECT COUNT(*) FROM ActSectionAssociation;   -- expect ~2,381,520
SELECT COUNT(*) FROM ComplainantDetails;      -- expect ~1,099,557
SELECT COUNT(*) FROM Employee;                -- expect ~50,001
SELECT COUNT(*) FROM GravityOffence;          -- expect 2
SELECT COUNT(*) FROM Rank;                    -- expect 10
SELECT COUNT(*) FROM ReligionMaster;          -- expect 7
SELECT COUNT(*) FROM State;                   -- expect 1

-- Spot-check joins
SELECT TOP 5 CaseMasterID, ActID, SectionID FROM ActSectionAssociation;
SELECT TOP 5 DistrictID, COUNT(*) AS personnel
  FROM Employee GROUP BY DistrictID ORDER BY personnel DESC;
```

## After import

1. Deploy the updated functions:
   ```bash
   catalyst deploy --functions
   ```
2. Open the **Diagnostic** page in the client and verify all 8 endpoints respond.
3. The new intelligence fields (act breakdown, police strength, heinous ratio,
   complainant demographics, resource gap, etc.) will now populate automatically.

If you deploy the functions **before** the tables are imported, the functions still
work — they gracefully skip the new-table queries and return the original response
shape. The new fields simply appear once the tables are available.
