"""Context-aware Visa Sponsorship / Relocation Support / EU Blue Card detection.

Single source of truth for JD evidence classification.
Extracted verbatim from ats_portal_scannerv5.py / career_portal_scanner_v7.py
(which carried an identical copy of this class marked "keep in sync").

Policy:
  * Never matches keywords alone - every mention is judged within its sentence
    or clause, with negation / requirement / conditional / scope qualifiers.
  * Verdicts are Yes / No / Unknown.  No fabricated "No" when evidence is absent.
"""

import html as _html
import re

VERDICT_YES, VERDICT_NO, VERDICT_UNKNOWN = "Yes", "No", "Unknown"


# FIX W-1: one place that folds every apostrophe variant to ASCII "'".
_APOSTROPHE_RE = re.compile("[\u2019\u2018\u02bc\u02b9\u00b4`\u055a]")


def _norm_apostrophes(text):
    """Return `text` with curly/typographic apostrophes folded to ASCII."""
    return _APOSTROPHE_RE.sub("'", text or "")


# ── FIX P22/P23 (2026-10-04): normalise JD text before ANY cue matching ────
# Every provider adapter hands this detector the description EXACTLY as the
# ATS returns it, which for greenhouse / workable / smartrecruiters / jobs.msd
# is HTML -- tags and character entities included. Two measured failures in
# run 20261004T202946:
#   * "<p>We can&rsquo;t offer visa sponsorship for this role.</p>" scored
#     Yes 0.9. The apostrophe fold (W-1/P20) only folds real U+2019; it never
#     sees "&rsquo;", so "can&rsquo;t" is not "can't", the NEGATION cue misses
#     and the POSITIVE verb "offer" carries the sentence. A false YES on a JD
#     that says the opposite is the worst failure direction this tool has.
#   * 36 accepted rows carried "&lt;" / "&nbsp;" and 9 carried raw "<li>" in
#     the stored evidence, i.e. markup was being classified as prose.
# Block-level tags become sentence boundaries so that one <li> per benefit
# does not merge into a single 2,000-character "sentence" whose +-90 char
# qualifier window then straddles unrelated bullets.
_BLOCK_TAG_RE = re.compile(
    r"(?is)</?(?:p|div|li|ul|ol|br|tr|td|th|h[1-6]|section|article"
    r"|table|dl|dt|dd|blockquote|hr)\b[^>]*>")
_ANY_TAG_RE = re.compile(r"(?s)<[^<>]{0,400}?>")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")


def normalize_jd_text(text):
    """Return `text` as plain prose: entities decoded, markup removed.

    Idempotent and safe on text that was never HTML. Entities are unescaped
    TWICE because several ATS payloads are double-encoded ("&amp;rsquo;").
    """
    if not text:
        return ""
    t = str(text)
    if "<" in t or "&" in t:
        t = _SCRIPT_STYLE_RE.sub(" ", t)
        t = _BLOCK_TAG_RE.sub(" . ", t)
        t = _ANY_TAG_RE.sub(" ", t)
        t = _html.unescape(t)
        if "&" in t:
            t = _html.unescape(t)
        # A second pass: unescaping can REVEAL markup ("&lt;li&gt;").
        if "<" in t:
            t = _BLOCK_TAG_RE.sub(" . ", t)
            t = _ANY_TAG_RE.sub(" ", t)
    t = t.replace("\u00a0", " ").replace("\u200b", "")
    t = re.sub(r"(?:\s*\.\s*){2,}", ". ", t)
    t = re.sub(r"[ \t\x0b\f\r]+", " ", t)
    return t.strip()


# ── FIX P21 (2026-10-04): structured "Label: Value" support fields ────────
# Workday-family boards (jobs.msd.com, wd3.myworkdayjobs.com) append a fixed
# field block to every JD:
#     Employee Status: Regular  Relocation: No relocation
#     VISA Sponsorship: No  Travel Requirements: 50%  Shift: Not Applicable
# That is the employer's OWN structured answer and outranks anything the prose
# says. Sentence classification handled it only by accident: the "No" form
# scored 0.9 but the "Yes" form scored 0.20 -> Unknown, because "Yes" carries
# no POSITIVE_VERBS cue. Asymmetric by construction, so a Workday "Yes" could
# never surface. In run 20261004T202946, 50 accepted rows carried an explicit
# "VISA Sponsorship:" label and 43 of them came out Unknown.
#: The field block has no delimiter between "value of field N" and "name of
#: field N+1" -- "... VISA Sponsorship: No Travel Requirements: 50%". Given a
#: chunk that starts just after a label's colon, strip the trailing words that
#: belong to the NEXT label. Only the MINIMAL trailing run is removed: the
#: value is read anchored at the start, so leaving a stray label word at the
#: end is harmless, while removing one word too many silently deletes the
#: answer (the first cut of this fix matched up to 4 trailing words and turned
#: "VISA Sponsorship: Yes Travel Requirements: 10%" into an empty field).
_TRAILING_LABEL_WORD_RE = re.compile(
    r"[A-Za-z\u00c0-\u024f][\w\u00c0-\u024f/&'.-]*[ \t]*$")


def _strip_next_label(chunk):
    """Return `chunk` up to the start of the following "Label:" field."""
    i = chunk.find(":")
    if i == -1:
        return chunk
    before = chunk[:i]
    m = _TRAILING_LABEL_WORD_RE.search(before)
    return before[:m.start()] if m else before

_LABEL_FIELD_RES = {
    "visa": re.compile(
        r"(?<![A-Za-z])("
        r"visa\s+sponsorship|sponsorship\s+visa|visa\s+support|visa\s+status|"
        r"immigration\s+sponsorship|employment\s+sponsorship|work\s+sponsorship|"
        r"sponsorship\s+available|sponsorship\s+offered|sponsorship|"
        r"visa\s+sponsoring|visumsponsoring|visum\s*sponsoring|"
        r"sponsorizzazione\s+(?:del\s+)?visto|parrainage\s+de\s+visa|"
        r"patroc[íi]nio\s+de\s+visto|wsparcie\s+wizow\w*|sponsorowanie\s+wiz\w*|"
        r"v[íi]zov[áa]\s+podpora|visumsponsring|visumsponsorering|"
        r"visumsponsing|viisumituki|autoriza[çc][ãa]o\s+de\s+trabalho|"
        r"visa|work\s+permit|werkvergunning|arbeitserlaubnis"
        r")\s*:", re.I),
    "relocation": re.compile(
        r"(?<![A-Za-z])("
        r"relocation\s+(?:assistance|support|package|allowance|benefit|offered|"
        r"provided|eligible)|relocation|umzugs?(?:hilfe|unterst\u00fctzung|pauschale)?|"
        r"verhuis(?:kosten|vergoeding|kostenvergoeding)?|"
        r"indennit\u00e0\s+di\s+trasferimento|trasferimento|"
        r"aide\s+au\s+d\u00e9m\u00e9nagement|ayuda\s+de\s+reubicaci\u00f3n"
        r")\s*:", re.I),
}

#: Values that answer the label. Anchored at the start of the value chunk, so
#: "No relocation" is NO but "Nordic markets" is not (word boundary required).
_LABEL_YES_RE = re.compile(
    r"^(?:yes|y|true|available|offered|provided|possible|eligible|supported|"
    r"full|partial|sponsored|will\s+sponsor|ja|jawohl|oui|s[i\u00ed\u00ec]|"
    r"sim|tak|ano|kyll\u00e4)\b", re.I)
_LABEL_NO_RE = re.compile(
    r"^(?:no|n|none|false|not\s+available|not\s+offered|not\s+provided|"
    r"not\s+eligible|not\s+applicable|no\s+relocation|no\s+sponsorship|"
    r"no\s+visa|unavailable|nein|nee|niet|non|ingen|"
    r"nie|nej|nei|ne|ei|n\u00e3o)\b", re.I)
#: Only whitespace and list bullets may precede the value. Anything else
#: (notably a stray "<" left by a mangled scrape) means the field is EMPTY and
#: the text that follows belongs to the NEXT field -- see MSD's
#: "VISA Sponsorship:< No relocation< Relocation:<", where reading "No" would
#: attribute the relocation answer to the visa field.
_LABEL_LEAD_RE = re.compile(r"^[\s\u2022\u00b7*|\-\u2013\u2014]{0,4}")


#: Only whitespace, list bullets and the sentence breaks that
#: ``normalize_jd_text`` inserts for <br>/<li> may stand between a label's
#: colon and its answer. jobs.msd.com really does serve
#: "Relocation:<br>No relocation<br>VISA Sponsorship:<br>No", so the dot the
#: normaliser leaves behind must not be mistaken for an empty field.
_LEAD_JUNK_RE = re.compile(r"^[\s\u2022\u00b7*|\-\u2013\u2014.,;:]{0,8}")
#: The NEXT field starting immediately => this field was printed blank.
_IMMEDIATE_LABEL_RE = re.compile(
    r"^[A-Za-z\u00c0-\u024f][\w\u00c0-\u024f &/'-]{0,32}:")
#: Scrape junk where the answer should be. jobs.msd.com also emits
#: "VISA Sponsorship:< No relocation< Relocation:<", in which the "No"
#: belongs to the RELOCATION field: an unreadable field is blank and must
#: never be allowed to borrow its neighbour's answer.
_JUNK_VALUE_START = ("<", ">", "\\", "/")

#: Outcomes of reading one "Label: Value" occurrence.
_FIELD_BLANK, _FIELD_UNPARSED = "blank", "unparsed"


