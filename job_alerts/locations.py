"""Conservative country/state handling; hybrid is not remote."""
import re
import settings

STATES = dict(zip(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(),
    ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming").split("|"),
))
FOREIGN = re.compile(
    r"\b(canada|toronto|vancouver|united kingdom|uk|england|london|europe|emea|apac|"
    r"india|bangalore|hyderabad|australia|germany|berlin|france|paris|ireland|poland|"
    r"mexico|brazil|latam|japan|singapore|philippines|netherlands|spain|israel|"
    r"switzerland|zurich|sweden|norway|denmark|finland|austria|belgium|portugal|"
    r"romania|italy|china|hong kong|taiwan|korea|indonesia|malaysia|pakistan|"
    r"bangladesh|sri lanka|south africa|nigeria|kenya|egypt|turkey|türkiye|"
    r"argentina|chile|colombia|peru|new zealand|dubai|uae|saudi arabia|tbilisi)\b", re.I)
US = re.compile(r"\b(united states(?: of america)?|usa|u\.s\.?a?\.?)\b", re.I)
NY_CITIES = re.compile(r"\b(nyc|new york city|manhattan|brooklyn|queens|bronx|staten island|long island)\b", re.I)
NJ_CITIES = re.compile(r"\b(jersey city|hoboken)\b", re.I)
EAST_CITIES = {"boston": "MA", "philadelphia": "PA", "pittsburgh": "PA", "baltimore": "MD", "raleigh": "NC", "charlotte": "NC", "miami": "FL", "atlanta": "GA"}


def state_codes(text):
    """Resolve explicit names/codes before using a short, unambiguous city list."""
    if FOREIGN.search(text):
        return set()
    codes = set()
    for code, name in STATES.items():
        if re.search(r"(?<![A-Za-z])" + code + r"(?![A-Za-z])", text):
            codes.add(code)
        elif re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
            # These names also identify countries or provinces without US evidence.
            if name in ("Georgia", "Washington") and not US.search(text) and "," not in text:
                continue
            codes.add(code)
    if NY_CITIES.search(text):
        codes.add("NY")
    if NJ_CITIES.search(text):
        codes.add("NJ")
    if not codes:
        for city, code in EAST_CITIES.items():
            if re.search(r"\b" + city + r"\b", text, re.I):
                codes.add(code)
    return codes


def matching_locations(locations, ny_nj_only=None):
    strict = settings.NY_NJ_ONLY if ny_nj_only is None else ny_nj_only
    allowed = {"NY", "NJ"} if strict else set(settings.EAST_COAST_STATES)
    accepted = []
    for original in locations or []:
        # Do not display unrelated alternatives as if they were matches.
        for part in re.split(r"\s*[;|]\s*", str(original)):
            if FOREIGN.search(part):
                continue
            codes = state_codes(part)
            if codes & allowed:
                accepted.append(part)
                continue
            if strict:
                continue
            remote = re.search(r"\b(remote|work from home|wfh|telecommute|worldwide|anywhere)\b", part, re.I)
            hybrid = re.search(r"\bhybrid\b", part, re.I)
            if settings.INCLUDE_REMOTE and remote and not hybrid and not codes and (
                US.search(part) or re.search(r"\b(worldwide|anywhere|north america|americas)\b", part, re.I)
            ):
                accepted.append(part)
    return list(dict.fromkeys(accepted))


def location_matches(locations, ny_nj_only=None):
    return bool(matching_locations(locations, ny_nj_only))


def search_states():
    return [STATES[c] for c in (("NY", "NJ") if settings.NY_NJ_ONLY else settings.EAST_COAST_STATES)]
