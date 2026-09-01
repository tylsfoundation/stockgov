# House of Representatives Filing Types and Sample Forms

## 1. Purpose

This catalog documents every `FilingType` value found in the U.S. House of Representatives Financial Disclosure XML index files for 2008 through 2026. It also identifies the meaning of each code, provides a locally downloaded official example, and recommends how the filing should be handled by the Congressional Trading Analytics Platform.

The inventory contains 41,881 XML entries across 19 annual index files and 13 distinct filing-type codes.

> In the House XML, `FilingType` is an XML element, not an attribute. Preserve the original value exactly as `filing_type_code_raw` during ingestion.

## 2. Filing-Type Summary

| Code | Normalized definition | XML entries | Official sample | Recommended processing |
|---|---|---:|---|---|
| A | Amendment Report | 3,686 | `A_amendment_2008_8139590.pdf` | Retain and associate with the filing it amends. An amendment can modify an annual disclosure, PTR, or another filing. |
| B | Blind Trust Filing | 25 | `B_blind_trust_2012_8208808.pdf` | Store as blind-trust documentation. This is not the House form named “Form B.” |
| C | Candidate Financial Disclosure Report | 10,289 | `C_candidate_report_2013_10000022.pdf` | Store and parse as a complete candidate financial disclosure report. |
| D | Candidate Under-$5,000 Threshold Declaration | 2,357 | `D_under_5000_declaration_2011_8207624.pdf` | Record the declaration and candidate status; it normally contains no transaction table. |
| E | Termination Report Exemption | 40 | `E_termination_exemption_2013_8212266.pdf` | Store the exemption reason and related office or reporting status. Do not confuse with an extension request. |
| G | Gift Disclosure Waiver Request | 56 | `G_gift_disclosure_waiver_2010_8212698.pdf` | Store as waiver correspondence or supporting disclosure material. |
| H | New Filer Report | 396 | `H_new_filer_report_2014_10007178.pdf` | Store and parse as a complete financial disclosure report. |
| O | Original Annual Financial Disclosure Report | 8,589 | `O_annual_report_2008_8135951.pdf` | Store and parse as the original annual report. |
| P | Periodic Transaction Report (PTR) | 8,354 | `P_periodic_transaction_report_2013_8214458.pdf` | Primary trade-bearing filing. Download from the PTR path and parse transaction rows. |
| R | PTR Reporting Waiver | 3 | `R_ptr_reporting_waiver_2012_8209407.pdf` | Store the waiver, affected asset, dates, and relationship to any relevant PTR. It is not itself a transaction report. |
| T | Termination Report | 630 | `T_termination_report_2009_8139728.pdf` | Store and parse as a complete termination financial disclosure report. |
| W | Candidate Withdrawal Notice | 1,143 | `W_candidate_withdrawal_2011_8206976.pdf` | Record withdrawal status and date; it normally contains no transaction table. |
| X | Financial Disclosure Extension Request | 6,313 | `X_extension_request_2011_8207032.pdf` | Store the requested or granted extension and revised deadline. |

## 3. Detailed Definitions and Examples

### 3.1 A — Amendment Report

An amendment corrects, clarifies, or supplements a previously submitted disclosure. The sample is a clarification of two items in an earlier annual financial disclosure. Because `A` does not identify the type of the original filing, the ingestion process should retain the amendment separately and attempt to link it to its parent filing using the filer, year, dates, document references, and document text.

- Sample DocID: `8139590`
- Sample year: `2008`
- Local file: `A_amendment_2008_8139590.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2008/8139590.pdf>

### 3.2 B — Blind Trust Filing

This category contains documents concerning a qualified blind trust or related trust action. The sample is an amendment involving the Eddie Bernice Johnson Qualified Revocable Blind Trust. Code `B` describes the filing category and should not be interpreted as a reference to the separately named House “Form B.”

- Sample DocID: `8208808`
- Sample year: `2012`
- Local file: `B_blind_trust_2012_8208808.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2012/8208808.pdf>

### 3.3 C — Candidate Financial Disclosure Report

A full financial disclosure filed by a candidate for the U.S. House. It may contain assets, liabilities, income, positions, agreements, gifts, travel reimbursements, and other reportable information. It is broader than a PTR and should be parsed using the full financial-disclosure schema.

- Sample DocID: `10000022`
- Sample year: `2013`
- Local file: `C_candidate_report_2013_10000022.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2013/10000022.pdf>

### 3.4 D — Candidate Under-$5,000 Threshold Declaration

This filing records a candidate's declaration that the campaign has not raised or spent more than the statutory threshold that triggers a full financial disclosure filing. It is one branch of the House candidate declaration form. Store it as candidate-status metadata rather than treating it as a financial or transaction report.

- Sample DocID: `8207624`
- Sample year: `2011`
- Local file: `D_under_5000_declaration_2011_8207624.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2011/8207624.pdf>

### 3.5 E — Termination Report Exemption

This category documents why a departing House filer is not required to submit a House termination report. The sample explains that the filer entered another covered reporting position. This differs from code `X`, which requests more time to file.

- Sample DocID: `8212266`
- Sample year: `2013`
- Local file: `E_termination_exemption_2013_8212266.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2013/8212266.pdf>

### 3.6 G — Gift Disclosure Waiver Request

A request concerning waiver of public gift-disclosure requirements. These are unusual supporting filings rather than annual reports or transaction reports. Preserve the document, request date, decision status when present, and any related filer or gift information.

- Sample DocID: `8212698`
- Sample year: `2010`
- Local file: `G_gift_disclosure_waiver_2010_8212698.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2010/8212698.pdf>

