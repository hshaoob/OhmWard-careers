// Shared by every page: settings, saved preferences, and the matching rules (same as wants() in jobscan.py).
const SUPABASE_URL = "";       // Supabase -> Project Settings -> API -> Project URL
const SUPABASE_ANON_KEY = "";  // the "anon public" key: safe to publish, row security guards the data

const SECTORS = {
  power: ["Power systems", "Getting electricity safely from power plants and solar farms to your outlet. Think grids, batteries, EV chargers."],
  comms: ["Communication systems", "Sending information through the air or through cables. Think Wi-Fi, 5G, satellites, antennas."],
  controls: ["Control systems", "Making machines run themselves, like cruise control or a thermostat. Think robots, factories, drones."],
  signal: ["Signal processing", "Cleaning up and making sense of sound, images and radio. Think noise-cancelling headphones, phone cameras."],
  computer: ["Computer systems", "Designing the chips and circuit boards inside computers and gadgets. Think processors, FPGAs, firmware."],
};
const ICONS = {  // 24px line icons, drawn with currentColor
  power: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  comms: '<path d="M5 10a7 7 0 0 1 14 0M8.5 10a3.5 3.5 0 0 1 7 0M12 10v11M8 21h8"/>',
  controls: '<path d="M4 6h8M16 6h4M4 12h2M10 12h10M4 18h10M18 18h2"/><circle cx="14" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="16" cy="18" r="2"/>',
  signal: '<path d="M2 12h3l2.5-6 3.5 12 3-9 2 5 1.5-2H22"/>',
  computer: '<rect x="7" y="7" width="10" height="10" rx="1.5"/><path d="M10 2v3M14 2v3M10 19v3M14 19v3M2 10h3M2 14h3M19 10h3M19 14h3"/>',
  general: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8M12 8v8"/>',
  all: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
};
const icon = k => `<svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[k]}</svg>`;
// Pages beyond the five areas: roles that fit no single area, and everything
const AREAS = {...SECTORS, general: ["General EE", "Roles that use EE broadly, like circuit design, testing or hardware. Good if you're still exploring."],
               all: ["All EE roles", "Every fresh role on the site, across all areas."]};
const inArea = (area, j) => area === "all" || (area === "general" ? !j.sectors.length : j.sectors.includes(area));
// Company logo, or its first letter when there's no logo (or the image fails)
function logo(j) {
  const u = typeof j.logo === "object" ? j.logo?.u : j.logo, letter = esc((j.company[0] || "?").toUpperCase());
  return http(u) ? `<img class="lg" src="${esc(u)}" alt="" width="58" height="58" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'lg',textContent:this.dataset.l}))" data-l="${letter}">`
                 : `<span class="lg" aria-hidden="true">${letter}</span>`;
}
const KINDS = ["Internship", "Co-op", "New Grad"], WORK = ["On-site", "Hybrid", "Remote"];
const STATES = "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split(" ");
const DEFAULT = {sectors: [], kinds: [], states: [], work: [], start: "", grad: "", favorites: [], skills: [], min_pay: 0, unlisted_pay: true,
                 max_ease: 3, no_citizen: false, need_sponsor: false, frequency: "instant"};

const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const http = u => /^https?:\/\//.test(u || "") ? u : "";
const load = (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d } catch { return d } };
const store = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)) } catch {} };

let prefs = {...DEFAULT, ...load("prefs", {})};
const savePrefs = () => store("prefs", prefs);

const favorite = (p, j) => p.favorites.some(f => f.trim() && j.company.toLowerCase().includes(f.trim().toLowerCase()));
function wants(p, j) {
  if (p.kinds.length && !p.kinds.includes(j.kind)) return false;
  if ((p.no_citizen && j.citizen) || (p.need_sponsor && j.nosponsor)) return false;
  if (favorite(p, j)) return true;  // dream companies skip every other filter
  const payOk = j.hr == null ? p.unlisted_pay : j.hr >= (p.min_pay || 0);
  const any = (a, b) => a.some(x => b.includes(x));
  return (!p.sectors.length || !j.sectors.length || any(j.sectors, p.sectors))
    && (!p.work.length || p.work.includes(j.work))
    && (!p.states.length || j.work === "Remote" || !j.states.length || any(j.states, p.states))
    && termOk(p, j)
    && payOk && j.ease <= (p.max_ease || 3);
}

