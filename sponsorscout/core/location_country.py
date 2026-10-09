"""
Derive a job's actual country from the raw location string returned by ATS APIs.

ATS APIs return strings like "São Paulo", "Berlin, DE", "New York, NY",
"Amsterdam, North Holland, Netherlands", "Remote - EU", etc.
This module maps all such strings to a canonical country name for filtering.
"""
from __future__ import annotations
import re
import unicodedata

# ── ISO 3166-1 alpha-2 → Country ─────────────────────────────────────────────
ISO2_TO_COUNTRY: dict[str, str] = {
    "af": "Afghanistan", "al": "Albania", "dz": "Algeria", "ar": "Argentina",
    "au": "Australia", "at": "Austria", "be": "Belgium", "br": "Brazil",
    "bg": "Bulgaria", "ca": "Canada", "cl": "Chile", "cn": "China",
    "co": "Colombia", "hr": "Croatia", "cy": "Cyprus", "cz": "Czech Republic",
    "dk": "Denmark", "ee": "Estonia", "fi": "Finland", "fr": "France",
    "de": "Germany", "gr": "Greece", "hu": "Hungary", "in": "India",
    "id": "Indonesia", "ie": "Ireland", "il": "Israel", "it": "Italy",
    "jp": "Japan", "kr": "South Korea", "lv": "Latvia", "lt": "Lithuania",
    "lu": "Luxembourg", "mt": "Malta", "mx": "Mexico", "nl": "Netherlands",
    "nz": "New Zealand", "no": "Norway", "pl": "Poland", "pt": "Portugal",
    "ro": "Romania", "ru": "Russia", "rs": "Serbia", "sg": "Singapore",
    "sk": "Slovakia", "si": "Slovenia", "za": "South Africa", "es": "Spain",
    "se": "Sweden", "ch": "Switzerland", "tw": "Taiwan", "th": "Thailand",
    "tr": "Turkey", "ua": "Ukraine", "ae": "United Arab Emirates",
    "gb": "United Kingdom", "uk": "United Kingdom",
    "us": "United States", "vn": "Vietnam",
    "my": "Malaysia", "ph": "Philippines", "sa": "Saudi Arabia",
    "qa": "Qatar", "kw": "Kuwait", "eg": "Egypt", "ng": "Nigeria",
    "ke": "Kenya", "pk": "Pakistan", "bd": "Bangladesh", "is": "Iceland",
    "kz": "Kazakhstan", "by": "Belarus", "mk": "North Macedonia",
    "ba": "Bosnia and Herzegovina", "uy": "Uruguay", "ec": "Ecuador",
    "pe": "Peru", "ve": "Venezuela", "gh": "Ghana", "jo": "Jordan",
    "lb": "Lebanon", "hk": "Hong Kong", "np": "Nepal", "lk": "Sri Lanka",
    "mm": "Myanmar",
    # NOTE: "md" (Maryland/Moldova) and "tn" (Tennessee/Tunisia) are
    # intentionally NOT mapped: the B8 rule resolves them from the
    # preceding city segment instead ("Chisinau, MD" -> Moldova,
    # "Bethesda, MD" -> United States).
}

# ── US states (both abbreviation and full name) ───────────────────────────────
US_STATES_ABBR: set[str] = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in",
    "ia","ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv",
    "nh","nj","nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn",
    "tx","ut","vt","va","wa","wv","wi","wy","dc",
}
US_STATES_FULL: set[str] = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut",
    "delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa",
    "kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan",
    "minnesota","mississippi","missouri","montana","nebraska","nevada",
    "new hampshire","new jersey","new mexico","new york","north carolina",
    "north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island",
    "south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming",
    "district of columbia",
}

# ── Canadian provinces (full name only — abbrevs conflict with ISO2) ──────────
CA_PROVINCES_FULL: set[str] = {
    "ontario","quebec","british columbia","alberta","manitoba","saskatchewan",
    "nova scotia","new brunswick","newfoundland","prince edward island",
    "northwest territories","nunavut","yukon",
}

