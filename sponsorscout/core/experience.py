"""Shared experience-requirement extraction (FIX P0-30, unified 2026-10-09).

Single source of truth for the four Experience columns:

  Experience Required   e.g. "5-8 years", "3+ years", "6 months", "None required"
  Experience Min Years  numeric, for sorting/filtering
  Experience Level      Internship / Junior / Mid / Senior / Lead / Executive
  Experience Source     api_field > api_description > card_context > title_inference

Previously an identical copy of this engine lived in each scanner
(``ats_scanner.py`` / ``career_scanner.py`` "kept in sync" by hand). They had
already drifted: the ATS copy carried FIX P0-46 ("Four or more years") while
the career copy did not, so the SAME job description scored differently
depending on which engine crawled it. Both scanners now import from here,
which is what makes ``test_multilingual_ats_career_parity`` hold by
construction instead of by discipline.

Design rules (mirrors the visa detector's evidence discipline):
  * Sentence-scoped, never keyword-alone. A number counts only when its own
    clause is about work experience (_EXP_ANCHOR) AND no disqualifier sits
    within +/-45 chars (_EXP_BLOCK_NEAR). That window is what keeps
    "at least 18 years old", "founded 25 years ago", "fixed-term contract of
    2 years", "visa valid for 3 years" and "notice period of 3 months" out.
  * Numeric years are reported ONLY when explicitly written. The LEVEL may be
    inferred from the job title; "Experience Source" always records which
    happened so an inference is never mistaken for a stated fact.
  * Multilingual by construction (EN/DE/IT/NL/FR/ES/PT/PL/CS/SV/DA/NO/FI).
    The seed set is heavily continental-European, so an English-only matcher
    would under-report.
  * Title inference beats year-band inference: a "Senior Consultant" asking
    for 4 years is Senior, not Mid.
"""
from __future__ import annotations

import re
from html import unescape

__all__ = [
    "extract_experience",
    "apply_experience_to_record",
    "_jd_plain",
    "_exp_num",
    "_exp_level_from_years",
    "_exp_level_from_text",
    "_exp_scan_numbers",
]

_EXP_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "a": 1, "an": 1,
    "ein": 1, "eine": 1, "eins": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fuenf": 5, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwoelf": 12, "zwölf": 12,
    "uno": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6,
    "sette": 7, "otto": 8, "nove": 9, "dieci": 10,
    "een": 1, "twee": 2, "drie": 3, "vijf": 5, "zes": 6, "zeven": 7,
    "negen": 9, "tien": 10,
    "un": 1, "une": 1, "deux": 2, "trois": 3, "cinq": 5, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10,
    "dos": 2, "cuatro": 4, "cinco": 5, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10,
    # FIX LOCEXP-1 (2026-10-09): fill the gaps + Portuguese. Digits dominate
    # in ads, but spelled-out "trois/quatre ans", "tres/cinco años",
    # "três/cinco anos", "vier/vijf jaar" are everyday phrasings in
    # FR/ES/PT/NL postings and previously fell through to "Mentioned".
    # Every entry still needs a year/month unit right after it plus an
    # experience anchor, so short words ("un", "um", "an") cannot fire alone.
    # "cinco"/"nove" already cover PT with the same values and are not repeated.
    "una": 1, "undici": 11, "dodici": 12,                       # IT (+ES una)
    "vier": 4, "acht": 8, "elf": 11, "twaalf": 12,               # NL
    "quatre": 4, "six": 6, "onze": 11, "douze": 12,             # FR
    "uno": 1, "tres": 3, "seis": 6, "once": 11, "doce": 12,     # ES
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "três": 3,          # PT
    "quatro": 4, "sete": 7, "oito": 8, "dez": 10,
}
_EXP_WORD_ALT = "|".join(sorted((re.escape(w) for w in _EXP_WORD_NUM),
                                key=len, reverse=True))

_EXP_YEAR_UNIT = (r"(?:years?|yrs?\.?|jahre?n?|anni|anno|jaar|jaren|"
                  r"ans|an|a[nñ]os|a[nñ]o|"
                  # FIX LOCEXP-2 (2026-10-09): PL/CS/Nordic year words. Short
                  # ones ("lat", "let", "rok") are safe because a unit only
                  # counts directly after a number inside an anchored clause.
                  r"lata?|rok(?:u|y)?|let|l[ée]ta|år|års|vuotta|vuoden)")