// Start: "Summer 2027", "Summer" or "2027" matches an internship's term. Graduation "2027-05" matches a new grad "Class of 2027".
function termOk(p, j) {
  if (!j.term) return true;  // the posting doesn't say: show it
  if (j.kind === "New Grad") return !p.grad || j.term.endsWith(p.grad.slice(0, 4));
  return !p.start || p.start.split(" ").every(x => j.term.includes(x));
}
// A typed skill matches a role if it's a whole word of one of the role's skills ("PCB" in "PCB design") or of its title.
// Whole words so "C" doesn't match "PCB" or "C++". Same rule as has_skill in jobscan.py.
const word = (a, b) => new RegExp(`(?<![a-z0-9+#])${a.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![a-z0-9+#])`).test(b);
const hasSkill = (j, s) => { s = s.toLowerCase(); return j.skills.some(k => word(s, k.toLowerCase()) || word(k.toLowerCase(), s)) || word(s, j.title.toLowerCase()); };
const mySkills = j => prefs.skills.filter(s => hasSkill(j, s));

async function loadJobs() {
  try { return await (await fetch("jobs.json", {cache: "no-store"})).json(); }
  catch { return {jobs: [], skills: []}; }
}
function ago(iso) {
  const d = Math.floor((Date.now() - new Date(iso)) / 864e5);
  return d <= 0 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
}
function minutesAgo(iso) {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso)) / 6e4));
  return m < 1 ? "just now" : m < 90 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}
// Multi-select chip buttons bound to prefs[key]
function chips(el, key, values, onChange, label = v => v) {
  el.replaceChildren(...values.map(v => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip"; b.textContent = label(v);
    b.setAttribute("aria-pressed", prefs[key].includes(v));
    b.onclick = () => {
      prefs[key] = prefs[key].includes(v) ? prefs[key].filter(x => x !== v) : [...prefs[key], v];
      b.setAttribute("aria-pressed", prefs[key].includes(v));
      savePrefs(); onChange();
    };
    return b;
  }));
}

// ---------- page frame: header with logo, search bar and links ----------
const UI = {  // small line icons
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  pin: '<path d="M12 21s-7-6.2-7-11.5a7 7 0 0 1 14 0C19 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
  bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  grid: ICONS.all, doc: '<path d="M14 3H6v18h12V7z"/><path d="M14 3v4h4M9 12h6M9 16h6"/>',
  bolt: ICONS.power, money: '<rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/><path d="M6 9v.01M18 15v.01"/>',
  cal: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>', star: '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z"/>',
  building: '<path d="M4 21V5l8-3v19M12 8h8v13M8 9v.01M8 13v.01M8 17v.01M16 12v.01M16 16v.01"/>', ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6"/>', down: '<path d="m6 9 6 6 6-6"/>',
};
const svg = (k, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${UI[k] || ICONS[k]}</svg>`;
// The Ohmward mark: an ohm sign (Ω) whose right foot runs forward into an arrow
const MARK = `<svg class="mark" viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="mk" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#38bdf8"/>
  <stop offset="1" stop-color="#0369a1"/></linearGradient></defs><rect width="64" height="64" rx="16" fill="url(#mk)"/><g transform="translate(8 7) scale(2)" fill="none"
  stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 19.5H7v-1.8A7 7 0 1 1 15 17.7v1.8h6.5"/><path d="M18.5 16.5l3 3-3 3"/></g></svg>`;
function chrome(page) {
  const url = new URLSearchParams(window.location.search);
  document.body.insertAdjacentHTML("afterbegin", `<header class="top"><div class="top-in">
    <a class="logo" href="./" aria-label="Ohmward Careers home">${MARK}<span>Ohmward <b>Careers</b></span></a>
    <form class="bar" id="bar" role="search" action="jobs.html">
      <input type="hidden" name="area" value="${esc(url.get("area") || "all")}">
      <input type="search" name="q" id="q" placeholder="Find PCB design internships in Texas…" aria-label="Search roles" value="${esc(url.get("q") || "")}">
      <button class="go" aria-label="Search">${svg("search")}</button></form>
    <a class="btn line cta" href="alerts.html">${svg("bell")}<span>Get alerts</span></a></div></header>`);
  const rail = [["./", "Search", "search", "jobs"], ["jobs.html?area=all&saved=1", "Saved", "star", "saved"],
                ["alerts.html", "Alerts", "bell", "alerts"], ["about.html", "Why fresh", "clock", "about"]];
  document.querySelector(".shell").insertAdjacentHTML("afterbegin", `<nav class="rail" aria-label="Main">${rail.map(([href, label, ic, key]) =>
    `<a href="${href}" ${key === page ? 'aria-current="page"' : ""}>${svg(ic)}<span>${label}</span></a>`).join("")}</nav>`);
}
// One filter pop-up open at a time; clicking outside closes it
document.addEventListener("toggle", e => {
  if (e.target.open && e.target.matches("details.fp"))
    document.querySelectorAll("details.fp[open]").forEach(d => d !== e.target && (d.open = false));
}, true);
document.addEventListener("click", e => {
  if (!e.target.closest("details.fp")) document.querySelectorAll("details.fp[open]").forEach(d => d.open = false);
});
const short = iso => {  // "3h", "2d": how long ago it was posted
  const h = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 36e5));
  return h < 24 ? `${h}h` : `${Math.floor(h / 24)}d`;
};