# ── Full country names (lower) → canonical ───────────────────────────────────
COUNTRY_NAMES: dict[str, str] = {
    "afghanistan": "Afghanistan", "albania": "Albania", "algeria": "Algeria",
    "argentina": "Argentina", "australia": "Australia", "austria": "Austria",
    "belgium": "Belgium", "brazil": "Brazil", "bulgaria": "Bulgaria",
    "canada": "Canada", "chile": "Chile", "china": "China",
    "colombia": "Colombia", "croatia": "Croatia", "cyprus": "Cyprus",
    "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "denmark": "Denmark", "estonia": "Estonia", "finland": "Finland",
    "france": "France", "germany": "Germany", "deutschland": "Germany",
    "greece": "Greece", "hungary": "Hungary", "india": "India",
    "indonesia": "Indonesia", "ireland": "Ireland", "israel": "Israel",
    "italy": "Italy", "japan": "Japan", "south korea": "South Korea",
    "latvia": "Latvia", "lithuania": "Lithuania", "luxembourg": "Luxembourg",
    "malta": "Malta", "mexico": "Mexico", "méxico": "Mexico",
    "netherlands": "Netherlands", "the netherlands": "Netherlands",
    "new zealand": "New Zealand", "norway": "Norway", "poland": "Poland",
    "portugal": "Portugal", "romania": "Romania", "russia": "Russia",
    "serbia": "Serbia", "singapore": "Singapore", "slovakia": "Slovakia",
    "slovenia": "Slovenia", "south africa": "South Africa", "spain": "Spain",
    "españa": "Spain", "sweden": "Sweden", "switzerland": "Switzerland",
    "taiwan": "Taiwan", "thailand": "Thailand", "turkey": "Turkey",
    "ukraine": "Ukraine", "united arab emirates": "United Arab Emirates",
    "uae": "United Arab Emirates",
    "united kingdom": "United Kingdom", "uk": "United Kingdom",
    "great britain": "United Kingdom", "england": "United Kingdom",
    "scotland": "United Kingdom", "wales": "United Kingdom",
    "united states": "United States", "usa": "United States",
    "u.s.a.": "United States", "u.s.": "United States",
    "america": "United States", "united states of america": "United States",
    "vietnam": "Vietnam",
    "bahrain": "Bahrain", "belarus": "Belarus",
    "bosnia and herzegovina": "Bosnia and Herzegovina",
    "bosnia": "Bosnia and Herzegovina",
    "ecuador": "Ecuador", "egypt": "Egypt", "ghana": "Ghana",
    "hong kong": "Hong Kong", "iceland": "Iceland", "jordan": "Jordan",
    "kenya": "Kenya", "kuwait": "Kuwait", "lebanon": "Lebanon",
    "malaysia": "Malaysia", "moldova": "Moldova",
    "republic of moldova": "Moldova", "morocco": "Morocco",
    "nigeria": "Nigeria", "north macedonia": "North Macedonia",
    "macedonia": "North Macedonia", "peru": "Peru",
    "philippines": "Philippines", "qatar": "Qatar",
    "saudi arabia": "Saudi Arabia", "tunisia": "Tunisia",
    "uruguay": "Uruguay", "venezuela": "Venezuela",
    "holland": "Netherlands", "korea": "South Korea",
    "republic of korea": "South Korea",
    "russian federation": "Russia", "u.k.": "United Kingdom",
}

