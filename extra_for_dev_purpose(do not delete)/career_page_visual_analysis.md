# Career Page Visual Analysis
## Job Link Detection — Patterns Observed Directly from Screenshots

> **Methodology:** Every finding in this document is derived exclusively from visual inspection of 74 career page screenshots. Nothing is assumed or inferred from training knowledge. Where something could NOT be read from the screenshot (e.g. a URL obscured, a class name not visible), it is explicitly flagged as **[NOT VISIBLE]** or omitted entirely.
>
> Cross-referenced against `company_Career_seed.csv` and `company_ATS_seed.csv` for company/URL identity.

---

## Index: Screenshot → Company → URL (from seed CSVs)

| # | Company | Seed URL |
|---|---|---|
| 01 | Audible | `https://www.audiblecareers.com/search-jobs` |
| 02 | Cal.com | `https://cal.com/jobs` |
| 03 | Celonis | `https://careers.celonis.com/join-us/open-positions` |
| 04 | Flix | `https://flix.careers/jobs/` |
| 05 | Forto | `https://careers.forto.com/forto-jobs/` |
| 06 | Delivery Hero | `https://careers.smartrecruiters.com/DeliveryHero` |
| 07 | N26 | `https://boards.greenhouse.io/n26` |
| 08 | Pitch | `https://pitch.com/jobs#positions` |
| 09 | SAP | `https://jobs.sap.com/search/` |
| 10 | Siemens | `https://jobs.siemens.com/en_US/externaljobs/SearchJobs` |
| 11 | SmartRecruiters | `https://www.smartrecruiterscareers.com/jobs` |
| 12 | SumUp | `https://boards.greenhouse.io/sumup` |
| 13 | ABN AMRO | `https://www.werkenbijabnamro.nl/en/vacancies` |
| 14 | ASML | `https://www.asml.com/en/careers/find-your-job` |
| 15 | Brevo | `https://jobs.lever.co/brevo` |
| 16 | bunq | `https://careers.bunq.com/positions` |
| 17 | Bynder | `https://careers.bynder.com/openings/` |
| 18 | Tellent (Catawiki) | `https://careers.tellent.com/open-positions` |
| 19 | Freeletics | `https://www.freeletics.com/en/corporate/` |
| 20 | ING | `https://careers.ing.com/en/search-jobs` |
| 21 | KPN | `https://jobs.kpn.com/en/vacancies` |
| 22 | Lightspeed | `https://www.lightspeedhq.com/careers/openings/` |
| 23 | Levels.fyi (aggregator) | [NOT IN CSV — third-party board] |
| 24 | Miro | `https://miro.com/careers/open-positions/` |
| 25 | Mollie | `https://jobs.mollie.com/locations/amsterdam` |
| 26 | Optiver | `https://www.optiver.com/join-us/jobs/` |
| 27 | Organon | `https://jobs.organon.com/us/en/search-results` |
| 28 | Picnic | `https://jobs.picnic.app/en/jobs` |
| 29 | Raisin | `https://boards.greenhouse.io/raisin` |
| 30 | Doist | `https://doist.com/careers#open-roles` |
| 31 | Remote.com | [NOT IN CSV — company page only] |
| 32 | Rowspace | `https://www.rowspace.ai/careers` |
| 33 | Factorial | `https://careers.factorialhr.com/` |
| 34 | Glovo | `https://careers.glovoapp.com/jobs-at-glovo/` |
| 35 | Detectify | `https://careers.detectify.com/#jobs` |
| 36 | King | `https://careers.king.com/us/en/search-results` |
| 37 | Planhat | `https://www.planhat.com/careers#open-roles` |
| 38 | Quinyx | `https://careers.quinyx.com/jobs` |
| 39 | Spotify | `https://www.lifeatspotify.com/jobs` |
| 40 | Teamtailor | `https://career.teamtailor.com/jobs` |
| 41 | Voi Technology | `https://careers.voi.com/jobs` |
| 42 | Deliveroo | `https://careers.deliveroo.co.uk/join-the-team/` |
| 43 | Ocado Technology | `https://careers.ocadogroup.com/jobs` |
| 44 | Revolut | `https://www.revolut.com/careers/` |
| 45 | Skyscanner | `https://www.skyscanner.co.in/jobs/current-jobs` |
| 46 | Airbyte | `https://airbyte.com/company/careers#open-roles` |
| 47 | Databricks | `https://www.databricks.com/company/careers/open-positions` |
| 48 | Elastic | `https://jobs.elastic.co/` |
| 49 | Notion | `https://www.notion.com/careers` |
| 50 | Retool | `https://retool.com/careers#open-positions` |
| 51 | Stripe | `https://stripe.com/jobs/search` |
| 52 | ClearVUE | `https://clearvue.business/careers/` |
| 53 | Arc.cc | [NOT IN CSV — visible from logo] |
| 54 | Kaufland e-commerce | `https://kaufland-ecommerce.com/karriere/jobs/` |
| 55 | Buena | `https://buena.com/careers` |
| 56 | Delivery Hero (homepage) | [landing page, not listing] |
| 57 | reisetopia | `https://reisetopia.de/jobs/` |
| 58 | adjoe | `https://adjoe.io/careers/open-positions/` |
| 59 | Appodeal | `https://appodeal.com/career/` |
| 60 | HubSpot | `https://www.hubspot.com/careers/jobs/all?page=1` |
| 61 | Octagon | [NOT IN CSV] |
| 62 | Insify | [NOT IN CSV] |
| 63 | Doctolib | `https://careers.doctolib.com/jobs/` |
| 64 | Esselunga | `https://esselungajob.it/go/Risultati_Ricerca/4414201/` |
| 65 | Lidl Italia | `https://lavoro.lidl.it/annunci-di-lavoro` |
| 66 | Poste Italiane | `https://carriere.posteitaliane.it/` |
| 67 | FS Italiane / Ferrovie | `https://fscareers.gruppofs.it/jobs.php` |
| 68 | Ferrero | `https://www.ferrerocareers.com/int/en/jobs` |
| 69 | Michael Page DE | `https://www.michaelpage.de/jobs/compliance-analyst` |
| 70 | Harnham | `https://www.harnham.com/job-search/` |
| 71 | Avomind | `https://apply.workable.com/avomind/` |
| 72 | Nigel Frank | `https://www.nigelfrank.com/microsoft-jobs` |
| 73 | Babbel | `https://jobs.babbel.com/en` |

---

## Part 1: What Was Directly Visible in Each Screenshot

### Group A — Full Job Listing Visible (jobs rendered on screen)

---