// Skills people type, with the other ways they write them. Typing an alias saves the name on the left.
const SKILL_NAMES = {
  "C++": ["cpp", "cplusplus", "c/c++", "c++/c", "candc++"], "C": [], "C#": ["csharp"], "Python": ["py", "python3"],
  "Java": [], "JavaScript": ["js"], "SQL": [], "MATLAB": [], "Simulink": [], "LabVIEW": [], "Excel": ["msexcel", "microsoftexcel"],
  "Verilog": [], "SystemVerilog": ["sv"], "VHDL": [], "FPGA": [], "ASIC": [], "RTL": ["rtldesign"],
  "PCB design": ["pcb", "pcblayout", "pcbdesign", "circuitboarddesign"], "Altium": ["altiumdesigner"], "KiCad": [], "OrCAD": [],
  "SPICE": ["pspice"], "LTspice": [], "Cadence": ["virtuoso", "cadencevirtuoso"], "Analog": ["analogdesign", "analogcircuits"],
  "Mixed-signal": ["mixedsignal"], "Power electronics": ["powerelectronic"], "Embedded C": [], "Embedded systems": ["embedded"],
  "Firmware": [], "RF": ["radiofrequency", "rfdesign"], "Microwave": [], "Antennas": ["antenna"], "DSP": ["digitalsignalprocessing", "signalprocessing"],
  "Control systems": ["controls", "controltheory", "controlsystem"], "PLC": ["plcprogramming", "ladderlogic"], "SCADA": [],
  "AutoCAD": ["acad", "autocadelectrical"], "SolidWorks": [], "ETAP": [], "PSS/E": ["psse"], "PSCAD": [], "Power systems": ["powersystem"],
  "Signal integrity": [], "Linux": [], "Git": ["github"], "SPI": [], "I2C": ["i²c", "iic"], "UART": [], "CAN": ["canbus"],
  "Arduino": [], "Raspberry Pi": ["rpi", "raspberrypi"], "Oscilloscope": ["scope", "oscilloscopes"], "Soldering": ["solder"],
  "Robotics": [], "ROS": [], "Machine learning": ["ml"],
};
const norm = v => v.toLowerCase().replace(/[\s._-]/g, "");
const SKILL_KEYS = Object.entries(SKILL_NAMES).flatMap(([name, aka]) => [[norm(name), name], ...aka.map(a => [a, name])]);
// Edits to turn a into b (a swapped pair of letters counts as one)
function edits(a, b) {
  const d = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) d[0][j] = j;
  for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++) {
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] !== b[j - 1]));
    if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d[i][j] = Math.min(d[i][j], d[i - 2][j - 2] + 1);
  }
  return d[a.length][b.length];
}
// "c/c++" -> save "C++". "pytoon" -> ask "Did you mean Python?". Anything else is saved as typed.
function fixSkill(v) {
  const n = norm(v), hit = SKILL_KEYS.find(([k]) => k === n);
  if (hit) return { value: hit[1] };
  const ok = n.length <= 3 ? 0 : n.length <= 6 ? 1 : 2;  // short names must be exact: "R" is not "C"
  const [d, name] = SKILL_KEYS.map(([k, name]) => [edits(n, k), name]).sort((a, b) => a[0] - b[0])[0];
  return d <= ok ? { value: v, ask: name } : { value: v };
}

// Every company we know a logo for (dream-company picker): name -> logo
const companyLogos = d => Object.assign({}, d.logos, ...d.jobs.map(j => ({ [j.company]: j.logo })));