# ── City → Country ────────────────────────────────────────────────────────────
CITY_TO_COUNTRY: dict[str, str] = {
    # Netherlands
    "amsterdam": "Netherlands", "rotterdam": "Netherlands",
    "the hague": "Netherlands", "den haag": "Netherlands",
    "utrecht": "Netherlands", "eindhoven": "Netherlands",
    "haarlem": "Netherlands", "delft": "Netherlands",
    "groningen": "Netherlands", "nijmegen": "Netherlands",
    "tilburg": "Netherlands", "breda": "Netherlands",
    "almere": "Netherlands", "arnhem": "Netherlands",
    "apeldoorn": "Netherlands", "enschede": "Netherlands",
    "zwolle": "Netherlands", "maastricht": "Netherlands",

    # Germany
    "berlin": "Germany", "munich": "Germany", "münchen": "Germany",
    "hamburg": "Germany", "frankfurt": "Germany", "frankfurt am main": "Germany",
    "cologne": "Germany", "köln": "Germany", "koln": "Germany",
    "düsseldorf": "Germany", "dusseldorf": "Germany", "stuttgart": "Germany",
    "dortmund": "Germany", "nuremberg": "Germany", "nürnberg": "Germany",
    "nurnberg": "Germany", "dresden": "Germany", "leipzig": "Germany",
    "hannover": "Germany", "hanover": "Germany", "bonn": "Germany",
    "mannheim": "Germany", "karlsruhe": "Germany", "augsburg": "Germany",
    "wiesbaden": "Germany", "münster": "Germany", "munster": "Germany",
    "freiburg": "Germany", "kiel": "Germany", "mainz": "Germany",
    "heidelberg": "Germany", "essen": "Germany",
    "bremen": "Germany", "duisburg": "Germany", "bochum": "Germany",
    "wuppertal": "Germany", "bielefeld": "Germany", "aachen": "Germany",
    "braunschweig": "Germany", "chemnitz": "Germany", "krefeld": "Germany",
    "halle": "Germany", "magdeburg": "Germany", "erfurt": "Germany",
    "rostock": "Germany", "potsdam": "Germany", "darmstadt": "Germany",
    "regensburg": "Germany", "ingolstadt": "Germany",
    "würzburg": "Germany", "wurzburg": "Germany", "ulm": "Germany",
    "pforzheim": "Germany", "wolfsburg": "Germany",
    "leverkusen": "Germany", "ludwigshafen": "Germany",
    "oldenburg": "Germany", "osnabrück": "Germany",
    "osnabruck": "Germany", "saarbrücken": "Germany",
    "saarbrucken": "Germany", "kassel": "Germany", "trier": "Germany",
    "koblenz": "Germany", "walldorf": "Germany", "wetzlar": "Germany",
    "giessen": "Germany",
    # United Kingdom
    "london": "United Kingdom", "manchester": "United Kingdom",
    "birmingham": "United Kingdom", "edinburgh": "United Kingdom",
    "glasgow": "United Kingdom", "bristol": "United Kingdom",
    "leeds": "United Kingdom", "liverpool": "United Kingdom",
    "cambridge": "United Kingdom", "oxford": "United Kingdom",
    "sheffield": "United Kingdom", "newcastle": "United Kingdom",
    "nottingham": "United Kingdom", "cardiff": "United Kingdom",
    "belfast": "United Kingdom", "coventry": "United Kingdom",
    "brighton": "United Kingdom", "reading": "United Kingdom",
    "southampton": "United Kingdom", "portsmouth": "United Kingdom",
    "aberdeen": "United Kingdom", "swansea": "United Kingdom",
    "york": "United Kingdom", "norwich": "United Kingdom",
    "luton": "United Kingdom", "derby": "United Kingdom",

    # Sweden
    "stockholm": "Sweden", "gothenburg": "Sweden", "göteborg": "Sweden",
    "goteborg": "Sweden", "malmö": "Sweden", "malmo": "Sweden",
    "uppsala": "Sweden", "linköping": "Sweden", "linkoping": "Sweden",
    "örebro": "Sweden", "orebro": "Sweden", "lund": "Sweden",

    # Denmark
    "copenhagen": "Denmark", "københavn": "Denmark", "kobenhavn": "Denmark",
    "aarhus": "Denmark", "odense": "Denmark", "aalborg": "Denmark",

    # Finland
    "helsinki": "Finland", "espoo": "Finland", "tampere": "Finland",
    "oulu": "Finland", "turku": "Finland", "vantaa": "Finland",

    # Norway
    "oslo": "Norway", "bergen": "Norway", "trondheim": "Norway",
    "stavanger": "Norway", "kristiansand": "Norway",

    # France
    "paris": "France", "lyon": "France", "marseille": "France",
    "toulouse": "France", "bordeaux": "France", "nantes": "France",
    "lille": "France", "strasbourg": "France", "nice": "France",
    "rennes": "France", "grenoble": "France", "montpellier": "France",
    "dijon": "France",

    # Spain
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain",
    "seville": "Spain", "sevilla": "Spain", "bilbao": "Spain",
    "málaga": "Spain", "malaga": "Spain", "zaragoza": "Spain",
    "palma": "Spain", "las palmas": "Spain",
    "alicante": "Spain", "córdoba": "Spain", "cordoba": "Spain",
    "valladolid": "Spain", "vigo": "Spain",

    # Portugal
    "lisbon": "Portugal", "lisboa": "Portugal", "porto": "Portugal",
    "braga": "Portugal", "coimbra": "Portugal", "faro": "Portugal",
    "funchal": "Portugal",

    # Ireland
    "dublin": "Ireland", "cork": "Ireland", "galway": "Ireland",
    "limerick": "Ireland", "waterford": "Ireland",

    # Poland
    "warsaw": "Poland", "wrocław": "Poland", "wroclaw": "Poland",
    "kraków": "Poland", "krakow": "Poland", "gdańsk": "Poland",
    "gdansk": "Poland", "poznan": "Poland", "poznań": "Poland",
    "łódź": "Poland", "lodz": "Poland", "katowice": "Poland",
    "szczecin": "Poland", "lublin": "Poland",

    # Czech Republic — synced with the career scanner's gazetteer so the
    # ingest-time fallback can attribute the same cities the scanners emit.
    "prague": "Czech Republic", "praha": "Czech Republic",
    "brno": "Czech Republic",
    "ostrava": "Czech Republic", "plzeň": "Czech Republic",
    "plzen": "Czech Republic", "liberec": "Czech Republic",
    "olomouc": "Czech Republic", "přerov": "Czech Republic",
    "prostějov": "Czech Republic", "šumperk": "Czech Republic",
    "jeseník": "Czech Republic", "krnov": "Czech Republic",
    "bruntál": "Czech Republic", "opava": "Czech Republic",
    "havířov": "Czech Republic", "karviná": "Czech Republic",
    "český těšín": "Czech Republic", "třinec": "Czech Republic",
    "trinec": "Czech Republic", "frýdek-místek": "Czech Republic",
    "nový jičín": "Czech Republic", "valašské meziříčí": "Czech Republic",
    "vsetín": "Czech Republic", "zlín": "Czech Republic",
    "kroměříž": "Czech Republic", "uherské hradiště": "Czech Republic",
    "břeclav": "Czech Republic", "hodonín": "Czech Republic",
    "mikulov": "Czech Republic", "znojmo": "Czech Republic",
    "jihlava": "Czech Republic", "havlíčkův brod": "Czech Republic",
    "chotěboř": "Czech Republic", "žďár nad sázavou": "Czech Republic",
    "velké meziříčí": "Czech Republic", "třebíč": "Czech Republic",
    "telč": "Czech Republic", "slavonice": "Czech Republic",
    "jindřichův hradec": "Czech Republic", "tábor": "Czech Republic",
    "písek": "Czech Republic", "strakonice": "Czech Republic",
    "prachatice": "Czech Republic", "vimperk": "Czech Republic",
    "český krumlov": "Czech Republic", "kaplice": "Czech Republic",
    "vyšší brod": "Czech Republic", "pardubice": "Czech Republic",

    # Romania
    "bucharest": "Romania", "cluj": "Romania", "cluj-napoca": "Romania",
    "timisoara": "Romania", "timișoara": "Romania", "iasi": "Romania",
    "brașov": "Romania", "brasov": "Romania",

    # Hungary
    "budapest": "Hungary", "debrecen": "Hungary", "pécs": "Hungary",

    # Austria
    "vienna": "Austria", "wien": "Austria", "graz": "Austria",
    "linz": "Austria", "salzburg": "Austria", "innsbruck": "Austria",

    # Switzerland
    "zurich": "Switzerland", "zürich": "Switzerland",
    "geneva": "Switzerland", "genève": "Switzerland", "geneve": "Switzerland",
    "bern": "Switzerland", "basel": "Switzerland", "lausanne": "Switzerland",
    "lucerne": "Switzerland", "luzern": "Switzerland",
    "winterthur": "Switzerland", "st. gallen": "Switzerland",
    "st gallen": "Switzerland", "lugano": "Switzerland",

    # Belgium
    "brussels": "Belgium", "bruxelles": "Belgium", "brussel": "Belgium",
    "ghent": "Belgium", "gent": "Belgium", "antwerp": "Belgium",
    "antwerpen": "Belgium", "liège": "Belgium", "liege": "Belgium",
    "leuven": "Belgium",

    # Italy
    "rome": "Italy", "roma": "Italy", "milan": "Italy", "milano": "Italy",
    "turin": "Italy", "torino": "Italy", "florence": "Italy",
    "firenze": "Italy", "naples": "Italy", "napoli": "Italy",
    "bologna": "Italy", "venice": "Italy", "venezia": "Italy",
    "genoa": "Italy", "genova": "Italy", "palermo": "Italy",
    "parma": "Italy", "verona": "Italy", "padua": "Italy",
    "padova": "Italy", "bergamo": "Italy", "brescia": "Italy",
    "modena": "Italy", "como": "Italy", "bari": "Italy",
    "catania": "Italy", "cagliari": "Italy",

    # Greece
    "athens": "Greece", "athen": "Greece", "thessaloniki": "Greece",

    # Baltic states
    "tallinn": "Estonia", "riga": "Latvia", "vilnius": "Lithuania",

    # Balkans
    "zagreb": "Croatia", "ljubljana": "Slovenia", "sarajevo": "Bosnia and Herzegovina",
    "belgrade": "Serbia", "beograd": "Serbia", "sofia": "Bulgaria",
    "skopje": "North Macedonia", "tirana": "Albania",

    # Nordics / small EU
    "luxembourg city": "Luxembourg", "valletta": "Malta",
    "nicosia": "Cyprus", "reykjavik": "Iceland",

    # Eastern Europe
    "kiev": "Ukraine", "kyiv": "Ukraine", "kharkiv": "Ukraine",
    "moscow": "Russia", "moskva": "Russia", "st. petersburg": "Russia",
    "saint petersburg": "Russia",
    "minsk": "Belarus", "chisinau": "Moldova",

    # Middle East
    "tel aviv": "Israel", "tel-aviv": "Israel", "tel aviv-yafo": "Israel",
    "jerusalem": "Israel", "haifa": "Israel", "herzliya": "Israel",
    "ramat gan": "Israel", "petah tikva": "Israel",
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "sharjah": "United Arab Emirates",
    "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia",
    "doha": "Qatar", "kuwait city": "Kuwait", "manama": "Bahrain",
    "amman": "Jordan", "beirut": "Lebanon", "cairo": "Egypt",
    "istanbul": "Turkey", "ankara": "Turkey",
    "izmir": "Turkey", "bursa": "Turkey", "antalya": "Turkey",
    "dammam": "Saudi Arabia", "alexandria": "Egypt",
    "marrakech": "Morocco", "rabat": "Morocco",

    # India
    "bangalore": "India", "bengaluru": "India", "mumbai": "India",
    "delhi": "India", "new delhi": "India", "hyderabad": "India",
    "pune": "India", "chennai": "India", "kolkata": "India",
    "gurgaon": "India", "gurugram": "India", "noida": "India",
    "ahmedabad": "India", "jaipur": "India",
    "kochi": "India", "indore": "India", "surat": "India",
    "nagpur": "India", "lucknow": "India", "coimbatore": "India",

    # Asia Pacific
    "singapore": "Singapore",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan",
    "yokohama": "Japan", "nagoya": "Japan",
    "kobe": "Japan", "fukuoka": "Japan", "sapporo": "Japan",
    "sendai": "Japan", "hiroshima": "Japan", "kawasaki": "Japan",
    "saitama": "Japan", "chiba": "Japan", "kitakyushu": "Japan",
    "seoul": "South Korea", "busan": "South Korea",
    "incheon": "South Korea", "daegu": "South Korea",
    "daejeon": "South Korea", "gwangju": "South Korea",
    "suwon": "South Korea",
    "beijing": "China", "shanghai": "China", "shenzhen": "China",
    "guangzhou": "China", "chengdu": "China", "hangzhou": "China",
    "tianjin": "China", "suzhou": "China", "wuxi": "China",
    "nanjing": "China", "ningbo": "China", "foshan": "China",
    "dongguan": "China", "wuhan": "China", "chongqing": "China",
    "xi'an": "China", "xian": "China", "qingdao": "China",
    "dalian": "China", "shenyang": "China", "jinan": "China",
    "changsha": "China", "zhengzhou": "China", "kunming": "China",
    "xiamen": "China", "fuzhou": "China", "hefei": "China",
    "changchun": "China", "harbin": "China", "nanchang": "China",
    "guiyang": "China", "nanning": "China", "wenzhou": "China",
    "changzhou": "China", "nantong": "China", "yangzhou": "China",
    "yixing": "China", "kunshan": "China", "jiaxing": "China",
    "wuxi": "China", "huizhou": "China", "zhongshan": "China",
    "lanzhou": "China", "urumqi": "China",
    "hong kong": "Hong Kong",
    "taipei": "Taiwan",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia",
    "perth": "Australia", "adelaide": "Australia", "canberra": "Australia",
    "gold coast": "Australia", "newcastle": "Australia",
    "wollongong": "Australia", "geelong": "Australia",
    "auckland": "New Zealand", "wellington": "New Zealand",
    "christchurch": "New Zealand", "hamilton": "New Zealand",
    "kuala lumpur": "Malaysia", "kl": "Malaysia",
    "penang": "Malaysia", "johor bahru": "Malaysia",
    "petaling jaya": "Malaysia", "shah alam": "Malaysia",
    "george town": "Malaysia", "cyberjaya": "Malaysia",
    "jakarta": "Indonesia", "surabaya": "Indonesia",
    "bandung": "Indonesia", "medan": "Indonesia",
    "semarang": "Indonesia", "denpasar": "Indonesia",
    "bangkok": "Thailand", "phuket": "Thailand",
    "chiang mai": "Thailand",
    "ho chi minh": "Vietnam", "hanoi": "Vietnam",
    "da nang": "Vietnam", "danang": "Vietnam",
    "manila": "Philippines", "cebu": "Philippines",
    "davao": "Philippines",

    # Africa
    "johannesburg": "South Africa", "cape town": "South Africa",
    "durban": "South Africa", "pretoria": "South Africa",
    "nairobi": "Kenya", "lagos": "Nigeria", "accra": "Ghana",
    "abuja": "Nigeria",
    "casablanca": "Morocco", "tunis": "Tunisia",

    # Canada
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada",
    "montréal": "Canada", "calgary": "Canada", "ottawa": "Canada",
    "edmonton": "Canada", "winnipeg": "Canada", "quebec city": "Canada",
    "mississauga": "Canada", "surrey": "Canada", "laval": "Canada",
    "halifax": "Canada",

    # United States — additional cities for state-abbrev conflict resolution
    "dover": "United States", "wilmington": "United States",
    "indianapolis": "United States", "fort wayne": "United States",
    "savannah": "United States", "augusta": "United States",
    "colorado springs": "United States", "boulder": "United States",
    "aurora": "United States", "fort collins": "United States",
    "montgomery": "United States", "huntsville": "United States",
    "little rock": "United States", "fayetteville": "United States",
    "billings": "United States", "missoula": "United States",
    "springfield": "United States", "rockford": "United States",
    "peoria": "United States", "anchorage": "United States",
    "honolulu": "United States", "boise": "United States",
    "des moines": "United States", "sioux falls": "United States",
    "bismarck": "United States", "cheyenne": "United States",
    "helena": "United States", "concord": "United States",
    "providence": "United States", "charleston": "United States",
    "jackson": "United States", "columbia": "United States",
    "richmond": "United States", "spokane": "United States",
    "san francisco": "United States", "sf": "United States",
    "los angeles": "United States", "la": "United States",
    "seattle": "United States", "chicago": "United States",
    "boston": "United States", "austin": "United States",
    "denver": "United States", "atlanta": "United States",
    "miami": "United States", "dallas": "United States",
    "houston": "United States", "washington": "United States",
    "washington dc": "United States", "washington d.c.": "United States",
    "portland": "United States", "san jose": "United States",
    "san diego": "United States", "phoenix": "United States",
    "minneapolis": "United States", "pittsburgh": "United States",
    "raleigh": "United States", "salt lake city": "United States",
    "nashville": "United States", "charlotte": "United States",
    "detroit": "United States", "philadelphia": "United States",
    "las vegas": "United States", "baltimore": "United States",
    "orlando": "United States", "tampa": "United States",
    "san antonio": "United States", "columbus": "United States",
    "jacksonville": "United States",
    "memphis": "United States", "louisville": "United States",
    "new orleans": "United States",
    "st. louis": "United States", "saint louis": "United States",
    "kansas city": "United States", "oklahoma city": "United States",
    "albuquerque": "United States", "tucson": "United States",
    "omaha": "United States", "sacramento": "United States",
    "plano": "United States", "tacoma": "United States",
    "irvine": "United States", "palo alto": "United States",
    "mountain view": "United States", "redmond": "United States",
    "reston": "United States", "arlington": "United States",
    "fort worth": "United States", "el paso": "United States",
    "wichita": "United States", "tulsa": "United States",
    "fresno": "United States", "mesa": "United States",

    # Latin America
    "são paulo": "Brazil", "sao paulo": "Brazil",
    "rio de janeiro": "Brazil", "brasília": "Brazil", "brasilia": "Brazil",
    "belo horizonte": "Brazil", "curitiba": "Brazil",
    "recife": "Brazil", "porto alegre": "Brazil",
    "mexico city": "Mexico", "guadalajara": "Mexico", "monterrey": "Mexico",
    "buenos aires": "Argentina", "córdoba": "Argentina", "rosario": "Argentina",
    "bogotá": "Colombia", "bogota": "Colombia", "medellín": "Colombia",
    "medellin": "Colombia", "cali": "Colombia",
    "santiago": "Chile", "lima": "Peru", "quito": "Ecuador",
    "caracas": "Venezuela", "montevideo": "Uruguay",
    "salvador": "Brazil", "fortaleza": "Brazil",
    "puebla": "Mexico", "barranquilla": "Colombia",
}

