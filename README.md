<img src="sponsorscout/data/sponsorscout.png" alt="SponsorScout" width="420">

# SponsorScout

[🇬🇧 English](#-english) · [🇮🇹 Italiano](#-italiano)

Find jobs that actually sponsor visas — scanned from official sources, stored
on your own computer.

---

# 🇬🇧 English

## 📖 Contents

- [Download](#-download)
- [What It Does](#-what-it-does)
- [Quick Start](#-quick-start)
- [The Five Tabs](#-the-five-tabs)
- [Scanning](#-scanning)
- [Where Your Data Lives](#-where-your-data-lives)
- [Troubleshooting](#-troubleshooting)
- [Building From Source](#-building-from-source)
- [Requirements](#-requirements)
- [License](#-license)

---

## 📥 Download

Installers are on the [GitHub Releases page](https://github.com/Kazake95/SponsorScout/releases):

| Platform | File |
|----------|------|
| Windows 10 / 11 | `sponsorscout-<version>-setup.exe` |
| Linux (Debian / Ubuntu) | `sponsorscout_<version>_amd64.deb` |

Pick the file for your platform. The app and its browser are bundled, so no
Python is needed.

---

## ✨ What It Does

- **Scans official sources only** — 8 ATS job boards (Ashby, Greenhouse,
  Lever, SmartRecruiters, Personio, Recruitee, Workable, Workday) through their
  public APIs, plus company career pages with a headless browser when needed.
- **Classifies every job** — sponsorship, relocation support, remote type and
  EU Blue Card eligibility, detected from the job description.
- **Extracts the experience requirement** — the exact figure the ad states
  (`4+`, `3-5 years`, `6 months`), or the seniority level (`Senior`), read in
  English, German, Italian, Dutch, French, Spanish and Portuguese.
- **Keeps everything local** — one SQLite file on your computer. Nothing is
  uploaded.
- **Tracks applications** — Saved → Applied → Interview → Offer → Rejected.
- **Two languages** — English and Italian, switchable at any time.

---

## 🚀 Quick Start

1. Download and install the package for your platform.
2. Launch SponsorScout. On first start, accept the prompt to run an initial
   scan (1–3 minutes).
3. Open **Search** to browse, and **Applications** to track what you apply to.

---

## 🗂 The Five Tabs

### 1. Dashboard
Totals for companies, verified jobs, sponsored, remote and EU Blue Card jobs,
plus top companies by sponsorship and jobs by country. **Rescan Companies**
starts a full scan; **Refresh** reloads the numbers.

### 2. Search
The main job browser.

- **Filter** by title, company, location, country, remote type, experience,
  sponsorship, Blue Card and relocation. Tick **Regex** to use a pattern in the
  text fields.
- **Dropdowns always show every value found in your data** — never narrowed by
  the current selection, so you can switch to any other value directly.
- **Filter values and table values are identical** and stay in one fixed
  (untranslated) form in both languages, so filters never break when you switch
  language. Only labels, headers and buttons are translated.
- **Experience** shows the requirement as stated: `4+`, `3-5`, `6 mo`, `None`
  when the ad explicitly asks for none, `Senior` for a level, `Mentioned` when
  it refers to experience without a figure, and `NA` when the ad never mentions
  it. Sorting is numeric, so `3-5` comes before `11+`. Hover for the original
  sentence.
- **Sort** by clicking any column header (click again to reverse).
- **Right-click** a row to open it in your browser or save it to Applications.
- **Pagination** — results are shown one page at a time (100, 200, 500 or 1000
  rows, default 500) with a result counter and ◀ / ▶ buttons, so large
  databases stay responsive and every row stays reachable.

### 3. Applications
Your pipeline. Select a saved job to set its status and add notes.

### 4. Tools
- **Scanner** — **Scan Now** runs a full scan; **Custom Scan** lets you pick
  companies and source types. **Pause** stops the scan with everything found so
  far already saved; **Resume** continues exactly the remaining companies, even
  after restarting the app. See [Scanning](#-scanning).
- **Scan History** — every past run; select one to read or download its
  per-company log. Paused runs show `cancelled` and become `resumed` when a
  later run finishes the remaining work.
- **Data Quality** — remove duplicate jobs/companies, clear expired jobs, or
  wipe all scanned data.
- **Freshness Check** — re-verify saved jobs against their live pages and mark
  dead listings as expired.

### 5. Data Management
Edit the company lists that get scanned (**ATS portals** and **Career
portals**). Changes apply on the next scan; **Reset to bundled defaults**
restores the original lists.

---

## 🔍 Scanning

### Full scan
The default. **Scan Now** (or the Dashboard's **Rescan Companies**) scans every
seeded company, because a partial scan would silently hide jobs you did not ask
for.

### Custom scan
**Tools → Custom Scan** lets you choose:

- **Source types** — *ATS portals* (fast, API-based) and/or *Career portals*
  (slower, crawled with a browser).
- **Companies** — tick the ones you want; filter or bulk-select to move fast.

A custom scan runs the exact same pipeline as a full scan, so result quality is
identical — only the scope and duration change. Use it to re-scan companies you
just edited. Custom runs appear as `custom` in Scan History.

### What a scan does
1. **ATS boards** — companies with a known ATS are pulled through the official
   job-board API.
2. **Career pages** — every company is also crawled through its own career page,
   so career-page-only companies are never skipped.
3. **Detail pages** — each job is checked for location, sponsorship,
   relocation, Blue Card evidence and experience. Verdicts are only set from
   explicit evidence, and weaker sources never overwrite stronger ones.
4. Results are saved to the local database and appear immediately.

Progress shows live (`ATS 12/46`, `Career 88/162`). A company with no open
roles counts as done.

---

## 💾 Where Your Data Lives

| Platform | Location |
|----------|----------|
| Windows | `%APPDATA%\SponsorScout` |
| Linux | `~/.sponsorscout` |

Contains `sponsorscout.db` (jobs, companies, applications, scan history),
`seeds/` (your editable company lists), `locale.json` (language) and raw scan
logs under `scan_output/`.

Copy `sponsorscout.db` to back everything up. Override the location with the
`SPONSORSCOUT_DATA_DIR` (or `SPONSORSCOUT_DB_PATH`) environment variable.

### Uninstalling

The Windows installer and the Linux `.deb` package remove SponsorScout-owned
user data during uninstall, including the SQLite database, editable seeds,
language setting and scan logs. The default locations are cleaned for the
current Windows user and for local Linux user accounts. Custom data paths are
also removed when their `SPONSORSCOUT_DATA_DIR` / `SPONSORSCOUT_DB_PATH`
environment variables are available to the uninstaller. Back up anything you
want to keep before uninstalling. The shared Playwright browser cache is not
removed because other applications may use it.

---

## 🧰 Troubleshooting

**The Dashboard looks empty after a scan.** Click **Refresh**. If still empty,
check the newest **Scan History** row — an `error` status, or failures in its
log, shows which companies returned nothing.

**"Chromium browser is not available".** Career pages are crawled with
Playwright. Installers bundle it; from source run
`python -m playwright install chromium`.

**Some companies returned no jobs.** They may have no open roles (`EMPTY` in
the log), or they may temporarily block automated access — try again later.
Nothing is dropped silently.

**The scan is slow.** The detail-page pass is the slow part, and it is bounded:
SponsorScout sizes its browser pool from your CPU/RAM, runs at below-normal
priority, and blocks images/fonts/media while crawling. The Dashboard stays
usable. You do not have to wait it out — press **Pause** (everything found is
saved) and **Resume** later, even after restarting the app.

**A job shows `?`.** `?` means *unknown*, never *no*. The ad had no explicit
evidence, so SponsorScout does not guess. Unknown jobs are never removed.

**Can I scan only some companies?** Yes — **Tools → Custom Scan**. Same
quality, only the scope changes.

**How do I search with a regular expression?** Tick **Regex** in **Search** and
type a pattern such as `(backend|platform).*engineer`. Case-insensitive; an
invalid pattern warns and falls back to a normal search.

**Does anything leave my computer?** No. The only outbound traffic is fetching
the job listings you asked for.

---

## 🏗 Building From Source

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install ".[dev]"          # pytest + pyinstaller
python -m playwright install chromium
```

**Windows installer:**
```powershell
.\build_exe.ps1               # -> dist\sponsorscout-<version>-setup.exe
```
(requires Inno Setup 6/7; bundles Playwright Chromium)

**Linux package (Debian / Ubuntu):**
```bash
./build_deb.sh                # -> dist/sponsorscout_<version>_amd64.deb
```
Run it as your normal user: **do not use `sudo`**. The script creates an
isolated build environment in `.build/deb-venv`, so it never modifies the
system Python. If Python's venv module is missing, install it once with
`sudo apt install python3-venv`, then run `./build_deb.sh` again.
Packaging the ~1.4 GB payload (PySide6 + bundled Chromium) is the slow step and
prints how long it took; the `.deb` only appears in `dist/` once it is complete.
For a much faster build that requires dpkg >= 1.21.18 to install, use
`DEB_COMPRESSION=zstd ./build_deb.sh`.

**Tests:**
```bash
python -m pytest sponsorscout/tests
```

---

## 📋 Requirements

- Python 3.10 or newer (to run from source; installers need nothing)
- Runtime: **PySide6**, **requests**, **playwright** (`requirements.txt`)
- Playwright Chromium: `python -m playwright install chromium`
- Dev/test: **pytest**, **pyinstaller** (`pip install ".[dev]"`)
- Linux packaging: `python3`, `python3-venv`, and `dpkg` (`sudo apt install python3-venv`)

---

## 📄 License

MIT — see [LICENSE](LICENSE).

---

# 🇮🇹 Italiano

## 📖 Indice

- [Scarica](#-scarica)
- [Cosa Fa](#-cosa-fa)
- [Avvio Rapido](#-avvio-rapido)
- [Le Cinque Schede](#-le-cinque-schede)
- [Scansione](#-scansione)
- [Dove Sono i Tuoi Dati](#-dove-sono-i-tuoi-dati)
- [Risoluzione Problemi](#-risoluzione-problemi)
- [Compilare dai Sorgenti](#-compilare-dai-sorgenti)
- [Requisiti](#-requisiti)
- [Licenza](#-licenza)

---

## 📥 Scarica

Gli installer sono nella [pagina GitHub Releases](https://github.com/Kazake95/SponsorScout/releases):

| Piattaforma | File |
|-------------|------|
| Windows 10 / 11 | `sponsorscout-<versione>-setup.exe` |
| Linux (Debian / Ubuntu) | `sponsorscout_<versione>_amd64.deb` |

Scegli il file per la tua piattaforma. L'app e il browser sono inclusi, quindi
non serve Python.

---

## ✨ Cosa Fa

- **Scansiona solo fonti ufficiali** — 8 bacheche ATS (Ashby, Greenhouse,
  Lever, SmartRecruiters, Personio, Recruitee, Workable, Workday) tramite le
  loro API pubbliche, più le pagine carriera delle aziende con un browser
  headless quando necessario.
- **Classifica ogni lavoro** — sponsorizzazione, trasferimento, tipo di lavoro
  remoto e idoneità alla Carta Blu UE, rilevati dalla descrizione.
- **Estrae l'esperienza richiesta** — la cifra esatta indicata
  (`4+`, `3-5 anni`, `6 mesi`), oppure il livello di seniority (`Senior`),
  riconosciuta in inglese, tedesco, italiano, olandese, francese, spagnolo e
  portoghese.
- **Mantiene tutto in locale** — un unico file SQLite sul tuo computer. Nulla
  viene caricato online.
- **Gestisce le candidature** — Salvata → Inviata → Colloquio → Offerta →
  Rifiutata.
- **Due lingue** — Italiano e Inglese, selezionabili in qualsiasi momento.

---

## 🚀 Avvio Rapido

1. Scarica e installa il pacchetto per la tua piattaforma.
2. Avvia SponsorScout. Al primo avvio accetta la richiesta di eseguire la
   scansione iniziale (1–3 minuti).
3. Apri **Cerca** per sfoglare e **Candidature** per gestire le candidature.

---

## 🗂 Le Cinque Schede

### 1. Pannello
Totali di aziende, lavori verificati, sponsorizzati, remoti e con Carta Blu
UE, più le migliori aziende per sponsorizzazione e i lavori per paese.
**Riscansiona Aziende** avvia una scansione completa; **Aggiorna** ricarica i
numeri.

### 2. Cerca
Il browser principale dei lavori.

- **Filtra** per posizione, azienda, località, paese, tipo di lavoro remoto,
  esperienza, sponsorizzazione, Carta Blu e trasferimento. Spunta **Regex** per
  usare un pattern nei campi di testo.
- **I menu a tendina mostrano sempre tutti i valori** presenti nei tuoi dati,
  mai ridotti alla selezione corrente: puoi passare direttamente a qualsiasi
  altro valore.
- **I valori dei filtri coincidono con quelli della tabella** e restano in una
  forma fissa (non tradotta) in entrambe le lingue, così i filtri non si
  rompono cambiando lingua. Solo etichette, intestazioni e pulsanti vengono
  tradotti.
- **Esperienza** mostra il requisito come indicato: `4+`, `3-5`, `6 mo`, `None`
  quando l'annuncio chiede esplicitamente nessuna esperienza, `Senior` per un
  livello, `Mentioned` quando si parla di esperienza senza cifre e `NA` quando
  l'annuncio non ne parla affatto. L'ordinamento è numerico, così `3-5`
  precede `11+`. Passa il mouse per la frase originale.
- **Ordina** cliccando l'intestazione di una colonna (clicca di nuovo per
  invertire).
- **Tasto destro** su una riga per aprirla nel browser o salvarla nelle
  Candidature.
- **Paginazione** — i risultati sono mostrati una pagina alla volta (100, 200,
  500 o 1000 righe, 500 per impostazione predefinita) con contatore e pulsanti
  ◀ / ▶, così i database grandi restano rapidi e ogni riga resta
  raggiungibile.

### 3. Candidature
Il tuo percorso. Seleziona un lavoro salvato per impostarne lo stato e
aggiungere note.

### 4. Strumenti
- **Scanner** — **Scansiona Ora** esegue una scansione completa; **Scansione
  Personalizzata** ti lascia scegliere aziende e tipi di fonte. **Pausa**
  interrompe la scansione con tutto ciò che è stato trovato già salvato;
  **Riprendi** continua esattamente le aziende restanti, anche dopo aver
  riavviato l'app. Vedi [Scansione](#-scansione).
- **Cronologia Scansioni** — ogni esecuzione passata; selezionane una per
  leggere o scaricare il registro per azienda. Le scansioni interrotte mostrano
  `cancelled` e diventano `resumed` quando un'esecuzione successiva completa il
  lavoro rimanente.
- **Qualità Dati** — rimuovi lavori/aziende duplicati, cancella lavori
  scaduti o elimina tutti i dati scansionati.
- **Verifica Aggiornamento** — riverifica i lavori salvati sulle pagine live e
  segna gli annunci non più disponibili come scaduti.

### 5. Gestione Dati
Modifica gli elenchi di aziende che vengono scansionati (**Portali ATS** e
**Portali Career**). Le modifiche hanno effetto dalla scansione successiva;
**Ripristina predefiniti** riporta gli elenchi originali.

---

## 🔍 Scansione

### Scansione completa
Quella predefinita. **Scansiona Ora** (o **Riscansiona Aziende** nel Pannello)
scansiona ogni azienda negli elenchi, perché una scansione parziale
nasconderebbe in silenzio dei lavori che non hai chiesto.

### Scansione personalizzata
**Strumenti → Scansione Personalizzata** ti lascia scegliere:

- **Tipi di fonte** — *Portali ATS* (veloci, via API) e/o *Portali Career*
  (più lenti, esplorati con un browser).
- **Aziende** — spunta quelle che ti servono; usa il filtro o la selezione
  rapida per spostarti in fretta.

Una scansione personalizzata usa esattamente la stessa pipeline di una scansione
completa, quindi la qualità dei risultati è identica: cambiano solo ambito
e durata. Usala per riscanare le aziende che hai appena modificato. Le
esecuzioni personalizzate compaiono come `custom` nella Cronologia Scansioni.

### Cosa fa una scansione
1. **Bacheche ATS** — le aziende con un ATS noto vengono interrogate tramite
   l'API ufficiale della bacheca.
2. **Pagine carriera** — ogni azienda viene esplorata anche sulla propria
   pagina carriera, quindi le aziende con la sola pagina carriera non vengono
   saltate.
3. **Pagine di dettaglio** — ogni lavoro viene verificato per località,
   sponsorizzazione, trasferimento, Carta Blu UE ed esperienza. I verdetti
   vengono impostati solo con evidenze esplicite e le fonti più deboli non
   sovrascrivono mai quelle più forti.
4. I risultati vengono salvati nel database locale e compaiono subito.

L'avanzamento è live (`ATS 12/46`, `Carriere 88/162`). Un'azienda senza
posizioni aperte conta come completata.

---

## 💾 Dove Sono i Tuoi Dati

| Piattaforma | Posizione |
|-------------|-----------|
| Windows | `%APPDATA%\SponsorScout` |
| Linux | `~/.sponsorscout` |

Contiene `sponsorscout.db` (lavori, aziende, candidature, cronologia scansioni),
`seeds/` (i tuoi elenchi modificabili), `locale.json` (lingua) e i log grezzi
in `scan_output/`.

Copia `sponsorscout.db` per salvare tutto. Puoi cambiare la posizione con la
variabile d'ambiente `SPONSORSCOUT_DATA_DIR` (o `SPONSORSCOUT_DB_PATH`).

### Disinstallazione

L'installer Windows e il pacchetto Linux `.deb` rimuovono durante la
disinstallazione tutti i dati locali di SponsorScout: database SQLite, elenchi
modificabili, lingua e log delle scansioni. Vengono pulite le posizioni
predefinite dell'utente Windows e degli account Linux locali. Vengono rimosse
anche le posizioni personalizzate se le variabili `SPONSORSCOUT_DATA_DIR` /
`SPONSORSCOUT_DB_PATH` sono disponibili al programma di disinstallazione.
Fai una copia di cio che vuoi conservare prima di disinstallare. La cache
condivisa dei browser Playwright non viene rimossa, perche potrebbe servire
ad altre applicazioni.

---

## 🧰 Risoluzione Problemi

**Il Pannello sembra vuoto dopo una scansione.** Clicca **Aggiorna**. Se è
ancora vuoto, controlla l'ultima riga della **Cronologia Scansioni**: uno stato
`error`, o errori nel registro, indicano quali aziende non hanno restituito
lavori.

**"Chromium browser is not available".** Le pagine carriera vengono esplorate
con Playwright. Gli installer lo includono; da codice sorgente esegui
`python -m playwright install chromium`.

**Alcune aziende non hanno restituito lavori.** Potrebbero non avere posizioni
aperte (`EMPTY` nel log) oppure bloccare temporaneamente l'accesso automatico:
riprova più tardi. Nulla viene perso in silenzio.

**La scansione è lenta.** La fase di dettaglio è la più lenta ed è limitata:
SponsorScout dimensiona i browser in base a CPU/RAM, gira con priorità
inferiore al normale e blocca immagini/font/media. Il Pannello resta
utilizzabile. Non devi aspettare tutto: premi **Pausa** (tutto ciò che è stato
trovato è già salvato) e **Riprendi** più tardi, anche dopo aver riavviato
l'app.

**Un lavoro mostra `?`.** `?` significa *sconosciuto*, mai *no*. L'annuncio non
conteneva evidenze esplicite, quindi SponsorScout non ipotizza nulla. I lavori
sconosciuti non vengono mai rimossi.

**Posso scansionare solo alcune aziende?** Sì — **Strumenti → Scansione
Personalizzata**. Stessa qualità, cambia solo l'ambito.

**Come si cerca con un'espressione regolare?** Spunta **Regex** in **Cerca** e
digita un pattern come `(backend|platform).*engineer`. Non distingue
maiuscole/minuscole; un pattern non valido mostra un avviso e ripiega su una
ricerca normale.

**Qualcosa esce dal mio computer?** No. L'unico traffico in uscita sono gli
annunci lavori che hai chiesto di scaricare.

---

## 🏗 Compilare dai Sorgenti

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install ".[dev]"          # pytest + pyinstaller
python -m playwright install chromium
```

**Installer Windows:**
```powershell
.\build_exe.ps1               # -> dist\sponsorscout-<versione>-setup.exe
```
(richiede Inno Setup 6/7; include Playwright Chromium)

**Pacchetto Linux (Debian / Ubuntu):**
```bash
./build_deb.sh                # -> dist/sponsorscout_<versione>_amd64.deb
```
Esegui lo script come utente normale: **non usare `sudo`**. Lo script crea un
ambiente di compilazione isolato in `.build/deb-venv`, quindi non modifica mai
Python di sistema. Se manca il modulo venv, installalo una sola volta con
`sudo apt install python3-venv`, poi esegui di nuovo `./build_deb.sh`.
La compressione del payload da ~1,4 GB (PySide6 + Chromium incluso) e il passo
piu lento e ne stampa la durata; il file `.deb` compare in `dist/` solo quando
e completo. Per una build molto piu veloce, che richiede dpkg >= 1.21.18 per
l'installazione, usa `DEB_COMPRESSION=zstd ./build_deb.sh`.

**Test:**
```bash
python -m pytest sponsorscout/tests
```

---

## 📋 Requisiti

- Python 3.10 o successivo (per eseguire da sorgenti; gli installer non richiedono nulla)
- Runtime: **PySide6**, **requests**, **playwright** (`requirements.txt`)
- Playwright Chromium: `python -m playwright install chromium`
- Solo sviluppo/test: **pytest**, **pyinstaller** (`pip install ".[dev]"`)
- Pacchetto Linux: `python3`, `python3-venv` e `dpkg` (`sudo apt install python3-venv`)

---

## 📄 Licenza

MIT — vedi [LICENSE](LICENSE).