// Type-and-add list bound to prefs[key] (skills, dream companies): Enter or "Add" adds one, × removes it. Returns a redraw function.
// logos ({name: logo}) shows a dropdown of matching names with logos as you type. fix(v) can rename what was typed ({value}) or ask first ({value, ask}).
function tagInput(box, key, logos, onChange, placeholder, fix) {
  box.innerHTML = `<form class="row combo"><input class="field" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}" autocomplete="off"
      ${logos ? `role="combobox" aria-expanded="false" aria-controls="${key}-menu" aria-autocomplete="list"` : ""} style="flex:1 1 180px">
    <button class="btn quiet" type="submit">Add</button>${logos ? `<ul class="menu" id="${key}-menu" role="listbox" hidden></ul>` : ""}</form>
    <p class="didyou" hidden></p><div class="chips" style="margin-top:8px"></div>`;
  const list = box.querySelector(".chips"), input = box.querySelector("input"), ask = box.querySelector(".didyou"), menu = box.querySelector(".menu");
  const names = Object.keys(logos || {});
  const mark = v => { const n = names.find(x => x.toLowerCase() === v.toLowerCase()); return n ? logo({ company: n, logo: logos[n] }) : ""; };
  const draw = () => list.replaceChildren(...prefs[key].map(v => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "chip rm"; b.innerHTML = mark(v) + esc(v); b.setAttribute("aria-label", `Remove ${v}`);
    b.onclick = e => { e.stopPropagation(); prefs[key] = prefs[key].filter(x => x !== v); savePrefs(); draw(); onChange(); };
    return b;
  }));
  let hits = [], pick = -1;
  const open = () => {
    if (!menu) return;
    const q = input.value.trim().toLowerCase();
    hits = q ? names.filter(n => n.toLowerCase().includes(q))
      .sort((a, b) => !a.toLowerCase().startsWith(q) - !b.toLowerCase().startsWith(q) || a.localeCompare(b)).slice(0, 8) : [];
    menu.innerHTML = hits.map((n, i) => `<li role="option" id="${key}-o${i}" aria-selected="${i === pick}" data-i="${i}">${mark(n)}<span>${esc(n)}</span></li>`).join("");
    menu.hidden = !hits.length;
    input.setAttribute("aria-expanded", String(!!hits.length));
    pick >= 0 ? input.setAttribute("aria-activedescendant", `${key}-o${pick}`) : input.removeAttribute("aria-activedescendant");
  };
  const add = v => {
    ask.hidden = true; input.value = ""; pick = -1; open(); input.focus();
    if (!prefs[key].some(x => x.toLowerCase() === v.toLowerCase())) { prefs[key].push(v); savePrefs(); draw(); onChange(); }
  };
  if (menu) {
    input.oninput = () => { pick = -1; open(); };
    input.onkeydown = e => {
      if (menu.hidden) return;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); pick = (pick + (e.key === "ArrowDown" ? 1 : hits.length - 1 + (pick < 0))) % hits.length; open(); }
      else if (e.key === "Enter" && pick >= 0) { e.preventDefault(); add(hits[pick]); }
      else if (e.key === "Escape") { menu.hidden = true; input.setAttribute("aria-expanded", "false"); }
    };
    input.onblur = () => setTimeout(() => { menu.hidden = true; input.setAttribute("aria-expanded", "false"); }, 150);
    menu.onmousedown = e => e.preventDefault();  // keep focus in the box
    menu.onclick = e => { const li = e.target.closest("li"); if (li) { e.stopPropagation(); add(hits[li.dataset.i]); } };
  }
  box.querySelector("form").onsubmit = e => {
    e.preventDefault();
    const v = input.value.trim();
    if (!v) return;
    const r = fix ? fix(v) : { value: names.find(n => n.toLowerCase() === v.toLowerCase()) || v };
    if (!r.ask) return add(r.value);
    ask.innerHTML = `Did you mean <b>${esc(r.ask)}</b>? <button type="button" class="btn quiet">Yes</button> <button type="button" class="btn quiet">No, keep "${esc(v)}"</button>`;
    const [yes, no] = ask.querySelectorAll("button");
    yes.onclick = e => { e.stopPropagation(); add(r.ask); };
    no.onclick = e => { e.stopPropagation(); add(v); };
    ask.hidden = false;
  };
  draw();
  return draw;
}
// "I want to start [season] [year]" and "I graduate [month] [year]", bound to prefs.start / prefs.grad
function whenPicker(el, onChange) {
  const y = new Date().getFullYear(), years = [y, y + 1, y + 2, y + 3].map(String);
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const [ss = "", sy = ""] = /\d/.test(prefs.start) && !prefs.start.includes(" ") ? ["", prefs.start] : prefs.start.split(" ");
  const [gy = "", gm = ""] = prefs.grad.split("-");
  const sel = (k, any, opts, cur) => `<select class="field" data-k="${k}" aria-label="${any}"><option value="">${any}</option>${opts.map(([v, l]) =>
    `<option value="${v}" ${v === cur ? "selected" : ""}>${l}</option>`).join("")}</select>`;
  el.innerHTML = `<div class="when-row"><span>I want to start</span>${sel("ss", "Any season", ["Spring", "Summer", "Fall", "Winter"].map(v => [v, v]), ss)}
      ${sel("sy", "Any year", years.map(v => [v, v]), sy)}</div>
    <div class="when-row"><span>I graduate in</span>${sel("gm", "Month", months.map((m, i) => [String(i + 1).padStart(2, "0"), m]), gm)}
      ${sel("gy", "Year", years.map(v => [v, v]), gy)}</div>`;
  el.onchange = () => {
    const v = k => el.querySelector(`[data-k=${k}]`).value;
    prefs.start = [v("ss"), v("sy")].filter(Boolean).join(" ");
    prefs.grad = v("gy") ? `${v("gy")}-${v("gm") || "05"}` : "";
    savePrefs(); onChange();
  };
}