# ── FIX LOCEXP-9 (2026-10-09): thin-country EU city expansion ────────────────
# The gazetteer above held 636 cities but the user's target countries were
# nearly absent (Luxembourg 1, Estonia/Lithuania/Latvia 1 each, Ireland 5,
# Norway/Denmark/Finland/Austria 5-6, Portugal 7, Belgium 10). A posting in
# "Drogheda", "Esch-sur-Alzette" or "Tartu" could not resolve, so its country
# fell back to the company HQ (or empty) and local search missed it.
# Kept as a separate, clearly-marked table (same lowercase-key contract) so
# future curation does not have to touch the original literal.
_CITY_ADDITIONS_2026_10: dict[str, str] = {
    # Ireland (+12)
    "athlone": "Ireland", "drogheda": "Ireland", "dundalk": "Ireland",
    "swords": "Ireland", "bray": "Ireland", "navan": "Ireland",
    "kilkenny": "Ireland", "carlow": "Ireland", "sligo": "Ireland",
    "wexford": "Ireland", "letterkenny": "Ireland", "killarney": "Ireland",
    # Belgium (+10)
    "bruges": "Belgium", "brugge": "Belgium", "namur": "Belgium",
    "mons": "Belgium", "hasselt": "Belgium", "charleroi": "Belgium",
    "ostend": "Belgium", "oostende": "Belgium", "kortrijk": "Belgium",
    "mechelen": "Belgium",
    # Luxembourg (+4)
    "esch-sur-alzette": "Luxembourg", "esch": "Luxembourg",
    "dudelange": "Luxembourg", "differdange": "Luxembourg",
    # Austria (+8)
    "klagenfurt": "Austria", "villach": "Austria", "wels": "Austria",
    "st. pölten": "Austria", "sankt pölten": "Austria",
    "dornbirn": "Austria", "wiener neustadt": "Austria", "bregenz": "Austria",
    # Denmark (+9)
    "esbjerg": "Denmark", "randers": "Denmark", "horsens": "Denmark",
    "vejle": "Denmark", "roskilde": "Denmark", "herning": "Denmark",
    "silkeborg": "Denmark", "helsingør": "Denmark",
    "frederiksberg": "Denmark",
    # Finland (+11)
    "lahti": "Finland", "kuopio": "Finland", "jyväskylä": "Finland",
    "pori": "Finland", "kouvola": "Finland", "joensuu": "Finland",
    "lappeenranta": "Finland", "vaasa": "Finland", "seinäjoki": "Finland",
    "rovaniemi": "Finland", "hämeenlinna": "Finland",
    # Estonia (+4)
    "tartu": "Estonia", "narva": "Estonia", "pärnu": "Estonia",
    "viljandi": "Estonia",
    # Portugal (+11)
    "setúbal": "Portugal", "aveiro": "Portugal", "leiria": "Portugal",
    "évora": "Portugal", "viseu": "Portugal", "guimarães": "Portugal",
    "portimão": "Portugal", "cascais": "Portugal", "sintra": "Portugal",
    "oeiras": "Portugal", "matosinhos": "Portugal",
    # Lithuania (+4)
    "kaunas": "Lithuania", "klaipėda": "Lithuania",
    "šiauliai": "Lithuania", "panevėžys": "Lithuania",
    # Latvia (+4)
    "daugavpils": "Latvia", "liepāja": "Latvia", "jelgava": "Latvia",
    "jūrmala": "Latvia",
    # Norway (+7)
    "tromsø": "Norway", "fredrikstad": "Norway", "drammen": "Norway",
    "sandnes": "Norway", "asker": "Norway", "haugesund": "Norway",
    "moss": "Norway",
    # Sweden (+10)
    "norrköping": "Sweden", "västerås": "Sweden", "helsingborg": "Sweden",
    "jönköping": "Sweden", "umeå": "Sweden", "sundsvall": "Sweden",
    "gävle": "Sweden", "borås": "Sweden", "halmstad": "Sweden",
    "växjö": "Sweden",
    # Spain (+11)
    "granada": "Spain", "santander": "Spain", "pamplona": "Spain",
    "salamanca": "Spain", "burgos": "Spain", "gijón": "Spain",
    "a coruña": "Spain", "san sebastián": "Spain", "murcia": "Spain",
    "tarragona": "Spain", "oviedo": "Spain",
    # Greece (+4)
    "patras": "Greece", "piraeus": "Greece", "heraklion": "Greece",
    "larissa": "Greece",
    # Netherlands (+8)
    "leiden": "Netherlands", "hilversum": "Netherlands",
    "amersfoort": "Netherlands", "zaandam": "Netherlands",
    "den bosch": "Netherlands", "s-hertogenbosch": "Netherlands",
    "alkmaar": "Netherlands", "dordrecht": "Netherlands",
    # Poland (+8)
    "gdynia": "Poland", "białystok": "Poland", "rzeszów": "Poland",
    "gliwice": "Poland", "toruń": "Poland", "kielce": "Poland",
    "olsztyn": "Poland", "bielsko-biała": "Poland",
    # France (+5)
    "nancy": "France", "rouen": "France", "clermont-ferrand": "France",
    "aix-en-provence": "France", "saint-étienne": "France",
    # Italy (+5)
    "trieste": "Italy", "trento": "Italy", "perugia": "Italy",
    "monza": "Italy", "reggio emilia": "Italy",
}
CITY_TO_COUNTRY.update(_CITY_ADDITIONS_2026_10)