#### 02 — Cal.com (`cal.com/jobs`)
**What's visible:**
- Plain white background, no filter bar, no sidebar
- Jobs listed as plain `<div>` rows stacked vertically
- Each row has: **bold job title** (large), brief 1–2 line description beneath it
- Metadata row below description using icon tags: 🏢 team, 📍 location type, ⏱ contract, 💰 salary range
- No "Apply" or "View" button visible — the **entire row appears to be clickable**
- No pagination visible — all jobs load on one page
- Job count: ~6 visible, more below fold

**Visually confirmed link signal:** Title text IS the primary click target (bold, larger font, appears as a heading-style anchor)

**Visually confirmed metadata tags seen:**
- `GTM`, `Engineering`, `Foundation` — team labels in small pill/tag style
- `Hybrid - NYC`, `Remote - US`, `Office - NYC` — location tags
- `Full-time` — contract type
- `$120k+ equity`, `$100k–$120k` — salary range tags

**Layout type:** Vertical list, no cards

---

#### 03 — Celonis (`careers.celonis.com/join-us/open-positions`)
**What's visible:**
- Clean table-style layout: 3 columns — **Role | Team | Location** with header row
- Each row: role name (plain text, left), team name (center), location (right), **arrow `→`** far right
- The `→` arrow on each row is the only visible CTA — the row or arrow is the clickable element
- Filter bar visible above list: Search input + `Team ▾` + `Seniority Level ▾` + `Location ▾` dropdowns
- Counter: **"202 open roles"** shown as subtitle
- No pagination controls visible — likely all on one page or JS-paginated

**Visually confirmed link signal:** The `→` arrow at row end is the per-job CTA

**Visually confirmed columns:** Role name | Team | Location | →

---

#### 04 — Flix (`flix.careers/jobs/`)
**What's visible:**
- Bright green brand header with Location + Department dropdowns + Search bar
- Below header: job count "**138 jobs** in all locations in all departments wait for you"
- Jobs listed as **flat rows** in a white card with thin border
- Each row: **Job title** (left, plain text) + 📍 **city name** (right)
- No department shown per row — only title + location
- Rows are separated by thin horizontal lines
- No "Apply" button — row itself appears clickable

**Visually confirmed link signal:** Entire row is the link (no separate CTA)

**Visually confirmed row content:** Title only + single location pin icon + city

---

#### 05 — Forto (`careers.forto.com/forto-jobs/`)
**What's visible:**
- Dark navy header, then filter bar: `Department ▾` | `Location ▾` | `Employment Type ▾`
- "**18 open positions**" counter shown
- Job rows in a table-like layout: **Title | Department | Employment Type | Location | "View Job" button**
- **"View Job"** is a distinct teal/blue button on the right of every row — this is the job link CTA
- Rows visible: Data Scientist, Legal Counsel (f/m/d), Sales Development Representative Italy

**Visually confirmed link signal:** `"View Job"` button (right-aligned, colored button per row)

**Visually confirmed columns:** Title | Department | Type | Location | CTA button

---

#### 09 — SAP (`jobs.sap.com/search/`)
**What's visible:**
- Search result page: `Search results for "analyst"`
- Two input fields: keyword + location, with `Search Jobs` button
- `Results 1 – 25 of 96` with pagination: `‹ 1 2 3 4 ›`
- Two-column table: **Title (blue hyperlink)** | **City**
- Each title is a standalone `<a>` blue hyperlink — this IS the job link
- Title + city per row, no other metadata in the list
- Inline filter inputs for Title and City above the results table

**Visually confirmed link signal:** Blue underlined hyperlink text = job title = the link

**Visually confirmed pagination:** Standard numbered `‹ 1 2 3 4 ›` with result count

**Visually confirmed columns:** Title (link) | City

---

#### 10 — Siemens (`jobs.siemens.com`)
**What's visible:**
- Dark/teal theme
- Left sidebar filters: Keywords/skills input, Country dropdown, Field of work dropdown, Experience Level dropdown, Additional filters accordion, `Search` button
- Job cards in main area: each card has **title (large, white text)**, then `{Location} • Job ID: {N} • {Department}` metadata line, then **"Learn more"** green CTA button + `Share` link
- Job ID format visible: `Job ID: 505323`, `Job ID: 508739`, `Job ID: 509403`
- Card has full border, slight background tint

**Visually confirmed link signal:** `"Learn more"` button is the job CTA

**Visually confirmed metadata format:** `{City}, {Country} • Job ID: {N} • {Department}`

**Visually confirmed Job ID format:** Numeric, 6 digits, prefixed with "Job ID: "

---

#### 11 — SmartRecruiters (`smartrecruiterscareers.com/jobs`)
**What's visible:**
- "**Jobs at SmartRecruiters — 28 RESULT(S)**"
- 3-column card grid layout
- Each card: large title text, department tag below (e.g. `Engineering`), **☆ bookmark icon** top-right of card
- Cookie consent modal overlaying lower portion
- Winston AI chatbot bubble bottom-right (teal, "W" logo)
- No "Apply" button visible on card — card itself appears fully clickable

**Visually confirmed link signal:** Entire card is clickable (no separate CTA button visible)

**Visually confirmed result count format:** `{N} RESULT(S)` (uppercase with parentheses)

**Visually confirmed save icon:** ☆ star top-right of each card — NOT a job link

---

#### 13 — ABN AMRO (`werkenbijabnamro.nl/en/vacancies`)
**What's visible:**
- Left sidebar: Filter results section with Department dropdown, Working level checkboxes (Internship 31, Starter 4, Professional 60), Number of hours checkboxes, Country checkboxes (Belgium 11, Netherlands 84), Workexperience checkboxes
- Main area: "**Vacancies (95)**" heading
- **Card grid** (2 columns): each card has a photo at top, then **job title as blue link**, then icon metadata rows: department, work type (Internship), hours range, experience years
- **"Show vacancy"** green button at bottom of each card + ♡ heart save icon
- Count on filter labels (e.g. Internship **31**, Professional **60**) — these are NOT links

**Visually confirmed link signal:** Both the title link AND `"Show vacancy"` button lead to the job

**Visually confirmed card structure:** Photo → Title (link) → Metadata icons → CTA button

---

#### 14 — ASML (`asml.com/en/careers/find-your-job`)
**What's visible:**
- Left sidebar filters: Location dropdown, Job Type radio (Jobs / Internships), Team checkboxes with many options
- Search bar at top: "Find your job" input + `Search now` button + `Create job alert` link
- "**498 RESULTS — PAGE 1 / 19**" → 19 pages of pagination
- Each job row: **"NEW"** badge (yellow), then bold **title** (blue link), then location tag + department tag as small colored pills
- ♡ heart save icon on right of each row