def _read_label_field(text, match):
    """Read the value that follows one label match.

    Returns ``(VERDICT_YES|VERDICT_NO, evidence)``, ``(_FIELD_BLANK, None)``
    when the employer printed the field and left it empty, or
    ``(_FIELD_UNPARSED, None)`` when free prose follows (in which case the
    normal sentence classifier must decide).
    """
    chunk = text[match.end():match.end() + 90].split("\n")[0]
    lead = _LEAD_JUNK_RE.match(chunk)
    value = chunk[lead.end():] if lead else chunk
    if not value.strip() or value[:1] in _JUNK_VALUE_START:
        return _FIELD_BLANK, None
    # Order matters: the answer is tested BEFORE the "next label" test,
    # because "No relocation. VISA Sponsorship:" also looks like a label.
    if _LABEL_NO_RE.match(value):
        verdict = VERDICT_NO
    elif _LABEL_YES_RE.match(value):
        verdict = VERDICT_YES
    else:
        if _IMMEDIATE_LABEL_RE.match(value):
            return _FIELD_BLANK, None
        return _FIELD_UNPARSED, None
    tail = _strip_next_label(value).split(". ")[0]
    return verdict, (match.group(1) + ": " + (tail or value).strip())[:240]


def label_field_verdict(text, kind):
    """Read an employer "Label: Value" support field.

    Returns ``(verdict, evidence)`` or ``None`` when the document carries no
    such field, or carries contradictory ones.
    """
    rx = _LABEL_FIELD_RES.get(kind)
    if not rx or not text:
        return None
    found = []
    for m in rx.finditer(text):
        verdict, evidence = _read_label_field(text, m)
        if verdict in (_FIELD_BLANK, _FIELD_UNPARSED):
            continue
        found.append((verdict, evidence))
    if not found:
        return None
    if len({v for v, _ in found}) > 1:
        # The employer contradicts itself across two blocks; do not guess.
        return None
    return found[0]


def label_field_blank(text, kind):
    """True when `kind`'s field is printed but carries no readable answer."""
    rx = _LABEL_FIELD_RES.get(kind)
    if not rx or not text:
        return False
    seen_blank = False
    for m in rx.finditer(text):
        verdict, _ = _read_label_field(text, m)
        if verdict == _FIELD_BLANK:
            seen_blank = True
        elif verdict != _FIELD_UNPARSED:
            return False          # an answered field wins over a blank one
    return seen_blank