_EXP_MONTH_UNIT = (r"(?:months?|mon\.?|monate?n?|mesi|mese|maanden|maand|"
                   r"mois|meses|m[êe]s|"
                   # FIX LOCEXP-2: PT "mês" + PL/CS/FI/Nordic month words,
                   # with the unaccented spellings PL/CS ads commonly use.
                   r"miesi[ęe]cy|miesi[ąa]ce|miesi[ąa]c|"
                   r"m[ěe]s[íi]ce?|m[ěe]s[íi]c[ůu]|"
                   r"kuukautta|kuukauden|månader|måneder)")

# The number must sit in a clause genuinely about work experience.
_EXP_ANCHOR = re.compile(
    r"experien|berufserfahrung|erfahrung|praxis|esperienz|ervaring|"
    r"exp[ée]rien|experienc|experienci|"
    r"similar role|comparable role|equivalent role|relevant|professional|"
    r"requirements?|qualif\w*|must[- ]haves?|anforderungen|"
    r"voraussetzungen|requisiti|requisitos|vereisten|wer du bist|"
    r"what you|das bringen|nous recherchons|buscamos|"
    r"hands[-\s]?on|proven|track record|background in|working (?:in|with|as)|"
    r"seniority|vergleichbarer? (?:position|rolle)|einschl[äa]gig|"
    r"ruolo simile|ambito|settore|soortgelijke|vergelijkbare|"
    r"poste similaire|puesto similar|en el (?:sector|[áa]rea)|"
    # FIX LOCEXP-3 (2026-10-09): PL/CS/Nordic anchors + PT "experiência"
    # (previously PT numbers only cleared the weak tier).
    r"do[śs]wiadczen|zku[šs]enost|erfarenhet|erfaring|kokemus|experi[êe]ncia",
    re.I)

_EXP_YEARS_IN = re.compile(r"(?:\d{1,2}|" + _EXP_WORD_ALT + r")\s*(?:\+|plus)?\s*(?:years?|yrs?|months?)\s+(?:in|of|as)\s+[A-Za-z]", re.I)

