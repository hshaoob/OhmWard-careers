#!/usr/bin/env python3
"""Hourly EE job board. Polls company career sites / ATS APIs directly, publishes fresh US internships, co-ops and
new grad roles to docs/jobs.json (the website), and emails each subscriber the new roles that match their filters."""
import collections, datetime, html, json, os, re, smtplib, statistics, sys, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from pathlib import Path

HERE = Path(__file__).parent
SEEN, BOARDS, COMPANIES, UNSCANNABLE = HERE / "seen.json", HERE / "boards.json", HERE / "companies.txt", HERE / "unscannable.txt"
NAMES = HERE / "names.json"  # board -> company display name
JOBS = HERE / "docs" / "jobs.json"  # what the website shows: every live role, posted within MAX_AGE days
BSTATE = HERE / "board_state.json"  # "kind|id" -> [last scanned, had EE roles?]
BATCH = 500  # boards per run (every 15 min)
MISSING = HERE / "missing_companies.txt"  # favorites subscribers asked for that no scanned board matches
POSTED = {}  # url -> when Simplify listed it (for roles we only see via Simplify)
SUPABASE, SERVICE_KEY = os.environ.get("SUPABASE_URL", "").rstrip("/"), os.environ.get("SUPABASE_SERVICE_KEY")
SMTP_HOST, SMTP_USER, SMTP_PASS = os.environ.get("SMTP_HOST", "smtp.resend.com"), os.environ.get("SMTP_USER", "resend"), os.environ.get("SMTP_PASS")
FROM = os.environ.get("MAIL_FROM")  # e.g. "Ohmward Careers <alerts@yourdomain.com>"
_owner, _, _repo = os.environ.get("GITHUB_REPOSITORY", "").partition("/")
SITE = os.environ.get("SITE_URL") or (f"https://{_owner.lower()}.github.io/{_repo}/" if _repo else "http://localhost:8000/")
PACIFIC = __import__("zoneinfo").ZoneInfo("America/Los_Angeles")
DISCOVERY = [  # company directory only: harvests which boards to poll (+ roles on sites we can't poll)
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
]
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140 Safari/537.36"

# ---------- filters ----------
INTERN = re.compile(r"\bintern(ship)?s?\b", re.I)
COOP = re.compile(r"\bco-?op\b", re.I)
NEWGRAD = re.compile(r"new (?:college )?grad\w*|(?:university|recent|college) grad\w*|entry[- ]level|early career|"
    r"graduate (?:engineer(?:ing)? )?(?:program|rotation)\w*|rotational|\bengineer (?:i|1)\b|associate (?:electrical|hardware) engineer", re.I)
SENIOR = re.compile(r"senior|\bsr\b|staff|principal|\blead\b|manager|director|\bii\b|\biii\b|\biv\b|\bengineer [2-5]\b", re.I)
EXPERIENCE = re.compile(r"(?<![-–\d])\b(?:[3-9]|1\d)\+?(?:\s*[-–]\s*\d+)?\s*(?:or more\s+)?years", re.I)  # new grad roles can't ask for this
# Five EE areas the site explains; a role can be in several, and a role in none ("Electrical Engineering Intern") goes to everyone.
SECTORS = {
    "power": r"\bpower(?! ?bi\b| ?apps| ?platform)|grid|substation|utilit|transmission|\bt&d\b|\bp&c\b|protection|relay|"
             r"renewable|solar|\bpv\b|photovoltaic|wind|batter|\bbms\b|\bev\b|electric vehicle|inverter|high[- ]voltage|energy|nuclear|\bmep\b",
    "comms": r"communications? (?:system|engineer|hardware|protocol|theory)|\bcomms\b|\brf\b|wireless|antenna|microwave|radar|"
             r"telecom|\b5g\b|\blte\b|optical|photonic|satcom|modem|bluetooth|wi-?fi",
    "controls": r"\bcontrols?\b|automation|\bplc|scada|robotic|mechatronic|\bgnc\b|guidance|navigation|\bhil\b|instrumentation|"
                r"\bi&c\b|motion control|feedback control",
    "signal": r"\bdsp\b|signal processing|image processing|audio|acoustic|speech|sensor fusion|radar|sonar|lidar|\bfft\b|imaging|"
              r"camera|video|(?:computer|machine) vision|ultrasound|\bmri\b|beamform|kalman|estimation|baseband|codec|"
              r"software[- ]defined radio|\bsdr\b|electronic warfare|\bsigint\b|filter design|spectr",
    "computer": r"fpga|asic|\brtl\b|verilog|vhdl|\bsoc\b|digital design|design verification|physical design|silicon|\bcpu\b|"
                r"\bgpu\b|embedded|firmware|microcontroller|computer architecture|semiconductor",
}
SECTORS = {k: re.compile(v, re.I) for k, v in SECTORS.items()}
CITIZEN = re.compile(r"u\.?s\.? citizen|united states citizen|security clearance|(?:secret|ts/sci) clearance|\bitar\b|"
                     r"export control|u\.?s\.? person", re.I)
NO_SPONSOR = re.compile(r"(?:not|unable to|cannot|will not|won't|does not|do not)\s+(?:\w+\s+){0,3}sponsor|"
                        r"sponsorship (?:is |will )?not (?:be )?(?:available|provided|offered)|without (?:\w+\s+){0,4}sponsorship", re.I)
SEASON = re.compile(r"\b(spring|summer|fall|autumn|winter)[\s,/-]*(20[23]\d|'2\d)\b", re.I)
# Skills a student can tick on the site; each role lists the ones its posting asks for, so fit = how many you have
SKILLS = [("PCB design", r"\bpcb|schematic|altium|kicad|orcad|board layout"), ("Analog / mixed-signal", r"analog|mixed[- ]signal|op[- ]?amp"),
    ("Power electronics", r"power electronics|dc[- ]dc|converter|inverter|motor drive"),
    ("Power system studies", r"\betap\b|pss/?e|pscad|powerworld|load flow|short[- ]circuit stud|arc flash"),
    ("Embedded C / C++", r"embedded|firmware|microcontroller|\bmcu\b|\bc\+\+|\bembedded c\b"), ("FPGA / Verilog / VHDL", r"fpga|verilog|vhdl|\brtl\b"),
    ("RF / microwave", r"\brf\b|microwave|antenna|\bvna\b|spectrum analy"), ("DSP", r"\bdsp\b|signal processing|\bfft\b|filter design"),
    ("MATLAB / Simulink", r"matlab|simulink"), ("Python", r"python"), ("PLC / SCADA", r"\bplc|scada|ladder logic|\bhmi\b"),
    ("SPICE / Cadence", r"spice|cadence|virtuoso"), ("Lab equipment", r"oscilloscope|multimeter|function generator|solder|logic analyzer"),
    ("AutoCAD / electrical drawings", r"autocad|revit|one[- ]line|single[- ]line diagram|electrical drawings"),
    ("SPI / I2C / UART / CAN", r"\bspi\b|\bi2c\b|\buart\b|\bcan bus\b"), ("Linux", r"\blinux\b")]
SKILLS = [(label, re.compile(pat, re.I)) for label, pat in SKILLS]
# How long applying takes, by application system: 1 = one page + resume, 3 = make an account and retype your resume
EASE = {"Greenhouse": 1, "Lever": 1, "Ashby": 1, "Workable": 1, "Rippling": 1, "BambooHR": 1,
        "Workday": 3, "Taleo": 3, "Oracle": 3, "SuccessFactors": 3}  # anything else: 2
EE = re.compile(r"electrical|electronic|hardware|\bpcb|layout|analog|mixed[- ]signal|power|\brf\b|microwave|antenna|"
    r"signal integrity|\bsi\b|\bpi\b|\brtl\b|asic|\bsoc\b|digital design|verification|\bdv\b|physical design|silicon|"
    r"circuit|\bic\b|validation|\btest|process|yield|semiconductor|fpga|embedded|firmware|systems eng|controls?\b|"
    r"\bhil\b|simulation|robotic|mechatronic|avionic|harness|ewis|integration|\bgnc\b|guidance|navigation|battery|"
    r"energy|\bemi\b|\bemc\b|propulsion|powertrain|motor|reliability|manufactur|quality|compliance|certification|"
    r"assembly|automation|component|supplier|substation|protection|\bp&c\b|transmission|distribution|\bt&d\b|grid|"
    r"interconnection|utility|renewable|solar|wind|storage|nuclear|generation|data ?center|critical|\bmep\b|lighting|"
    r"field eng|construction|project eng|estimat|low[- ]voltage|telecom|\bdsp\b|signal processing|communications|"
    r"wireless|network hardware|optical|photonic|laser|applications eng|\bfae\b|technical sales|program manag|"
    r"research (?:intern|eng)|engineering research|instrumentation|\bi&c\b|medical device|technician|\blab\b|sensor|"
    r"aerospace|space|satellite", re.I)
NOT_EE = re.compile(r"marketing|finance|\bbusiness\b|devops|\btax\b|mrna|biolog|pharma|clinical|supply chain|customer|"
    r"support engineer|security engineer|cyber|penetration|human computer|\bhci\b|human factors|traffic|accounting|human resources|\bhr\b|recruit|legal|\bux\b|graphic|"
    r"analyst|data scien|civil|agronom|web|quant|site reliability|user experience|game|corporate|"
    r"frontend|backend|full[- ]?stack|product design|communications? (?:intern|specialist|manager)", re.I)

STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
    "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|"
    "Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|"
    "Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|"
    "Washington|West Virginia|Wisconsin|Wyoming|District of Columbia")
ABBR = ("AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|"
    "OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC")
US = re.compile(rf"United States|\bUSA?\b|U\.S\.|\b(?:{STATES})\b|(?:^|[,\s-])(?:{ABBR})(?=$|[,\s\d-])")
US_EXPLICIT = re.compile(r"United States|\bUSA?\b|U\.S\.")
FOREIGN = re.compile(r"Canada|Ontario|Quebec|British Columbia|Alberta|Toronto|Vancouver|Montr[eé]al|Waterloo|Mexico|"
    r"India|China|Taiwan|Japan|Korea|Singapore|Malaysia|Philippines|Vietnam|Thailand|Indonesia|Israel|Germany|France|"
    r"United Kingdom|\bUK\b|England|Scotland|Ireland|Netherlands|Poland|Spain|Italy|Switzerland|Sweden|Denmark|Norway|"
    r"Finland|Belgium|Austria|Czech|Romania|Hungary|Portugal|Brazil|Argentina|Chile|Colombia|Costa Rica|Australia|"
    r"New Zealand|Egypt|South Africa|Emirates|Dubai|Saudi|Turkey|Hsinchu|Bangalore|Bengaluru|Hyderabad|Shanghai|"
    r"Beijing|Shenzhen|Tokyo|Seoul|Penang|London|Munich|Paris|Dublin|Krak[oó]w|Berlin", re.I)
AMBIGUOUS = re.compile(r"^[\s,;]*$|\d+ Locations|Multiple|Various|Flexible|\bAny\b|Nationwide|TBD|^Remote$|Hybrid$", re.I)
STATE_ABBR = dict(zip(STATES.split("|"), ABBR.split("|")))