class JDSupportDetector:
    VISA_CONCEPTS = re.compile(
        r"\b(visa|visas|work permit|work permits|work authorization|work authorisation|"
        r"authorized to work|authorised to work|legally authorized to work|legally authorised to work|"
        r"immigration|h-?1b|h1b|tier\s*2|blue card|blue-card|blaue karte|carta blu|"
        r"highly skilled migrant|skilled worker|aufenthaltstitel|permesso di soggiorno|"
        r"sponsorship"
        # ── Added: the most common way a European ad states the requirement ──
        # "right to work" / "eligible to work" are the standard UK and IE
        # phrasings and appeared in NONE of the concept lists, so a sentence
        # like "You must have the right to work in Germany" produced no
        # evidence at all (verdict Unknown, confidence 0.0) instead of the
        # correct "candidate must already be authorised".
        # DE: "Aufenthaltserlaubnis" is the standard residence/work permit term.
        # NL: the "wetten overplichting" notice is printed on every posting.
        # FR: "carte de séjour" / "titre de séjour"; ES: "permiso de residencia".
        #
        # ⚠️ Each continuation line below must NOT begin with "|" while the
        # previous line ends with one — that produces "||", an EMPTY alternative.
        # An empty branch makes the whole pattern match ZERO characters at every
        # word boundary, so every sentence registers as a visa mention and any
        # nearby positive verb ("support", "provide") turns it into a false
        # "Yes". Guarded by
        # tests/test_sponsorship_detection.py::test_visa_concepts_never_matches_zero_width.
        r"|right to work|eligible to work|eligibility to work"
        r"|residence permit|settlement visa"
        r"|aufenthaltserlaubnis|anmeldung|anmeldebescheinigung"
        r"|verblijfsvergunning|wwft|wetten\s+overplichting"
        r"|carte\s+de\s+s[ée]jour|titre\s+de\s+s[ée]jour|permis\s+de\s+s[ée]jour"
        r"|permiso\s+de\s+residencia"
        r"|visum\w*|arbeitserlaubnis|blauen karte|blaue karte"          # DE
        r"|visto\w*|visti|permesso di lavoro|carta blu|sponsorizzazione|immigrazione"  # IT
        # FIX P66 (B3): run 20261008T202403 was Netherlands-heavy -- 561 rows
        # in Blue Card countries -- and "kennismigrant", the standard Dutch
        # term for the highly-skilled-migrant permit (the route most of those
        # employers actually sponsor), was in NO concept list. The English
        # "highly skilled migrant" was already here; its Dutch name was not.
        # NB this is deliberately a VISA concept, not a Blue Card one: the
        # kennismigrantenregeling is a Dutch NATIONAL permit, not the EU Blue
        # Card, and labelling it as one would make that column wrong.
        r"|kennismigrant\w*|hooggekwalificeerde\s+migrant\w*"
        r"|tewerkstellingsvergunning|gecombineerde\s+vergunning|\bgvva\b"
        r"|visum\w*|werkvergunning|arbeidsvergunning|blauwe kaart|sponsoring"  # NL
        r"|visa\w*|permis de travail|carte bleue|parrainage|immigration|sponsorisons?|"
        r"sponsorisent|parrainons|parraine"  # FR
        r"|visad\w*|permiso de trabajo|tarjeta azul|patrocin\w*|inmigración"  # ES
        # FIX P0-59: "sponsorship" (the noun) was a concept but "sponsor"
        # applied to a PERSON was not, so "We are willing to sponsor the
        # right candidate" -- an unambiguous offer -- produced no evidence at
        # all and came back Unknown with confidence 0.0. Mirrors the keyword
        # the scanners' guard (1) accepts (career/ats P0-53), so the detector
        # and the guard cannot disagree about what counts as a visa mention.
        # Still cannot fire on "sponsoring projects" or "sponsoring events":
        # the object has to be a person.
        r"|sponsor\w*\s+(?:the\s+)?(?:right\s+|suitable\s+|successful\s+"
        r"|eligible\s+|qualified\s+|international\s+|overseas\s+|foreign\s+)?"
        r"(?:candidate|applicant|employee|hire|new\s+joiner|individual|person"
        r"|professional|talent|worker|you)s?"
        # ── PT / PL / CS / SV / DA / NO / FI (2026-10-09) ──────────────────
        r"|visto\s+de\s+trabalho|patroc[íi]nio|autoriza[çc][ãa]o\s+de\s+trabalho"
        r"|wiza\w*|wizy\b|wizow\w*|wiz\b|pozwoleni\w*\s+na\s+prac[eę]|"
        r"prawo\s+do\s+pracy|sponsorowani\w*|wsparcie\s+wizow\w*"
        r"|v[íi]zum\w*|v[íi]za\b|v[íi]zov\w*|pracovn[íi]\s+povolen[íi]|"
        r"povolen[íi]\s+k\s+pr[áa]ci|sponzorov\w*"
        r"|visumsponsring|arbetstillst[åa]nd"
        r"|visumsponsorering|arbejdstilladelse"
        r"|visumsponsing|arbeidstillatelse"
        r"|viisumituk\w*|viisum\w*|ty[öo]lupa\w*|ty[öo]luv\w*"
        r"|patrocin\w*\s+(?:o\s+|os\s+|a\s+|as\s+)?(?:candidat\w*|"
        r"funcion[áa]ri\w*|profissional\w*|voc[êe]|t[ée]cnic\w*)"
        r")\b",
        re.I,
    )
    RELOCATION_CONCEPTS = re.compile(
        r"\brelocat(e|es|ed|ing|ion|ions)?\b|\b(moving|move|relocation)\s+(assistance|"
        r"package|allowance|support|benefit|reimbursement|stipend|costs|expenses|bonus|"
        # FIX P65 (A2): the bare `move` branch fired on "AI-assisted tools
        # to move faster in your day-to-day" (Factorial, reloc=Yes@0.9) --
        # "assist*" within 25 chars of any "move". `move` now needs a
        # destination to count as a relocation.
        r"budget|help|aid)\b|\bassist\w*\b.{0,25}\brelocat\w*\b"
        r"|\bassist\w*\b.{0,25}\bmove\s+(?:to|abroad|overseas|countries|"
        r"country|cities|city|closer\s+to)\b"
        r"|\bumzug\w*|\bumzuziehen\b|\bumsiedl\w*|relokation"   # DE
        # FIX P25: "\btrasfer\w*" also matched "trasferta"/"trasferte" -- Italian
        # for a BUSINESS TRIP, not a move. In run 20261004T202946 the line
        # "disponibilita a trasferte presso gli stabilimenti del Gruppo"
        # (= willing to travel between plants) was scored
        # relocation = No 0.80 "requirement-not-support" at Chef Express HQ,
        # Oniverse and Piazza Italia. Only the genuine move words are kept:
        # trasferimento / trasferirsi / trasferito, never trasfert[ae].
        r"|\bricolloc\w*|\btrasferiment\w*|\btrasferir\w*|\btrasferisc\w*"
        r"|\btrasferit\w*|\btrasferiamo\b|\btrasloc\w*|relocazione"
        r"|assistenza al trasferimento|\bvitto e alloggio\b"
        r"|\balloggio\b.{0,30}\b(?:azienda|aziendale|convenzionat\w*|gratuito|\u00a0?offerto)\b"
        r"|\b(?:contributo|indennit\u00e0|rimborso)\s+(?:per\s+l\W?)?alloggio\b"  # IT
        # FIX P66 (B3): the 30% ruling is the Dutch expat tax facility and is
        # advertised as a relocation benefit in nearly every NL posting.
        r"|\b30\s*%?\s*[-\u2013]?\s*(?:ruling|regeling|tax\s+ruling)\b"
        r"|\bverhuis\w*|\bverhuiz\w*|relocatie"  # NL (verhuis- compounds + verhuizen verb)
        r"|\brelocalis\w*|\bdéménag\w*|\bréinstall\w*|frais de déménagement"  # FR
        r"|\breubic\w*|\btraslad\w*|\bmudanz\w*|ayuda de reubicación|gastos de reubicación"  # ES
        r"|relokacj\w*|przeprowadzk\w*|pakiet\s+relokacyjn\w*"
        r"|p[řr]est[ěe]hov\w*|st[ěe]hov\w*|relokac\w*"
        r"|flytt\w*|relokera\w*"
        r"|flytn\w*|flytte\w*"
        r"|muutto\w*"
        r"|relocaliza\w*"
        r"|\bassist\w*\b.{0,25}\b(umzug|trasfer|verhuis|déménag|reubic)\b",
        re.I,
    )
    POSITIVE_VERBS = re.compile(
        r"\b(offer|offers|offered|offering|provide|provides|provided|providing|support|"
        r"supports|supported|supporting|assist|assists|assisted|assisting|help|helps|"
        r"helped|cover|covers|covered|covering|pay|pays|paid|reimburse|reimburses|"
        r"reimbursed|arrange|arranges|arranged|handle|handles|handled|"
        r"manage|manages|managed|managing|sponsor|sponsors|"
        r"sponsored|sponsoring|include|includes|included|including|available|is offered|"
        r"is provided|will be provided|is included|granted|we will|receive|receives|"
        r"received|get|gets|enjoy|enjoys)\b",
        re.I,
    )
    # FIX W-1 (2026-10-04): the cue list carried the long forms of *be* and
    # *have* ("are not", "is not") and the apostrophe forms of the modals
    # ("can't", "don't") but NOT the apostrophe forms of be/have, so
    #     "We aren't able to sponsor visas for this role"
    # scored Yes 0.9 while the identical "We are not able to ..." scored No.
    # A false Yes is the worst failure direction this tool has. Typographic
    # apostrophes are folded to ASCII before matching (_norm_apostrophes),
    # so the far more common "aren\u2019t" spelling is covered too.
    NEGATION = re.compile(
        r"\b(not|no|never|without|cannot|can't|can not|does not|doesn't|do not|don't|"
        r"will not|won't|would not|wouldn't|unable|unfortunately|regret|except|excluding|"
        r"isn't|aren't|wasn't|weren't|hasn't|haven't|hadn't|couldn't|shouldn't|"
        r"mustn't|didn't|ain't|isnt|arent|wasnt|werent|hasnt|havent|hadnt|dont|"
        r"doesnt|cant|wont|couldnt|wouldnt|shouldnt|"
        r"no longer|not offered|not provided|not available|not supported|not included|"
        r"no sponsorship|no support|cannot be|is not|are not|not able|fail|fails|decline|"
        r"declines)\b",
        re.I,
    )
    REQUIREMENT = re.compile(
        r"\b(willing|ready|open|prepared|able|expected|required|must|need|needs|"
        r"should|asked|willingness|availability)\b.{0,25}\b(relocat\w*|move|transfer)\b"
        r"|\b(relocat\w*|move|transfer)\b.{0,25}\b(is|are)?\s*(required|mandatory|expected)\b",
        re.I,
    )
    #: FIX P65 (A2): "This role is eligible for visa sponsorship." scored
    #: Unknown 0.20 ("bare-mention") because no POSITIVE_VERB appears -- an
    #: unambiguous offer, lost. Deliberately NOT solved by adding "eligible"
    #: to POSITIVE_VERBS: "candidates must be eligible to work in Germany" is
    #: a REQUIREMENT and that change would have turned it into a false Yes,
    #: the worst failure direction this tool has. The discriminator is the
    #: preposition -- eligible FOR sponsorship (the role offers it) vs
    #: eligible TO WORK (the candidate must already be).
    ROLE_SPONSORSHIP_ELIGIBLE = re.compile(
        r"\beligib\w*\s+(?:for|to\s+receive|to\s+apply\s+for)\s+"
        r"(?:a\s+|an\s+|the\s+|full\s+|visa\s+|work\s+|uk\s+|us\s+)*"
        r"(?:sponsorship|visa\s+sponsorship|work\s+visa|work\s+permit|"
        r"skilled\s+worker\s+visa|relocation)\b",
        re.I,
    )
    REQUIRES_VERB = re.compile(
        r"\b(require|requires|required|requiring|need|needs|needed|mandatory|mandated)\b", re.I,
    )
    CONDITIONAL = re.compile(
        r"\b(case[- ]by[- ]case|subject to|may be|might be|could be|depending on|"
        r"at (our|the|company's|their) discretion|negotiable|on request|if applicable|"
        r"not guaranteed|can be discussed|at discretion|on a case|reviewed on|"
        r"limited to|restricted to|only for)\b",
        re.I,
    )
    #: A possibility modal with a STATIVE positive ("may be available") is
    #: speculation about an offer, not a scoped offer -- so it stays Unknown,
    #: unlike an active offer verb with a conditional scope ("supports X, may
    #: be limited to ..."), which is Yes. Without this split, P65 (A2)
    #: ("positive-but-conditional -> Yes") turned "Sponsorship may be
    #: available on a case-by-case basis." into Yes 0.6 and broke the hedged
    #: contract the tests pin: a hedge must never read as decisive. The
    #: speculative phrase is stripped and the REMAINDER must carry something
    #: stronger -- an active verb, an "eligible for sponsorship" role
    #: statement, or a non-English positive -- otherwise the sentence is
    #: speculative-only.
    SPECULATIVE_OFFER = re.compile(
        r"\b(may|might|could)\s+be\s+(?:\w+\s+){0,2}?"
        r"(available|possible|considered"
        r"|able(?:\s+to\s+(?:offer|provide|sponsor|support|assist|help|cover|"
        r"pay|arrange|handle|fund)?)?)\b",
        re.I,
    )
    SCOPE = re.compile(
        r"\b(for|to|towards|covering|regarding|concerning|in the case of)\b.{0,20}"
        r"\b(international|foreign|overseas|non-eu|non eu|expat|expatriate|"
        r"external|outside|relocating|new hires|senior|executive|management)\b",
        re.I,
    )
    #: A negation that is ITSELF a hedge. "Sponsorship is not guaranteed" does
    #: not mean "no sponsorship" — the clause still leaves it on the table.
    #:
    #: BUGFIX: ``CONDITIONAL`` already listed "not guaranteed", but the plain
    #: negation branch below returned a hard VERDICT_NO *before* CONDITIONAL
    #: was ever consulted, so that entry was unreachable for exactly the
    #: phrasing it was written for. For a sponsorship finder that is the worst
    #: possible failure direction: real, hedged offers were reported as hard
    #: refusals and disappeared from the results.
    HEDGED_NEGATION = re.compile(
        r"\b(?:not|never|no)\s+(?:guaranteed|assured|possible|granted|"
        r"committed|given|confirmed|ruled out)\b"
        r"|\bnot\s+necessarily\b|\bmay\s+not\b|\bmight\s+not\b|\bcannot\s+be\s+"
        r"(?:guaranteed|assured|ruled out)\b|\bdoes\s+not\s+mean\s+that\s+we\s+can(?:no|\x27)t\b"
        r"|\bnicht\s+garantiert\b|\bkeine\s+garantie\b|\bnicht\s+zugesichert\b"
        r"|\bnon\s+garanti\b|\bnon\s+garantie\b|\bno\s+garantizado\b|\bgeen\s+garantie\b"
        # A possibility left open ("can't rule out") is a hedge, not a refusal.
        r"|\bcannot\s+(?:rule\s+out|exclude)\b|\bcan'?t\s+(?:rule\s+out|exclude)\b"
        r"|\bcould\s+not\s+(?:rule\s+out|exclude)\b"
        r"|\bnot\s+(?:be\s+)?(?:ruled|excluded)\s+out\b"
        # Hedged negatives in the seven new languages (and FR/ES already above).
        r"|\bnie\s+jest\s+gwarantowan\w*\b|\bnie\s+gwarantujemy\b"
        r"|\bnen[íi]\s+zaru[č]en\w*\b|\bnezaru[č]en\w*\b"
        r"|\bgaranteras\s+inte\b|\binte\s+garanterat\b"
        r"|\bikke\s+garanter(?:et|t)\b"
        r"|\bei\s+(?:ole\s+)?taattu\b"
        r"|\bn[ãa]o\s+(?:[ée]\s+)?garantid\w*\b",
        re.I,
    )

    #: A negation spent on the PROBLEM noun, not on the support: "A visa is
    #: not a problem", "kein Problem", "n'est pas un obstacle", "no es un
    #: problema", "nie jest problemem", "viisumi ei ole ongelma". The plain
    #: negation branch used to read every one of those as a hard refusal --
    #: the worst failure direction for a sponsorship finder: the warmest
    #: sentence in the advert ("don't worry, visas are fine") was reported as
    #: "No" and filtered the best candidates straight out of the results.
    #: Checked against the qualifier window; the matched phrase is REMOVED and
    #: only a negation that remains outside it counts (so "we do not sponsor
    #: visas even though they are not a problem" is still a refusal). Covers
    #: all thirteen supported languages from day one, because every new
    #: negation list would otherwise reintroduce the same false "No".
    PROBLEM_NEGATION = re.compile(
        r"\b(?:"
        # EN: "is/are not a problem", "isn't a problem", "is no problem",
        # "not an issue", "no barrier" (hedge adverbs tolerated)
        r"(?:isn'?t|aren'?t|wasn'?t|weren'?t|'s\s+not"
        r"|(?:is|are|was|were)\s+(?:not|never))\s+"
        r"(?:really\s+|just\s+|simply\s+|at\s+all\s+)?(?:a\s+|an\s+)?"
        r"(?:problem|issue|barrier|obstacle|hurdle|deal[- ]?breaker|blocker"
        r"|concern)s?\b"
        r"|(?:is|are)\s+no\s+(?:problem|issue|barrier|obstacle|hurdle)s?\b"
        r"|no\s+(?:problem|issue|barrier|obstacle)s?\b"
        r"|not\s+(?:really\s+|just\s+|simply\s+)?(?:a\s+|an\s+)"
        r"(?:problem|issue|barrier|obstacle|hurdle)s?\b"
        # DE: "kein Problem" ("ist kein Problem" contains this)
        r"|kein(?:e|en|er)?\s+(?:problem\w*|thema\b|hindernis\w*"
        r"|h\u00fcrde\w*|huerde\w*|barriere\w*)"
        # IT: "non e un problema"
        r"|non\s+\u00e8\s+(?:un\s+)?(?:problema|ostacolo|impedimento|barriera)"
        r"|nessun\w*\s+(?:problema|ostacolo)"
        # FR: "n'est pas un probleme"
        r"|n['\u2019]est\s+pas\s+(?:un\s+|une\s+)?"
        r"(?:probl[\u00e8e\u00e9]me|obstacle|frein|souci)"
        r"|aucun\w*\s+(?:probl[\u00e8e\u00e9]me|obstacle)"
        r"|ne\s+pose\s+pas\s+de\s+probl[\u00e8e\u00e9]me"
        r"|ne\s+repr[\u00e9e]sente\s+pas\s+(?:un\s+)?(?:probl[\u00e8e\u00e9]me|obstacle)"
        # ES: "no es un problema"
        r"|no\s+es\s+(?:un\s+)?(?:problema|obst[\u00e1a]culo)"
        r"|no\s+(?:representa|supone|constituye)\s+(?:un\s+)?"
        r"(?:problema|obst[\u00e1a]culo)"
        r"|ning\u00fan\w*\s+(?:problema|obst[\u00e1a]culo)"
        # NL: "geen probleem"
        r"|geen\s+(?:probleem|obstakel|hindernis|belemmering)"
        r"|(?:is|vormt)\s+geen\s+probleem"
        # PT: "nao e um problema"
        r"|n[\u00e3a]o\s+[e\u00e9]\s+(?:um\s+)?(?:problema|obst[\u00e1a]culo)"
        r"|n[\u00e3a]o\s+(?:representa|h[\u00e1a])\s+(?:um\s+)?problema"
        r"|nenhum\w*\s+(?:problema|obst[\u00e1a]culo)"
        # PL: "nie jest problemem" (instrumental)
        r"|nie\s+(?:jest|stanowi|b[\u0119e]dzie)\s+"
        r"(?:problemem|problemu|przeszkod[\u0105a]|barier[\u0105a])"
        r"|brak\s+(?:problemu|przeszkody)"
        r"|[\u017bz]aden\w*\s+(?:problem|przeszkoda)"
        # CS: "neni problem"
        r"|nen[\u00edi]\s+(?:probl[\u00e9e]m\w*|p[\u0159r]ek[\u00e1a][\u017e]k\w*)"
        r"|[\u017e]adn[\u00fdy]\s+probl[\u00e9e]m"
        # SV: "inget problem" / "inte ett problem"
        r"|(?:[\u00e4a]r\s+)?(?:inte|ej)\s+"
        r"(?:n[\u00e5a]got\s+|ett\s+|et\s+|en\s+)?"
        r"(?:problem|hinder|hindring)"
        r"|inget\s+(?:problem|hinder)|inga\s+problem"
        # DA/NO: "er ikke et problem"
        r"|(?:er\s+)?ikke\s+(?:noget\s+|noe\s+|et\s+|en\s+|ett\s+)?"
        r"(?:problem|hinder|hindring)"
        r"|intet\s+(?:problem|hinder)|ingen\s+problemer"
        # FI: "ei ole ongelma"
        r"|ei\s+(?:ole\s+)?(?:ongelm\w*|este\w*|ongelmaa)"
        r"|ei\s+aiheuta\s+ongelm\w*"
        r")",
        re.I,
    )

    #: FIX P26: "<article> <benefit> <noun> [available]" and nothing else.
    #: Anchored at both ends so it can only fire on a list item that IS the
    #: benefit, never on a sentence that merely contains the words.
    BENEFIT_NOUN_PHRASE = re.compile(
        r"^[\s\u2022\u00b7*\-\u2013\u2014|]{0,4}"
        r"(?:a|an|the|our|your|plus|incl\.?|including|with)?\s*"
        r"(?:full|fully\s+paid|generous|competitive|attractive|complete|"
        r"international|domestic|global|partial)?\s*"
        r"(?:relocation|re-location|moving|umzugs?|verhuis|verhuiz\w*|"
        r"d\u00e9m\u00e9nagement|reubicaci\u00f3n|traslado|trasferimento)"
        r"[\s\-]*"
        r"(?:package|packages|assistance|allowance|support|bonus|budget|"
        r"stipend|reimbursement|help|benefits?|costs?|expenses|"
        r"hilfe|pauschale|unterst\u00fctzung|kostenvergoeding|vergoeding|"
        r"pakket|aide|ayuda|gastos|supporto|contributo)?"
        r"\s*(?:is\s+|are\s+|fully\s+)?"
        r"(?:available|offered|provided|included|possible|paid)?"
        r"[\s.;:,!)\u2013\u2014-]*$", re.I)

    # ── Multi-language qualifier patterns (DE / IT / NL / FR / ES) ──
    EXTRA_LANGS = {
        "de": {
            "pos": re.compile(r"\b(bieten|bietet|unterstützen|unterstützt|helfen|hilft|übernehmen|"
                              r"übernimmt|zahlen|zahlt|erstatten|erstattet|beinhaltet|inklusive|"
                              r"verfügbar|erhalten|bereitstellen|bereitgestellt|"
                              r"stellen\s+ein|gestellt|kümmern|kümmert)\b", re.I),
            "neg": re.compile(r"\b(kein|keine|keinen|nicht|ohne|leider|können nicht|kann nicht|"
                              r"nicht möglich|keine unterstützung|kein sponsoring|kein visum)\b", re.I),
            "req": re.compile(r"\b(bereit|willens|verpflichtet|erforderlich|müssen|muss)\b"
                              r".{0,30}\b(umziehen|umzuziehen|umzug\w*|umsiedl\w*|relokation)\b"
                              r"|\b(umziehen|umzuziehen)\b.{0,30}\b(erforderlich|notwendig|"
                              r"verpflichtend|müssen)\b", re.I),
            "reqverb": re.compile(r"\b(erfordert|erforderlich|benötigt|verlangt|notwendig)\b", re.I),
            "cond": re.compile(r"\b(auf anfrage|nach absprache|je nach|ggf\.|gegebenenfalls|"
                               r"kann diskutiert werden|nicht garantiert|im einzelfall|"
                               r"individuell)\b", re.I),
        },
        "it": {
            # FIX P25: "garantiamo"/"mettiamo a disposizione" is how an Italian
            # ad offers a benefit. Nova Coop's
            # "Per gli inserimenti che richiedono il trasferimento, garantiamo
            #  3 mesi di alloggio in struttura convenzionata."
            # -- an unambiguous relocation offer -- scored Unknown 0.20.
            "pos": re.compile(r"\b(offriamo|offre|offrono|forniamo|fornisce|supportiamo|"
                              r"supportare|supporta|aiutiamo|copriamo|copre|paghiamo|"
                              r"rimborsiamo|rimborsa|garantiamo|garantisce|garantito|"
                              # FIX P47 (2026-10-07): the masculine forms were
                              # listed but not the FEMININE ones, and the two
                              # nouns that matter most in Italian are both
                              # feminine -- "la sponsorizzazione", "l'assistenza".
                              # So "E prevista la sponsorizzazione del visto di
                              # lavoro" matched the visa CONCEPT but found no
                              # positive verb and scored Unknown 0.20, while the
                              # masculine "e previsto il supporto" scored Yes 0.90.
                              # A gender agreement, not a missing phrase.
                              r"mettiamo a disposizione|previsto|previsti|prevista|previste|prevede|"
                              r"include|incluso|inclusa|disponibile|ricevere|ricevono)\b", re.I),
            "neg": re.compile(r"\b(non|nessun|nessuna|senza|purtroppo|non possiamo|non è possibile|"
                              r"non disponibile|non offre|non forniamo)\b", re.I),
            # FIX P25: see RELOCATION_CONCEPTS -- "trasfert[ae]" is a business
            # trip and must not raise the candidate-must-move requirement.
            "req": re.compile(r"\b(disposto|disposta|pronto|pronta|disponibile|disponibilità|"
                              r"disponibilita)\b.{0,30}\b(trasferiment\w*|trasferir\w*|"
                              r"spost\w*|ricolloc\w*)\b"
                              r"|\b(trasferiment\w*|trasferir\w*|spost\w*)\b.{0,30}"
                              r"\b(richiesto|obbligatorio|necessario|richiede)\b"
                              # FIX 2026-10-09 (batch 20261009T225341):
                              # "Sono previste trasferte e trasferimenti."
                              # (= travel and transfers are expected) scored
                              # reloc Yes 0.9 via P47 "previste" -- it is the
                              # P25 duty shape. Impersonal "previst*" + the
                              # mobility nouns is a requirement; a benefit
                              # ("È previsto un trasferimento con rimborso
                              # spese") is exempt via the lookahead.
                              r"|\b(?:[eè]\s+|sono\s+|saranno\s+|si\s+)?"
                              r"previst\w*\b.{0,30}\b(?:trasfert\w*|trasferir\w*|"
                              r"spost\w*)\b"
                              r"(?!\s+(?:con|e)\s+(?:\w+\s+){0,2}?"
                              r"(?:rimborso|contributo|alloggio|indennit\w*|"
                              r"voucher|bonus)\b)"
                              r"|\b(?:trasfert\w*|trasferir\w*)\b\s*(?:e|,|ed)\s*"
                              r"\b(?:trasfert\w*|trasferir\w*)\b", re.I),
            "reqverb": re.compile(r"\b(richiede|richiedono|necessario|obbligatorio)\b", re.I),
            "cond": re.compile(r"\b(caso per caso|soggetto a|può essere|dipende da|negoziabile|"
                               r"su richiesta|se applicabile|non garantito)\b", re.I),
        },
        "nl": {
            # "mogelijk" (= possible/available) is how Dutch ads normally say
            # sponsorship is on offer; without it "Visumsponsoring is
            # mogelijk." scored as a bare mention (Unknown, c=0.20) instead of
            # a positive.
            "pos": re.compile(r"\b(bieden|biedt|ondersteunen|ondersteunt|helpen|helpt|vergoeden|"
                              r"vergoedt|vergoed|betalen|betaalt|omvat|inbegrepen|beschikbaar|"
                              r"mogelijk|mogelijkheden|inbegrepen|verzorgen|regelen|"
                              r"ontvangen|ontvangt|wordt\s+vergoed|worden\s+vergoed)\b", re.I),
            "neg": re.compile(r"\b(geen|niet|zonder|helaas|kunnen niet|kan niet|niet beschikbaar|"
                              r"geen ondersteuning|geen sponsoring)\b", re.I),
            "req": re.compile(r"\b(bereid|verplicht|moet|moeten|dienen)\b.{0,30}"
                              r"\b(verhuis\w*|verhuiz\w*|relocatie)\b"
                              r"|\b(verhuis\w*|verhuiz\w*)\b.{0,30}\b(verplicht|vereist|noodzakelijk)\b", re.I),
            "reqverb": re.compile(r"\b(vereist|vereisen|noodzakelijk|verplicht)\b", re.I),
            "cond": re.compile(r"\b(op aanvraag|in overleg|afhankelijk van|eventueel|"
                               r"kan worden besproken|niet gegarandeerd|per geval)\b", re.I),
        },
        "fr": {
            # "sponsoriser"/"parrainer" are the French verbs for sponsorship.
            # Only the nouns were listed, so every conjugated form
            # ("nous sponsorisons", "ils parrainons", "parraine") missed the
            # positive-verb check and the sentence degraded to Unknown.
            # "prend en charge" covers the idiomatic "we take care of your visa".
            "pos": re.compile(r"\b(offrons|offre|fournissons|fournit|soutenons|soutient|aidons|"
                              r"couvrons|couvre|payons|paye|remboursons|rembourse|comprend|"
                              r"disponible|recevoir|reçoivent|"
                              r"sponsorise?s?|sponsorisons|sponsorisent|sponsoris\w*|"
                              r"parraine?s?|parrainons|parrainent|parrain\w*|"
                              r"prend\s+en\s+charge|prenons\s+en\s+charge|"
                              r"prenons\s+en\s+charge\s+du\s+visa)\b", re.I),
            "neg": re.compile(r"\b(pas de|aucun|aucune|sans|malheureusement|ne pouvons pas|"
                              r"ne peut pas|pas disponible|ne fournissons|ne soutenons)\b"
                              r"|\bn['’]?\w{0,8}\s+pas\b", re.I),
            "req": re.compile(r"\b(prêt|prête|disposé|disposée|obligé)\b.{0,30}"
                              r"\b(déménag\w*|relocalis\w*|réinstall\w*)\b"
                              r"|\b(déménag\w*|relocalis\w*)\b.{0,30}\b(requis|obligatoire|"
                              r"nécessaire)\b", re.I),
            "reqverb": re.compile(r"\b(exige|exigent|nécessite|obligatoire|requis)\b", re.I),
            "cond": re.compile(r"\b(au cas par cas|selon|peut être|négociable|sur demande|"
                               r"si applicable|non garanti)\b", re.I),
        },
        "es": {
            "pos": re.compile(r"\b(ofrecemos|ofrece|proporcionamos|proporciona|apoyamos|apoya|"
                              r"ayudamos|ayuda|cubrimos|cubre|pagamos|paga|reembolsamos|"
                              r"reembolsa|incluye|disponible|recibir|reciben)\b", re.I),
            "neg": re.compile(r"\b(no|ningún|ninguna|sin|lamentablemente|no podemos|no puede|"
                              r"no disponible|no ofrecemos|no proporcionamos)\b", re.I),
            "req": re.compile(r"\b(dispuesto|dispuesta|preparado|preparada|obligado)\b.{0,30}"
                              r"\b(reubic\w*|traslad\w*|mud\w*)\b"
                              r"|\b(reubic\w*|traslad\w*|mud\w*)\b.{0,30}\b(requerido|"
                              r"obligatorio|necesario)\b", re.I),
            "reqverb": re.compile(r"\b(requiere|requieren|necesita|obligatorio|exige)\b", re.I),
            "cond": re.compile(r"\b(caso por caso|sujeto a|puede ser|negociable|bajo petición|"
                               r"si aplica|no garantizado)\b", re.I),
        },
        "pl": {
            "pos": re.compile(r"\b(oferujemy|oferuje|oferują|zapewniamy|zapewnia|"
                              r"zapewniają|pomagamy|pomaga|pokrywamy|pokrywa|"
                              r"sponsorujemy|sponsoruje|wspieramy|wspiera|"
                              r"udostępniamy|dofinansowujemy|możliwe|dostępne)\b", re.I),
            "neg": re.compile(r"\b(nie|brak|niestety|bez)\b", re.I),
            "req": re.compile(r"\b(gotow\w*|chętn\w*|zobowiązan\w*)\b.{0,30}"
                              r"\b(przeprowadzk\w*|relokacj\w*|przeprowadzić)\b"
                              r"|\b(przeprowadzk\w*|relokacj\w*)\b.{0,30}"
                              r"\b(wymagan\w*|konieczn\w*|obowiązkow\w*)\b", re.I),
            "reqverb": re.compile(r"\b(wymagan\w*|wymaga|wymagają|niezbędne|"
                                  r"konieczne|konieczny|wymogiem)\b", re.I),
            "cond": re.compile(r"\b(w zależności od|na życzenie|do negocjacji|"
                               r"nie gwarantowane|rozpatrywane indywidualnie|"
                               r"w indywidualnych przypadkach|po uzgodnieniu)\b", re.I),
        },
        "cs": {
            "pos": re.compile(r"\b(nabízíme|nabízí|poskytujeme|poskytuje|zajišťujeme|"
                              r"zajišťuje|podporujeme|podporuje|hradíme|hradí|"
                              r"pomáháme|pomáhá|zahrnuto|zahrnuje|k dispozici|"
                              r"možné|sponzorujeme)\b", re.I),
            "neg": re.compile(r"\b(není|nejsou|nenabízíme|neposkytujeme|"
                              r"nezajišťujeme|nemůžeme|bez|bohužel)\b", re.I),
            "req": re.compile(r"\b(připraven\w*|ochotn\w*|schopen|schopna)\b.{0,30}"
                              r"\b(přestěhov\w*|stěhov\w*|relokac\w*)\b"
                              r"|\b(přestěhov\w*|stěhov\w*)\b.{0,30}"
                              r"\b(vyžadov\w*|požadov\w*|povinn\w*)\b", re.I),
            "reqverb": re.compile(r"\b(vyžaduje|vyžadují|vyžadováno|požadováno|"
                                  r"nutné|nezbytné|povinné)\b", re.I),
            "cond": re.compile(r"\b(dle dohody|na vyžádání|dle situace|"
                               r"případ od případu|není zaručeno|individuálně)\b", re.I),
        },
        "sv": {
            "pos": re.compile(r"\b(erbjuder|erbjuds|tillhandahåller|tillhandahålls|"
                              r"stödjer|stöder|hjälper|täcker|betalar|ingår|"
                              r"tillgängligt|möjligt|sponsrar)\b", re.I),
            "neg": re.compile(r"\b(inte|ingen|inget|inga|utan|tyvärr)\b", re.I),
            "req": re.compile(r"\b(beredd|villig|förväntas)\b.{0,30}"
                              r"\b(flytt\w*|relokera\w*)\b"
                              r"|\b(flytt\w*|relokera\w*)\b.{0,30}"
                              r"\b(krävs|kräver|nödvändigt|obligatoriskt)\b", re.I),
            "reqverb": re.compile(r"\b(krävs|kräver|nödvändigt|obligatoriskt|"
                                  r"krävas)\b", re.I),
            "cond": re.compile(r"\b(efter överenskommelse|på begäran|beroende på|"
                               r"ej garanterat|inte garanterat|fall för fall)\b", re.I),
        },
        "da": {
            "pos": re.compile(r"\b(tilbyder|tilbydes|yder|støtter|hjælper|dækker|"
                              r"betaler|indgår|tilgængelig|mulig|sponsorerer)\b", re.I),
            "neg": re.compile(r"\b(ikke|ingen|intet|uden|desværre)\b", re.I),
            "req": re.compile(r"\b(indstillet|parat|villig|forventes)\b.{0,30}"
                              r"\b(flytn\w*|flytte\w*)\b"
                              r"|\b(flytn\w*|flytte\w*)\b.{0,30}"
                              r"\b(kræves|kræver|nødvendig|obligatorisk)\b", re.I),
            "reqverb": re.compile(r"\b(kræves|kræver|nødvendig|obligatorisk|"
                                  r"påkrævet)\b", re.I),
            "cond": re.compile(r"\b(efter aftale|på anmodning|afhængig af|"
                               r"ikke garanteret|fra sag til sag)\b", re.I),
        },
        "no": {
            "pos": re.compile(r"\b(tilbyr|tilbys|yter|støtter|hjelper|dekker|"
                              r"betaler|inngår|tilgjengelig|mulig|sponser)\b", re.I),
            "neg": re.compile(r"\b(ikke|ingen|intet|uten|dessverre)\b", re.I),
            "req": re.compile(r"\b(villig|innstilt|forventes)\b.{0,30}"
                              r"\b(flytt\w*|flytting\w*)\b"
                              r"|\b(flytt\w*|flytting\w*)\b.{0,30}"
                              r"\b(kreves|krever|nødvendig|obligatorisk)\b", re.I),
            "reqverb": re.compile(r"\b(kreves|krever|nødvendig|obligatorisk|"
                                  r"påkrevd)\b", re.I),
            "cond": re.compile(r"\b(etter avtale|på forespørsel|avhengig av|"
                               r"ikke garantert|fra sak til sak)\b", re.I),
        },
        "fi": {
            "pos": re.compile(r"\b(tarjoamme|tarjoaa|tarjotaan|tarjoavat|tuetamme|"
                              r"tukee|autamme|auttaa|kustannamme|kattaa|sisältyy|"
                              r"saatavilla|mahdollista|sponsataan)\b", re.I),
            "neg": re.compile(r"\b(ei|emme|ette|eivät|valitettavasti|ilman)\b", re.I),
            "req": re.compile(r"\b(valmis|halukas)\b.{0,30}\b(muutto\w*|muuttamaan)\b"
                              r"|\b(muutto\w*)\b.{0,30}"
                              r"\b(vaaditaan|vaatii|välttämätön|pakollinen)\b", re.I),
            "reqverb": re.compile(r"\b(vaaditaan|vaatii|välttämätön|pakollinen)\b",
                                  re.I),
            "cond": re.compile(r"\b(sopimuksen mukaan|pyydettäessä|tilanteen mukaan|"
                               r"ei taattu|tapauskohtaisesti)\b", re.I),
        },
        "pt": {
            "pos": re.compile(r"\b(oferecemos|oferece|proporcionamos|proporciona|"
                              r"apoiamos|apoia|ajudamos|ajuda|cubrimos|cobre|"
                              r"pagamos|paga|reembolsamos|inclui|incluímos|"
                              r"disponível|possível|patrocinamos|patrocina|"
                              r"fornecemos)\b", re.I),
            "neg": re.compile(r"\b(não|nao|nenhum|nenhuma|sem|infelizmente)\b", re.I),
            "req": re.compile(r"\b(disposto|disposta|pronto|pronta|disponível)\b"
                              r".{0,30}\b(relocaliza\w*|mudan[çc]a\w*|reinstalar)\b"
                              r"|\b(relocaliza\w*)\b.{0,30}"
                              r"\b(necess[áa]ri\w*|obrigat\w*|requerid\w*)\b", re.I),
            "reqverb": re.compile(r"\b(requer|requerem|exige|exigem|necess[áa]ri\w*|"
                                  r"obrigat\w*|requerid\w*)\b", re.I),
            "cond": re.compile(r"\b(caso a caso|conforme|negociável|a pedido|"
                               r"se aplicável|não garantido|mediante acordo)\b", re.I),
        },
    }

    @staticmethod
    def split_sentences(text):
        text = re.sub(r"[ \t]+", " ", text or "")
        parts = re.split(
            r"(?<=[.!?])\s+|\n+|;\s*|\s+(?:but|while|whereas|however|yet|though|although|"
            r"aber|jedoch|während|aber|ma|mentre|però|tuttavia|maar|echter|terwijl|"
            r"mais|cependant|tandis que|pero|sin embargo|mientras|"
            r"ale|jednak|avšak|zatímco|däremot|hvorimot|medan|"
            r"mutta|vaikka|kun taas|mas|porém|embora|enquanto)\s+",
            text,
        )
        return [p.strip() for p in parts if p.strip()]

    #: FIX P26b: an interrogative never states policy. "Do you need a
    #: relocation package?" was scoring relocation = No 0.80 via the
    #: candidate-must-move branch, and application forms ("Is this role
    #: eligible for Immigration Sponsorship?", "Do you require sponsorship
    #: now or in the future?") are extremely common at the foot of a JD.
    INTERROGATIVE = re.compile(
        r"^[\s\u2022\u00b7*|\-\u2013\u2014]{0,4}"
        r"(?:do|does|did|are|is|was|were|will|would|can|could|should|have|has|"
        r"what|which|who|whom|how|why|when|where|"
        r"sind|ist|haben|hat|werden|k\u00f6nnen|ben\u00f6tigen|brauchen|"
        r"avete|siete|hai|serve|necessiti|"
        r"heeft|hebt|bent|kunt|moet|"
        r"est-ce|avez|\u00eates|pouvez|"
        r"necesita|necesitas|requiere|tiene|puede|"
        r"czy|je|jsou|zda|vy[žz]aduj\w*|kräver|kræver|krever|"
        r"vaatitko|onko|é|você|voce|precisa|tem|possui)\b",
        re.I)

    #: FIX 2026-10-09 (batch 20261009T225341): the job FUNCTION, not a
    #: benefit. "Relocation & Immigration Revamp: Take over our global
    #: mobility support and manage external service providers ..." (HR
    #: lead) and "Manage job offers, onboarding, and offboarding
    #: end-to-end--from negotiation and visa support to administrative
    #: updates ..." (people administration manager) both scored Yes 0.90.
    #: The hire would ADMINISTER mobility/visa support for OTHERS. The
    #: frame is an imperative/role-task verb plus the function plus an
    #: admin noun; "your ..." still vetoes ("Manage your visa process").
    #: Sentence-level on purpose: the scanner's whole-text guard (5) would
    #: also erase a genuine perk block on the same career page.
    FUNCTION_DUTY_FRAME = re.compile(
        r"^(?:[^.;!?]{0,60}?)"
        r"(?:(?:you|he|she|they)\s+(?:will|would|shall)\s+)?"
        r"(?:take\s+over|take\s+ownership|manage|managing|handle|handling|"
        r"coordinate|coordinating|oversee|overseeing|administer|"
        r"administering|drive|own|lead|run)\b"
        r"[^.;!?]{0,90}\b(?:global\s+mobility|immigration|mobility|"
        r"relocation|visa)s?\b"
        r"[^.;!?]{0,60}\b(?:support|services?|service\s+providers?|"
        r"vendors?|operations?|program|process(?:es)?|requests?|cases|"
        r"applications?|agreements?|administration|administrative|updates?)\b",
        re.I)

    def sentence_verdict(self, sentence, concept_re):
        # FIX W-1: fold typographic apostrophes to ASCII before ANY cue
        # matching. Only the matching copy is folded; the evidence string the
        # caller stores still comes from the untouched sentence.
        sentence = _norm_apostrophes(sentence)
        concept_match = concept_re.search(sentence)
        if not concept_match:
            return None
        # FIX P26b: questions ask, they do not promise or refuse.
        if sentence.rstrip().endswith("?") and self.INTERROGATIVE.match(sentence):
            return VERDICT_UNKNOWN, 0.2, ["interrogative"]
        # Relocation can describe equipment, vehicles, offices or a job function.
        # Those are not candidate benefits.
        if concept_re is self.RELOCATION_CONCEPTS and re.search(
            r"\b(relocat(?:e|ing|ion)?\s+(?:vehicles?|cars?|equipment|machines?|systems?|"
            r"offices?|data centers?|assets?)|(?:install|upgrade|fleet|vehicle)\b.{0,35}"
            r"\brelocat|relocation\s+(?:engineer|specialist|coordinator|project))\b",
            sentence, re.I,
        ):
            return VERDICT_UNKNOWN, 0.0, ["non-candidate-relocation-context"]
        # FIX P65 (A2): the guard above needs the object ADJACENT to the verb,
        # so GDIT's "the acquisition, storage, relocation, and deployment of
        # communications equipment" (reloc=Yes@0.9, "Support" supplied the
        # positive verb) slipped through. Allow distance to the object noun,
        # but only when the sentence carries no candidate-relocation benefit
        # wording -- "we offer relocation support and provide equipment" must
        # still read as a genuine offer.
        if concept_re is self.RELOCATION_CONCEPTS and re.search(
            r"\brelocat\w*\b.{0,60}\b(?:equipment|hardware|servers?|machinery|"
            r"inventory|stock|warehouse|furniture|assets?|cabling|freight|"
            r"infrastructure|data\s+cent(?:er|re)s?|fleet|goods)\b",
            sentence, re.I,
        ) and not re.search(
            r"\brelocat\w*[\s\-]*(?:assistance|package|allowance|support|"
            r"benefit|reimbursement|stipend|costs?|expenses|bonus|budget|help)"
            r"|\b(?:your|employee|family|candidate|personal|staff)\b"
            r".{0,25}\brelocat",
            sentence, re.I,
        ):
            return VERDICT_UNKNOWN, 0.0, ["non-candidate-relocation-context"]
        if self.FUNCTION_DUTY_FRAME.search(sentence) and not re.search(
                r"\byour\b|\bfor\s+you\b", sentence, re.I):
            return VERDICT_UNKNOWN, 0.0, ["function-duty-context"]
        if concept_re is self.VISA_CONCEPTS and re.search(
            # (a) requirement stated BEFORE the authorisation noun:
            #     "Applicants must have the right to work in the UK"
            r"\b(?:must|required to|need to|should have|have to)\b.{0,45}"
            r"\b(?:already\s+)?(?:have|hold|possess|be eligible for)\b.{0,35}"
            r"\b(?:valid\s+)?(?:work permit|work authori[sz]ation|visa|right to work)\b|"
            r"\b(?:must be|are|should be)\s+(?:already\s+)?"
            r"(?:authori[sz]ed to work|eligible to work)\b|"
            # (b) requirement stated AFTER it — the mirror word order:
            #     "A valid work permit is required", "Aufenthaltserlaubnis
            #     required", "right to work is a prerequisite".
            #     Previously only (a) existed, so every post-posed requirement
            #     fell through to a bare "Unknown" mention. The copula is
            #     OPTIONAL because German/Italian ads drop it:
            #     "Aufenthaltserlaubnis required".
            r"\b(?:valid\s+)?(?:work permit|visa|right to work|residence permit|"
            r"aufenthalt\w*|anmeldebescheinigung|anmeldung|"
            r"permis\s+de\s+s[ée]jour|permiso\s+de\s+residencia|"
            r"prawo\s+do\s+pracy|pozwoleni\w*\s+na\s+prac[eę]|"
            r"pracovn[íi]\s+povolen[íi]|povolen[íi]\s+k\s+pr[áa]ci|"
            r"arbetstillst[åa]nd|arbejdstilladelse|arbeidstillatelse|"
            r"ty[öo]lupa\w*|autoriza[çc][ãa]o\s+de\s+trabalho)\b"
            r".{0,30}\b(?:(?:is|are|must\s+be)\s+)?"
            r"(?:required|mandatory|mandatory|essential|needed|obligatory|"
            r"a\s+prerequisite|erforderlich|necessario|wymagan\w*|"
            r"vy[žz]adov\w*|kr[äa]vs?|p[åa]kr[æe]vet|p[åa]krevd|kreves?|"
            r"vaaditaan|vaadittu|obrigat\w*|necess[áa]ri\w*|obligatorisk\w*|"
            r"povinn\w*)\b|"
            r"\brequired\b.{0,20}\b(?:right to work|work permit|visa)\b|"
            # Pre-posed requirement ("Wymagane prawo do pracy", "É necessária
            # autorização de trabalho") -- noun AFTER the requirement word.
            r"(?:wymagan\w*|vy[žz]adov\w*|kr[äa]vet|kreves?|p[åa]kr[æe]vet|"
            r"p[åa]krevd|vaaditaan|vaadittu|obrigat\w*|necess[áa]ri\w*)"
            r"\s+(?:\w+\s+){0,2}?(?:prawo\s+do\s+pracy|"
            r"pozwoleni\w*\s+na\s+prac[eę]|pracovn[íi]\s+povolen[íi]|"
            r"povolen[íi]\s+k\s+pr[áa]ci|arbetstillst[åa]nd|"
            r"arbejdstilladelse|arbeidstillatelse|ty[öo]lupa\w*|"
            r"autoriza[çc][ãa]o\s+de\s+trabalho)\b"
            # FIX 2026-10-09 (batch 20261009T225341): "All offers ... are
            # subject to background checks, including right to work, ...
            # financial checks." is an eligibility screen -- the candidate
            # must ALREADY be employable. Was Yes 0.6 ("including" +
            # "subject to"), only saved to Unknown by the scanners' guard (1).
            r"|\bright\s+to\s+work\b[^.;!?]{0,60}\b(?:checks?|verification|"
            r"verified|screening|screen)\b"
            r"|\b(?:background|employment|pre[- ]employment)\s+checks?\b"
            r"[^.;!?]{0,80}\bright\s+to\s+work\b",
            sentence, re.I,
        ):
            return VERDICT_NO, 0.9, ["candidate-must-already-have-authorization"]
        # Qualifiers must be near the concept; unrelated verbs/negations elsewhere
        # in a long sentence or bullet must not leak polarity.
        left = max(0, concept_match.start() - 90)
        right = min(len(sentence), concept_match.end() + 90)
        window = sentence[left:right]
        has_positive = bool(self.POSITIVE_VERBS.search(window))
        has_negation = bool(self.NEGATION.search(window))
        has_requirement = bool(self.REQUIREMENT.search(window))
        has_conditional = bool(self.CONDITIONAL.search(window))
        # OR in the other languages' nearby qualifiers.
        for lang, pats in self.EXTRA_LANGS.items():
            if pats["pos"].search(window):
                has_positive = True
            if pats["neg"].search(window):
                has_negation = True
            if pats["req"].search(window):
                has_requirement = True
            if pats["cond"].search(window):
                has_conditional = True
        # A negation spent on the PROBLEM noun is not a refusal -- see
        # PROBLEM_NEGATION. The phrase is removed and only a negation that
        # remains OUTSIDE it counts, so "we do not sponsor visas even though
        # they are not a problem" is still hard "No".
        problem_negated = False
        if self.PROBLEM_NEGATION.search(window):
            residual = self.PROBLEM_NEGATION.sub(" ", window)
            has_negation = bool(self.NEGATION.search(residual)) or any(
                pats["neg"].search(residual)
                for pats in self.EXTRA_LANGS.values())
            problem_negated = True
        # FIX P65 (A2): "eligible for visa sponsorship" is an offer, not a verb.
        if self.ROLE_SPONSORSHIP_ELIGIBLE.search(window):
            has_positive = True

        flags = []
        if has_requirement:
            flags.append("candidate-must-move")
        if has_conditional:
            flags.append("conditional")
        m_scope = self.SCOPE.search(sentence)
        if m_scope:
            flags.append("scope:" + m_scope.group(0).strip()[:30])

        if has_requirement and not has_negation:
            return VERDICT_NO, 0.8, flags + ["requirement-not-support"]
        has_requires_verb = bool(self.REQUIRES_VERB.search(window)) or any(
            pats["reqverb"].search(window) for pats in self.EXTRA_LANGS.values())
        if has_negation and re.search(
            r"(?:do not|don't|dont|cannot|can't|cant|will not|won't|wont|are not|"
            r"aren't|arent|is not|isn't|isnt|unable to|not able to)"
            r"\s+(?:accept|consider|hire|employ|sponsor)|"
            r"applicants?\s+who\s+need\s+(?:visa\s+)?sponsorship",
            sentence, re.I,
        ):
            return VERDICT_NO, 0.95, flags + ["explicit-candidate-denial"]
        if has_negation and has_requires_verb:
            return VERDICT_UNKNOWN, 0.6, flags + ["not-required-neutral"]
        if has_negation and self.HEDGED_NEGATION.search(window):
            # Checked BEFORE the plain-negation return below: "sponsorship is
            # not guaranteed" is a hedge, not a refusal.
            return VERDICT_UNKNOWN, 0.5, flags + ["hedged-negation"]
        if has_negation:
            return VERDICT_NO, 0.9, flags + ["negated"]
        if problem_negated and not has_positive:
            # "A visa is not a problem" -- a warm signal, but it never says
            # the employer SPONSORS. Unknown (never No, never a fabricated
            # offer), ranked above a bare mention.
            return VERDICT_UNKNOWN, 0.5, flags + ["negated-problem"]
        if has_positive:
            if has_conditional:
                # A speculative offer ("may be available") with nothing
                # stronger behind it is not a scoped offer -- see
                # SPECULATIVE_OFFER above. Reached only when NO negation
                # matched, so hedged negations are unaffected.
                if self.SPECULATIVE_OFFER.search(window):
                    rest = self.SPECULATIVE_OFFER.sub(" ", window)
                    if (not self.POSITIVE_VERBS.search(rest)
                            and not self.ROLE_SPONSORSHIP_ELIGIBLE.search(window)
                            and not any(pats["pos"].search(window)
                                        for pats in self.EXTRA_LANGS.values())):
                        return VERDICT_UNKNOWN, 0.5, flags + ["speculative-conditional"]
                # FIX P65 (A2): "While Etsy supports visa sponsorship,
                # opportunities may be limited to certain roles" returned
                # Unknown -- indistinguishable from "we never looked", for the
                # single most valuable sentence a sponsorship hunter can find.
                # A hedged offer is still an offer: report Yes and carry the
                # hedge in the confidence (0.6) and the "conditional" flag.
                # Reached only when NO negation matched above, so
                # "sponsorship is not guaranteed" is unaffected.
                return VERDICT_YES, 0.6, flags + ["positive-but-conditional"]
            return VERDICT_YES, 0.9, flags
        if has_conditional:
            return VERDICT_UNKNOWN, 0.4, flags + ["bare-conditional"]
        # FIX P26: a benefits list prints the benefit as a NOUN PHRASE, with
        # no verb for POSITIVE_VERBS to find:
        #     - A relocation package
        #     - Relocation assistance
        # Amazon Italia produced "A relocation package" -> Unknown 0.20 while
        # the identical "We offer a relocation package." -> Yes 0.90. The
        # phrase only counts when the whole fragment IS the benefit (short,
        # unnegated, not a requirement, not a question), so "relocation
        # assistance is not provided" and "Do you need a relocation package?"
        # are untouched.
        if (concept_re is self.RELOCATION_CONCEPTS
                and not has_negation and not has_requirement
                and not sentence.rstrip().endswith("?")
                and len(sentence) <= 80
                and self.BENEFIT_NOUN_PHRASE.match(sentence)):
            return VERDICT_YES, 0.8, flags + ["benefit-noun-phrase"]
        return VERDICT_UNKNOWN, 0.2, flags + ["bare-mention"]

    #: FIX P66 (B2): how much context to keep either side of the match.
    EVIDENCE_LEAD = 150
    EVIDENCE_TRAIL = 250

    @classmethod
    def _evidence_window(cls, sent, concept_re):
        """Quote the clause that decided the verdict, not the sentence head."""
        limit = cls.EVIDENCE_LEAD + cls.EVIDENCE_TRAIL
        if len(sent) <= limit:
            return sent
        m = concept_re.search(sent)
        if not m:
            return sent[:limit]
        lo = max(0, m.start() - cls.EVIDENCE_LEAD)
        hi = min(len(sent), m.end() + cls.EVIDENCE_TRAIL)
        snip = sent[lo:hi].strip()
        if lo > 0:
            snip = "\u2026 " + snip
        if hi < len(sent):
            snip = snip + " \u2026"
        return snip

    def _aggregate(self, text, concept_re):
        scores = {VERDICT_YES: 0.0, VERDICT_NO: 0.0, VERDICT_UNKNOWN: 0.0}
        evidence = []
        required = False
        for sent in self.split_sentences(text):
            res = self.sentence_verdict(sent, concept_re)
            if not res:
                continue
            verdict, conf, flags = res
            scores[verdict] += conf
            # FIX P65 (A2): 140 chars truncated the evidence mid-sentence --
            # eDreams' "...birthday day off, and a relocation package to h"
            # cut off the very words that justified reloc=Yes@0.9, so the
            # verdict was correct but unauditable in the app. 400 keeps the
            # matched clause for every sentence seen in run 20261008T113455.
            # FIX P66 (B2): P65 raised the cap 140 -> 400 but kept slicing
            # from the START of the sentence. A bullet list scraped without
            # punctuation is ONE sentence, so four Booking.com rows justified
            # "Visa Sponsorship = No" with a paragraph about medical
            # insurance and product discounts, while the clause that actually
            # decided it -- "This role does not come with relocation and visa
            # sponsorship assistance." -- sat past the cut and was never
            # shown. The verdict was right and the proof was missing, which
            # is exactly what A2c set out to fix. Centre the window on the
            # concept match instead.
            evidence.append(
                (verdict, conf, flags, self._evidence_window(sent, concept_re)))
            if "candidate-must-move" in flags or "requirement-not-support" in flags:
                required = True
        if not evidence:
            return {"verdict": VERDICT_UNKNOWN, "confidence": 0.0,
                    "required": False, "evidence": []}
        if scores[VERDICT_YES] > scores[VERDICT_NO] and scores[VERDICT_YES] >= scores[VERDICT_UNKNOWN]:
            verdict = VERDICT_YES
        elif scores[VERDICT_NO] > scores[VERDICT_YES] and scores[VERDICT_NO] >= scores[VERDICT_UNKNOWN]:
            verdict = VERDICT_NO
        else:
            verdict = VERDICT_UNKNOWN
        total = sum(scores.values())
        return {
            "verdict": verdict,
            # v7: evidence strength, not vote share. A lone weak Unknown mention
            # remains 0.2 rather than becoming a misleading 1.0.
            "confidence": round(max((e[1] for e in evidence), default=0.0), 2),
            "required": required,
            "evidence": sorted(evidence, key=lambda e: -e[1])[:4],
        }

    def detect(self, text):
        """Returns {visa, relocation} each with verdict/confidence/required/evidence.

        FIX P22/P23: the input is normalised (entities decoded, markup
        stripped) before anything is matched, so an adapter that hands over
        raw HTML is classified as the prose it contains and the stored
        evidence is readable.

        FIX P21: an employer "VISA Sponsorship: Yes/No" style field, when
        present and self-consistent, OVERRIDES the prose aggregate. It is a
        structured answer from the employer, not an inference.
        """
        text = normalize_jd_text(text)
        out = {
            "visa": self._aggregate(text, self.VISA_CONCEPTS),
            "relocation": self._aggregate(text, self.RELOCATION_CONCEPTS),
        }
        for kind in ("visa", "relocation"):
            lab = label_field_verdict(text, kind)
            if not lab:
                if label_field_blank(text, kind):
                    out[kind] = {
                        "verdict": VERDICT_UNKNOWN,
                        "confidence": 0.3,
                        "required": False,
                        "evidence": [(VERDICT_UNKNOWN, 0.3,
                                      ["employer-declared-field-blank"],
                                      kind + " field present but empty")],
                    }
                continue
            verdict, evidence = lab
            prior = out[kind].get("evidence") or []
            out[kind] = {
                "verdict": verdict,
                "confidence": 0.95,
                "required": out[kind].get("required", False),
                "evidence": ([(verdict, 0.95, ["employer-declared-field"],
                               evidence)] + list(prior))[:4],
            }
        return out

    def best_evidence(self, result, limit=2):
        return "; ".join(e[3] for e in result["evidence"][:limit])