#: Extra foldings NFKD cannot derive (no Unicode decomposition): ł/ø/å/æ/œ/ß/ð/þ/ı/ș/ț.
_ASCII_FOLD_EXTRA = str.maketrans({
    "ł": "l", "ø": "o", "å": "a", "æ": "ae", "œ": "oe", "ß": "ss",
    "đ": "d", "ð": "d", "þ": "th", "ı": "i", "ș": "s", "ț": "t",
})


def _ascii_fold(text: str) -> str:
    """Return `text` with diacritics folded to ASCII (NFC-normalised first)."""
    t = unicodedata.normalize("NFC", text).translate(_ASCII_FOLD_EXTRA)
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


#: Folded aliases that collided with an existing key of another country and
#: were therefore NOT added (existing key always wins). Introspectable for
#: debugging; expected to stay empty.
ALIAS_COLLISIONS: list[tuple[str, str, str]] = []


def _expand_ascii_aliases() -> int:
    """Add unaccented aliases for every diacritic city key (runs at import).

    FIX LOCEXP-10 (2026-10-09): lookups are exact-match on lowercase keys, so
    "München" resolved but the equally common ASCII spelling "Munchen" did
    not (same for Köln/Koln and dozens of others — the table carried both
    spellings only for a hand-picked few). The NFC pass also heals keys
    stored in a non-composed normalisation form. Purely additive: no existing
    key is modified or removed, so no previously-resolving string can change
    its answer; on a cross-country collision the pre-existing key wins and
    the collision is recorded in ALIAS_COLLISIONS instead of guessing.
    """
    added = 0
    for key in sorted(CITY_TO_COUNTRY):
        country = CITY_TO_COUNTRY[key]
        nfc = unicodedata.normalize("NFC", key)
        if nfc != key and nfc not in CITY_TO_COUNTRY:
            CITY_TO_COUNTRY[nfc] = country
            added += 1
        folded = _ascii_fold(nfc)
        if not folded or folded == nfc or folded in CITY_TO_COUNTRY:
            if (folded and folded != nfc and folded in CITY_TO_COUNTRY
                    and CITY_TO_COUNTRY[folded] != country):
                ALIAS_COLLISIONS.append((folded, country, CITY_TO_COUNTRY[folded]))
            continue
        CITY_TO_COUNTRY[folded] = country
        added += 1
    return added