# Disqualifiers, checked in a tight window around the number so a sentence
# that merely also mentions a degree is not discarded wholesale.
_EXP_BLOCK_NEAR = re.compile(
    r"\b(?:old|of age|age of|ago|"
    r"last|past|next|recent|"
    # FIX LOCEXP-5 (2026-10-09): "since" words. "Wij zijn sinds 10 jaar
    # actief" (company tenure) was extracted as "10 years"/Lead because only
    # EN "since" was listed. All four are unambiguous in their language.
    r"founded|established|since|sinds|seit|depuis|desde|anniversar|"
    # FIX LOCEXP-5: PL/FI/Nordic/CS "years ago" phrasings. Deliberately the
    # full "[unit] + ago-word" compounds, NOT the bare words: bare "temu" is
    # also a marketplace brand ("selling on Temu"), bare "sitten"/"tillbaka"
    # also mean "then"/"welcome back", and bare "před" means "before".
    r"(?:lat|lata|miesi[ęe]cy)\s+temu|"
    r"(?:vuotta|vuoden|kuukautta)\s+sitten|"
    r"(?:år|års)\s+(?:tillbaka|tilbage|tilbake|siden)|před\s+\d|"
    r"fixed[-\s]?term|befristet|tempo determinato|"
    r"contract|vertrag|contratto|duur|dur[ée]e|duration|"
    r"visa|permit|warrant|guarantee|garantie|"
    r"degree|bachelor|master|phd|doctora|studi|studium|laurea|"
    r"we(?:'|\s)?(?:ve|have)\s+(?:over|more than|than|nearly|almost|\d)|"
    r"our (?:company|team|group|history|story|brand)|"
    r"the company (?:has|was|is|been)|nous (?:avons|existons)|"
    # FIX LOCEXP-5b: PL/CS "the company exists [N] years" tenure. The bare
    # verbs ("istnieje" = "there is") are too common to block alone, so only
    # the "firma + exists" compound counts.
    r"firma\s+(?:istnieje|existuje)|"
    r"abbiamo|la nostra azienda|im unternehmen|in the company|"
    r"in azienda|chez nous|ons bedrijf|wij bestaan|"
    r"universit|school|apprenticeship duration|"
    r"notice period|k[üu]ndigungsfrist|preavviso|"
    # FIX P16 (2026-10-04): PERKS AND TENURE ARE NOT EXPERIENCE.
    # LOOP (run 20261003T233023) wrote "Experience Required = 1 years" on 13
    # of its 17 rows. The JD says "EXPERIENCE  Experienced" -- no number
    # anywhere. The phantom came from the benefits copy:
    #   "4-day Workweek At LOOP you can choose between a 5-day or a 4-day
    #    workweek AFTER ONE YEAR WITH US"
    # a <=100-char fragment, so the weak tier scanned it, and nothing in the
    # +/-45 window disqualified it. Perk, loyalty and probation wording now
    # does. "with us" / "bei uns" / "da noi" is the giveaway: it describes
    # time SPENT AT THE EMPLOYER, never experience brought to the job.
    r"with us\b|bei uns|da noi|chez nous|bij ons|con nosotros|"
    r"workweek|work week|working week|arbeitswoche|settimana lavorativa|"
    r"holiday|vacation|urlaub|ferien|vakantie|vacanze|cong[ée]s|"
    r"sabbatical|probation|probezeit|periodo di prova|proeftijd|"
    r"bonus|pension|insurance|versicherung|assicurazione|"
    r"anniversary|jubil[äa]um|loyalty|tenure|betriebszugeh[öo]rigkeit|"
    r"once a year|per year|every year|annually|j[äa]hrlich|ogni anno|"
    r"pay ri[sz]e|salary review|gehaltserh[öo]hung)\b", re.I)

_EXP_NONE = re.compile(
    r"(no (?:prior |previous |work |professional )?experience (?:is )?"
    r"(?:required|necessary|needed)|"
    r"without (?:prior |previous )?experience|"
    r"keine (?:berufserfahrung|vorkenntnisse)|ohne (?:vor)?erfahrung|"
    r"nessuna esperienza (?:richiesta|necessaria)|senza esperienza|"
    r"geen ervaring (?:vereist|nodig)|"
    r"aucune exp[ée]rience (?:requise|n[ée]cessaire)|"
    r"sin experiencia (?:previa)?|no se requiere experiencia|"
    r"entry[-\s]?level|no experience|"
    # FIX LOCEXP-6 (2026-10-09): PT/PL/CS/Nordic "no experience" phrasings.
    r"sem experi[êe]ncia|nenhuma experi[êe]ncia|bez do[śs]wiadczenia|"
    r"bez zku[šs]enosti|ingen erfarenhet|utan erfarenhet|"
    r"ingen erfaring|uten erfaring|ei kokemusta)", re.I)

