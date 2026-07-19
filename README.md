# KSP Datathon — PredictCrimeAI

**CrimePredictAI** (aka *KSP Strategic Intelligence Hub*) is a crime analytics and
forecasting platform built on **Zoho Catalyst**, developed for the Karnataka State
Police (KSP) Datathon. It combines historical FIR/case data with socio-economic and
personnel datasets to surface crime trends, hotspots, anomalies, and predictive risk
scores for districts across Karnataka.

## Features

- **District Hotspot Map** — geographic view of crime concentration by district
- **Predictive Risk Forecasting** — next-month crime risk per district/category using
  a fitted seasonal regression model (trend + seasonality decomposition)
- **Historical Trends Analysis** — long-term crime trend visualization
- **Category Breakdown Analysis** — crime type distribution and shifts
- **Anomaly & Emerging Trend Alerts** — statistical detection of unusual spikes
- **Hidden Correlations** — relationships between crime patterns and other factors
- **Socio-Economic Correlation** — overlays demographic/economic data on crime data
- **Relational Network & Link Analysis** — case/entity relationship graphs
- **API Diagnostic Tool** — internal tool for testing backend endpoints

## Architecture

- **Backend:** Python serverless functions on Zoho Catalyst (`/functions`)
- **Frontend:** Static HTML/CSS/JS client (`/client`)
- **Data layer:** Zoho Catalyst Datastore — see [`schema/datastore_tables.md`](./PredictCrimeAI/schema/datastore_tables.md)
  for the full table reference (CaseMaster, District, Unit, Accused, Victim,
  CrimeHead, plus imported tables: ActSectionAssociation, ComplainantDetails,
  Employee, GravityOffence, Rank, ReligionMaster, State)

All backend functions degrade gracefully — if an optional imported table isn't
present yet, the function falls back to its base response shape instead of failing.

### Functions

| Function | Purpose |
|---|---|
| `getCrimeTrends` | Historical trend analytics |
| `getCrimeCategoryBreakdown` | Crime category distribution |
| `getDistrictCrimeStats` | Per-district statistics |
| `detectAnomalies` | Spike/anomaly detection |
| `getNetworkData` | Entity relationship/network data |
| `predictCrimeRisk` | ML-driven risk forecasting |
| `getHiddenCorrelations` | Correlation discovery |
| `getSocioEconomicCorrelation` | Socio-economic overlay analysis |

## Getting Started

1. Install the Catalyst CLI:
```bash
   npm install -g catalyst-cli
```
2. Log in and link the project:
```bash
   catalyst login
   cd PredictCrimeAI
   catalyst link
```
3. Import the required datastore tables — see
   [`schema/import_csv_guide.md`](./PredictCrimeAI/schema/import_csv_guide.md).
4. Deploy:
```bash
   catalyst deploy
```

## Data Sources

Karnataka Police case data (CaseMaster, Accused, Victim), along with imported
datasets covering legal act/section associations, complainant demographics,
police personnel deployment, and offence gravity classification.

## Project Structure

```
PredictCrimeAI/
├── client/              # Frontend dashboards (HTML/CSS/JS)
├── functions/           # Python serverless backend functions
├── schema/              # Datastore table reference & CSV import guide
└── catalyst.json        # Catalyst project config
```