_ALIAS_COUNT = _expand_ascii_aliases()

# Phrases that mean truly remote — no single country
_GLOBAL_REMOTE = re.compile(
    r"^(remote|worldwide|global|anywhere|distributed|"
    r"work from anywhere|fully remote|100%\s*remote|"
    r"remote\s*[-–]\s*(global|worldwide|anywhere|eu|europe|emea|apac|latam|"
    r"us|usa|uk|north america|south america|us\s*(and|&|/)\s*canada|"
    r"europe\s*(and|&|/)?\s*middle east|international)|"
    r"multiple locations?|various locations?|"
    r"flexible\s*/\s*remote|global\s*/\s*remote|"
    r"not specified|tbd|n/a)$",
    re.IGNORECASE,
)

# 2-letter codes that appear as BOTH US state abbrev AND ISO-2 country code.
# When following a city name, treat as US state (city, CA = California not Canada).
_AMBIGUOUS_US_STATES: set[str] = {"al", "ar", "ca", "co", "de", "id", "il", "in", "mt"}


def _strip_postal(seg: str) -> str:
    """Remove a trailing postal code from one location segment.

    Handles US ZIPs ("WA 98101", "94105-1234"), UK postcodes
    ("London SW1A 1AA") and continental 4-5 digit codes, but only when
    other text remains (a bare "9362" segment is skipped by the caller).
    F5 fix: previously "Tacoma, WA 98101" derived no country at all.
    """
    new = re.sub(r"\s+\d{4,5}(?:-\d{4})?\s*$", "", seg).strip()
    new = re.sub(r"\s+[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}\s*$", "", new, flags=re.I).strip()
    return new if new else seg