_EXP_LEVEL_PATTERNS = [
    ("Internship", re.compile(
        r"\b(intern(?:ship)?|internship|praktikum|praktikant|werkstudent|"
        r"working student|stage(?:air)?|stagiaire|stagista|tirocini|"
        r"becari|pr[áa]cticas|alternance|apprentice|apprendist|"
        r"ausbildung|azubi|lehrling|summer analyst|"
        # FIX LOCEXP-7: PL/CS intern words.
        r"sta[żz]ysta|st[áa][žz]ista)\b", re.I)),
    ("Executive", re.compile(
        r"\b(chief|c[etofi]o\b|cxo|vp\b|vice[-\s]president|svp|evp|"
        r"managing director|general manager|gesch[äa]ftsf[üu]hrer|"
        r"head of|leiter(?:in)?\b|direttore|directeur|director\b|"
        # "Partner" alone matched "Partner Solution Architect", "Partner
        # Manager" and "HR Business Partner" — none are executives.
        r"(?:managing|equity|founding|general)\s+partner\b|"
        r"amministratore)\b", re.I)),
    ("Lead", re.compile(
        r"\b(lead\b|leader\b|principal\b|"
        r"staff(?:\s+\w+){0,2}\s+(?:engineer|scientist|designer|developer|"
        r"researcher|analyst)|architect\b|team ?lead|tech ?lead|"
        r"capo(?:squadra)?|responsabile|teamleiter)\b", re.I)),
    ("Senior", re.compile(
        r"\b(senior|sr\.?\b|snr\b|experienced|expert(?:e)?\b|"
        r"esperto|senior[-\s]?level|erfahrene[rn]?|"
        # FIX LOCEXP-7: PL "starszy" (senior).
        r"starszy)\b", re.I)),
    ("Mid", re.compile(
        r"\b(mid[-\s]?(?:level|weight)|intermediate|regular\b|"
        r"medior|confirm[ée]\b)\b", re.I)),
    ("Junior", re.compile(
        r"\b(junior|jr\.?\b|graduate|grad\b|entry[-\s]?level|entry\b|"
        r"einsteiger|berufseinsteiger|absolvent|neolaureat|"
        r"d[ée]butant|reci[ée]n titulad|starter|trainee|"
        r"associate\b|assistant\b|"
        # FIX LOCEXP-7: PL "młodszy" (junior).
        r"m[łl]odszy)\b", re.I)),
]

# "Senior Care Assistant" is not a senior role; a role that TALKS TO senior
# people is not itself senior.
_EXP_LEVEL_FALSE = re.compile(
    r"\b(senior (?:care|living|citizen|home|school|resident|manager of care)|"
    r"junior (?:school|college|suite)|"
    r"director of (?:nursing|care)|seniorenheim|seniorenbetreuung|"
    r"(?:with|to|for|among|across|manage|managing|engage|engaging|"
    r"influence|influencing|present(?:ing)? to|report(?:ing)? to|"
    r"partner(?:ing)? with|work(?:ing)? with|liais(?:e|ing) with)\s+"
    r"(?:our\s+|the\s+|various\s+|multiple\s+|key\s+|"
    r"internal\s+|external\s+|c-level\s+)*"
    r"senior\s+(?:stakeholder|leader|management|leadership|executive|"
    r"colleague|team|member|client|partner|sponsor|manager)s?|"
    r"senior\s+(?:stakeholder|leadership|management)\b)", re.I)

# A seniority word in body text only counts when the sentence is about the
# person being hired. Without this, "you will lead a team for the next 2
# years" scored the row as a Lead role.
_EXP_HIRING_CUE = re.compile(
    r"(we are (?:looking|seeking|hiring)|are you an?|you are an?|"
    r"looking for an?|seeking an?|hiring an?|join us as|as an?\s|"
    r"the role|this role|position of|role of|vacancy|opening for|"
    r"wir suchen|du bist|sie sind|als\s|stelle als|"
    r"cerchiamo|sei un|ricerchiamo|posizione di|"
    r"wij zoeken|je bent|functie van|"
    r"nous recherchons|vous [êe]tes|poste de|"
    r"buscamos|eres un|puesto de|"
    # FIX LOCEXP-8 (2026-10-09): PL/CS/Nordic hiring cues.
    r"szukamy|poszukujemy|hled[áa]me|vi s[öo]ker|vi s[øo]ger|etsimme)", re.I)

_EXP_SENT_SPLIT = re.compile(r"(?<=[.!?;:])\s+|[\n\r]+|\s*[•·▪▸–—]\s+")