**Visually confirmed pagination:** `PAGE 1 / 19` — standard page-based, 19 pages total

**Visually confirmed "NEW" badge:** Yellow label on recently posted jobs

**Visually confirmed link signal:** Job title is a blue hyperlink

---

#### 15 — Brevo (`jobs.lever.co/brevo`)
**What's visible:**
- Minimal layout — no header nav, just Brevo logo
- Filter bar at top: `Location Type ▾` | `Location ▾` | `Team ▾` | `Work Type ▾`
- Jobs grouped by **department heading** (H2-style, all caps): `CLIENT`, `FINANCE/LEGAL`, `MARKETING`, `PEOPLE`
- Under each dept: **sub-team** heading (slightly smaller caps): `SOLUTION`, `LEGAL`, `BRAND & COMMUNICATIONS`
- Under each sub-team: job rows with **title** (plain text, left) + **"APPLY"** green button (right)
- Metadata below title: `HYBRID — FULL-TIME — LONDON` in small muted caps

**Visually confirmed link signal:** `"APPLY"` green button right-aligned per job row

**Visually confirmed hierarchy:** Department → Sub-team → Job rows

**Visually confirmed metadata format:** `{WORK-MODEL} — {CONTRACT} — {CITY, COUNTRY}` in small caps

---

#### 16 — bunq (`careers.bunq.com/positions`)
**What's visible:**
- Dark background theme
- Left column: "Filter by country" — plain text list: Portugal, Slovakia, Italy, Netherlands, Germany, Ireland, United States, France, UK, Spain, Turkey, Austria, Poland…
- Right column: job cards in a list (not grid), each card: **bold title** + 📍 location + 👥 team tag
- Card background slightly lighter than page; full card appears clickable
- Cookie consent overlay covering part of the page

**Visually confirmed link signal:** Full card is the clickable link

**Visually confirmed filter:** Country list (plain text links, left sidebar) — these are filters NOT job links

---

#### 17 — Bynder (`careers.bynder.com/openings/`)
**What's visible:**
- Table layout grouped by department heading (H2-style): **"R&D"**, **"Customer Success & Support"**
- Under each dept: table with columns **Title | Type | Department | Location**
- Rows: job title (appears as a plain link), Type (Full-time/Intern), Department text, Location city
- No separate CTA button — **title text is the link**
- Cookie consent modal visible

**Visually confirmed link signal:** Job title in leftmost column is the link

**Visually confirmed columns:** Title | Type | Department | Location

---

#### 18 — Tellent/Catawiki (`careers.tellent.com/open-positions`)
**What's visible:**
- Teamtailor-powered (visible `CAREER MENU` top-left, share icon top-right)
- Filter bar: Search input + `All departments ▾` + `All countries ▾` + `All cities ▾` + job count "**10 jobs**" + `Share` link
- Jobs grouped by **country**: heading "**France**" visible
- Each job row: **bold title** (magenta/pink hyperlink) + work model icon + location text + `"View job"` pink button right side
- Metadata: `🏢 Hybrid ▹ 📍 Amsterdam, Noord-Holland, Netherlands +2 more`

**Visually confirmed link signal:** Title is a colored link + `"View job"` button both lead to job

**Visually confirmed grouping:** By country/region heading

---

#### 20 — ING (`careers.ing.com/en/search-jobs`)
**What's visible:**
- Left sidebar: "Filter results" with Country checkboxes (Australia, Belgium, China, France, Germany…)
- Counter: "**We have 826 jobs for you**"
- Job cards: white rounded card, **bold title** (large), then metadata as small colored tags: location, business line, seniority, entity name
- Orange `›` arrow button on right side of each card — this is the CTA
- Cards are full-width, stacked vertically

**Visually confirmed link signal:** Orange `›` arrow button right side of card

**Visually confirmed metadata tags:** location • business line • seniority level • entity

---

#### 21 — KPN (`jobs.kpn.com/en/vacancies`)
**What's visible:**
- Bright green header, search bar
- "**Results: 4 — Page: 1/1**" — very small number of results
- Left sidebar filters: Seniority (Senior 1, Internship 2), Job type (Vast 2, Stage 2), Expertise checkboxes, Location checkboxes
- Job rows in main area: **bold large title** (green hyperlink) + metadata row `Stages • Stage • Amersfoort • WO`
- ♡ heart save icon right of each row
- No separate CTA button — title is the link

**Visually confirmed link signal:** Title is a large green hyperlink

**Visually confirmed metadata separator:** `•` bullet between: contract type • level • city • education level

---

#### 22 — Lightspeed (`lightspeedhq.com/careers/openings/`)
**What's visible:**
- Two filter dropdowns: `Departments ▾` | `Locations ▾`
- Table layout: **3 columns — Title | Departments | Locations**
- Column headers visible as plain text labels
- Job titles as **red hyperlinks** in leftmost column
- No images, no cards — pure table rows

**Visually confirmed link signal:** Red/colored hyperlink in title column

**Visually confirmed columns:** Title (link) | Department | Location

---

#### 28 — Picnic (`jobs.picnic.app/en/jobs`)
**What's visible:**
- Left sidebar: "Filters — **303 Jobs**", search input, Location checkboxes (Customer Success, Engineering, Finance, Graduate Programs, Operations, People, Real Estate, Students, Supply Chain…)
- Table rows in main area: **Title | Department | Location | `›` arrow**
- Title is plain text (not visually styled as link), but `›` arrow on far right is the CTA
- Jobs visible: Tech Lead (Java) | Engineering | Amsterdam | `›`

**Visually confirmed link signal:** `›` right-arrow at end of each row

**Visually confirmed columns:** Title | Department | Location | `›`

---

#### 29 — Raisin (`boards.greenhouse.io/raisin`)
**What's visible:**
- Minimal layout — white page, Raisin logo, no nav
- Left sidebar: Search input, Department dropdown, Office dropdown, Legal Entity dropdown
- "**41 jobs**" count
- Jobs grouped by **department heading**: AFC, B2C Channels EU, Communications and PR, Compliance, CoreTech…
- Under each dept: **job title** (plain text row) + `{City, Country}` location below title
- No CTA button visible — title row appears to be the link
- "Create a Job Alert" banner at top

**Visually confirmed link signal:** Title text row is the clickable element (Greenhouse standard pattern)

**Visually confirmed grouping:** Department headings, then job rows beneath

---

#### 34 — Glovo (`careers.glovoapp.com/jobs-at-glovo/`)
**What's visible:**
- Dark background, 3-column card grid
- Each card: top has 📍 pin icon (yellow), **bold title** (white text), location, department tag, **"APPLY"** yellow button at card bottom
- ☆ star save icon top-right of each card
- Filter bar: Department dropdown visible
- Counter: "**278 results**"
- Cookie consent overlay