# Unmistakably EE in the title: enough on its own. Any other title (generic "Engineering Intern", "Test", "Systems",
# "Energy", ...) is only sent if the posting's own description asks for EE (EE_DESC).
CORE_EE = re.compile(r"electrical|electronic|electro-?mechanical|hardware|\bpcb|analog|mixed[- ]signal|power(?! ?bi\b| ?apps| ?platform)|\brf\b|"
    r"microwave|antenna|signal integrity|\bsi\b|\bpi\b|\brtl\b|asic|\bsoc\b|digital design|design verification|\bdv\b|"
    r"physical design|silicon|circuit|\bic\b|semiconductor|fpga|embedded|firmware|\bhil\b|mechatronic|avionic|harness|ewis|"
    r"\bemi\b|\bemc\b|substation|\bp&c\b|\bt&d\b|grid|low[- ]voltage|\bdsp\b|signal processing|wireless|photonic|"
    r"instrumentation|\bi&c\b|controls? eng|layout (?:design|eng)|\bev\b|electric vehicle|\bbms\b|high[- ]voltage|"
    r"batter(?:y|ies) (?:systems?|pack|management|electrical|integration|test)|transmission (?:&|and) distribution|"
    r"protection (?:&|and) control|\brelay|\bpv\b|photovoltaic|inverter|\bate\b|radar|\bplc|\bscada|"
    r"controls? (?:&|and) automation|automation (?:&|and) controls?", re.I)
EE_DESC = re.compile(r"electrical|electronics\b|circuit|\bpcb|embedded|firmware|fpga|oscilloscope|analog|\bee\b|\bplc|"
    r"power (?:systems|electronics)|\brf\b|antenna|avionic|wire harness|\bhil\b|signal integrity|\bbms\b|high[- ]voltage", re.I)
# Not EE work unless the title also says so ("Electrical Manufacturing Intern" passes, "Manufacturing Engineering Intern" doesn't)
OTHER_DISCIPLINE = re.compile(r"mechanical|chemical|industrial|environmental|biomedical|structural|geotech|petroleum|"
    r"materials|process|business|operations|supply|sourcing|finance|manufactur|quality|production|assembly|construction|"
    r"estimat|program manag|project manag|procurement|purchasing|supplier|technician|facilit|safety|maintenance|"
    r"logistics|warehouse|planner|buyer|transportation|roadway|transit|mining|naval|marine|water|fire protection|packaging|"
    r"compliance|inventory|budget|project control|\bsre\b|observability|infrastructure", re.I)
MAX_AGE = 14  # days; older or undated postings are never sent


GRAD = re.compile(r"ph\.?d|doctoral|post-?doc|master'?s|\bmba\b|\bms\b|\bm\.s\.|(?<!under)graduate|residency", re.I)
UNDERGRAD = re.compile(r"undergrad|bachelor|\bbs\b|\bb\.s\.", re.I)
NOT_HW = re.compile(r"software|\bai\b|artificial intelligence|machine learning|\bml\b|deep learning|data (?:engineer|platform)|"
    r"applied scien|research scien|\bdata (?!cent)|recommendation|agentic|python|\bllm|\bnlp\b", re.I)
HW = re.compile(r"embedded|firmware|hardware|electrical|fpga|asic|silicon|circuit", re.I)


MECH_ONLY = re.compile(r"mechanical", re.I)
EE_IN_MECH = re.compile(r"electr|mechatronic|\bcontrols?\b|embedded|firmware|\bpcb", re.I)  # "Electro-Mechanical", "Mechatronics" stay


def kind(t):
    if COOP.search(t): return "Co-op"
    if INTERN.search(t): return "Internship"
    if NEWGRAD.search(t) and not SENIOR.search(t): return "New Grad"


def match(t):
    if GRAD.search(NEWGRAD.sub("", t)) and not UNDERGRAD.search(t): return False  # undergrad / new grad roles only
    if MECH_ONLY.search(t) and not EE_IN_MECH.search(t): return False  # "Avionics Mechanical Engineer" is mechanical work
    sw = NOT_HW.search(t) and not HW.search(t)
    ee = CORE_EE.search(t) or (not OTHER_DISCIPLINE.search(t) and (EE.search(t) or re.search(r"engineer", t, re.I)))
    return bool(kind(t) and ee and not NOT_EE.search(t) and not sw)


def us(loc):
    """Any ;-separated location in the US -> True."""
    for part in loc.split(";"):
        if AMBIGUOUS.search(part): return True  # "3 Locations" etc: can't tell, don't drop
        if FOREIGN.search(part) and not US_EXPLICIT.search(part): continue
        if re.search(r", [A-Z]{2}, (?!US$)[A-Z]{2}\s*$", part): continue  # "Markham, ON, CA" / "Cork, CO, IE"
        if US.search(part): return True
    return False


def keep(title, loc):
    """Internships, co-ops and new grad roles anywhere in the US."""
    return match(title) and us(loc)


# ---------- fetching ----------
def fetch(url, data=None, headers=None, raw=False, timeout=30, tries=4):
    body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
    req = urllib.request.Request(url.replace(" ", "%20"), body, {  # %20: multi-word searches ("new grad")
                                "Content-Type": "application/json", "User-Agent": UA,
                                "Accept-Language": "en-US", **(headers or {})})
    for attempt in range(tries):  # retry rate limits / flaky servers; a 404 (dead board) fails fast
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                s = r.read().decode("utf-8", "ignore")
            return s if raw else json.loads(s)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1: raise
            time.sleep(min(int(e.headers.get("Retry-After") or 0) or 5 * 2 ** attempt, 60))
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1: raise
            time.sleep(5 * 2 ** attempt)


QUERIES = ("intern", "co-op", "new grad", "university")  # ponytail: 2x the keyword requests of interns-only; drop "university" if runs near the 45 min timeout