# --- Requirements-section awareness (bare requirement fragments) ----------
# _EXP_SENT_SPLIT breaks at ':', bullets and newlines, so "Requirements: 3-5
# years" can arrive as the bare fragment "3-5 years" with the anchor word
# stranded in the header. These headers re-open the gate: a fragment under a
# requirements/qualifications heading, or any short fragment (a list bullet,
# <=100 chars), may be scanned even without an anchor. Disqualifiers and all
# numeric rules still apply; a strong (anchored) hit always wins.
_EXP_REQ_HEADER = re.compile(
    r"^(?:experience|requirements?|qualifications?|must[- ]haves?|"
    r"what you(?:'?ll)? (?:bring|need|have)|what we(?:'?re| are) looking for|"
    r"about you|your (?:profile|background|skills|experience)|"
    r"profil(?:e)?|we expect|ideal candidate|key (?:skills|qualifications)|"
    r"minimum (?:qualifications?|requirements?)|preferred qualifications?|"
    r"skills required|about the role|role requirements|who you are|"
    r"anforderungen|voraussetzungen|wer du bist|das erwarten wir|"
    r"das bringen sie mit|berufserfahrung|erfahrung|"
    r"requisiti|profilo|chi cerchiamo|esperienza|"
    r"ervaring|werkervaring|eisen|vereisten|profiel|"
    r"nous recherchons|exp[\u00e9e]rience|qu\u00e9 buscas|buscamos|"
    r"perfil|"
    # FIX LOCEXP-8: PL/CS/Nordic/FI requirement headers. Full-line anchored,
    # so short words ("krav") are safe here.
    r"wymagania|kwalifikacje|po[žz]adavky|kvalifikace|krav|"
    r"kvalifikationer|kvalifikasjoner|vaatimukset)$",
    re.I)

# Section headings that end requirements scope - after one of these a relaxed
# fragment no longer counts, so marketing numbers ("About us: 25 years of
# experience") stay unclaimed by the weak tier.
_EXP_SECTION_END = re.compile(
    r"^(?:about (?:us|the company)|our (?:story|history|mission|values|"
    r"culture|offer)|what we (?:offer|provide|value)|we (?:offer|provide)|"
    r"benefits?|perks?|how to apply|apply (?:now|by)|next steps|"
    r"hiring process|equal opportunit|diversity|privacy|company culture|"
    r"chi siamo|cosa ti offriamo|la nostra (?:missione|azienda)|"
    r"wir bieten|[\u00fc]ber uns|unser angebot|ons aanbod|wij bieden|"
    r"ce que nous offrons|notre entreprise|qu\u00e9 ofrecemos|nuestra empresa|"
    r"bewerbung|candidatura|"
    # FIX LOCEXP-8: PL/CS/Nordic/FI "about us / what we offer" headings.
    r"o nas|oferujemy|o n[áa]s|nab[íi]z[íi]me|om oss|vi erbjuder|"
    r"vi tilbyr|vi tilbyder|meist[äa]|tarjoamme)$",
    re.I)

# Strict experience NOUN - narrower than _EXP_ANCHOR (no 'relevant' /
# 'professional' / 'working in'): the mention fallback that yields
# "Mentioned" instead of NA may only fire when the JD actually names
# experience in some language.
_EXP_MENTION_NOUN = re.compile(
    r"experien|berufserfahrung|erfahrung|vorkenntnis|"
    r"esperienz|exp\u00e9rience|ervaring|experiencia|experi[e\u00ea]nc|"
    r"do[śs]wiadczen|zku[šs]enost|erfarenhet|erfaring|kokemus|experi[êe]ncia",
    re.I)

_EXP_SOURCE_RANK = {"": 0, "none": 0, "title_inference": 1,
                    "card_context": 2, "api_description": 3, "detail_text": 3,
                    "api_field": 4}

# ATS-published seniority vocabulary -> canonical level. Employer-set, so it
# outranks anything inferred from a title or from prose.
_EXP_ATS_LEVEL = {
    "internship": "Internship", "intern": "Internship", "student": "Internship",
    "entry level": "Junior", "entry_level": "Junior", "entry": "Junior",
    "graduate": "Junior", "junior": "Junior", "associate": "Junior",
    "mid level": "Mid", "mid-level": "Mid", "intermediate": "Mid",
    "experienced": "Mid", "professional": "Mid",
    "mid-senior level": "Senior", "mid_senior_level": "Senior",
    "senior level": "Senior", "senior": "Senior", "expert": "Senior",
    "lead": "Lead", "principal": "Lead", "staff": "Lead", "manager": "Lead",
    "director": "Executive", "executive": "Executive", "vp": "Executive",
    "c-level": "Executive", "chief": "Executive",
}


