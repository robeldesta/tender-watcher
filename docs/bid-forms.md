# Bid forms: what Ethiopian bids are made of

Bid Room builds each bid out of the forms a real Ethiopian bidding document asks for. This is the
map behind `app/tw/bidforms.py`, from reading the documents below (November 2026).

## Sources read

- PPA, Standard Bidding Document for Goods, National Competitive Bidding (ppa.gov.et, uploaded 2024):
  https://www.ppa.gov.et/wp-content/uploads/2024/09/SBD_Goods_NCB_November-Final.pdf
- PPA, SBD for Works / Turnkey for state-owned enterprises (ppa.gov.et, version 8 Jan 2026):
  https://www.ppa.gov.et/wp-content/uploads/2026/01/SBD-Works.pdf
- Ministry of Water and Energy, works bidding document NCB (water well drilling, 2026):
  https://www.mowe.gov.et/sites/default/files/tender/Works%20BD%20NCB-%20SWE_Draft%20V2_0.docx
- Ministry of Water and Energy, goods bidding document (office furniture, Dec 2025):
  https://mowe.gov.et/sites/default/files/resource/Bid_document_For_office_furniture_for_new_renovation_PMU_office.pdf
- Ministry of Health, works request for bids (April 2026), and World Bank standard procurement documents it follows.

## The forms, by kind of procurement

| Form | Works | Goods | Services | Consultancy | Source |
|---|---|---|---|---|---|
| Bid Submission Sheet (Letter of Bid): price in figures and words, discounts, validity, bid security, performance security, eligibility, tax, anti-corruption and one-bid-per-bidder declarations | yes | yes | yes | FIN-1 | PPA SBD s.4 A |
| Technical bid submission letter (two-envelope bids) | yes | yes | yes | TECH-1 | BDS two-envelope rule |
| Power of attorney for the signatory (authenticated at DARS when not the owner/GM) | yes | yes | yes | yes | ITB documents comprising the bid |
| Bidder information: legal name, registration, address, authorised representative, bank account, licences | yes | yes | yes | yes | PPA SBD s.4 C.1, C.9 |
| Bid security: CPO, unconditional bank guarantee, or bid-securing declaration; valid 28 days past bid validity; original only | yes | yes | yes | rarely | PPA SBD s.4 E, ITB 20-22 |
| Eligibility documents: renewed trade licence, TIN, VAT registration, tax clearance, PPA supplier list registration, competency/grade certificate | yes | yes | yes | yes | ITB 23.2, BDS 11.1 |
| Similar experience (contract, employer, value, year, role, nature) with completion letters | EXP-4.2 | C.3 | yes | TECH-2 | |
| Average annual turnover (3 or 5 years, audited) | FIN-3.2 | C.2 | yes | yes | |
| Financial situation (assets, liabilities, working capital, line of credit) | FIN-3.1 | C.2 | optional | | |
| Current contract commitments | CCC | | | | works SBD |
| Litigation and non-performance | CON-2 | | | | works SBD |
| Key personnel (position, name, qualification, years, years in similar work) with CVs | yes | C.4 | yes | TECH-5/6 | |
| Equipment (type and capacity, number, owned/leased, year) with libre or rental agreement | yes | C.6 | yes | | |
| Site organisation, method statement, work programme | yes | | | | ITB 16.1 works |
| Technical specification compliance sheet (requirement, offered, remark) | | s.6 B | | | |
| Technical offer: brand, delivery, warranty, after-sales | | yes | | | |
| Methodology and work plan | | | yes | TECH-4 | |
| Comments on the ToR | | | | TECH-3 | |
| Manufacturer's authorisation | | when asked | | | s.4 F |
| Bill of quantities (item, description, unit, qty, rate, amount; VAT in the summary) | yes | | | | |
| Price schedule (item, description, origin, unit, qty, unit price, total) | | s.4 B | activity schedule | FIN-2 to 4 | |

## Rules Bid Room applies

- Bid security must stay valid 28 days past the bid validity period (ITB). The guarantee page shows the date.
- A price not marked VAT-inclusive is treated as VAT-inclusive (works SBD ITB 13.7), so the Bid
  Submission Sheet always states the VAT position and the price in words.
- Two-envelope bids: the bid security and eligibility papers go in the technical envelope; the Bid
  Submission Sheet and the priced BoQ or schedule go in the financial one. Each envelope gets a label:
  buyer, reference, TECHNICAL/FINANCIAL BID, ORIGINAL/COPY, "do not open before" the opening time.
- The buyer's own form wins: every letter can be edited to the buyer's wording; the {tokens} keep it filled.
- Every page can carry the company stamp and "Page N of M" (some buyers reject unstamped pages).