def s_gh(tok):
    return [(j["absolute_url"], tok, j["title"], j["location"]["name"])
            for j in fetch(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs", timeout=120)["jobs"]]  # some boards are MBs


def s_lever(tok, api="api.lever.co"):
    return [(j["hostedUrl"], tok, j["text"], j["categories"].get("location") or "")
            for j in fetch(f"https://{api}/v0/postings/{tok}?mode=json")]


def s_workable(tok):
    out, body = [], {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    for _ in range(20):
        d = fetch(f"https://apply.workable.com/api/v3/accounts/{tok}/jobs", body)
        out += [(f"https://apply.workable.com/{tok}/j/{j['shortcode']}/", tok, j["title"],
                 ", ".join(filter(None, (j["location"].get(k) for k in ("city", "region", "country"))))) for j in d["results"]]
        if not d.get("nextPage"): break
        body = {**body, "token": d["nextPage"]}
    return out


def s_bamboo(tok):
    return [(f"https://{tok}.bamboohr.com/careers/{j['id']}", tok, j["jobOpeningName"],
             ", ".join(filter(None, (j["location"].get("city"), j["location"].get("state"), (j.get("atsLocation") or {}).get("country")))))
            for j in fetch(f"https://{tok}.bamboohr.com/careers/list")["result"]]


def s_rippling(tok):
    return [(j["url"], tok, j["name"], "; ".join(f"{l.get('name')}, {l.get('country')}" for l in j["locations"]))
            for j in fetch(f"https://ats.rippling.com/api/v2/board/{tok}/jobs?page=0&pageSize=200")["items"]]


def s_bytedance(api, path, job_url):
    """TikTok / ByteDance share one job API."""
    out = []
    for q in QUERIES:
        for off in range(0, 500, 100):
            js = fetch(api, {"keyword": q, "limit": 100, "offset": off, "recruitment_id_list": [], "job_category_id_list": [],
                             "subject_id_list": [], "location_code_list": []}, headers={"website-path": path})["data"]["job_post_list"]
            for j in js:
                c, loc = j.get("city_info"), []
                while c: loc.append(c.get("en_name") or ""); c = c.get("parent")
                out.append((job_url + j["id"], path.replace("en", "bytedance"), j["title"], ", ".join(loc)))
            if len(js) < 100: break
    return out


def s_ashby(tok):
    return [(j["jobUrl"], tok, j["title"], j.get("location", ""))
            for j in fetch(f"https://api.ashbyhq.com/posting-api/job-board/{tok}")["jobs"]]


def s_sr(tok):
    return [(f"https://jobs.smartrecruiters.com/{tok}/{j['id']}", j["company"]["name"], j["name"],
             j["location"].get("fullLocation") or f"{j['location'].get('city')}, {j['location'].get('country')}")
            for q in QUERIES for j in fetch(f"https://api.smartrecruiters.com/v1/companies/{tok}/postings?q={q}&country=us")["content"]]


WD_US = "bc33aa3152ec42d4995f4791a106ed09"  # Workday's id for "United States of America", the same in every tenant


def wd_filter(facets):
    """US-only filter under whatever name this tenant gives it; else its intern filter (Micron). {} if neither."""
    pairs = [(p, vs) for f in facets for p, vs in [(f["facetParameter"], f["values"])] +
             [(v["facetParameter"], v["values"]) for v in f["values"] if "values" in v]]  # country is often nested in locations
    us = [{p: [WD_US]} for p, vs in pairs if any(v.get("id") == WD_US for v in vs)]
    interns = [{p: [v["id"] for v in vs if INTERN.search(v.get("descriptor", ""))]} for p, vs in pairs
               if any(INTERN.search(v.get("descriptor", "")) for v in vs)]
    return (us or interns or [{}])[0]


def s_wd(host, tenant, site):
    out = []
    prefix = f"https://{host}/recruiting/{tenant}/{site}" if "myworkdaysite" in host else f"https://{host}/{site}"
    api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    facets = wd_filter(fetch(api, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": "intern"}).get("facets", []))
    for q, pages in (("intern", 5), ("co-op", 1), ("new grad", 1), ("university", 1)):  # ponytail: 100 relevance-ranked hits per tenant; raise if a giant tenant misses roles
        for off in range(0, 20 * pages, 20):
            page = fetch(api, {"appliedFacets": facets if q == "intern" or WD_US in str(facets) else {},
                               "limit": 20, "offset": off, "searchText": q}).get("jobPostings", [])
            us_only = WD_US in str(facets)  # filtered to the US: "Austin (Oakhill, Office)" is in the US even without a state
            out += [(prefix + j["externalPath"], tenant, j["title"], j.get("locationsText", "") + ", United States" * us_only)
                    for j in page if "externalPath" in j and "title" in j]
            if len(page) < 20: break
    return out


def s_oracle(host, site):
    out = []
    for q in QUERIES:
        for off in range(0, 100, 25):
            it = fetch(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
                       f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},keyword={q},"
                       f"limit=25,offset={off},sortBy=POSTING_DATES_DESC")["items"][0]
            reqs = it.get("requisitionList", [])
            out += [(f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}", host.split(".")[0],
                     r["Title"], f"{r.get('PrimaryLocation', '')}, {r.get('PrimaryLocationCountry', '')}") for r in reqs]
            if len(reqs) < 25: break
    return out


def s_icims(host):
    out = []
    for q in QUERIES:
        s = fetch(f"https://{host}/jobs/search?ss=1&searchKeyword={q}&in_iframe=1", raw=True)
        for row in s.split('class="col-xs-12 title"')[1:]:
            u = re.search(r'href="([^"]+)"', row); t = re.search(r"<h3[^>]*>\s*([^<]+)", row)
            loc = re.search(r"Job Location.*?<span[^>]*>\s*([^<]+)", row, re.S)
            if u and t: out.append((u.group(1).replace("?in_iframe=1", ""), host.split(".")[0].split("-", 1)[-1],
                                    html.unescape(t.group(1).strip()), loc.group(1).strip() if loc else ""))
    return out


def s_jibe(host):
    out = []
    us = "&country=United%20States"
    for q in QUERIES:
        for p in range(1, 11):
            jobs = fetch(f"https://{host}/api/jobs?keywords={q}&page={p}{us}")["jobs"]
            if not jobs and p == 1 and us:  # site spells it differently ("USA", TRC): search unfiltered
                us = ""; jobs = fetch(f"https://{host}/api/jobs?keywords={q}&page={p}")["jobs"]
            out += [(f"https://{host}/jobs/{d['slug']}", host, d["title"],
                     f"{d.get('city', '')}, {d.get('state', '')}, {d.get('country', '')}") for d in (j["data"] for j in jobs)]
            if len(jobs) < 10: break
    return out


HD_JOB = re.compile(r'\{"id":"(\d+)","title":"((?:[^"\\]|\\.)*)","url":"([^"]+)"[^{}]*?"locations":"((?:[^"\\]|\\.)*)"')


def s_hd(host):
    """Next.js career sites (Gulfstream, built by happydance): the job list is JSON inside each search page."""
    out = {}
    for q in QUERIES:
        for n in range(1, 11):
            s = fetch(f"https://{host}/en/jobs/?search={q}&page={n}", raw=True).replace('\\"', '"')
            before = len(out)
            for i, t, u, loc in HD_JOB.findall(s):
                out.setdefault(i, (f"https://{host}{u}", host, json.loads(f'"{t}"'), json.loads(f'"{loc}"')))
            if len(out) == before: break  # past the last page it repeats results
    return list(out.values())


def s_sfrmk(host):
    """SuccessFactors Recruiting Marketing sites (…/job/City-Title-ST-12345/123/): HTML search pages."""
    out = {}
    for q in QUERIES:
        for row in range(0, 200, 25):
            s = fetch(f"https://{host}/search/?q={q}&startrow={row}", raw=True)
            before = len(out)
            for m in re.finditer(r'href="(/job/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>', s):
                loc = re.search(r'(?:section-location-value"|class="jobLocation[^"]*")>\s*([^<]+?)\s*<', s[m.end():m.end() + 4000])
                out.setdefault(m.group(1), (f"https://{host}{m.group(1)}", host, html.unescape(m.group(2)), loc.group(1) if loc else ""))
            if len(out) == before: break
    return list(out.values()) or s_sfrmk_api(host)


def s_sfrmk_api(host):
    """Newer SuccessFactors sites (Skyworks, Seagate, BMW) render results with JS; their search API gives the post date too."""
    out = []
    for q in QUERIES:
        for n in range(20):
            res = fetch(f"https://{host}/services/recruiting/v1/jobs", {"locale": "en_US", "pageNumber": n, "sortBy": "", "keywords": q,
                        "location": "", "facetFilters": {}, "brand": "", "skills": [], "categoryId": 0, "alertId": "", "rcmCandidateId": ""})["jobSearchResult"]
            for j in (r["response"] for r in res):
                u = f"https://{host}/job/{j['urlTitle']}/{j['id']}-en_US/"
                if d := j.get("unifiedStandardStart"):
                    POSTED[u] = datetime.datetime.strptime(d, "%m/%d/%y").replace(tzinfo=PACIFIC)
                out.append((u, host, j["unifiedStandardTitle"], "; ".join(l.strip() for l in j.get("jobLocationShort") or [])))
            if len(res) < 10: break
    return out


def s_phenom(base):
    host, *rest = base.split("/")
    country, lang = (rest + ["global", "en"])[:2] if len(rest) == 2 else ("global", rest[0] if rest else "en")
    out = []
    for q in QUERIES:
        for frm in range(0, 200, 50):
            jobs = fetch(f"https://{host}/widgets", {
                "lang": f"{lang}_{country}", "deviceType": "desktop", "country": country, "pageName": "search-results",
                "ddoKey": "refineSearch", "sortBy": "", "subsearch": "", "from": frm, "jobs": True, "counts": False,
                "all_fields": [], "size": 50, "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                "pageId": "page1", "siteType": "external", "keywords": q, "global": True, "selected_fields": {},
                "locationData": {}})["refineSearch"]["data"]["jobs"]
            # Phenom's own job pages can show "no longer available" for live roles (RTX); prefer the real Workday posting
            out += [((j.get("applyUrl") or "").removesuffix("/apply") if "myworkday" in (j.get("applyUrl") or "")
                     else f"https://{base}/job/{j['jobId']}", host, j["title"],
                     j.get("cityStateCountry") or j.get("location", "")) for j in jobs]
            if len(jobs) < 50: break
    return out


def s_ef(host, domain):
    out = []
    for q in QUERIES:
        for start in range(0, 100, 10):
            ps = fetch(f"https://{host}/api/pcsx/search?domain={domain}&query={q}&start={start}&location=United%20States",
                       headers={"Accept": "application/json"})["data"]["positions"]
            out += [(f"https://{host}{p['positionUrl']}", domain, p["name"], "; ".join(p.get("standardizedLocations") or p["locations"]))
                    for p in ps]
            if len(ps) < 10: break
    return out


def s_taleo(host, section):
    page = fetch(f"https://{host}/careersection/{section}/jobsearch.ftl", raw=True)
    portal = re.search(r"portal=(\d+)", page)
    if not portal: return []
    out = []
    for q in QUERIES:
        for n in range(1, 5):
            reqs = fetch(f"https://{host}/careersection/rest/jobboard/searchjobs?lang=en&portal={portal.group(1)}",
                         {"multilineEnabled": False, "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
                          "fieldData": {"fields": {"KEYWORD": q, "LOCATION": ""}, "valid": True},
                          "filterSelectionParam": {"searchFilterSelections": []},
                          "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []}, "pageNo": n},
                         headers={"tz": "GMT-07:00"}).get("requisitionList", [])
            out += [(f"https://{host}/careersection/{section}/jobdetail.ftl?job={r['contestNo']}", host.split(".")[0],
                     r["column"][0], " ".join(json.loads(r["column"][1])) if r["column"][1].startswith("[") else r["column"][1])
                    for r in reqs]
            if len(reqs) < 25: break
    return out


def s_apple():
    out = []
    for p in range(1, 6):  # Apple's "Internships" team; keyword search is full-text and useless
        s = fetch(f"https://jobs.apple.com/en-us/search?location=united-states-USA&team=internships-STDNT-INTRN&page={p}", raw=True)
        m = re.search(r'__staticRouterHydrationData = JSON.parse\((".*?")\);', s, re.S)
        res = json.loads(json.loads(m.group(1)))["loaderData"]["search"]["searchResults"] if m else []
        out += [(f"https://jobs.apple.com/en-us/details/{r['positionId']}", "Apple", r["postingTitle"],
                 "; ".join(f"{l.get('name')}, {l.get('countryName')}" for l in r["locations"])) for r in res]
        if len(res) < 20: break
    return out


def s_google():
    out = []
    for q in QUERIES:
        for p in range(1, 6):
            s = fetch(f"https://www.google.com/about/careers/applications/jobs/results?q={q}&location=United%20States&page={p}", raw=True)
            m = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(\[.*?\]), sideChannel", s, re.S)
            jobs = (json.loads(m.group(1))[0] or []) if m else []
            out += [(f"https://www.google.com/about/careers/applications/jobs/results/{j[0]}", "Google", j[1],
                     "; ".join(l[0] for l in j[9] or [])) for j in jobs]
            if len(jobs) < 20: break
    return out


def s_amazon():
    out = []
    for q in QUERIES:
        for off in range(0, 500, 100):
            jobs = fetch(f"https://www.amazon.jobs/en/search.json?base_query={q}&country=USA&result_limit=100&offset={off}&sort=recent")["jobs"]
            out += [("https://www.amazon.jobs" + j["job_path"], "Amazon", j["title"], j["normalized_location"] or j["location"]) for j in jobs]
            if len(jobs) < 100: break
    return out


SCANNERS = {"gh": s_gh, "lever": s_lever, "ashby": s_ashby, "sr": s_sr, "wd": s_wd, "oracle": s_oracle, "icims": s_icims,
            "jibe": s_jibe, "hd": s_hd, "sfrmk": s_sfrmk, "phenom": s_phenom, "ef": s_ef, "taleo": s_taleo,
            "apple": s_apple, "google": s_google, "amazon": s_amazon,
            "workable": s_workable, "bamboo": s_bamboo, "rippling": s_rippling, "bytedance": s_bytedance}
MSFT = ("ef", "apply.careers.microsoft.com", "microsoft.com")
TIKTOK = ("bytedance", "https://api.lifeattiktok.com/api/v1/public/supplier/search/job/posts", "tiktok", "https://lifeattiktok.com/search/")
BYTEDANCE = ("bytedance", "https://jobs.bytedance.com/api/v1/search/job/posts", "en", "https://jobs.bytedance.com/en/position/")
BUILTIN = {("apple",), ("google",), ("amazon",), MSFT, TIKTOK, BYTEDANCE}


def detect(url):
    """Career/job URL -> board tuple, or None if we can't poll that site."""
    u = urllib.parse.unquote(url)
    for dom, b in (("jobs.apple.com", ("apple",)), ("google.com/about/careers", ("google",)), ("amazon.jobs", ("amazon",)),
                   ("careers.microsoft.com", MSFT), ("lifeattiktok.com", TIKTOK), ("jobs.bytedance.com", BYTEDANCE)):
        if dom in u: return b
    rules = [
        ("wd", r"https?://(wd\d+\.myworkdaysite\.com)/(?:[a-z]{2}-[a-z]{2}/)?recruiting/([\w-]+)/([\w-]+)", lambda h, t, s: (h, t, s)),
        ("wd", r"https?://(([\w-]+)\.wd\d+\.myworkdayjobs\.com)/(?:[a-z]{2}-[a-z]{2}/)?([\w-]+)", lambda h, t, s: (h, t, s)),
        ("gh", r"greenhouse\.io/(?:embed/job_app\?for=|v1/boards/)?([\w-]+)", lambda t: (t,)),
        ("ashby", r"jobs\.ashbyhq\.com/([^/?#]+)", lambda t: (t,)),
        ("lever", r"jobs\.eu\.lever\.co/([^/?#]+)", lambda t: (t, "api.eu.lever.co")),
        ("lever", r"jobs\.lever\.co/([^/?#]+)", lambda t: (t,)),
        ("workable", r"apply\.workable\.com/(?!api/)([\w-]+)", lambda t: (t,)),
        ("bamboo", r"https?://([\w-]+)\.bamboohr\.com", lambda t: (t,)),
        ("rippling", r"ats\.rippling\.com/(?!api/)([\w-]+)", lambda t: (t,)),
        ("sr", r"(?:jobs|careers)\.smartrecruiters\.com/([^/?#]+)", lambda t: (t,)),
        ("oracle", r"https?://([\w.-]+\.oraclecloud\.com)(?::443)?/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)", lambda h, s: (h, s)),
        ("jibe", r"https?://([\w.-]+)/jobs/\d+\?(?:lang=[\w-]+&)?icims=1", lambda h: (h,)),
        ("jibe", r"https?://([\w.-]+)/?\?jibe$", lambda h: (h,)),
        ("icims", r"https?://((?!internal-)[\w-]+\.icims\.com)/jobs", lambda h: (h,)),
        ("taleo", r"https?://([\w-]+\.taleo\.net)/careersection/([\w-]+)/", lambda h, s: (h, s)),
        ("ef", r"https?://([\w-]+)\.eightfold\.ai", lambda s: (f"{s}.eightfold.ai", f"{s}.com")),
        ("ef", r"https?://([\w.-]+)/careers\?.*\bdomain=([\w.-]+)", lambda h, d: (h, d)),
        ("sfrmk", r"https?://([\w.-]+)/(?:job|search)/.*[?&]ats=successfactors", lambda h: (h,)),
        ("sfrmk", r"https?://([\w.-]+)/?\?sfrmk$", lambda h: (h,)),
        ("hd", r"https?://([\w.-]+)/?\?happydance$", lambda h: (h,)),
        ("phenom", r"https?://([\w.-]+/(?:[a-z]{2,6}/)?[a-z]{2})/job/", lambda b: (b,)),
        ("phenom", r"https?://([\w.-]+/(?:[a-z]{2,6}/)?[a-z]{2})/?\?phenom$", lambda b: (b,)),
    ]
    for kind, pat, f in rules:
        if m := re.search(pat, u, re.I):
            b = (kind, *f(*m.groups()))
            return None if kind == "wd" and b[-1].lower() in ("job", "wday", "login") else b
    return None


def gh_custom_domain(j):
    """Greenhouse board behind a company's own domain (…?gh_jid=123): find its board token so we poll it directly."""
    if "gh_jid=" not in j.get("url", "") or not j.get("active") or not keep(j["title"], "; ".join(j.get("locations", []))):
        return None
    norm = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    label = urllib.parse.urlparse(j["url"]).netloc.split(".")[-2]
    for tok in dict.fromkeys([norm(j["company_name"]), label, "fly" + label, label.removeprefix("with")]):
        try:
            if norm(fetch(f"https://boards-api.greenhouse.io/v1/boards/{tok}")["name"])[:5] == norm(j["company_name"])[:5]:
                return ("gh", tok)
        except Exception: pass
    return None


def discover():
    """Boards to poll = saved boards ∪ companies.txt ∪ boards harvested from public listings. Only grows."""
    boards = set(map(tuple, json.loads(BOARDS.read_text()))) if BOARDS.exists() else set()
    boards |= BUILTIN
    names = json.loads(NAMES.read_text()) if NAMES.exists() else {}
    names.update({"|".join(b): n for b, n in ((("apple",), "Apple"), (("google",), "Google"), (("amazon",), "Amazon"),
                                              (MSFT, "Microsoft"), (TIKTOK, "TikTok"), (BYTEDANCE, "ByteDance"))})
    if COMPANIES.exists():
        for raw in COMPANIES.read_text().splitlines():
            line, _, comment = raw.partition("#")
            if (line := line.strip()) and (b := detect(line)):
                boards.add(b)
                if comment.strip(): names["|".join(b)] = comment.strip()
            elif line: print("companies.txt: can't poll", line, file=sys.stderr)
    fallback, blind = [], collections.defaultdict(list)
    for src in DISCOVERY:
        try: listings = fetch(src)
        except Exception as e: print("discovery failed", src, e, file=sys.stderr); continue
        for j in listings:
            if b := detect(j.get("url", "")) or gh_custom_domain(j):
                boards.add(b)
                names.setdefault("|".join(b), j["company_name"])
            elif j.get("active"):  # site we can't poll (Tesla, Meta, ...): use the listing itself, with Simplify's post time
                POSTED[j["url"]] = datetime.datetime.fromtimestamp(j["date_posted"], datetime.timezone.utc)
                fallback.append((j["url"], f"{j['company_name']} (via Simplify)", j["title"], "; ".join(j.get("locations", []))))
                if keep(j["title"], fallback[-1][3]):
                    blind[f"{j['company_name']} ({urllib.parse.urlparse(j['url']).netloc})"].append(j["date_posted"])
    UNSCANNABLE.write_text(
        "Companies with open EE internships whose own career site can't be polled; their roles come from Simplify.\n"
        "EE roles | newest listed on Simplify | company (site)\n" + "".join(
            f"{len(t):8} | {datetime.datetime.fromtimestamp(max(t)):%b %d %I:%M %p}        | {c}\n"
            for c, t in sorted(blind.items(), key=lambda kv: -len(kv[1]))))
    boards = set({tuple(x.lower() for x in b): b for b in sorted(boards)}.values())  # "External" == "external"
    BOARDS.write_text(json.dumps(sorted(boards), indent=0))
    NAMES.write_text(json.dumps(names, indent=0, sort_keys=True))
    return boards, fallback


def scan(b):
    try: return SCANNERS[b[0]](*b[1:])
    except Exception as e: return e  # dead/renamed board or site hiccup; next run retries


# ---------- verifying a posting ----------
NUM = r"\d{1,3}(?:,?\d{3})*(?:\.\d{2})?"
PAY = re.compile(rf"(?:\$|USD ?){NUM}(?: ?USD)?[kK]? ?(?:-|–|—|to) ?(?:\$|USD ?)?{NUM}(?: ?USD)?[kK]?(?: ?(?:/|per) ?(?:hour|hr|year|yr|annum|month))?|"
                 rf"{NUM} ?USD ?(?:-|–|to) ?{NUM} ?USD(?: ?(?:/|per) ?(?:hour|hr|year|yr))?|\$\d{{2,3}}(?:\.\d{{2}})? ?(?:/|per) ?(?:hour|hr)", re.I)


CLOSED = re.compile(r"no longer (?:accepting applications|available|open|active|posted)|(?:position|job|role|requisition) (?:has been|is) "
                    r"(?:filled|closed|expired)|job (?:you are looking for|you're looking for) (?:is|was|has)", re.I)


def details(url):
    """-> (posted datetime|None, pay str|None, company str|None, description text) from the job's own page/API. Best effort.
    Description None means the posting is gone (404/410, past its end date, or says it is closed)."""
    try:
        if m := re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url):
            d = fetch(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}?pay_transparency=true", timeout=15, tries=1)
            rng = (d.get("pay_input_ranges") or [{}])[0]
            pay = rng and rng.get("min_cents") and f"${rng['min_cents'] / 100:,.0f} – ${rng['max_cents'] / 100:,.0f}"
            text = html.unescape(re.sub(r"<[^>]+>|&lt;[^&]*&gt;", " ", html.unescape(d.get("content", ""))))
            return (datetime.datetime.fromisoformat(d.get("first_published") or d["updated_at"]),
                    pay or (PAY.search(text) or [None])[0], d.get("company_name"), text)
        if m := re.search(r"https://([\w.-]+myworkday(?:jobs|site)\.com)/(?:recruiting/([\w-]+)/)?([\w-]+)(/job/.*)", url):
            host, tenant, site, path = m.groups()
            tenant = tenant or host.split(".")[0]
            info = fetch(f"https://{host}/wday/cxs/{tenant}/{site}{path}", timeout=15, tries=1)["jobPostingInfo"]
            text = html.unescape(re.sub(r"<[^>]+>", " ", info.get("jobDescription", "")))
            return (datetime.datetime.fromisoformat(info["startDate"]).replace(tzinfo=PACIFIC) if info.get("startDate") else None,
                    (PAY.search(text) or [None])[0], None, text)
        if m := re.search(r"https://([\w.-]+oraclecloud\.com)/hcmUI/CandidateExperience/[a-z]{2}/sites/([\w-]+)/job/(\d+)", url):
            it = fetch(f"https://{m[1]}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails?expand=all&onlyData=true"
                       f"&finder=ById;Id=%22{m[3]}%22,siteNumber={m[2]}", timeout=15, tries=1)["items"]
            if not it: return None, None, None, None
            it = it[0]
            text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(it.get(k) or "" for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))))
            p = it.get("ExternalPostedStartDate")
            return datetime.datetime.fromisoformat(p) if p else None, (PAY.search(text) or [None])[0], None, text
        if m := re.search(r"smartrecruiters\.com/([^/?#]+)/(\d+)", url):
            d = fetch(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}", timeout=15, tries=1)
            if d.get("active") is False: return None, None, None, None
            text = html.unescape(re.sub(r"<[^>]+>", " ", " ".join(
                s.get("text") or "" for s in ((d.get("jobAd") or {}).get("sections") or {}).values())))
            p = d.get("releasedDate")
            return (datetime.datetime.fromisoformat(p.replace("Z", "+00:00")) if p else None,
                    (PAY.search(text) or [None])[0], (d.get("company") or {}).get("name"), text)
        if re.search(r"\.bamboohr\.com/careers/\d+$", url):
            d = fetch(url + "/detail", timeout=15, tries=1)["result"]["jobOpening"]
            text = html.unescape(re.sub(r"<[^>]+>", " ", d.get("description") or ""))
            p = d.get("datePosted")
            return datetime.datetime.fromisoformat(p) if p else None, (PAY.search(text) or [None])[0], None, text
        page = fetch(url + ("&" if "?" in url else "?") + "in_iframe=1" if ".icims.com/" in url else url, raw=True, timeout=15, tries=1)
        posted = pay = org = desc = None
        for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page, re.S):
            try: d = json.loads(block)
            except ValueError: continue
            d = next((x for x in (d if isinstance(d, list) else [d]) if "JobPosting" in str(x.get("@type"))), None)
            if not d: continue
            if d.get("datePosted"):
                posted = datetime.datetime.fromisoformat(d["datePosted"].replace("Z", "+00:00").replace("+0000", "+00:00"))
            if (vt := d.get("validThrough")) and vt[:10] < f"{datetime.date.today()}": return None, None, None, None
            org = (d.get("hiringOrganization") or {}).get("name")
            desc = html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(d.get("description") or "")))
            v = ((d.get("baseSalary") or {}).get("value") or {})
            if v.get("minValue"):
                pay = f"${float(v['minValue']):,.2f} – ${float(v.get('maxValue') or v['minValue']):,.2f}".replace(".00", "") + \
                      (f" / {v['unitText'].lower()}" if v.get("unitText") else "")
        if not posted and (m := re.search(r'\\?"(?:datePosted|postDateInGMT|postingDate|createdOn|publishedAt|postedDate)\\?"\s*:\s*'
                                          r'\\?"(\d{4}-\d\d-\d\d(?:T[\d:.]+(?:Z|[+-]\d\d:?\d\d)?)?)', page)):  # Apple, Rippling, ...
            posted = datetime.datetime.fromisoformat(m[1].replace("Z", "+00:00"))
        if not posted and (m := re.search(r'itemprop="datePosted" content="(\w{3} \w{3} \d\d [\d:]{8}) UTC (\d{4})"', page)):  # SuccessFactors
            posted = datetime.datetime.strptime(f"{m[1]} {m[2]}", "%a %b %d %H:%M:%S %Y").replace(tzinfo=datetime.timezone.utc)
        text = html.unescape(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", page, flags=re.S))
        if not desc and CLOSED.search(text): return None, None, None, None
        return posted, pay or (PAY.search(text) or [None])[0], org, desc or text
    except urllib.error.HTTPError as e:
        return (None, None, None, None if e.code in (404, 410) else "")
    except Exception:
        return None, None, None, ""


SOURCES = {"myworkday": "Workday", "greenhouse": "Greenhouse", "lever.co": "Lever", "ashbyhq": "Ashby", "smartrecruiters": "SmartRecruiters",
           "oraclecloud": "Oracle", "icims": "iCIMS", "taleo": "Taleo", "eightfold": "Eightfold", "workable": "Workable",
           "bamboohr": "BambooHR", "rippling": "Rippling"}


def job_id(u):
    """The company's own requisition/job number, pulled from the link — search it on their careers site to confirm."""
    m = re.search(r"_((?:JR|REQ|R)?-?\d{4,}[\w-]*?)(?:/apply)?$|[?&](?:job|gh_jid|jobId)=(\d+)|/details/(\d+)|/jobs?/(\d{4,})|"
                  r"/job/(\d{4,})|/j/([0-9A-F]{8,})|/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})|/(\d{6,})/?$", u)
    return next((g for g in m.groups() if g), None) if m else None


def source(u):
    host = urllib.parse.urlparse(u).netloc
    return next((v for k, v in SOURCES.items() if k in host), host.removeprefix("www."))


GRAD_REQUIRED = re.compile(r"(?:pursuing|enrolled in|currently in)\s+(?:a\s+)?(?:master|ph\.?d|graduate)[^.]{0,60}(?:required|degree)|"
                           r"must be (?:a )?(?:master|ph\.?d|graduate) student", re.I)


def pretty(c):
    if c.islower() and " " in c: return c.title()  # "trane technologies" -> "Trane Technologies"
    c = re.sub(r"^(?:careers?|jobs|corningjobs)[.-]|\.(?:com|org|net|io|ai)(?:/.*)?$", "", c)
    return c[:1].upper() + c[1:]


LOGOS = HERE / "logos.json"  # company -> logo URL ("" = none found); cached so each company is looked up once
ATS_HOSTS = ("myworkday", "greenhouse", "lever.co", "ashbyhq", "smartrecruiters", "oraclecloud", "icims", "taleo",
             "eightfold", "workable", "bamboohr", "rippling", "simplify", "applytojob", "paylocity", "jobvite", "avature")


def _png_size(b):
    return int.from_bytes(b[16:20], "big") if b[:4] == b"\x89PNG" else 0


def _get(u, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.url, r.read()


def site_icon(d):
    """Sharp icon straight from a company's own site: apple-touch-icon, else the biggest PNG icon its homepage declares."""
    for touch in (f"https://www.{d}/apple-touch-icon.png", f"https://{d}/apple-touch-icon.png"):
        try:
            final, b = _get(touch)
            if _png_size(b) >= 57: return final
        except Exception: pass
    try:
        base, page = _get(f"https://www.{d}/")
        links = re.findall(r"<link[^>]+rel=[\"'][^\"']*icon[^\"']*[\"'][^>]*>", page.decode("utf-8", "ignore"), re.I)
        hrefs = [urllib.parse.urljoin(base, html.unescape(h)) for l in links if (h := (re.search(r'href=["\']([^"\']+\.png[^"\']*)', l) or [0, 0])[1])]
        for h in sorted(hrefs, key=lambda h: -int((re.search(r"(\d{2,3})x\d{2,3}", h) or [0, 0])[1])):
            if _png_size(_get(h)[1]) >= 32: return h
    except Exception: pass
    return None


def logo(company, url, cache):
    """Company logo URL. Finds the company's web domain (career-site host, name.com, ATS tenant, Clearbit's company search),
    then takes the sharpest icon: Google's favicon if >=48px, else the site's apple-touch-icon, else any real favicon."""
    if cache.get(company): return cache[company]
    host = urllib.parse.urlparse(url).netloc.lower()
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    tenant = (host.split(".")[0].removeprefix("careers-") if any(a in host for a in ("myworkdayjobs", "icims", "eightfold"))
              else parts[0] if any(a in host for a in ("greenhouse", "lever.co", "ashbyhq")) and parts else "")
    squash = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    cands = [] if any(a in host for a in ATS_HOSTS) else [".".join(host.split(".")[-2:])]
    trusted = set(cands)  # domains we know belong to this company (vs. name.com guesses)
    acronym = (re.search(r"\(([A-Za-z&]{2,8})\)", company) or [None, ""])[1].replace("&", "")
    clean = re.sub(r"\(.*?\)|\b(?:Group|Company|Companies|Family of|Inc|LLC|USA|US|Solutions|Corporation|Corp)\b\.?", "", company).strip(" ,-")
    for q in dict.fromkeys(filter(None, (company, clean if " " in clean else None))):  # a lone generic word matches strangers
        try:  # Clearbit's company search still returns domains (its logo service is gone)
            hits = json.loads(_get("https://autocomplete.clearbit.com/v1/companies/suggest?query=" + urllib.parse.quote(q))[1])
            exact = [h["domain"] for h in hits[:3] if squash(h["name"]) in (squash(company), squash(clean))]  # exact name only
            cands += exact; trusted |= set(exact)
        except Exception: pass
    cands += [squash(clean) + ".com", squash(company) + ".com", squash(tenant) + ".com", acronym.lower() + ".com"]

    exact = {squash(company) + ".com", squash(clean) + ".com"} | trusted  # domains safe to keep searching after a blurry hit
    best = (0, "")  # (pixel size, url): keep the sharpest real logo found
    for d in dict.fromkeys(c for c in cands if len(c) > 5):  # most-trusted first; "ti.com" is a real domain
        if best[1] and d not in exact: break  # only name-exact domains may improve a logo we already have
        g = f"https://www.google.com/s2/favicons?domain={d}&sz=128"
        try: best = max(best, (_png_size(_get(g)[1]), g))
        except Exception:  # Google has no icon for it; only keep going if we know the domain is this company's
            if d not in trusted: continue
        if best[0] >= 96: break
        if gh := github_avatar(d, company): best = (400, gh); break
        if best[0] < 48 and (icon := site_icon(d)): best = max(best, (100, icon))
        if best[0] >= 48: break
    if best[0] < 32 and (w := wikidata_logo(company)): best = (0, w)  # crisp official logo beats a blurry 16px favicon
    cache[company] = best[1]
    return cache[company]


def github_avatar(domain, company):
    """Square high-res logo from the company's GitHub org, only if the org lists this company's website."""
    head = {"User-Agent": UA, **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"} if os.environ.get("GITHUB_TOKEN") else {})}
    for org in dict.fromkeys((re.sub(r"[^a-z0-9]", "", company.lower()), domain.split(".")[0])):
        try:
            with urllib.request.urlopen(urllib.request.Request(f"https://api.github.com/orgs/{org}", headers=head), timeout=10) as r:
                o = json.load(r)
            if (urllib.parse.urlparse(o.get("blog") or "").netloc or o.get("blog") or "").removeprefix("www.").rstrip("/").endswith(domain):
                return o["avatar_url"] + "&s=128"
        except Exception: pass
    return None


def wikidata_logo(company):
    """Official logo (often a wide wordmark) from Wikidata/Commons, rendered as PNG. -> {"u", "w", "h"} or None."""
    head = {"User-Agent": "ee-jobscan/1.0 (personal internship alerts)"}
    get = lambda u: urllib.request.urlopen(urllib.request.Request(u, headers=head), timeout=15)
    try:
        hits = json.load(get("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&type=item&limit=3&search="
                             + urllib.parse.quote(company)))["search"]
        for h in hits:
            if not re.search(r"compan|corporat|manufactur|develop|firm|business|laborator|utility|contractor|provider|maker|agency",
                             h.get("description", ""), re.I): continue
            claims = json.load(get(f"https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=claims&ids={h['id']}"))
            logo_file = claims["entities"][h["id"]]["claims"].get("P154", [{}])[0].get("mainsnak", {}).get("datavalue", {}).get("value")
            if not logo_file: continue
            with get("https://commons.wikimedia.org/wiki/Special:FilePath/" + urllib.parse.quote(logo_file) + "?width=200") as r:
                png, final = r.read(), r.url
            if png[:4] == b"\x89PNG": return {"u": final, "w": _png_size(png), "h": int.from_bytes(png[20:24], "big")}
    except Exception: pass
    return None




BLURBS = HERE / "blurbs.json"  # company -> 1-2 sentences on what it does ("" = none found yet); looked up once per company
COMPANY_WORDS = re.compile(r"compan|corporat|manufactur|develop|firm|business|laborator|utility|contractor|provider|maker|agency|"
                           r"startup|conglomerate|operator|supplier|producer|designer|subsidiary", re.I)


def sentences(text, cap=240):
    """First sentence, plus the second if both fit in cap characters. Doesn't split on "Inc." or "U.S." mid-sentence."""
    parts = re.split(r"(?<=[a-z0-9)][.!?])\s+(?=[A-Z])", re.sub(r"\s+", " ", text).strip())
    return parts[0] + (" " + parts[1] if len(parts) > 1 and len(parts[0]) + len(parts[1]) < cap else "")


def wiki_blurb(company):
    """Wikipedia's summary, only if Wikipedia says the page is about a company (not "Inversion", the math term)."""
    clean = re.sub(r"\(.*?\)|\b(?:Inc|LLC|Corp|Corporation|Co)\b\.?|,", "", company).strip()
    for name in dict.fromkeys((company, clean, f"{clean} (company)", " ".join(clean.split()[:2]))):
        try:
            req = urllib.request.Request("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(name.replace(" ", "_")),
                                         headers={"User-Agent": "ohmward/1.0 (EE job board)"})
            with urllib.request.urlopen(req, timeout=10) as r: d = json.load(r)
            if d.get("type") == "standard" and COMPANY_WORDS.search(d.get("description", "")): return sentences(d["extract"])
        except Exception: pass
    return ""


def blurb_from_posting(company, text):
    """The posting's own sentence about the company: "Micron Technology is a world leader in memory…"."""
    first = company.split()[0] if len(company.split()[0]) > 3 else company
    m = re.search(rf"\b{re.escape(first)}\b[^.]{{0,60}}?\b(?:is|are)\s+(?:a|an|the|one|building|creating|developing|on a mission|"
                  rf"reimagining|transforming|redefining|pioneering)\b[^\n]{{30,500}}", text)
    return sentences(m[0]) if m else ""


def when(p):
    """Exact time in PT, never "2h ago": an email read hours later must not still claim the role just dropped."""
    if (p.hour, p.minute, p.second) == (0, 0, 0): return f"{p:%b %-d}"  # the site gave a date only, no time of day
    return f"{p.astimezone(PACIFIC):%b %-d, %-I:%M %p} PT"


# ---------- describing a role for the site ----------
def place(loc):
    """'US-VA-Manassas, United States of America' -> 'Manassas, VA'."""
    loc = re.sub(r"(?i),? United States(?: of America)?|, USA?$|, US$", "", loc)
    loc = re.sub(r"^US-([A-Z]{2})-(.+)$", r"\2, \1", loc)
    if loc[:1].islower(): loc = re.sub(r"\b[a-z]", lambda m: m[0].upper(), loc)
    return loc if len(loc) < 60 else loc[:57] + "…"


def states(loc):
    """Two-letter codes of the US states a role is in (foreign sites of a multi-site role are skipped)."""
    out = set()
    for part in loc.split(";"):
        if not us(part): continue
        part = re.sub(r"Washington,? D\.?\s?C\.?", "DC", part)
        out |= {STATE_ABBR[s] for s in re.findall(rf"\b({STATES})\b", part)}
        out |= set(re.findall(rf"(?:^|[,\s-])({ABBR})(?=$|[,\s\d-])", part))
    return sorted(out)


def work(loc, title, text):
    head = f"{loc} {title}"
    if re.search(r"hybrid", head, re.I): return "Hybrid"
    if re.search(r"\bremote\b", head, re.I): return "Remote"
    if re.search(r"\bhybrid (?:work|schedule|role|position|model)", text, re.I): return "Hybrid"
    if re.search(r"(?:fully|100%) remote|remote[- ]first", text, re.I): return "Remote"
    return "On-site"


def term(k, title, text):
    """'Summer 2027' for internships/co-ops, 'Class of 2027' for new grad; None if the posting doesn't say."""
    if k == "New Grad":
        y = re.search(r"\b(20[23]\d)\b", title) or re.search(r"graduat\w*[^.]{0,60}?\b(20[23]\d)\b", text, re.I)
        return y and f"Class of {y[1]}"
    m = SEASON.search(title) or SEASON.search(text)
    return m and f"{m[1].title().replace('Autumn', 'Fall')} 20{m[2][-2:]}"


def sectors(title, text):
    """EE areas the title names; if it names none, the one area the description mentions most (at least twice)."""
    if named := [k for k, rx in SECTORS.items() if rx.search(title)]: return named
    n, k = max((len(rx.findall(text)), k) for k, rx in SECTORS.items())
    return [k] if n >= 2 else []


def hourly(pay):
    """Low end of a pay range in $/hr: '$94,000 – $125,000' -> 45, '$25 - $30/hr' -> 25, '$5k/month' -> 29."""
    nums = [float(n.replace(",", "")) * (1000 if k else 1) for n, k in re.findall(r"(\d[\d,]*(?:\.\d+)?)\s*([kK])?\b", pay or "")]
    if not nums: return None
    return round(nums[0] / 173 if re.search(r"month", pay, re.I) else nums[0] / 2080 if nums[0] > 1000 else nums[0])


def describe(u, company, title, loc, posted, pay, text, found):
    """One role as the website and the alert emails see it."""
    k, ats = kind(title), source(u)
    pay = pay and re.sub(r"(\d[\d,.]*) ?USD", r"$\1", pay).replace("$$", "$").replace(" - ", "–").replace(" – ", "–") \
        .replace(" to ", "–").replace("/Hr", "/hr").replace(" /", "/").replace(".00", "")
    if pay:  # one style for every site: "$40–$45/hr", "$90,000–$136,000/yr"
        pay = re.sub(r"\s*(?:/|per)\s*(?:hour|hr)\b", "/hr", re.sub(r"\s*(?:/|per)\s*(?:year|yr|annum)\b", "/yr", pay, flags=re.I), flags=re.I)
        if not re.search(r"/(?:hr|yr)|month", pay, re.I): pay += "/yr" if re.search(r"\d,\d{3}|\d{4}|\dk", pay, re.I) else "/hr"
    return {"url": u, "title": title, "company": company, "loc": place(loc), "states": states(loc), "work": work(loc, title, text),
            "kind": k, "term": term(k, title, text), "sectors": sectors(title, text), "ats": ats, "ease": EASE.get(ats, 2),
            "pay": pay or None, "hr": hourly(pay), "posted": posted.isoformat(timespec="minutes"), "found": found,
            "citizen": bool(CITIZEN.search(text)), "nosponsor": bool(NO_SPONSOR.search(text)),
            "skills": [label for label, rx in SKILLS if rx.search(title) or rx.search(text)], "rid": job_id(u)}


# ---------- subscribers ----------
def db(path, data=None, method="GET"):
    """Supabase REST call with the service key (scanner only; the website uses the public anon key + row security)."""
    req = urllib.request.Request(f"{SUPABASE}/rest/v1/{path}", json.dumps(data).encode() if data is not None else None, method=method,
                                 headers={"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        s = r.read()
    return json.loads(s) if s else None


def subscribers():
    if not (SUPABASE and SERVICE_KEY): return []
    return [s for s in db("subscribers?active=eq.true&select=id,email,prefs,unsub_token,last_sent") if isinstance(s["prefs"], dict)]


def favorite(p, j):
    return any(f.strip().lower() in j["company"].lower() for f in p.get("favorites") or [] if f.strip())


def term_ok(p, j):
    """Start "Summer 2027" / "Summer" / "2027" matches an internship's term; graduation "2027-05" matches a new grad "Class of 2027"."""
    if not j["term"]: return True  # the posting doesn't say: send it
    if j["kind"] == "New Grad": return not p.get("grad") or j["term"].endswith(p["grad"][:4])
    return all(x in j["term"] for x in (p.get("start") or "").split())


def has_skill(j, skill):
    """A typed skill matches if it's a whole word of one of the role's skills ("PCB" in "PCB design") or of its title.
    Whole words so "C" doesn't match "PCB" or "C++". Same rule as hasSkill in docs/common.js."""
    word = lambda a, b: re.search(r"(?<![a-z0-9+#])" + re.escape(a) + r"(?![a-z0-9+#])", b)
    s = skill.lower()
    return any(word(s, k.lower()) or word(k.lower(), s) for k in j["skills"]) or bool(word(s, j["title"].lower()))


def wants(p, j):
    """Does a subscriber with filters p want role j? Same rules as wants() in docs/index.html.
    Favorite companies skip every filter except role type and eligibility, which would make the role useless to you."""
    if p.get("kinds") and j["kind"] not in p["kinds"]: return False
    if (p.get("no_citizen") and j["citizen"]) or (p.get("need_sponsor") and j["nosponsor"]): return False
    if favorite(p, j): return True
    pay_ok = p.get("unlisted_pay", True) if j["hr"] is None else j["hr"] >= (p.get("min_pay") or 0)
    return bool((not p.get("sectors") or not j["sectors"] or set(j["sectors"]) & set(p["sectors"]))
                and (not p.get("work") or j["work"] in p["work"])
                and (not p.get("states") or j["work"] == "Remote" or not j["states"] or set(j["states"]) & set(p["states"]))
                and term_ok(p, j)
                and pay_ok and j["ease"] <= (p.get("max_ease") or 3))


def alert(jobs, sub):
    """One subscriber's email -> (subject, html). Favorite companies first, then best skill fit, then newest."""
    esc, p = html.escape, sub["prefs"]
    mine = [s for s in p.get("skills") or [] if isinstance(s, str) and s.strip()]
    jobs = sorted(sorted(jobs, key=lambda j: datetime.datetime.fromisoformat(j["posted"]), reverse=True),
                  key=lambda j: (not favorite(p, j), -sum(has_skill(j, s) for s in mine)))
    shown = jobs[:30]  # ~2.5KB a card keeps the email under Gmail's ~100KB clip
    INK, MUTED, LINE, GREEN, BLUE, AMBER, RED = "#111827", "#6b7280", "#e5e7eb", "#1f7a5c", "#1f5f8b", "#8a5a14", "#9b1c1c"
    FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

    def tag(s, bg, fg):
        return (f'<span style="display:inline-block;background:{bg};color:{fg};font:600 11px/1 {FONT};padding:4px 7px;'
                f'border-radius:999px;margin:6px 4px 0 0">{esc(s)}</span>')

    def card(j):
        tags = [tag("★ Favorite company", "#f7efdf", AMBER)] if favorite(p, j) else []
        tags.append(tag(" · ".join(filter(None, (j["kind"], j["term"]))), "#eef2f7", INK))
        if j["pay"]: tags.append(tag(j["pay"], "#d9e8f3", BLUE))
        tags.append(tag(f'{["", "Quick", "Medium", "Long"][j["ease"]]} application · {j["ats"]}', "#eef7f2" if j["ease"] == 1 else "#f3f4f6",
                        GREEN if j["ease"] == 1 else MUTED))
        if j["citizen"]: tags.append(tag("Citizenship or clearance required", "#fdecec", RED))
        if j["nosponsor"]: tags.append(tag("No visa sponsorship", "#fdecec", RED))
        fit = [s for s in mine if has_skill(j, s)]
        return f"""<tr><td style="padding:16px 0;border-top:1px solid {LINE}">
<div style="font:600 16px/1.35 {FONT};color:{INK}">{esc(j["title"])}</div>
<div style="font:14px/1.5 {FONT};color:{MUTED};padding-top:2px">{esc(j["company"])} · {esc(j["loc"] or "Location not listed")} · {esc(j["work"])}</div>
<div>{"".join(tags)}</div>
{f'<div style="font:13px/1.5 {FONT};color:{GREEN};padding-top:6px">Matches your skills: {esc(", ".join(fit))}</div>' if fit else ""}
<div style="font:12px/1.6 {FONT};color:{MUTED};padding-top:8px">Posted {esc(when(datetime.datetime.fromisoformat(j["posted"])))}
 &nbsp;<a href="{esc(j["url"])}" style="display:inline-block;background:{GREEN};color:#ffffff;font:600 13px/1 {FONT};
 text-decoration:none;padding:9px 16px;border-radius:8px">Apply</a></div></td></tr>"""

    more = len(jobs) - len(shown)
    body = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;background:#f3f4f7"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:14px">
<tr><td bgcolor="#2c2f5c" style="padding:26px 28px 22px;background:#2c2f5c;border-radius:14px 14px 0 0">
 <div style="font:800 12px/1 {FONT};letter-spacing:.1em;text-transform:uppercase;color:#a7d7c5">Ohmward Careers</div>
 <div style="font:800 24px/1.3 {FONT};color:#ffffff;padding-top:8px">{len(jobs)} new role{"s" * (len(jobs) != 1)} that match your filters</div></td></tr>
<tr><td style="padding:0 28px"><table role="presentation" width="100%" cellpadding="0" cellspacing="0">{"".join(map(card, shown))}</table></td></tr>
<tr><td style="padding:18px 28px 26px;border-top:1px solid {LINE};font:12px/1.6 {FONT};color:{MUTED}">
 {f'{more} more on <a href="{esc(SITE)}">the board</a>. ' if more else ""}Every role was open when we checked, and none is over {MAX_AGE} days old.
 Apply soon: many companies review applications as they arrive.<br><br>
 <a href="{esc(SITE)}alerts.html">Change your alerts</a><br><br>
 Don't want these emails anymore? One click stops them right away.<br>
 <a href="{esc(SITE)}alerts.html?unsub={esc(str(sub["unsub_token"]))}" style="display:inline-block;margin-top:8px;border:1px solid {LINE};color:{INK};font:600 13px/1 {FONT};
 text-decoration:none;padding:9px 16px;border-radius:8px">Unsubscribe</a></td></tr>
</table></td></tr></table></body></html>"""
    top = list(dict.fromkeys(j["company"] for j in shown))[:3]
    return f"{len(jobs)} new EE role{'s' * (len(jobs) != 1)}: {', '.join(top)}", body


def notify(subs, live, now):
    """Email each subscriber the roles found since their last alert that match their filters.
    Instant: every run. Daily: the first run after 8am PT. A favorite company's role goes out right away either way."""
    if not subs: return print("no subscribers")
    if not (SMTP_PASS and FROM): return print(f"{len(subs)} subscribers, but SMTP_PASS / MAIL_FROM aren't set: no alerts sent")
    digest = now.astimezone(PACIFIC).replace(hour=8, minute=0, second=0, microsecond=0)  # today's daily alert time
    sent = 0
    with smtplib.SMTP_SSL(SMTP_HOST, 465) as smtp:
        smtp.login(SMTP_USER, SMTP_PASS)
        for s in subs:
            try:
                p, last = s["prefs"], datetime.datetime.fromisoformat(s["last_sent"])
                pending = [j for j in live if datetime.datetime.fromisoformat(j["found"]) > last and wants(p, j)]
                due = p.get("frequency") != "daily" or any(favorite(p, j) for j in pending) or last < digest <= now
                if not (pending and due): continue
                subject, body = alert(pending, s)
                msg = MIMEText(body, "html")
                msg["Subject"], msg["From"], msg["To"] = subject, FROM, s["email"]
                msg["List-Unsubscribe"] = f"<{SITE}alerts.html?unsub={s['unsub_token']}>"  # ponytail: link only; Gmail wants one-click POST (RFC 8058) past 5k emails/day
                smtp.send_message(msg)
                db(f"subscribers?id=eq.{s['id']}", {"last_sent": now.isoformat()}, "PATCH")
                sent += 1
                time.sleep(0.6)  # ponytail: one SMTP connection at ~2 emails/s (Resend's limit); use a batch API past ~1k subscribers
            except Exception as e:  # one bad row must not stop everyone else's alert; it retries next run (last_sent unchanged)
                print("alert failed for one subscriber:", type(e).__name__, str(e)[:200], file=sys.stderr)
    print(f"alerts sent to {sent} of {len(subs)} subscribers")


def main(dry=False):
    boards, jobs = discover()
    jobs = [j for j in jobs if keep(j[2], j[3])]
    low = lambda b: tuple(x.lower() for x in b)
    priority = {low(b) for b in BUILTIN} | {low(b) for l in (COMPANIES.read_text().splitlines() if COMPANIES.exists() else []) if (b := detect(l.split("#")[0].strip()))}
    failed, names = [], json.loads(NAMES.read_text())
    # 500 boards a run, oldest-checked first: 450 from companies that had EE roles last time (every ~30 min),
    # 50 from companies with none (every ~day), so one that starts hiring is still found.
    state = json.loads(BSTATE.read_text()) if BSTATE.exists() else {}
    key, last = "|".join, lambda b: state.get("|".join(b), [""])[0]
    hot = sorted((b for b in boards if low(b) in priority or state.get(key(b), ["", False])[1]), key=last)
    cold = sorted((b for b in boards if not (low(b) in priority or state.get(key(b), ["", False])[1])), key=last)
    batch = hot[:BATCH - 50] + cold[:BATCH - len(hot[:BATCH - 50])] if state else list(boards)  # first run ever: scan everything once
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="minutes")
    with ThreadPoolExecutor(64) as ex:
        for b, res in zip(batch, ex.map(scan, batch)):
            if isinstance(res, Exception):
                if low(b) in priority: failed.append(f"{b[-1]}: {res!r:.80}")
                continue
            name = pretty(names.get("|".join(b)) or (b[1] if len(b) > 1 else b[0]))
            mine = [(u, name, t, l) for u, c, t, l in res if keep(t, l)]
            jobs += mine
            state[key(b)] = [stamp, bool(mine)]
    jobs = list({j[0]: j for j in jobs}.values())
    for f in failed: print("PRIORITY BOARD FAILED", f, file=sys.stderr)
    if dry:
        for u, c, t, l in jobs: print(f"{kind(t):10} {c} | {t} | {l}\n    {u}")
        return print(f"{len(jobs)} matches")

    now = datetime.datetime.now(datetime.timezone.utc)
    BSTATE.write_text(json.dumps(state, indent=0, sort_keys=True))
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}  # url -> when we first checked it (listed or dropped)
    live = {j["url"]: j for j in json.loads(JOBS.read_text())["jobs"]} if JOBS.exists() else {}
    subs = subscribers()
    favs = {f.strip().lower() for s in subs for f in s["prefs"].get("favorites") or [] if isinstance(f, str) and f.strip()}
    new = sorted((j for j in jobs if j[0] not in seen), key=lambda j: not any(f in j[1].lower() for f in favs))
    looked = new[:BATCH]  # ponytail: 500 new lookups a run, subscribers' favorite companies first; the rest wait 15 minutes
    urls = [j[0] for j in looked] + list(live)  # every live role is re-checked each run, so closed ones leave within 15 minutes
    with ThreadPoolExecutor(32) as ex:
        info = dict(zip(urls, ex.map(details, urls)))
    print(f"{len(batch)} of {len(boards)} boards ({len(hot)} with EE roles), {len(jobs)} matches, {len(new)} new ({len(looked)} checked), {len(live)} live", flush=True)

    for u, j in list(live.items()):
        days = (now - datetime.datetime.fromisoformat(j["posted"])).days
        if info[u][3] is None or days > MAX_AGE:  # closed, or too old to be worth applying to
            del live[u]
        elif not match(j["title"]): del live[u]  # filter rules changed since it was listed
        elif info[u][3]: j["sectors"] = sectors(j["title"], info[u][3])  # so new area rules reach roles already listed
    for u, company, title, loc in looked:
        p, pay, org, text = info[u]
        if text == "" and not POSTED.get(u): continue  # page didn't load: check again next run
        seen[u] = stamp
        posted = POSTED.get(u) or p
        if posted and posted.tzinfo is None: posted = posted.replace(tzinfo=datetime.timezone.utc)
        if (text is None or not posted or (now - posted).days > MAX_AGE  # closed, undated, or old: never listed
                or not (CORE_EE.search(title) or EE_DESC.search(text))  # generic title: the posting itself must ask for EE
                or GRAD_REQUIRED.search(text) or (kind(title) == "New Grad" and EXPERIENCE.search(text))):
            continue
        name = re.sub(r",? (?:Inc\.?|LLC\.?|Corp\.?|Corporation)$", "", (org if org and len(org) < 40 else None) or company.split(" (via")[0])
        j = describe(u, name, title, loc, posted, pay, text, stamp)
        if (j["company"], j["title"], j["loc"]) not in {(x["company"], x["title"], x["loc"]) for x in live.values()}:  # same role, two requisitions
            live[u] = j

    blurbs = json.loads(BLURBS.read_text()) if BLURBS.exists() else {}
    todo = [c for c in dict.fromkeys(j["company"] for j in live.values()) if c not in blurbs][:40]  # ponytail: 40 Wikipedia lookups a run
    with ThreadPoolExecutor(8) as ex: blurbs.update(zip(todo, ex.map(wiki_blurb, todo)))
    for u, j in live.items():  # no Wikipedia page: use the posting's own description of the company
        if not blurbs.get(j["company"]) and (info.get(u) or (0, 0, 0, ""))[3]:
            blurbs[j["company"]] = blurb_from_posting(j["company"], info[u][3])
    BLURBS.write_text(json.dumps(blurbs, indent=0, sort_keys=True))

    cache = json.loads(LOGOS.read_text()) if LOGOS.exists() else {}
    todo = list({j["company"]: j["url"] for j in live.values() if j["company"] not in cache}.items())[:60]  # ponytail: 60 new logos a run
    with ThreadPoolExecutor(16) as ex: list(ex.map(lambda kv: logo(kv[0], kv[1], cache), todo))
    LOGOS.write_text(json.dumps(cache, indent=0, sort_keys=True))
    for j in live.values(): j["logo"] = cache.get(j["company"]) or ""

    out = sorted(live.values(), key=lambda j: datetime.datetime.fromisoformat(j["posted"]), reverse=True)
    JOBS.write_text(json.dumps({"updated": stamp, "boards": len(boards), "skills": [label for label, _ in SKILLS], "jobs": out,
                                "about": {c: blurbs[c] for c in dict.fromkeys(j["company"] for j in out) if blurbs.get(c)},
                                "logos": {c: u for c, u in cache.items() if u}},  # dream-company picker: every company we have a logo for
                               separators=(",", ":")))
    cutoff = (now - datetime.timedelta(days=60)).isoformat(timespec="minutes")
    SEEN.write_text(json.dumps({u: t for u, t in seen.items() if t >= cutoff}, indent=0))
    known = [n.lower() for n in names.values()] + [j["company"].lower() for j in out]
    MISSING.write_text("".join(f"{f}\n" for f in sorted(favs) if not any(f in k for k in known)))
    (HERE / "last_run.txt").write_text(f"{stamp} {len(jobs)} matches, {len(out)} live, {len(subs)} subscribers\n")  # keeps the repo active
    notify(subs, out, now)
    if len(failed) > len(priority) // 4 or not jobs:  # make the run fail loudly (GitHub emails you) instead of quietly missing roles
        sys.exit(f"{len(failed)} of {len(priority)} priority boards failed; {len(jobs)} matches")


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        assert match("PCB Design Intern") and match("Power Systems Engineering Intern - Summer 2027")
        assert match("Design Verification Co-op") and match("FPGA Engineering Intern")
        assert not match("Senior Electrical Engineer") and not match("Marketing Intern") and match("Technical Sales Intern")
        assert not match("Software Engineer Intern") and match("Embedded Software Engineer Intern")
        assert keep("Summer 2027 Engineering Internship/Co-op", "Flexible - Any SpaceX Site")
        assert match("Spring 2027 Engineering Internship/Co-op") and not match("Spring 2027 Business Operations Internship/Co-op")
        assert not match("Hardware Masters Engineering Internships") and match("Hardware Undergrad Engineering Internships")
        assert not match("Silicon Engineering Intern, PhD, Summer 2027") and match("Silicon Engineering Intern, BS/MS, Summer 2027")
        assert not match("Spring 2027 Graduate Engineer Internship/Co-op") and match("Undergraduate Electrical Intern")
        assert not match("AI Engineering Intern") and not match("Data Engineering Intern") and match("AI Hardware Engineering Intern")
        assert not match("Income Tax Compliance Internship") and not match("Research Intern - Data Systems")
        assert match("Data Center Critical Power Intern") and not match("Supply Chain Program Management Intern")
        assert not match("Applied Research Intern, NLP - Fall 2026")
        for t in ("Manufacturing Engineering Intern", "Quality Engineering Intern", "Process Engineering Intern",
                  "Test Technician Intern", "Construction Project Engineering Intern", "Supplier Quality Intern",
                  "Production Engineering Co-op", "Industrial Engineering Intern", "Estimating Intern"):
            assert not match(t), t
        assert match("Electrical Manufacturing Engineering Intern") and match("Signal Processing Intern")
        assert match("Test Engineering Intern") and not CORE_EE.search("Test Engineering Intern")  # needs EE in its description
        assert not CORE_EE.search("Power BI Intern") and CORE_EE.search("Power Systems Intern")
        for t in ("PCB Layout Design Intern", "HIL Validation Intern", "Signal Integrity Intern", "Wire Harness Design Intern",
                  "EV Systems Intern", "Battery Systems Engineering Intern", "BMS Engineering Intern", "High Voltage Engineering Intern",
                  "Power Electronics Intern", "Transmission & Distribution Intern", "Protection and Control Intern", "Solar PV Design Intern",
                  "Data Center Power Intern", "ASIC Design Intern", "Semiconductor Test Engineering Intern", "ATE Test Engineering Intern",
                  "Antenna Design Intern", "Radar Systems Intern", "Controls & Automation Intern", "PLC Programming Intern",
                  "SCADA Intern", "MEP Electrical Design Intern", "Medical Device Electrical Intern", "Robotics Electrical Intern"):
            assert match(t) and CORE_EE.search(t), t
        nested = [{"facetParameter": "locationMainGroup", "values": [{"facetParameter": "locationCountry", "values": [{"id": WD_US, "descriptor": "United States of America"}]}]}]
        assert wd_filter(nested) == {"locationCountry": [WD_US]}
        assert wd_filter([{"facetParameter": "workerSubType", "values": [{"id": "a", "descriptor": "Regular"}, {"id": "b", "descriptor": "Intern - Regular"}]}]) == {"workerSubType": ["b"]}
        assert wd_filter([{"facetParameter": "timeType", "values": [{"id": "a", "descriptor": "Full time"}]}]) == {}
        assert not match("Project Controls Intern") and not match("Battery Cell Manufacturing Intern")
        assert PAY.search("The hourly rate for our interns is 20 USD - 71 USD.")[0] == "20 USD - 71 USD"
        assert PAY.search("pay range $94000 - $125000 plus")[0] == "$94000 - $125000" and not PAY.search("since 2019 - 2026")
        assert job_id("https://nvidia.wd5.myworkdayjobs.com/X/job/US-CA/Hardware-Intern_JR2024692") == "JR2024692"
        assert job_id("https://job-boards.greenhouse.io/spacex/jobs/8616338002") == "8616338002"
        assert job_id("https://textron.taleo.net/careersection/textron/jobdetail.ftl?job=342717") == "342717"
        assert source("https://careers-gdms.icims.com/jobs/75140/x/job") == "iCIMS" and source("https://careers.amd.com/jobs/1") == "careers.amd.com"
        n = datetime.datetime(2026, 9, 30, 12, tzinfo=datetime.timezone.utc)
        assert when(n.replace(hour=22, minute=20)) == "Sep 30, 3:20 PM PT" and when(n.replace(hour=0)) == "Sep 30"  # PT; date-only stays a date
        assert re.sub(r"(\d[\d,.]*) ?USD", r"$\1", "42,000 USD - 88,000 USD").replace(" - ", " – ") == "$42,000 – $88,000"
        assert logo("Texas Instruments", "https://edbz.fa.us2.oraclecloud.com/x", {})
        assert "lockheedmartin.com" in logo("Lockheed Martin", "https://lockheedmartin.eightfold.ai/careers/job/1", {})
        assert re.split(f"((?:{EE.pattern})\\w*)", "Systems Engineering Intern", flags=re.I)[1] == "Systems Engineering"
        assert detect("https://jobs.eu.lever.co/quantinuum/abc") == ("lever", "quantinuum", "api.eu.lever.co")
        assert detect("https://ats.rippling.com/rev-robotics/jobs/1") == ("rippling", "rev-robotics")
        assert detect("https://lifeattiktok.com/search/7602395891774802181") == TIKTOK
        assert not match("Mechanical Engineer Intern") and not match("Site Reliability Engineer Intern")
        assert keep("Electrical Engineering Intern", "US-VA-Manassas") and keep("RF Intern", "Torrance, CA")
        assert not keep("Electrical Engineering Intern", "Taiwan, Hsinchu") and not keep("RF Intern", "Toronto, ON, Canada")
        assert keep("Electrical Engineering Intern", "2 Locations") and keep("Hardware Intern", "US, CA, Santa Clara")
        assert keep("Hardware Co-op", "Seattle, WA") and keep("Hardware Co-op", "Austin, TX") and not keep("Hardware Co-op", "Toronto, ON, Canada")
        assert keep("Hardware Intern/Co-op", "Austin, TX") and keep("RF Intern", "Markham, ON, CA; Austin, TX, US")
        assert not keep("RF Intern", "Markham, ON, CA") and not keep("IT Intern", "Cork, CO, IE") and keep("RF Intern", "San Diego, CA, US")
        assert detect("https://wd5.myworkdaysite.com/en-US/recruiting/guidewire/external/job/x") == ("wd", "wd5.myworkdaysite.com", "guidewire", "external")
        assert detect("https://intel.wd1.myworkdayjobs.com/en-us/External/job/x") == ("wd", "intel.wd1.myworkdayjobs.com", "intel", "External")
        assert detect("https://hdjq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1") == ("oracle", "hdjq.fa.us2.oraclecloud.com", "CX_1")
        assert detect("https://careers.amd.com/jobs/86868?icims=1") == ("jibe", "careers.amd.com")
        assert detect("https://careers.cisco.com/global/en/job/2008430") == ("phenom", "careers.cisco.com/global/en")
        assert detect("https://careers.qorvo.com/job/Richardson-X-TX-75081/1374272000/?ats=successfactors") == ("sfrmk", "careers.qorvo.com")
        assert detect("https://www.tesla.com/careers/search/job/x-269198") is None
        assert detect("https://careers.gulfstream.com?happydance") == ("hd", "careers.gulfstream.com")
        # new grad roles, and the senior / experienced roles that only look like them
        assert kind("New Grad Electrical Engineer") == "New Grad" and match("New Grad Electrical Engineer")
        assert match("Hardware Engineer I") and match("University Graduate - Analog Design Engineer") and match("Electrical Engineer - Early Career")
        assert not match("Senior Electrical Engineer") and not match("Electrical Engineer II") and not match("Lead Hardware Engineer - Entry Level Program")
        assert not match("New Grad Software Engineer") and kind("Electrical Engineering Intern") == "Internship" and kind("RF Co-op") == "Co-op"
        assert EXPERIENCE.search("requires 3+ years of experience") and EXPERIENCE.search("5-7 years in power")
        assert not EXPERIENCE.search("0-2 years of experience") and not EXPERIENCE.search("0-3 years")
        assert sectors("Power Systems Intern", "") == ["power"] and sectors("FPGA Design Intern", "") == ["computer"]
        assert sectors("Radar Signal Processing Intern", "") == ["comms", "signal"] and sectors("Electrical Engineering Intern", "") == []
        assert sectors("Electrical Engineering Intern", "PLC and SCADA work, plus controls") == ["controls"]
        assert blurb_from_posting("Micron", "Our vision. Micron Technology is a world leader in innovating memory and storage solutions. "
            "We transform how the world uses information. Requirements: ...") == ("Micron Technology is a world leader in innovating memory and storage "
            "solutions. We transform how the world uses information.")
        assert blurb_from_posting("Acme", "Acme is required to be on site.") == "" and sentences("Micron Technology, Inc. is an American maker. It is big.") \
            == "Micron Technology, Inc. is an American maker. It is big."
        assert not match("Avionics Mechanical Engineer - Summer Internship 2027") and match("Electro-Mechanical Engineering Intern")
        assert match("Mechatronics Intern") and not match("Mechanical Engineering Intern")
        assert sectors("Audio DSP Intern", "") == ["signal"] and sectors("Camera Image Quality Intern", "") == ["signal"]
        assert "signal" in sectors("Radar Systems Engineer I", "") and sectors("Hardware Intern", "kalman filter, kalman, sensor estimation") == ["signal"]
        assert sectors("Avionics Mechanical Engineer", "power power controls controls controls fpga fpga") == ["controls"]
        assert "comms" not in sectors("Electrical Intern", "strong communication skills, communication skills")
        assert states("Austin, TX; Markham, ON, CA") == ["TX"] and states("US-VA-Manassas") == ["VA"] and states("West Virginia") == ["WV"]
        assert states("Washington, DC") == ["DC"] and states("Seattle, Washington, United States") == ["WA"] and states("3 Locations") == []
        assert work("Remote - US", "RF Intern", "") == "Remote" and work("Austin, TX", "RF Intern", "a hybrid schedule") == "Hybrid"
        assert work("Austin, TX", "RF Intern", "") == "On-site"
        assert term("Internship", "Hardware Intern - Summer 2027", "") == "Summer 2027" and term("Co-op", "RF Co-op (Fall '26)", "") == "Fall 2026"
        assert term("New Grad", "2027 New Grad - Hardware", "") == "Class of 2027" and term("Internship", "RF Intern", "") is None
        assert term("New Grad", "Hardware Engineer I", "graduating between Dec 2026 and June 2027") == "Class of 2026"
        assert hourly("$94,000 – $125,000") == 45 and hourly("$25 - $30/hr") == 25 and hourly("$5k/month") == 29 and hourly(None) is None
        assert hourly("$40k – $60k") == 19 and hourly("20 USD - 71 USD") == 20
        assert CITIZEN.search("Must be a U.S. citizen due to ITAR") and CITIZEN.search("requires an active Secret clearance")
        assert not CITIZEN.search("without regard to citizenship status")
        assert NO_SPONSOR.search("We are unable to sponsor visas") and NO_SPONSOR.search("will not provide sponsorship")
        assert NO_SPONSOR.search("Sponsorship is not available for this role") and NO_SPONSOR.search("without the need for visa sponsorship")
        assert not NO_SPONSOR.search("We sponsor H-1B visas") and EASE.get(source("https://job-boards.greenhouse.io/x/jobs/1"), 2) == 1
        assert EASE.get(source("https://nvidia.wd5.myworkdayjobs.com/X/job/1"), 2) == 3 and EASE.get(source("https://careers.amd.com/jobs/1"), 2) == 2
        assert len(STATES.split("|")) == len(ABBR.split("|")) == 51
        # subscriber filters: favorites skip location/sector/pay, never eligibility
        j = describe("https://job-boards.greenhouse.io/x/jobs/1", "Acme Power", "Power Systems Intern - Summer 2027", "Austin, TX",
                     datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc), "$25 - $30/hr", "Must be a US citizen. PCB and MATLAB.", "2026-10-01T00:00+00:00")
        assert (j["kind"], j["term"], j["states"], j["hr"], j["ease"], j["citizen"]) == ("Internship", "Summer 2027", ["TX"], 25, 1, True)
        assert j["skills"] == ["PCB design", "MATLAB / Simulink"] and j["sectors"] == ["power"] and j["pay"] == "$25–$30/hr"
        assert describe("u", "c", "RF Intern", "", datetime.datetime(2026, 1, 1), "$40.00 - $45.00 per hour", "", "")["pay"] == "$40–$45/hr"
        assert describe("u", "c", "RF Intern", "", datetime.datetime(2026, 1, 1), "$90,000 - $136,000", "", "")["pay"] == "$90,000–$136,000/yr"
        assert wants({}, j) and wants({"sectors": ["power"], "states": ["TX", "CA"], "min_pay": 20, "max_ease": 1}, j)
        assert not wants({"sectors": ["comms"]}, j) and not wants({"states": ["CA"]}, j) and not wants({"min_pay": 30}, j)
        assert not wants({"kinds": ["New Grad"]}, j) and not wants({"start": "Fall 2027"}, j) and not wants({"work": ["Remote"]}, j)
        assert wants({"states": ["CA"], "sectors": ["comms"], "favorites": ["acme"]}, j) and not wants({"favorites": ["acme"], "no_citizen": True}, j)
        assert wants({"start": "Summer 2027"}, j) and wants({"start": "Summer"}, j) and wants({"start": "2027"}, j) and not wants({"start": "Summer 2028"}, j)
        ng = {**j, "kind": "New Grad", "term": "Class of 2027"}
        assert wants({"grad": "2027-05"}, ng) and not wants({"grad": "2026-12"}, ng) and wants({"grad": "2026-12"}, {**ng, "term": None})
        assert has_skill(j, "pcb") and has_skill(j, "Power systems") and not has_skill(j, "Verilog") and not has_skill(j, "C")
        assert has_skill({**j, "skills": ["Embedded C / C++"]}, "C") and has_skill({**j, "skills": ["Embedded C / C++"]}, "C++")
        assert wants({"states": ["CA"]}, {**j, "work": "Remote"}) and wants({"min_pay": 30}, {**j, "hr": None})
        assert not wants({"min_pay": 30, "unlisted_pay": False}, {**j, "hr": None})
        subj, body = alert([j], {"prefs": {"favorites": ["Acme"], "skills": ["PCB"]}, "unsub_token": "t"})
        assert subj == "1 new EE role: Acme Power" and "★ Favorite company" in body and "Matches your skills: PCB" in body and "?unsub=t" in body
        print("ok")
    elif sys.argv[1:] == ["dry"]:  # full scan, print results, no email, no seen.json
        main(dry=True)
        b = detect(sys.argv[2]); print(b)
        for j in SCANNERS[b[0]](*b[1:]): print("KEEP" if keep(j[2], j[3]) else "    ", j[2], "|", j[3])
    else:
        main()
