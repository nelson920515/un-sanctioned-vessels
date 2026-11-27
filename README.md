# UN-sanctioned vessels

Every ship under UN Security Council sanctions, as a checked, machine-readable
feed. Used by an Excel client-name screening tool and available to
other tools such as PRAECERNO.

| Source | What it gives | How it is read |
|---|---|---|
| [1718 Designated Vessels List](https://main.un.org/securitycouncil/en/sanctions/1718/materials/1718-Designated-Vessels-List) (DPRK, PDF only) | 59 vessels as of the list of 4 June 2020 | parsed from the PDF |
| [UN Consolidated List XML](https://scsanctions.un.org/resources/xml/en/consolidated.xml) | vessels listed as entities (Libya, resolution 2146) and the IMO company numbers of listed shipping companies | read daily |

The Consolidated List, which most screening tools load, does **not** contain
the 1718 vessels; that is the gap this feed fills.

## Files

| File | For | Content |
|---|---|---|
| `feed/vessels.tsv` | Excel tool | one row per vessel, UTF-8, tab-separated, LF, no tabs or line breaks in a field |
| `feed/status.txt` | Excel tool | `key=value`: version, list date, row count, CRC-32 of the TSV, last check, pending announcement |
| `feed/vessels.json` | other tools | the same records with lists as arrays |
| `feed/CHANGELOG.md` | people | what changed in each version |
| `sources/1718/current.pdf` | audit | the PDF the 1718 rows were parsed from (older ones in `archive/`) |

Raw URLs, for example
`https://raw.githubusercontent.com/nelson920515/un-sanctioned-vessels/main/feed/status.txt`.

Columns: `DATAID` (stable: `V1718-<IMO>` for 1718 vessels, the UN DATAID for
Consolidated List vessels), `SOURCE`, `REGIME`, `REFERENCE`, `VESSEL_NAME`,
`OTHER_NAMES`, `IMO`, `MMSI`, `CALL_SIGN`, `FLAG`, `VESSEL_TYPE`,
`DESIGNATED_ON`, `MEASURES`, `OTHER_INFORMATION`, `LINKED_ENTITIES` (UN-listed
companies that own or manage the vessel), `LISTING_EXPIRES`, `SOURCE_URL`.
Multiple values are separated by `;`.

## How it stays current

The UN site that hosts the 1718 PDF answers every automated request with a
bot challenge, GitHub's servers included, so the PDF cannot be fetched
automatically. The daily job (`.github/workflows/build.yml`) therefore:

1. re-reads the Consolidated List XML: Libya vessel listings and expiries update by themselves;
2. still tries the PDF, in case the UN lifts the block;
3. reads the UN press releases. When the 1718 Committee announces a vessel
   designation, amendment or removal, the feed is marked **pending**
   (`pending_note` in `status.txt`; the Excel tool shows it in red) and an
   issue is opened in this repository, which emails the owner.

**When an issue says a 1718 vessel change was announced:**

1. Open the [1718 Designated Vessels List page](https://main.un.org/securitycouncil/en/sanctions/1718/materials/1718-Designated-Vessels-List) in a browser and download the PDF.
2. In this repository open `sources/1718/incoming/`, choose *Add file > Upload files*, drop the PDF in and commit.

The build then parses it, runs the checks, publishes the new feed, archives the
old PDF and clears the pending flag. The Excel tool loads the new list on its
next refresh (opening, the Refresh button, or the hourly refresh). If the
announcement does not change the vessel list, run the workflow by hand with
*clear pending* ticked.

## Checks before anything is published

* the PDF table has the expected shape; rows are numbered 1..n with no gaps;
* every ship IMO number passes the IMO check digit (a misread digit fails);
* no IMO number appears twice; every vessel has a name, a date and a measure;
* a list that loses vessels or shrinks by more than 10% is only published from
  a PDF a person uploaded, never from an automatic download;
* if a new PDF cannot be read, the previous list stays published and an issue
  explains why.

`python -m pytest` runs the parser against the real June 2020 PDF and the
feed format against the contract the Excel tool relies on (including the
CRC-32 that the VBA code recomputes).

## Running it locally

```
pip install -r requirements.txt
python -m unvessels.build                 # what the daily job runs
python -m unvessels.build --pdf FILE.pdf  # a new 1718 PDF
python -m unvessels.build --offline       # re-parse the stored sources only
```

## Data

Facts published by the UN Security Council. No third-party sanctions datasets
are used.
