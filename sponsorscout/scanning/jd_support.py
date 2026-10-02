"""Context-aware Visa Sponsorship / Relocation Support / EU Blue Card detection.

Single source of truth for JD evidence classification.
Extracted verbatim from ats_portal_scannerv5.py / career_portal_scanner_v7.py
(which carried an identical copy of this class marked "keep in sync").

Policy:
  * Never matches keywords alone - every mention is judged within its sentence
    or clause, with negation / requirement / conditional / scope qualifiers.
  * Verdicts are Yes / No / Unknown.  No fabricated "No" when evidence is absent.
"""

import re

VERDICT_YES, VERDICT_NO, VERDICT_UNKNOWN = "Yes", "No", "Unknown"


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
        r"|visum\w*|werkvergunning|arbeidsvergunning|blauwe kaart|sponsoring"  # NL
        r"|visa\w*|permis de travail|carte bleue|parrainage|immigration|sponsorisons?|"
        r"sponsorisent|parrainons|parraine"  # FR
        r"|visad\w*|permiso de trabajo|tarjeta azul|patrocin\w*|inmigración"  # ES
        r")\b",
        re.I,
    )
    RELOCATION_CONCEPTS = re.compile(
        r"\brelocat(e|es|ed|ing|ion|ions)?\b|\b(moving|move|relocation)\s+(assistance|"
        r"package|allowance|support|benefit|reimbursement|stipend|costs|expenses|bonus|"
        r"budget|help|aid)\b|\bassist\w*\b.{0,25}\b(relocat|move)\b"
        r"|\bumzug\w*|\bumzuziehen\b|\bumsiedl\w*|relokation"   # DE
        r"|\bricolloc\w*|\btrasfer\w*|\btrasloc\w*|relocazione|assistenza al trasferimento"  # IT
        r"|\bverhuis\w*|\bverhuiz\w*|relocatie"  # NL (verhuis- compounds + verhuizen verb)
        r"|\brelocalis\w*|\bdéménag\w*|\bréinstall\w*|frais de déménagement"  # FR
        r"|\breubic\w*|\btraslad\w*|\bmudanz\w*|ayuda de reubicación|gastos de reubicación"  # ES
        r"|\bassist\w*\b.{0,25}\b(umzug|trasfer|verhuis|déménag|reubic)\b",
        re.I,
    )
    POSITIVE_VERBS = re.compile(
        r"\b(offer|offers|offered|offering|provide|provides|provided|providing|support|"
        r"supports|supported|supporting|assist|assists|assisted|assisting|help|helps|"
        r"helped|cover|covers|covered|covering|pay|pays|paid|reimburse|reimburses|"
        r"reimbursed|arrange|arranges|arranged|handle|handles|handled|sponsor|sponsors|"
        r"sponsored|sponsoring|include|includes|included|including|available|is offered|"
        r"is provided|will be provided|is included|granted|we will|receive|receives|"
        r"received|get|gets|enjoy|enjoys)\b",
        re.I,
    )
    NEGATION = re.compile(
        r"\b(not|no|never|without|cannot|can't|can not|does not|doesn't|do not|don't|"
        r"will not|won't|would not|wouldn't|unable|unfortunately|regret|except|excluding|"
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
        r"\b(?:not|never|no)\s+(?:guaranteed|assured|available|offered|provided|"
        r"possible|granted|committed|given|confirmed|ruled out)\b"
        r"|\bnot\s+necessarily\b|\bmay\s+not\b|\bmight\s+not\b|\bcannot\s+be\s+"
        r"(?:guaranteed|assured|ruled out)\b|\bdoes\s+not\s+mean\s+that\s+we\s+can(?:no|\x27)t\b"
        r"|\bnicht\s+garantiert\b|\bkeine\s+garantie\b|\bnicht\s+zugesichert\b"
        r"|\bnon\s+garanti\b|\bnon\s+garantie\b|\bno\s+garantizado\b|\bgeen\s+garantie\b",
        re.I,
    )

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
            "pos": re.compile(r"\b(offriamo|offre|forniamo|fornisce|supportiamo|supportare|"
                              r"supporta|aiutiamo|copriamo|paghiamo|rimborsiamo|include|incluso|"
                              r"disponibile|ricevere|ricevono)\b", re.I),
            "neg": re.compile(r"\b(non|nessun|nessuna|senza|purtroppo|non possiamo|non è possibile|"
                              r"non disponibile|non offre|non forniamo)\b", re.I),
            "req": re.compile(r"\b(disposto|disposta|pronto|pronta|disponibile|disponibilità|"
                              r"disponibilita)\b.{0,30}\b(trasfer\w*|spost\w*|ricolloc\w*)\b"
                              r"|\b(trasfer\w*|spost\w*)\b.{0,30}\b(richiesto|obbligatorio|"
                              r"necessario|richiede)\b", re.I),
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
    }

    @staticmethod
    def split_sentences(text):
        text = re.sub(r"[ \t]+", " ", text or "")
        parts = re.split(
            r"(?<=[.!?])\s+|\n+|;\s*|\s+(?:but|while|whereas|however|yet|though|although|"
            r"aber|jedoch|während|aber|ma|mentre|però|tuttavia|maar|echter|terwijl|"
            r"mais|cependant|tandis que|pero|sin embargo|mientras)\s+",
            text,
        )
        return [p.strip() for p in parts if p.strip()]

    def sentence_verdict(self, sentence, concept_re):
        concept_match = concept_re.search(sentence)
        if not concept_match:
            return None
        # Relocation can describe equipment, vehicles, offices or a job function.
        # Those are not candidate benefits.
        if concept_re is self.RELOCATION_CONCEPTS and re.search(
            r"\b(relocat(?:e|ing|ion)?\s+(?:vehicles?|cars?|equipment|machines?|systems?|"
            r"offices?|data centers?|assets?)|(?:install|upgrade|fleet|vehicle)\b.{0,35}"
            r"\brelocat|relocation\s+(?:engineer|specialist|coordinator|project))\b",
            sentence, re.I,
        ):
            return VERDICT_UNKNOWN, 0.0, ["non-candidate-relocation-context"]
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
            r"permis\s+de\s+s[ée]jour|permiso\s+de\s+residencia)\b"
            r".{0,30}\b(?:(?:is|are|must\s+be)\s+)?"
            r"(?:required|mandatory|mandatory|essential|needed|obligatory|"
            r"a\s+prerequisite|erforderlich|necessario)\b|"
            r"\brequired\b.{0,20}\b(?:right to work|work permit|visa)\b",
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
            r"(?:do not|don't|cannot|can't|will not|won't)\s+(?:accept|consider|hire|employ|sponsor)|"
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
        if has_positive:
            if has_conditional:
                return VERDICT_UNKNOWN, 0.5, flags + ["positive-but-conditional"]
            return VERDICT_YES, 0.9, flags
        if has_conditional:
            return VERDICT_UNKNOWN, 0.4, flags + ["bare-conditional"]
        return VERDICT_UNKNOWN, 0.2, flags + ["bare-mention"]

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
            evidence.append((verdict, conf, flags, sent[:140]))
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
        """Returns {visa, relocation} each with verdict/confidence/required/evidence."""
        text = text or ""
        return {
            "visa": self._aggregate(text, self.VISA_CONCEPTS),
            "relocation": self._aggregate(text, self.RELOCATION_CONCEPTS),
        }

    def best_evidence(self, result, limit=2):
        return "; ".join(e[3] for e in result["evidence"][:limit])


def detect_blue_card(detector, text):
    """A single, consistent EU Blue Card classifier used by BOTH scanners.

    Reconciles a divergence found during the migration:

      * ats_portal_scannerv5 decided blue-card per-sentence using an explicit
        keyword match plus negation/positive-verb guards.
      * career_portal_scanner_v7's detail enrichment short-circuited to
        ``"Y"`` whenever the visa verdict was ``Yes`` - too permissive.

    This function is the shared, authoritative classifier (the v5 approach):
    explicit EU Blue Card keywords in a sentence, with negation guarding the
    verdict.  Returns "Y" / "N" / "Unknown".
    """
    if not text:
        return VERDICT_UNKNOWN
    for sent in detector.split_sentences(text):
        if re.search(
            r"\b(?:eu\s+)?blue[- ]?card|blaue karte|carta blu|blauwe kaart|carte bleue|tarjeta azul\b",
            sent, re.I,
        ):
            if detector.NEGATION.search(sent):
                return VERDICT_NO
            if detector.POSITIVE_VERBS.search(sent):
                return VERDICT_YES
    return VERDICT_UNKNOWN