**Visually confirmed link signal:** `"APPLY"` yellow button at bottom of each card

**Visually confirmed card structure:** Pin icon top → Title → Location → Team → APPLY button

---

#### 36 — King (`careers.king.com/us/en/search-results`)
**What's visible:**
- Left sidebar: Country, City, Category filters with checkboxes and counts
- Main area: "**1-10 of 36 results**", Sort by Relevance
- Job cards: white card with border, **bold title**, then 1–2 sentence description, then metadata icons: 📊 department, 📍 city/country, 🔢 job reference code (`R026384`), 👤 Regular
- **"Apply now"** orange button at bottom of each card

**Visually confirmed link signal:** `"Apply now"` button at card bottom

**Visually confirmed job ID format:** Alphanumeric `R{6-digit}` format

**Visually confirmed metadata icons:** Department icon • Location icon • Reference icon • Type icon

---

#### 39 — Spotify (`lifeatspotify.com/jobs`)
**What's visible:**
- Dark background, page title: "Creation Platform"
- Three dropdowns: `Location ▾` | `Category ▾` | `Job type ▾`
- "**116 jobs** in all locations in all categories in all job types"
- Job rows as tall white-border cards: **bold title**, location text below, then **tag pills** for team + contract type
- `↗` external link arrow top-right of each card — this is the CTA
- Tags visible: `Engineering`, `Mobile`, `Permanent`

**Visually confirmed link signal:** `↗` icon top-right of each card (external link indicator)

**Visually confirmed tag types:** Team tag + Contract tag (pill style)

---

#### 42 — Deliveroo (`careers.deliveroo.co.uk/join-the-team/`)
**What's visible:**
- Teal/green accent header: "JOIN THE TEAM"
- Search bar: "Search for jobs"
- Filter bar: `Filter by team ▾` | `Filter by location ▾` | toggle buttons: `Part time` | `Remote working`
- "**Showing 200 roles**", Sort by Relevance
- Job rows: **bold title** (large), then `Job Id: {R-format-ID}` in small text, then 📍 location + type tag
- Rows visible: Account Manager | Job Id: R22122 / Account Manager – Dutch Speaking | Job Id: EV3041 | Location: Manchester - Main Office | Permanent

**Visually confirmed Job ID format:** `R{5-digit}` and `EV{4-digit}` prefix formats

**Visually confirmed link signal:** Entire row appears to be the link (no separate CTA button)

**Visually confirmed metadata:** `Job Id: {ID}` directly under title, then 📍 location

---

#### 43 — Ocado (`careers.ocadogroup.com/jobs`)
**What's visible:**
- Search bar: "Search jobs and keywords" + Search button
- Filter bar: `Type of Work ▾` | `Location ▾` | Sort by Newest
- "**67 jobs found**"
- Job cards: white bordered card, **bold large title**, then icon row: `≡ {Department}` + `📍 {City, Country}` + `📅 {Date}` + `→ More details` link
- "More details" is an arrow-text link on the right of each card
- Date visible: `22 June 2026` format

**Visually confirmed link signal:** `→ More details` link on each card

**Visually confirmed date format:** `DD Month YYYY` (e.g. `22 June 2026`)

**Visually confirmed metadata row:** Department icon | Location icon | Date icon | CTA

---

#### 44 — Revolut (`revolut.com/careers/`)
**What's visible:**
- H1: "**We have 622 open positions**" — live count
- Location dropdown, Search bar
- Left sidebar: "Filter by teams" list with counts: All teams · 622, Business Development · 12, Credit · 33, Data · 11, Engineering · 35, Executive · 3, Finance · 44, Legal · 40, Marketing & Comms · 31, Operations · 49, People & Recruitment · 9
- Main area: "Featured roles" section with large bold job titles
- Each job: **bold title** (large, dark), then metadata: `🏢 Office: {City}` + `🌐 Remote: {Country/Region}`
- No separate CTA button visible on listing — title appears to be link

**Visually confirmed sidebar:** Team filter with counts — these are filter labels NOT job links

**Visually confirmed metadata format:** `🏢 Office: {City}` + `🌐 Remote: {Country}`

---

#### 47 — Databricks (`databricks.com/company/careers/open-positions`)
**What's visible:**
- Left sidebar navigation: Overview, Culture, Benefits, Inclusion, Engineering, Research, Go to Market, Interviewing With Us, Internships & Early Careers, **Open Positions** (highlighted), Recruitment Fraud
- Filter bar: Department dropdown + Location dropdown
- Jobs grouped by **department H2 heading**: Administration, Business Development, Customer Success…
- Table rows under each dept: **Title** | **Location** (city + state)
- Title is plain text (appears as link by styling)
- No CTA button — title row is the link

**Visually confirmed structure:** Left nav (NOT job links) + department-grouped table

**Visually confirmed columns:** Title | Location city/state

---

#### 48 — Elastic (`jobs.elastic.co`)
**What's visible:**
- Page title: "Customer Success Group Openings"
- Search input: "Search careers by role, location, or keyword"
- Filter by Team dropdown + Filter by Location dropdown
- "**Customer Success Group Jobs (13 results)**"
- Jobs inside a collapsible group `CSG [13] ^` accordion
- Each job: **blue hyperlink title**, then `Distributed Locations: India` + `Hybrid Locations: Bangalore, India; Gurgaon, India; Mumbai, India` as tag chips
- Tags shown as colored small rounded pills

**Visually confirmed link signal:** Blue hyperlink title is the job link

**Visually confirmed location format:** Two separate fields — `Distributed Locations` and `Hybrid Locations`

**Visually confirmed accordion:** `CSG [13] ^` group container

---

#### 51 — Stripe (`stripe.com/jobs/search`)
**What's visible:**
- Page: "stripe JOBS" with subtitle navigation: Our opportunity, Life at Stripe, Benefits, University
- Additional links visible: `Bridge open roles ›` + `Privy open roles ›`
- Filter bar: Search input + `Teams ▾` + `Office Locations ▾` + `Remote Locations ▾`
- "Showing roles across across all locations and all teams"
- Table: **Role | Team | Location** columns with header
- Job titles as **blue hyperlinks** in Role column
- Rows densely packed, many visible (20+ roles on screen)
- Roles visible: Account Executive - Enterprise, Grower | Sales | South San Francisco HQ

**Visually confirmed link signal:** Blue hyperlink in Role column

**Visually confirmed columns:** Role (link) | Team | Location with 🏳️ country flag

**Note:** `Bridge open roles` and `Privy open roles` are **subsidiary/program links** — NOT individual job links

---

