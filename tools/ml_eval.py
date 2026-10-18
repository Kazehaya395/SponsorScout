"""Detector regression gate (run from anywhere): python3 tools/ml_eval.py"""
"""Multilingual sponsorship-detector probe (IT/NL/DE/FR/EN). Structure-only sentences."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from sponsorscout.scanning.jd_support import JDSupportDetector
d = JDSupportDetector()
# (text, expected visa verdict, expected relocation verdict)  Y=Yes N=No U=Unknown
CASES = [
 # ---- Italian positive
 ("Offriamo supporto per il visto di lavoro e la relocation per i candidati selezionati.", "Y", "Y"),
 ("Siamo disponibili a sponsorizzare il permesso di soggiorno per candidati extra-UE.", "Y", "U"),
 ("Garantiamo l'assistenza per il trasferimento e per il permesso di soggiorno.", "Y", "Y"),
 ("Sponsorizzazione del visto: sì, supportiamo il processo di richiesta.", "Y", "U"),
 ("Siamo in grado di sponsorizzare il visto per il candidato ideale.", "Y", "U"),
 ("Rimborso delle spese di trasferimento per chi si trasferisce a Milano.", "U", "Y"),
 # ---- Italian negative
 ("Non siamo in grado di offrire sponsorizzazione del visto per questa posizione.", "N", "U"),
 ("Non forniamo supporto per il permesso di soggiorno: è richiesta la residenza in Italia.", "N", "U"),
 ("Il candidato deve già essere in possesso del permesso di soggiorno per lavorare in Italia.", "N", "U"),
 ("Sono richiesti diritto al lavoro in Italia senza necessità di sponsorizzazione.", "N", "U"),
 # ---- Italian unknown / neutral
 ("Cerchiamo un sales manager con esperienza nel retail e buona conoscenza dell'inglese.", "U", "U"),
 ("Sponsorship of the Milan marathon event by our brand team.", "U", "U"),
 ("Lavoriamo con i nostri sponsor per eventi sportivi in tutta Italia.", "U", "U"),
 # ---- Dutch positive
 ("Wij bieden ondersteuning bij de kennismigrantenregeling en het verkrijgen van een verblijfsvergunning.", "Y", "U"),
 ("We kunnen je helpen met de visumaanvraag en de relocatie naar Amsterdam.", "Y", "Y"),
 ("Wij sponsoren de werkvergunning voor kandidaten van buiten de EU.", "Y", "U"),
 ("Een relocatiebonus en hulp bij het zoeken naar huisvesting in Nederland.", "U", "Y"),
 # ---- Dutch negative
 ("Helaas kunnen wij geen visum of werkvergunning sponsoren voor deze functie.", "N", "U"),
 ("Je moet over een geldige verblijfsvergunning beschikken; wij bieden geen sponsoring.", "N", "U"),
 ("Het is vereist dat je werkgerechtigd bent in Nederland.", "N", "U"),
 ("Wij zijn een ondernemende organisatie met een groot netwerk van klanten.", "U", "U"),
 # ---- German positive
 ("Wir unterstützen bei der Aufenthaltserlaubnis und der Blue Card für internationale Fachkräfte.", "Y", "U"),
 ("Wir bieten Visa-Sponsoring und Umzugsunterstützung für den richtigen Kandidaten.", "Y", "Y"),
 ("Wir übernehmen die Kosten für das Visum und unterstützen bei der Relocation nach Berlin.", "Y", "Y"),
 ("Für diese Stelle sponsern wir die Arbeitserlaubnis.", "Y", "U"),
 # ---- German negative
 ("Wir können leider kein Visum-Sponsoring für diese Position anbieten.", "N", "U"),
 ("Eine gültige Arbeitserlaubnis für Deutschland ist Voraussetzung.", "N", "U"),
 ("Bewerber müssen zum Arbeiten in der EU berechtigt sein; eine Unterstützung bei der Aufenthaltserlaubnis findet nicht statt.", "N", "U"),
 ("Wir suchen eine Teamleitung für unser Sponsoring-Team im Marketing.", "U", "U"),
 # ---- French
 ("Nous proposons un accompagnement visa et une aide à la relocalisation pour les candidats retenus.", "Y", "Y"),
 ("Aucun parrainage de visa ne sera accordé pour ce poste.", "N", "U"),
 # ---- English positive / negative / hedged
 ("We are happy to sponsor visas for the right candidate.", "Y", "U"),
 ("We offer relocation assistance including flights and temporary housing.", "U", "Y"),
 ("We are unable to offer visa sponsorship for this role.", "N", "U"),
 ("You must have the right to work in Italy; we do not sponsor work visas.", "N", "U"),
 # DOCUMENTED CASE (fix plan §2, 2026-10-13): hedges map to Unknown, never Yes.
 ("Visa sponsorship may be available for exceptional candidates.", "U", "U"),
 # ---- P0-2 golden sentences from the 2026-10-12 run (fix plan §2) ----
 ("Searching for Devops roles that provide visa sponsorship? Explore our comprehensive directory of visa sponsorship jobs from employers worldwide who are ready to sponsor talented international professionals.", "U", "U"),
 ("Language Courses, Visa Support, LOOP.Horizon workshops and trainings, Klimaticket Contribution, Audiobook Library, Headspace App Subscription, etc.", "U", "U"),
 ("H-1B Visa & Relocation Assistance: We support Visa or Green Card sponsorships, plus our Journey Travel Program lets you work across the U.S.", "Y", "Y"),
 ("All offers of employment are subject to background checks, including right to work, reference education and for some roles criminal, and financial checks.", "U", "U"),
 ("Work Authorization Notice: Please note that we do not provide visa sponsorship or immigration support for this position.", "N", "U"),
 ("Applicants must already be authorized to work in the United States on a full-time, permanent basis without the need for current or future sponsorship.", "N", "U"),
 ("Our package includes visa assistance, destination services to help you and your family settle in comfortably, and a relocation bonus to cover additional expenses, such as temporary housing.", "Y", "Y"),
 ("A Relocation Package : Includes visa support for those who need it.", "Y", "Y"),
 ("Right to Work and Checks: All applicants must have a valid right to work in the UK.", "N", "U"),  # precondition wording stays No (pinned)
 ("Visa sponsorship is available for exceptional candidates.", "U", "U"),
 ("Sponsorship is not guaranteed and is considered case by case.", "U", "U"),
 ("Our executive sponsorship program supports local charities.", "U", "U"),
 ("Candidates must have the right to work in the UK. No sponsorship is available.", "N", "U"),
]
def norm(v):
    return {'Yes':'Y','No':'N','Unknown':'U'}.get(v, v)
tot=ok=0; fails=[]
for text, ev, er in CASES:
    r = d.detect(text)
    gv = norm(r['visa']['verdict']); gr = norm(r['relocation']['verdict'])
    tot += 2
    ok += (gv == ev) + (gr == er)
    if gv != ev: fails.append(('visa', ev, gv, text))
    if gr != er: fails.append(('reloc', er, gr, text))
print(f"accuracy {ok}/{tot} = {ok/tot:.1%}  ({len(CASES)} sentences)")
for kind, exp, got, text in fails:
    print(f"  [{kind}] expected {exp} got {got} :: {text[:110]}")