def country_from_location(location: str, fallback: str = "") -> str:
    """
    Derive the job's actual country from its raw ATS location string.

    Priority order:
    1. Pure remote / worldwide → ""  (no country)
    2. Whole string is a known country name (incl. new aliases)
    3. Parenthetical tails stripped: "London (Hybrid)" -> "London" (F3)
    4. Leading site code stripped: "DE - Darmstadt - Europahaus" (F12)
    5. Comma- and dash-segments right-to-left, postal codes removed (F5):
       country name > ISO-2 > ambiguous code w/ city guard (B8) >
       US state (full, or abbr. with non-US-city guard, F1) > CA province
    6. City lookup on each segment; prefix-stripped remainders are also
       checked against country names/codes, not just cities (F4)
    7. City/name lookup on the stripped whole string
    8. fallback (company HQ country)
    """
    if not location:
        return fallback

    raw = location.strip()
    raw_lower = raw.lower()

    # ── 1. Global-remote patterns → no country ───────────────────────────────
    if _GLOBAL_REMOTE.match(raw_lower):
        return ""

    # ── 2. Whole string is a country name ────────────────────────────────────
    if raw_lower in COUNTRY_NAMES:
        return COUNTRY_NAMES[raw_lower]
    # Handle parenthetical remote: "Remote (Netherlands)"
    m = re.match(r"^remote\s*[\(\[](.*?)[\)\]]$", raw_lower)
    if m:
        inner = m.group(1).strip()
        if inner in COUNTRY_NAMES:
            return COUNTRY_NAMES[inner]
        if inner in CITY_TO_COUNTRY:
            return CITY_TO_COUNTRY[inner]

    # ── 3. Strip parenthetical tails (F3) ─────────────────────────────────────
    # "London (Hybrid)", "Amsterdam (Remote)", "Austin (Ed Bluestein,
    # Manufacturing" (unbalanced). The Remote(X) form above is handled
    # first so it keeps working; work modes carry no country signal.
    no_paren = re.sub(r"\s*[\(\[].*", "", raw_lower).strip()
    work = no_paren if no_paren else raw_lower

    # ── 4. Strip a leading ISO-2 site code (F12) ──────────────────────────────
    # "DE - Darmstadt - Europahaus" -> "Darmstadt - Europahaus".
    # Only ISO-2 codes trigger this ("La Paz - Bolivia" is safe because
    # "la" is deliberately not an ISO2 key in this module).
    mm = re.match(r"^([a-z]{2})\s*[-–]\s+(.+)$", work)
    if mm and mm.group(1) in ISO2_TO_COUNTRY:
        work = mm.group(2).strip()

    # ── 5. Split on commas, then on spaced dashes/slashes ────────────────────
    # "Remote - Germany" -> ["remote", "germany"] (F4),
    # "DE - Darmstadt - Europahaus" -> ["darmstadt", "europahaus"] (F12).
    # Dashes WITHOUT surrounding spaces ("Tel-Aviv", "Baden-Württemberg",
    # "Frankfurt am Main") are never split.
    parts: list[str] = []
    for chunk in work.split(","):
        for sub in re.split(r"\s+(?:[-–]|/)\s+", chunk):
            s = _strip_postal(sub.strip())
            if s:
                parts.append(s)

    # Try segments from right to left (rightmost is most likely country)
    for i, part in enumerate(reversed(parts)):
        part_lower = part.lower().strip()
        if not part_lower or part_lower.isdigit():
            continue
        part_idx = len(parts) - 1 - i  # original index

        # Full country name
        if part_lower in COUNTRY_NAMES:
            return COUNTRY_NAMES[part_lower]

        # ISO-2 country code — but only if it's NOT an ambiguous US-state abbrev
        # (e.g. CA after a city = California, not Canada)
        if part_lower in ISO2_TO_COUNTRY and part_lower not in _AMBIGUOUS_US_STATES:
            return ISO2_TO_COUNTRY[part_lower]

        # Ambiguous 2-letter code: check if the preceding segment is a US city
        if part_lower in _AMBIGUOUS_US_STATES and part_idx > 0:
            prev_city = parts[part_idx - 1].lower().strip()
            # B8 fix: trust a known city. If the preceding segment is not
            # a known city, fall through to the ISO2 lookup below.
            if prev_city in CITY_TO_COUNTRY:
                return CITY_TO_COUNTRY[prev_city]
            # Otherwise treat as ISO2 country
            if part_lower in ISO2_TO_COUNTRY:
                return ISO2_TO_COUNTRY[part_lower]
            return "United States"

        # US state full name
        if part_lower in US_STATES_FULL:
            return "United States"

        # F1 fix: a bare state abbreviation must not overrule a known
        # non-US city ("Milan, MI" is Italy, not Michigan). Mirror of B8.
        if len(part_lower) == 2 and part_lower in US_STATES_ABBR and part_lower not in ISO2_TO_COUNTRY:
            if part_idx > 0:
                prev = parts[part_idx - 1].lower().strip()
                if prev in CITY_TO_COUNTRY:
                    return CITY_TO_COUNTRY[prev]
            return "United States"

        # Canadian province full name
        if part_lower in CA_PROVINCES_FULL:
            return "Canada"

    # ── 6. City lookup on each segment (F4: remainder also checked ────────────
    # against country names/codes, so "Remote - Netherlands" resolves) ───────
    for part in parts:
        part_lower = part.lower().strip()
        # Strip "Hybrid -" prefix etc.
        cleaned = re.sub(r"^(hybrid|remote|onsite|flexible)\s*[-–/]\s*", "", part_lower).strip()
        for cand in (cleaned, part_lower):
            if cand in CITY_TO_COUNTRY:
                return CITY_TO_COUNTRY[cand]
            if cand in COUNTRY_NAMES:
                return COUNTRY_NAMES[cand]
            if cand in ISO2_TO_COUNTRY:
                return ISO2_TO_COUNTRY[cand]

    # ── 7. Lookup on the whole stripped string ────────────────────────────────
    stripped = re.sub(r"^(hybrid|remote|onsite|flexible)\s*[-–/]\s*", "", work).strip()
    if stripped in CITY_TO_COUNTRY:
        return CITY_TO_COUNTRY[stripped]
    if stripped in COUNTRY_NAMES:
        return COUNTRY_NAMES[stripped]

    # ── 8. Nothing matched → use HQ country as fallback ──────────────────────
    return fallback