### 3.7 H — New Filer Report

A full financial disclosure submitted when an individual newly becomes subject to House financial-disclosure requirements. Parse it using the full financial-disclosure schema and retain its new-filer classification.

- Sample DocID: `10007178`
- Sample year: `2014`
- Local file: `H_new_filer_report_2014_10007178.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2014/10007178.pdf>

### 3.8 O — Original Annual Financial Disclosure Report

The original annual financial disclosure for the reporting year. It may include assets, income, transactions reported under the annual-report rules, liabilities, positions, agreements, gifts, and travel. Later corrections may appear separately as code `A`.

- Sample DocID: `8135951`
- Sample year: `2008`
- Local file: `O_annual_report_2008_8135951.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2008/8135951.pdf>

### 3.9 P — Periodic Transaction Report (PTR)

The principal filing for reportable purchases, sales, and exchanges made by the filer, spouse, or dependent child. A PTR typically contains transaction dates, notification dates, asset descriptions, owner codes, transaction types, and value ranges. This is the primary category for the stock-trading analysis pipeline.

- Sample DocID: `8214458`
- Sample year: `2013`
- Local file: `P_periodic_transaction_report_2013_8214458.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2013/8214458.pdf>

### 3.10 R — PTR Reporting Waiver

An asset-specific waiver from periodic transaction reporting. The sample is an Ethics Committee letter granting a PTR waiver for a specified asset. Retain it because it explains why expected PTR entries may be absent, but do not parse it as if it contained reported trades.

- Sample DocID: `8209407`
- Sample year: `2012`
- Local file: `R_ptr_reporting_waiver_2012_8209407.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2012/8209407.pdf>

### 3.11 T — Termination Report

A full financial disclosure filed when an individual's covered House service or employment ends. Parse it using the full financial-disclosure schema while retaining the termination date and termination-report classification.

- Sample DocID: `8139728`
- Sample year: `2009`
- Local file: `T_termination_report_2009_8139728.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2009/8139728.pdf>

### 3.12 W — Candidate Withdrawal Notice

This filing records that a candidate has withdrawn before becoming obligated to submit a full candidate financial disclosure. It is the withdrawal branch of the candidate declaration form, while code `D` is the under-$5,000 threshold branch.

- Sample DocID: `8206976`
- Sample year: `2011`
- Local file: `W_candidate_withdrawal_2011_8206976.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2011/8206976.pdf>

### 3.13 X — Financial Disclosure Extension Request

A request for additional time to file a required financial disclosure report. Store the request date, original deadline, requested or approved extension, disposition, and the report category when those facts are available. Do not confuse this with code `E`, which concerns an exemption from a termination report.

- Sample DocID: `8207032`
- Sample year: `2011`
- Local file: `X_extension_request_2011_8207032.pdf`
- Official source: <https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2011/8207032.pdf>

## 4. Official Download Rules

The House disclosure system exposes predictable PDF URLs based on the annual XML index year and `DocID`.

### 4.1 Periodic Transaction Reports

Use this pattern for code `P`:

```text
https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{YEAR}/{DOCID}.pdf
```

### 4.2 Other Filing Types

Use this pattern for codes `A`, `B`, `C`, `D`, `E`, `G`, `H`, `O`, `R`, `T`, `W`, and `X`:

```text
https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{YEAR}/{DOCID}.pdf
```

`YEAR` should initially be the year of the XML index containing the record. It is the server folder used to locate the document and does not necessarily equal every date printed inside the filing.

## 5. Recommended Database Treatment

For every XML entry, retain at least:

- `doc_id`
- `filing_type_code_raw`
- normalized `filing_type`
- XML index year
- filer name as published
- state and district when supplied
- filing date when supplied
- source PDF URL
- local file path
- download status, HTTP status, attempt count, and timestamps
- PDF checksum and page count
- parsing status and parser version

Suggested processing categories are:

| Processing category | Codes | Treatment |
|---|---|---|
| Transaction extraction | P | Parse PTR transaction rows. |
| Conditional transaction correction | A | Detect the amended filing and apply version-aware corrections without deleting the original evidence. |
| Full financial disclosure | C, H, O, T | Parse the broader report schedules; transaction-like sections may also be present. |
| Waiver or exemption | E, G, R | Preserve metadata, decisions, and relationships to expected reports. |
| Candidate status | D, W | Preserve candidate status and dates; normally no transaction extraction. |
| Administrative or trust documentation | B, X | Preserve structured metadata and the source document. |

Never discard a filing solely because it has no transaction table. Waivers, exemptions, extensions, amendments, and candidate-status filings explain gaps and changes in the disclosure record.

## 6. Source References

- House Committee on Ethics, Financial Disclosure Forms and Filing: <https://ethics.house.gov/financial-disclosure-forms-and-filing/>
- House Committee on Ethics, Filing Deadlines, Committee Review, and Amendments: <https://ethics.house.gov/manual/filing-deadlines-committee-review-and-amendments/>
- Clerk of the House, Financial Disclosure Reports Database: <https://disclosures-clerk.house.gov/FinancialDisclosure/ViewReport>
- Local source indexes: `C:\Home\StockGov\data\raw\houseofreptrans\2008FD.xml` through `2026FD.xml`

## 7. Verification Notes

All 13 sample files in this folder were downloaded from the official Clerk of the House disclosure server. Each file was verified as a readable PDF and visually inspected to confirm that its contents match the XML filing-type classification. The samples are reference documents; production ingestion should retain the original annual XML record and the downloaded PDF together so provenance can always be reconstructed.