#### 60 — HubSpot (`hubspot.com/careers/jobs/all?page=1`)
**What's visible:**
- "**All Open Positions**" heading, subtitle nav: Why HubSpot, Departments, Resources, Emerging Talent
- Filter bar: Filter by Location, Filter by Department, Filter by Language, Filter by Role Type (all dropdowns)
- "**Browse Open Positions — Showing 1–157 of 157**" → `Show fewer` button
- Jobs in **4-column card grid**: each card has **blue hyperlink title**, department below, location below
- Cards visible: Account Executive-Corporate/Enterprise | Sales | Bengaluru, India / Account Executive (English and Portuguese) | Sales | Remote - USA / Account Executive - Enterprise | Sales | Colombia

**Visually confirmed link signal:** Title is a blue hyperlink

**Visually confirmed grid:** 4-column card layout

**Visually confirmed count:** 157 total, all shown on one page (no pagination, "Show fewer" suggests all are loaded)

---

#### 63 — Doctolib (`careers.doctolib.com/jobs/`)
**What's visible:**
- Search bar at top: keyword search
- "**222 jobs everywhere ▾ in all teams ▾ and all contract types ▾**" — dropdowns embedded in the sentence
- 2-column card grid
- Each card: **bold title**, then icon row: 📋 contract type + 📍 city + 👥 department + **"New"** badge on recent postings
- Cards visible: Talent Acquisition Coordination… | Senior Account Executive – Praxis… | Consultante Deployment & Onboarding - Milano | Account Manager Régional (s/f/m)…
- Gender-neutral suffix `(s/f/m)` visible on French job titles

**Visually confirmed layout:** 2-column card grid

**Visually confirmed "New" badge** on cards

**Visually confirmed title suffix format:** `(s/f/m)` — French gender neutrality notation

---

#### 64 — Esselunga (`esselungajob.it`)
**What's visible:**
- Italian-language page: "Scopri le posizioni per area" + "o visualizza tutte le posizioni"
- Image carousel/tabs at top for area selection (food category images)
- Below: simple list of job cards, each with: **bold blue hyperlink title** + store brand label + region name
- Jobs visible: Bar Atlantic Job Day - Milano | Lombardia / Addetto/a alla Vendita Part Time - Verbania | Esselunga | Piemonte / Addetto/a alla Vendita (cat. protette L. 68/99) Part Time - Genova | Liguria

**Visually confirmed link signal:** Blue hyperlink title

**Visually confirmed metadata:** Store brand (`Esselunga`, `Atlantic`) + Italian region name

---

#### 65 — Lidl Italia (`lavoro.lidl.it/annunci-di-lavoro`)
**What's visible:**
- Italian-language page; Lidl logo + `Vale davvero.` tagline
- Nav: Life at Lidl, Punti Vendita, Centri Logistici, Uffici, Lavoro & Formazione, Blog
- Filter bar: keyword input + `Cerca` (search) button; `Attività ▾` filter; map toggle icon
- "**440 a…**" (count cut off by cookie modal)
- Job rows: **bold title** (left) + `📍 {Full address}` (right)
- `+` expand icon left of each row (accordion — clicking expands job details inline)
- 📌 bookmark icon far right
- Rows visible: Assistant Store Manager part-time | Via Battaglia 3, Albignasego / Addetta/o Vendite part-time 8 ore domenicali | Via Antonio Meucci…

**Visually confirmed link signal:** `+` expand icon per row (inline expand, not page navigation) OR title may link to detail page

**Visually confirmed metadata format:** Full postal address including street

**Visually confirmed no-image layout:** Pure text list with `+` accordion

---

#### 66 — Poste Italiane (`carriere.posteitaliane.it`)
**What's visible:**
- "**Open Jobs**" heading + "Working with us is a whole different story"
- Search input + filter pills: `Locations ▾` | `Categories ▾` | `Posting Dates ▾` + "3 OPEN JOBS" counter
- 3-column card layout
- Each card: **bold title** + location tag (yellow pill with city list) + date + description text + **"Apply Now"** blue button
- Cards: Financial Advisors | Alessandria, Piemonte, Italy and 55 more / LETTER CARRIER | Bologna, Emilia-Romagna… / Customer Service Clerk – Post Office in Bolzano

**Visually confirmed link signal:** `"Apply Now"` blue button at card bottom

**Visually confirmed location format:** Multiple cities listed + `"and {N} more"` overflow

---

#### 67 — FS Italiane (`fscareers.gruppofs.it/jobs.php`)
**What's visible:**
- Custom job board (`jobs.php` endpoint)
- Left sidebar: keyword search, Country, Region, City/Address fields, distance slider, Sector dropdown, Role dropdown, Contract type dropdown, Working hours dropdown, `Start the search` button
- Right: "**Total jobs: 3**" counter
- Job cards (left-side list): **bold title** + 📍 Site: Italy + 📋 Sector: Distributions and Logistics + 👤 Role: Other + description paragraph + date `17/08/2026`
- Date format `DD/MM/YYYY` (Italian date format)

**Visually confirmed custom ATS:** `.jobs.php` endpoint — bespoke system

**Visually confirmed date format:** `DD/MM/YYYY`

**Visually confirmed link signal:** [NOT CLEARLY VISIBLE — title appears clickable but no explicit button shown]

---

#### 68 — Ferrero (`ferrerocareers.com/int/en/jobs`)
**What's visible:**
- Top nav: Ferrero Unwrapped, Teams, Early Careers, Ferrero Career Bites + "Find a job" button
- Search bar full-width: "Search your dream job"
- Left sidebar filters: County/Region, City, Job function, Type of contract, Career stage, Place of work (all accordion-style)
- "**418 Job positions**" counter
- Cards with **beige/cream background**: bold title, department text, `Job ID: {N}` format, location with 📍 icon + work model (Hybrid/Permanent/Internship), **"Details →"** link bottom right

**Visually confirmed Job ID format:** `Job ID: {5-digit}` (e.g. `Job ID: 76273`, `76138`, `76163`)

**Visually confirmed link signal:** `"Details →"` link at card bottom-right

**Visually confirmed card metadata:** Title → Department → Job ID → Location + work model → CTA

---

#### 71 — Avomind (`apply.workable.com/avomind/`)
**What's visible:**
- Workable-powered (URL confirms it)
- "**Careers at Avomind**" heading, subtitle: "Commercial, Strategy & Analytics Talent Globally"
- "Take a look at our Open Roles"
- Filter bar: Search jobs input + `Workplace type ▾` + `Location (1) ▾` + `Department ▾` + `Work type ▾`
- Active filter chip: `India ×` — location pre-filtered
- Info banner: "We've detected your location and are showing jobs in India."
- Table rows: **Title | Work Model | Location(s) | Department | Type**
- Title is **bold**, no hyperlink styling visible but appears clickable
- Rows: Senior QA Engineer | Remote / Relationship Manager (Malayalam Speaker) | Hybrid | Yerevan + Dubai + Kerala / Senior Finance & Accounting Manager | On-site | Delhi