def _jd_plain(raw, limit=20000):
    """HTML/markup -> plain text for experience extraction.

    Several ATS APIs already return the FULL job description in the same
    response used for titles (Ashby descriptionPlain, Greenhouse content,
    Workable description, Lever/Recruitee description). Feeding that straight
    in costs ZERO extra HTTP requests.

    Greenhouse returns HTML-ESCAPED markup ("&lt;p&gt;"); stripping tags before
    unescaping would leave every tag in the text, so unescape comes FIRST.
    """
    if not raw:
        return ""
    txt = str(raw)
    if "&lt;" in txt or "&gt;" in txt or "&amp;" in txt:
        txt = unescape(txt)
    txt = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", txt)
    txt = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h[1-6]>", "\n", txt)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = unescape(txt)
    txt = re.sub(r"[ \t\xa0]+", " ", txt)
    txt = re.sub(r"\n\s*\n+", "\n", txt)
    return txt.strip()[:limit]


def _exp_num(tok):
    tok = (tok or "").strip().lower().rstrip(".")
    if tok.isdigit():
        return int(tok)
    return _EXP_WORD_NUM.get(tok)


def _exp_level_from_years(lo, hi):
    # For a stated range use the midpoint: "5-8 years" is a Senior ask, but
    # anchoring on the lower bound alone would call it Mid.
    if lo is not None and hi is not None:
        v = (lo + hi) / 2.0
    else:
        v = lo if lo is not None else hi
    if v is None:
        return ""
    if v <= 0:
        return "Entry"
    if v <= 2:
        return "Junior"
    if v < 5:
        return "Mid"
    if v < 10:
        return "Senior"
    return "Lead"


def _exp_level_from_text(text):
    if not text:
        return ""
    if _EXP_LEVEL_FALSE.search(text):
        return ""
    for level, rx in _EXP_LEVEL_PATTERNS:
        if rx.search(text):
            return level
    return ""