# FIX P0-57: Blue Card keywords, including German declension ("Blauen Karte"),
# the hyphenated "EU-Blue Card" and the trailing-EU ordering ("Blue Card EU").
_BLUE_CARD_RE = re.compile(
    r"\b(?:eu[-\s]*)?blue[-\s]?card(?:\s*eu)?\b"
    r"|\bblaue[nrms]?\s+karte(?:\s*eu)?\b"
    r"|\bcarta\s+blu(?:\s*ue)?\b"
    r"|\bblauwe\s+kaart\b"
    r"|\bcarte\s+bleue(?:\s+europ\w*)?\b"
    r"|\btarjeta\s+azul(?:\s+ue)?\b"
    r"|\bniebiesk\w*\s+kart\w*\b"
    r"|\bmodr\w*\s+kart\w*\b"
    r"|\bcart[ãa]o\s+azul\b"
    r"|\b(?:eu[-\s]*)?bl[åa][-\s]?kort\w*\b"
    r"|\bsinis\w*\s+kortt?\w*\b",
    re.I)

# FIX P0-57: non-English affirmative verbs. detector.POSITIVE_VERBS is
# English-only; without this a German, Italian, Spanish, French or Dutch ad
# offering a Blue Card could satisfy the keyword test and still never reach a
# "Yes". Includes the noun forms these languages actually use in benefit
# lists ("Unterstuetzung bei...", "supporto per...").
_BLUE_CARD_POSITIVE_RE = re.compile(
    r"\b(?:unterst[uü]tz\w*|hilfe|helfen|hilft|bieten|bietet|angeboten|"
    r"[uü]bernehmen|[uü]bernimmt|erm[oö]glich\w*|beantrag\w*|"
    r"beantragung|visum\w*|"
    r"support\w*|sosten\w*|aiut\w*|offriamo|offre|forniamo|fornisce|"
    r"assistenza|agevol\w*|"
    r"ofrecemos|ofrece|apoyo|ayuda|facilitamos|"
    r"proposons|propose|offrons|aide|accompagn\w*|prise\s+en\s+charge|"
    r"bieden|biedt|ondersteun\w*|helpen|verzorgen|regelen|"
    r"oferujemy|oferuje|zapewniamy|zapewnia|pomagamy|pomoc|uzyskani\w*|"
    r"nab[íi]z[íi]me|nab[íz]z[íi]|zaji[šs][ťt]ujeme|podpor\w*|sponzorujeme|"
    r"erbjuder|erbjuds|tillhandah[åa]ller|st[öo]d|hj[äa]lp|ans[öo]kan|"
    r"tilbyder|tilbydes|yder|st[øo]tte|hj[æa]lp|"
    r"tilbyr|tilbys|hjelp|s[øo]ker|"
    r"tarjoamme|tarjoaa|tarjotaan|tuki|apu|autamme|hakemus|"
    r"oferecemos|oferece|proporcionamos|apoio|ajuda|suporte|apoiamos)\b",
    re.I)