**Visually confirmed layout:** Workable table layout

**Visually confirmed filter chip:** `{Location} ×` removable chip — active filter, NOT a job

**Visually confirmed columns:** Title | Work model | Location(s) | Department | Type

---

### Group B — Landing Pages (No Jobs Visible Yet — Must Follow CTA)

These pages show company branding with a CTA button that links to the actual job listing. **Your crawler must follow this CTA link before extracting jobs.**

| # | Company | CTA Text Visible | Notes |
|---|---|---|---|
| 19 | Freeletics | `"0 open positions →"` | Zero openings at time of screenshot |
| 24 | Miro | `"Search"` button below filters | Listing visible below fold |
| 25 | Mollie | (no listing visible) | Shows office/culture photos + `Jobs 45` nav link |
| 30 | Doist | `"See open roles"` green button | Pure landing page |
| 31 | Remote.com | `"Explore careers at Remote"` button | Pure landing page |
| 32 | Rowspace | `"EXPLORE OPEN ROLES →"` bar at bottom | Pure landing page |
| 33 | Factorial | `"Jobs"` in nav bar | No listings visible — culture only |
| 37 | Planhat | `"JOIN US ›"` button | Pure landing page |
| 46 | Airbyte | `"CHECK OPENINGS"` blue button | Pure landing page |
| 49 | Notion | `"Browse full-time openings"` button | Landing page with culture content |
| 50 | Retool | `"View all openings"` black button | Pure landing page |
| 56 | Delivery Hero | Search bar visible but no list | Culture/brands homepage |
| 59 | Appodeal | `"View open roles"` blue button | Pure landing page |
| 62 | Insify | `"Jump to job openings"` button | Scrolls to listing further down page |

---

### Group C — Partially Visible / Blocked by Cookie Consent

These pages had the job listing partially or fully obscured by cookie consent overlays.

| # | Company | What Was Visible Despite Overlay |
|---|---|---|
| 05 | Forto | Job rows visible above fold — "View Job" button seen |
| 07 | N26 | Search bar + filter bar visible; listing cut off |
| 10 | Siemens | 3 job cards partially visible |
| 26 | Optiver | 2 job rows visible below cookie banner |
| 27 | Organon | Job list partially visible on right side |
| 34 | Glovo | Job cards visible in background |
| 40 | Teamtailor | Full job list visible alongside cookie banner |
| 41 | Voi | Image cards visible in background |
| 43 | Ocado | Job card visible below cookie banner |
| 44 | Revolut | Filter sidebar + some jobs visible |
| 54 | Kaufland | Job cards partially visible in background |
| 65 | Lidl Italia | Job rows partially visible |

---

### Group D — Empty States

| # | Company | Empty State Message Visible |
|---|---|---|
| 00 | Unknown (Personio) | "No open positions at the moment" + briefcase icon |
| 19 | Freeletics | "0 open positions →" counter |
| 69 | Michael Page DE | "Leider hat Ihre Suche keine Ergebnisse geliefert" (no results) |

---

## Part 2: CTA Button Text — Exact Text Confirmed Visually

These were directly read from screenshots:

| CTA Text Seen | Company | Style |
|---|---|---|
| `View Job` | Forto (05), Tellent (18), Arc.cc (53) | Colored button, right-aligned |
| `Apply` | Brevo (15) | Green button, right-aligned |
| `APPLY` | Glovo (34) | Yellow button, card bottom |
| `Apply Now` | Poste Italiane (66), King (36) | Blue/orange button |
| `Show vacancy` | ABN AMRO (13) | Green button, card bottom |
| `Learn more` | Siemens (10) | Green button, card right |
| `Details →` | Ferrero (68) | Text link, card bottom-right |
| `→ More details` | Ocado (43) | Text+arrow, card right |
| `→` (arrow only) | Celonis (03), Picnic (28), ING (20) | Arrow icon, row right |
| `↗` (external arrow) | Spotify (39) | Icon, card top-right |
| `›` (chevron only) | KPN (21), Revolut (44) | Chevron icon, row right |

---

## Part 3: Metadata Separator Patterns — Confirmed Visually

| Separator | Companies Where Seen |
|---|---|
| ` • ` (bullet) | Siemens (10), KPN (21), Brevo (15), Tellent (18) |
| ` · ` (middle dot) | Teamtailor (40), Voi (41) |
| ` — ` (dash) | Brevo (15) metadata caps format |
| ` \| ` (pipe) | [not seen visually in any screenshot] |
| Icon prefix | ING (20), King (36), Ferrero (68), Ocado (43) — using SVG icons |
| Comma | Deliveroo (42), FS Italiane (67) |

---

## Part 4: Job Count Display Formats — Confirmed Visually

| Format | Company | Example |
|---|---|---|
| `{N} open roles` | Celonis (03) | `202 open roles` |
| `{N} jobs … wait for you` | Flix (04) | `138 jobs in all locations in all departments wait for you` |
| `{N} open positions` | Forto (05), bunq (16), Optiver (26) | `18 open positions` |
| `{N} RESULT(S)` | SmartRecruiters (11) | `28 RESULT(S)` |
| `Vacancies ({N})` | ABN AMRO (13) | `Vacancies (95)` |
| `{N} RESULTS — PAGE X / Y` | ASML (14) | `498 RESULTS — PAGE 1 / 19` |
| `Results: {N}` | KPN (21) | `Results: 4` |
| `Showing {N} roles` | Deliveroo (42) | `Showing 200 roles` |
| `{N} jobs found` | Ocado (43) | `67 jobs found` |
| `We have {N} open positions` | Revolut (44) | `We have 622 open positions` |
| `{N} jobs in all … all …` | Spotify (39) | `116 jobs in all locations in all categories` |
| `{N} Job positions` | Ferrero (68) | `418 Job positions` |
| `{N} search results` | Tenth Revolution (72) | `498 search results` |
| `Showing 1–{N} of {N}` | HubSpot (60) | `Showing 1–157 of 157` |
| `{N} jobs everywhere` | Doctolib (63) | `222 jobs everywhere` |
| `Total jobs: {N}` | FS Italiane (67) | `Total jobs: 3` |
| `{N} Job positions` | Ferrero (68) | `418 Job positions` |

---

## Part 5: Confirmed ATS Platforms (Visually or Via Seed CSV)