def _exp_scan_numbers(text):
    """Return the strongest explicit experience statement, or None.

    Two tiers, because _EXP_SENT_SPLIT breaks at ':', bullets and newlines
    and can strand a bare fragment ("Requirements: 3-5 years" arrives as just
    "3-5 years") away from the anchor words in its header:

    * STRONG - the fragment itself names experience (anchor / years-in).
      Anchored hits are unambiguous: they always win, lowest minimum first
      (the historic behaviour).
    * WEAK - the fragment sits under a requirements/qualifications heading
      (_EXP_REQ_HEADER, state persists until _EXP_SECTION_END) or is a short
      list fragment (<=100 chars). Used only when no strong hit exists.

    Every candidate, strong or weak, must clear the +/-45 char disqualifier
    window, so ages, contract durations and company-tenure marketing never
    become experience requirements.
    """
    strong_best = None
    weak_best = None
    in_req = False
    for sent in _EXP_SENT_SPLIT.split(text or ""):
        sent = (sent or "").strip()
        if not sent:
            continue
        probe = sent.strip(" *#>_:-")
        if _EXP_REQ_HEADER.match(probe):
            in_req = True
            continue
        if _EXP_SECTION_END.match(probe):
            in_req = False
            continue
        if len(sent) > 600:
            continue
        strong = bool(_EXP_ANCHOR.search(sent) or _EXP_YEARS_IN.search(sent))
        if not strong and not (in_req or len(sent) <= 100):
            continue
        num = rf"(?:\d{{1,2}}|{_EXP_WORD_ALT})"
        unit = rf"(?:{_EXP_YEAR_UNIT}|{_EXP_MONTH_UNIT})"
        # FIX P0-46: "Four or more years" / "4 or more years" produced
        # required="Mentioned", level=Unknown. "or" was only listed as a RANGE
        # connector, so the parser demanded a second NUMBER after it and
        # "more" is not one -- the whole match then failed. Every sibling
        # phrasing already worked ("at least four years", "minimum four
        # years", "Four+ years", "Four years"), which is why this stayed
        # hidden. It is NOT a words-vs-digits problem: the digit form failed
        # identically. Also accepts the restated-numeral form American
        # Express uses, "Four (4) or more years".
        rx = re.compile(
            rf"(?<![\w.,/-])({num})\s*(?:\+|plus)?\s*"
            rf"(?:\(\s*\d{{1,2}}\s*\)\s*)?"
            rf"(?P<ormore>\b(?:or|and|o|oder|of|ou|y)\s+"
            # FIX LOCEXP-4: PT "ou mais" ("3 ou mais anos"). FR "ou"+"mais"
            # adjacency is ungrammatical, so this cannot misfire on French.
            rf"(?:more|above|greater|over|higher|plus|pi[uù]|mehr|meer|"
            rf"m[aá]s|mais|superiore?)\b\s*)?"
            rf"(?:(?:-|–|—|\bto\b|\bbis\b|\ba\b|\btot\b|\b[àa]\b|\bund\b|"
            rf"\be\b|\by\b|\bet\b|\bor\b|\bof\b)\s*"
            rf"({num})\s*(?:\+|plus)?\s*)?"
            rf"({unit})\b", re.I)
        for m in rx.finditer(sent):
            _g = m.groups()
            lo, hi = _exp_num(m.group(1)), _exp_num(_g[2])
            u = _g[3].lower()
            if lo is None:
                continue
            # "a month-end close process" / "year-end reporting": the unit is
            # part of a compound noun, not a duration.
            if re.match(r"\s*[-\u2010-\u2015](?:end|on-end|round)", sent[m.end():]):
                continue
            # A bare article is only a real quantity when something
            # quantifies it, else "a month-end" produces a phantom 1.
            if m.group(1).strip().lower() in {"a", "an"} and not re.search(
                    r"\b(?:at least|minimum|min\.|over|more than|within|after|"
                    r"first|least)\b", sent[:m.start()], re.I):
                continue
            win = sent[max(0, m.start() - 45):m.end() + 45]
            if _EXP_BLOCK_NEAR.search(win):
                continue
            months = bool(re.fullmatch(_EXP_MONTH_UNIT, u, re.I))
            # Word boundaries are mandatory: an unanchored "[üu]ber" matched
            # inside "K-uber-netes" and turned "6 months" into "6+ months".
            # FIX LOCEXP-4: PT "pelo menos"/"ou mais"/"mais de", ES "más de",
            # PL "co najmniej", CS "nejméně"/"minimálně", Nordic "minst", FI
            # "vähintään". "mais/más de" is comma-guarded: FR ", mais de ..."
            # ("but ...") must not turn "5 ans" into "5+ ans".
            plus = bool(m.group("ormore")) or bool(re.search(
                r"\+|\b(?:plus|at least|minimum|min\.|mindestens|almeno|"
                r"minimaal|au moins|al menos|over|more than|[üu]ber|oltre|"
                r"upwards of|no less than|m[ií]nimo|minimo de|mindest|"
                r"ten minste|minstens|pelo\s+menos|ou\s+mais|co\s+najmniej|"
                r"minst|nejm[ée]n[ěe]|minim[áa]ln[ěe]|v[äa]hint[äa][äa]n)\b|"
                r"(?<!, )(?<!,)(?:mais\s+de|m[áa]s\s+de)\b", sent, re.I))
            if hi is not None and hi < lo:
                hi = None
            lo_y = lo / 12.0 if months else float(lo)
            hi_y = (hi / 12.0 if months else float(hi)) if hi is not None else None
            if lo_y > 40:
                continue
            cand = (lo_y, hi_y, months, plus, lo, hi, sent[:300])
            # Prefer the lowest stated minimum: "3-5 years" beats a stray "10".
            # Anchored (strong) candidates are kept apart from relaxed (weak)
            # ones so a stray bullet can never outrank an explicit statement.
            if strong:
                if strong_best is None or lo_y < strong_best[0]:
                    strong_best = cand
            elif weak_best is None or lo_y < weak_best[0]:
                weak_best = cand
    return strong_best or weak_best