def detect_blue_card(detector, text):
    """A single, consistent EU Blue Card classifier used by BOTH scanners.

    Reconciles a divergence found during the migration:

      * ats_portal_scannerv5 decided blue-card per-sentence using an explicit
        keyword match plus negation/positive-verb guards.
      * career_portal_scanner_v7's detail enrichment short-circuited to
        ``"Y"`` whenever the visa verdict was ``Yes`` - too permissive.

    This function is the shared, authoritative classifier (the v5 approach):
    explicit EU Blue Card keywords in a sentence, with negation guarding the
    verdict.  Returns ``VERDICT_YES`` / ``VERDICT_NO`` / ``VERDICT_UNKNOWN``,
    i.e. the strings "Yes" / "No" / "Unknown".

    FIX P0-57: the docstring used to claim it returned "Y"/"N"/"Unknown",
    which is NOT what the code does and is exactly the confusion that left
    persistence.py comparing against "y" (see FIX P0-54 there).

    FIX P0-57 also closes two detection holes that mattered on a
    Germany-heavy seed set:

    * ``POSITIVE_VERBS`` is English-only, so a German ad -- the single most
      likely place on earth to mention a Blue Card -- could never satisfy the
      verb gate. "Wir unterstuetzen Sie bei der Beantragung der Blauen Karte
      EU" matched the keyword, found no English verb, and returned Unknown.
    * The keyword list did not match German declension ("Blauen Karte"),
      the hyphenated "EU-Blue Card", or the common "Blue Card EU" ordering.
    """
    if not text:
        return VERDICT_UNKNOWN
    for sent in detector.split_sentences(text):
        if _BLUE_CARD_RE.search(sent):
            negated = bool(detector.NEGATION.search(sent)) or any(
                pats["neg"].search(sent)
                for pats in detector.EXTRA_LANGS.values())
            if negated:
                return VERDICT_NO
            if (detector.POSITIVE_VERBS.search(sent)
                    or _BLUE_CARD_POSITIVE_RE.search(sent)):
                return VERDICT_YES
    return VERDICT_UNKNOWN