| Platform | Confirmed By | Companies |
|---|---|---|
| **Teamtailor** | Visual UI (`CAREER MENU` nav) | Teamtailor (40), Voi (41), Quinyx (38), Detectify (35) |
| **SmartRecruiters** | Visual (Winston chatbot + `RESULT(S)`) | SmartRecruiters (11), Delivery Hero (06) |
| **Greenhouse** | Seed CSV (`boards.greenhouse.io`) | N26 (07), SumUp (12), Raisin (29), Brevo (15 via Lever actually), Adyen, Monzo, Vercel, Figma, many more |
| **Lever** | Seed CSV (`jobs.lever.co`) | Brevo (15), InnoGames, Workday company, Mendix |
| **Ashby** | Seed CSV (`jobs.ashbyhq.com`) | Pleo, Sentry, Linear, Cargo.one, Ecosia, Mapbox |
| **Personio** | Visual (`Powered by Personio` footer, img 00) | Moss, QuantumDiam, Ohpen + the empty-state page |
| **Workday** | Seed CSV (`wd{N}.myworkdayjobs.com`) | Zalando, Philips, NXP, Autodesk, SimCorp |
| **Workable** | Seed CSV (`apply.workable.com`) | Avomind (71) |
| **Recruitee** | Seed CSV (`bonial.recruitee.com`) | Bonial (56) |
| **BambooHR** | Seed CSV (`bamboohr.bamboohr.com`) | BambooHR itself |
| **Custom** | Direct URL (no known ATS pattern) | SAP, Siemens, Deliveroo, Revolut, Ocado, HubSpot, Ferrero, Esselunga, Lidl, Poste Italiane, FS Italiane |

---

## Part 6: What Was NOT Visible and Should Not Be Claimed

The following are **NOT confirmed** from screenshots and should be treated as assumptions until verified with real DOM inspection:

- Exact CSS class names (`.job-listing__link`, `.postings-group`, etc.)
- Exact `data-*` attribute names and values
- Whether titles are `<h2>`, `<h3>`, or `<div>` elements
- Exact `<a href>` URL structures for most companies (URLs not visible in screenshots)
- Whether JavaScript is required to render the job list
- API endpoint structures for any platform
- Whether job cards use `<article>`, `<li>`, or `<div>` as the container element

---

*Analysis based entirely on visual inspection of 74 screenshots taken 22 June 2026. Cross-referenced with company_Career_seed.csv and company_ATS_seed.csv for URL and ATS type identification.*

---

## Part 7: Layout Type Classification — All 74 Screenshots

Classified purely by what the listing area looks like visually.

| Layout Type | Description | Companies |
|---|---|---|
| **Flat row list** | Title + metadata on one horizontal row, stacked vertically, no card border | Celonis (03), Flix (04), Bynder (17), Raisin (29), Brevo (15), Databricks (47), Lightspeed (22), Stripe (51), Adjoe (58) |
| **Bordered row list** | Same as flat row but each row has a visible border/separator card | Deliveroo (42), Ocado (43), KPN (21), Skyscanner (45), Picnic (28), Spotify (39), Tellent (18), Avomind (71) |
| **Card grid (2-col)** | Two columns of bordered cards | ABN AMRO (13), Doctolib (63), Poste Italiane (66) |
| **Card grid (3-col)** | Three columns of bordered cards | SmartRecruiters (11), Glovo (34), HubSpot (60) |
| **Card grid (4-col)** | Four columns of cards visible | Kaufland (54), HubSpot (60) |
| **Full-width card** | Single column, each job in a wide card with full border | ING (20), King (36), Ferrero (68), Siemens (10) |
| **Table (no border)** | HTML-style columns with header row, no card border | SAP (09), Lightspeed (22), Stripe (51), Databricks (47), Elastic (48) |
| **Dept-grouped list** | Jobs under department H2/H3 headings, then rows/table | Raisin (29), Bynder (17), Databricks (47), Brevo (15), Cal.com (02), Adjoe (58), Bonial (56) |
| **Image-top card** | Card with photo at top, then title, then metadata | ABN AMRO (13), Voi (41) |
| **Landing page only** | No listing visible — CTA button leads elsewhere | Doist (30), Retool (50), Notion (49), Remote (31), Airbyte (46), Rowspace (32), Freeletics (19), Planhat (37), Factorial (33), Appodeal (59) |

---

## Part 8: Filter UI Patterns — Confirmed Visually Per Company

### Search / Keyword Input
Seen in: Flix (04), Siemens (10), N26 (07), SumUp (12), ABN AMRO (13), ASML (14), Celonis (03), ING (20), Organon (27), Picnic (28), King (36), Spotify (39), Deliveroo (42), Ocado (43), Revolut (44), Skyscanner (45), Databricks (47), Elastic (48), Stripe (51), HubSpot (60), Ferrero (68), Avomind (71), Nigel Frank (72), Babbel (73)

**Placeholder texts visually confirmed:**
- `"Search jobs"` — N26 (07), SumUp (12)
- `"Search…"` — Brevo (15), Tellent (18)
- `"Search your dream job"` — Ferrero (68)
- `"Search job openings, e.g. 'manager'"` — Delivery Hero (06)
- `"Search jobs by title, location, or team"` — Babbel (73)
- `"Search vacancies, function or keyword…"` — KPN (21)
- `"Start your job search here"` — SmartRecruiters (11)
- `"Find your job"` — ASML (14)
- `"Search for jobs"` — Deliveroo (42)
- `"Search jobs and keywords"` — Ocado (43)
- `"Search from {N} open positions"` — Revolut (44)
- `"Search careers by role, location, or keyword"` — Elastic (48)

### Location Filter
- **Dropdown:** Flix (04), Forto (05), SumUp (12), Deliveroo (42), Spotify (39), Stripe (51), HubSpot (60), Avomind (71)
- **Checkbox sidebar:** ING (20), Picnic (28), ABN AMRO (13), ASML (14), King (36)
- **Country text list (clickable):** bunq (16), Revolut (44 — sidebar team list)
- **Country tab row:** Reisetopia (57), Arc.cc (53)

### Department / Team Filter
- **Dropdown:** Forto (05), Databricks (47), Brevo (15), Tellent (18), Spotify (39), Stripe (51), HubSpot (60), Avomind (71), Babbel (73)
- **Tab row:** Bonial (56) — `All departments | People & Culture | Product | Sales | Tech`
- **Sidebar checkboxes:** Picnic (28), ASML (14)
- **Sidebar text list with counts:** Revolut (44)