def extract_experience(text="", title=""):
    """Detect required experience from JD text and/or job title.

    Returns a dict: required / min_years / max_years / level / evidence / source.
    Numeric years appear ONLY when explicitly stated in the text.
    """
    out = {"required": "Unknown", "min_years": "", "max_years": "",
           "level": "Unknown", "evidence": "", "source": "none"}
    text = text or ""
    title = title or ""

    hit = _exp_scan_numbers(text)
    if hit:
        lo_y, hi_y, months, plus, lo_raw, hi_raw, ev = hit
        unit = "months" if months else "years"
        if hi_raw is not None:
            label = f"{lo_raw}-{hi_raw} {unit}"
        elif plus:
            label = f"{lo_raw}+ {unit}"
        else:
            label = f"{lo_raw} {unit}"
        out["required"] = label
        out["min_years"] = round(lo_y, 2) if months else int(lo_y)
        if hi_y is not None:
            out["max_years"] = round(hi_y, 2) if months else int(hi_y)
        out["level"] = (_exp_level_from_text(title)
                        or _exp_level_from_years(lo_y, hi_y))
        out["evidence"] = ev
        out["source"] = "detail_text"
        return out

    none_hit = _EXP_NONE.search(text) or _EXP_NONE.search(title)
    if none_hit:
        out.update({"required": "None required", "min_years": 0,
                    "level": _exp_level_from_text(title) or "Entry",
                    "evidence": none_hit.group(0)[:300],
                    "source": ("detail_text" if _EXP_NONE.search(text)
                               else "title_inference")})
        return out

    lvl = _exp_level_from_text(title)
    if lvl:
        out.update({"level": lvl, "source": "title_inference",
                    "evidence": title[:300]})
        return out

    for sent in _EXP_SENT_SPLIT.split(text):
        sent = (sent or "").strip()
        if not sent or len(sent) > 400:
            continue
        if not _EXP_HIRING_CUE.search(sent):
            continue
        lvl = _exp_level_from_text(sent)
        if lvl:
            out.update({"level": lvl, "source": "detail_text",
                        "evidence": sent[:300]})
            return out

    # The JD names experience somewhere but yielded neither a number nor a
    # seniority word: that is a reference, not an absence - report it as
    # "Mentioned" so the UI can reserve NA for descriptions that never name
    # experience at all. Disqualified sentences (company tenure, ages, ...)
    # are not references.
    for sent in _EXP_SENT_SPLIT.split(text):
        sent = (sent or "").strip()
        if not sent or len(sent) > 400:
            continue
        m = _EXP_MENTION_NOUN.search(sent)
        if not m:
            continue
        win = sent[max(0, m.start() - 60):m.end() + 60]
        if _EXP_BLOCK_NEAR.search(win):
            continue
        out.update({"required": "Mentioned", "evidence": sent[:300],
                    "source": "detail_text"})
        return out
    return out


def apply_experience_to_record(rec, jd_text="", card_context="", title="",
                               level_hint="", key_prefix="Experience "):
    """Fill the four Experience columns on a record, respecting source rank.

    Never downgrades: a weaker source cannot overwrite a stronger one.
    `level_hint` is an employer-published seniority string (e.g.
    SmartRecruiters experienceLevel) and outranks every inference.
    """
    jd = (jd_text or "").strip()
    if "&lt;" in jd or ("<" in jd and ">" in jd):
        # Raw HTML slipped through (some adapters pass markup verbatim);
        # extraction must always see plain text.
        jd = _jd_plain(jd)
    exp = extract_experience(jd or card_context or "", title or "")
    src = exp["source"]
    if src == "detail_text":
        src = "api_description" if jd else "card_context"

    hint = (level_hint or "").strip()
    if hint:
        mapped = _EXP_ATS_LEVEL.get(hint.lower())
        if mapped:
            exp["level"] = mapped
            if exp["required"] in ("Unknown", "Mentioned"):
                src = "api_field"

    cur = rec.get(key_prefix + "Source") or "none"
    if _EXP_SOURCE_RANK.get(src, 0) < _EXP_SOURCE_RANK.get(cur, 0):
        return False
    if exp["required"] == "Unknown" and exp["level"] == "Unknown":
        return False
    rec[key_prefix + "Required"] = exp["required"]
    rec[key_prefix + "Min Years"] = exp["min_years"]
    rec[key_prefix + "Level"] = exp["level"]
    rec[key_prefix + "Source"] = src
    return True