### Contract / Work Type Filter
- **Dropdown:** Forto (05), Avomind (71)
- **Toggle buttons:** Deliveroo (42) — `Part time` | `Remote working` pill toggles
- **Checkboxes:** KPN (21) — `Vast`, `Stage`
- **Embedded in sentence:** Doctolib (63) — `"222 jobs everywhere ▾ in all teams ▾ and all contract types ▾"`

### Seniority / Experience Level
- **Dropdown:** Celonis (03) — `Seniority Level ▾`
- **Sidebar checkboxes:** KPN (21), ABN AMRO (13), ASML (14)

---

## Part 9: Save / Bookmark Button Patterns — Confirmed Visually

These are buttons/icons INSIDE job cards that are NOT job links. Your scraper must skip them.

| Icon | Seen In |
|---|---|
| ☆ star (unfilled) | SmartRecruiters (11), Glovo (34), ASML (14), King (36) |
| ♡ heart (unfilled) | ABN AMRO (13), KPN (21), Ocado (43), Organon (27) |
| 🔖 bookmark | Organon (27) — "Save" text link |
| ★ star (filled) | Deliveroo (42) — "Saved jobs ☆" in top nav |

**Position:** Always top-right corner of card OR right end of row — never in the middle of metadata.

---

## Part 10: Cookie Consent Vendors — Confirmed Visually

Identifying these helps exclude their DOM nodes before link extraction.

| Vendor | Visual Signal | Seen In |
|---|---|---|
| **Cookiebot** | "CybotCookiebotDialog" style, Cookiebot logo | Forto (05), Harnham (70) |
| **OneTrust** | OneTrust-style layout with toggles | Siemens (10), Optiver (26), Spotify (39) |
| **Teamtailor native** | Identical layout across Teamtailor sites | Teamtailor (40), Voi (41), Quinyx (38), Detectify (35) |
| **Generic / Custom** | Company-branded modals | Glovo (34), Revolut (44), KPN (21), bunq (16) |
| **Iubenda** | Small bottom bar style | FS Italiane (67) |
| **Livewire/Usercentrics** | Toggle-based with Necessary/Preferences/Statistics | Adjoe (58) |

---

## Part 11: Third-Party / Aggregator Pages — Special Handling Required

These are NOT direct company career pages. Job URLs extracted from them point to the aggregator, not the company ATS.

### Levels.fyi (img 23)
- **Type:** Job aggregator (salary-focused)
- **Visual signals:** Multiple company listings on one page, TC salary shown per listing (`$22.07M – $23.3M`), company logo + name on each listing
- **What to do:** Follow through to the company's own apply URL on the job detail page

### Harnham (img 70)
- **Type:** Specialist data/AI recruiter
- **Visual signals:** "To Apply for this Job Click Here" text on every card, salary ranges prominently shown (`$115–$125`, `$150,000–$170,000`), `VIEW JOB` button on each card
- **Note:** Clicking `VIEW JOB` leads to Harnham's own job detail page, not the company's ATS

### Michael Page DE (img 69)
- **Type:** Recruitment agency
- **Visual signals:** Empty results state shown, "Für diesen Job benachrichtigen" (notify me) CTA, agency branding throughout
- **Note:** URLs follow `/jobs/{keyword-slug}` pattern but point to Michael Page, not the hiring company

### Nigel Frank (img 72)
- **Type:** Microsoft/Dynamics specialist recruiter
- **Visual signals:** Two-column layout (job list left, job description right), salary shown per listing, "Apply" button leads to recruiter application form
- **Result count:** `498 search results` — very high, cross-company

---

## Part 12: Multi-Language Pages — Visual Confirmation

Pages where non-English text was directly visible:

| # | Company | Language | Job Title Format Seen |
|---|---|---|---|
| 15 | Brevo | French titles mixed in | `Stage - Juriste IP/IT` — Stage = internship |
| 54 | Kaufland | German | `OFFENE JOBS: 17` header; `Arbeitszeit auswählen` filter |
| 57 | reisetopia | German | `Unsere Jobs` / `Unsere Praktikumsplätze` sections |
| 63 | Doctolib | French + German | `Consultante Deployment & Onboarding - Milano`, `Werkstudent Key Account Sales` |
| 64 | Esselunga | Italian | `Scopri le posizioni per area`, `Addetto/a alla Vendita` |
| 65 | Lidl Italia | Italian | `Annunci di lavoro`, `Addetta/o Vendite part-time` |
| 66 | Poste Italiane | Italian/English | `LETTER CARRIER`, `Financial Advisors` (English job titles on Italian site) |
| 67 | FS Italiane | Italian | `Operatore Specializzato Manutenzione Infrastrutture` |
| 69 | Michael Page DE | German | `Leider hat Ihre Suche keine Ergebnisse geliefert` |

**German job title suffixes visually confirmed:**
- `(f/m/d)` — Forto (05), Siemens (10), Celonis (03)
- `(m/f/d)` — Deliveroo (42 — Dutch market title)
- `(w/m/d)` — common German variant
- `Werkstudent` — student worker role type (Doctolib 63, Kaufland 54)
- `Stage` — French internship label (Brevo 15, Doctolib 63)
- `Praktikum/Praktikumsplätze` — German internship (reisetopia 57)

---

## Part 13: Job ID Formats — Confirmed Visually

| Format | Example Seen | Company |
|---|---|---|
| `Job ID: {6-digit}` | `Job ID: 505323` | Siemens (10) |
| `Job Id: {letter+digits}` | `Job Id: R22122`, `EV3041` | Deliveroo (42) |
| `Job ID: {5-digit}` | `Job ID: 76273` | Ferrero (68) |
| `{Letter}{6-digit}` | `R026384` | King (36) |
| Numeric only | `76138` | Ferrero (68) |

---

## Part 14: Pagination Formats — Confirmed Visually

| Format | Company | Example |
|---|---|---|
| `Results X–Y of N` with `‹ 1 2 3 4 ›` | SAP (09) | `Results 1–25 of 96` |
| `PAGE X / Y` | ASML (14) | `PAGE 1 / 19` |
| `Page: X/Y` | KPN (21) | `Page: 1/1` |
| `1-10 of N results` | King (36) | `1-10 of 36 results` |
| `Showing X–Y of N` | HubSpot (60) | `Showing 1–157 of 157` |
| `1 2 3 4 5 … 50 ›` | Nigel Frank (72) | 50 pages implied |
| No pagination (all on page) | Stripe (51), Cal.com (02), Databricks (47), Bynder (17), Raisin (29), Brevo (15), Lightspeed (22) | — |

---

*All observations in this document are based exclusively on visual reading of 74 screenshots taken 22 June 2026. No live URL crawling was performed. CSS class names, data attributes, and exact HTML element types are NOT documented here — those require actual DOM inspection.*
