"use strict";

const TOKEN = document.querySelector('meta[name="sessioner-token"]').content;

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const reduceMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

let state = null;
let stateVersion = 0; // a changed generation makes pending polls obsolete
let receivedAt = 0; // when the current state arrived, so ages and countdowns can keep running
let view = null; // {kind: "home" | "account" | "sessions" | "switching", number?}
let busy = false;
let stopped = false;
let toastTimer = 0;
let draftName = null; // what the person has typed in the setup name field, kept across re-renders
let formError = null;
let focusId = null; // an input to focus after the next render
let renaming = null; // slot being renamed on its page
let renameDraft = "";
let renameError = null;
let justConnected = null; // slot whose lamp should flash after a switch
let hold = null; // slot the plug is parked on while a switch is being confirmed
let aim = null; // slot chosen with the arrow keys on the plug, before Enter
let drag = null; // {x, y, over, moved} while the plug is being dragged
let dirty = false; // a render was skipped because the plug was in someone's hand
let tokens = null; // token report, loaded only for the pages that show it
let tokensAt = 0;
let tokensLoading = false;
let tokensError = null;
let tokenVersion = 0; // discard an in-flight count if privacy is enabled before it returns
let sessionFilter = "live";
let sessionsShown = 40;
let activityShown = 8;
let soundOn = false;
try { soundOn = localStorage.getItem("sessioner-sound") === "on"; } catch { /* storage can be blocked */ }
let audio = null;

class Offline extends Error {}

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "style") for (const [name, v] of Object.entries(value)) node.style.setProperty(name, v); // CSSOM: the CSP forbids style attributes
    else if (key.startsWith("on")) { if (typeof value === "function") node.addEventListener(key.slice(2), value); }
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat(3)) node.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
  return node;
}

function svgNode(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
  node.append(...kids);
  return node;
}

async function call(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "X-Sessioner-Token": TOKEN, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Offline(state?.desktop?.running
      ? "Lost contact with Sessioner. Open the Sessioner desktop shortcut to reopen it."
      : "Lost contact with Sessioner. Run it again to reopen this page.");
  }
  const data = await response.json().catch(() => ({ error: "Sessioner sent an answer this page could not read." }));
  if (!response.ok) throw new Error(data.error || "That did not work.");
  return data;
}

function toast(message, bad) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("bad", !!bad);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), bad ? 6000 : 3600);
}

function goOffline(message) {
  document.body.classList.add("offline-mode");
  const bar = $("offline");
  bar.textContent = message;
  bar.hidden = false;
}

/* ---------- Time and numbers ---------- */

const pad = (n) => String(n).padStart(2, "0");

// "42s", "12m 04s", "2h 03m", "3d 4h": elapsed time and ages.
function duration(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${pad(m)}m`;
  if (m) return `${m}m ${pad(sec)}s`;
  return `${sec}s`;
}

// Countdowns drop the seconds once the wait is long enough that they stop mattering.
function countdown(ms) {
  const s = Math.floor(ms / 1000);
  if (s <= 0) return "now";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${pad(m)}m`;
  return s < 600 ? `${m}m ${pad(s % 60)}s` : `${m}m`;
}

function when(iso) {
  const date = new Date(iso);
  const now = new Date();
  const today = date.toDateString() === now.toDateString();
  const recent = Math.abs(now - date) < 6 * 86400000;
  return new Intl.DateTimeFormat(undefined, today
    ? { hour: "numeric", minute: "2-digit" }
    : recent ? { weekday: "short", hour: "numeric", minute: "2-digit" }
      : { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
}

// Live text: the clock below rewrites these once a second, so no polling is needed for them.
const since = (ms) => el("time", { class: "num", "data-since": String(ms) }, duration((Date.now() - ms) / 1000));
const until = (iso) => el("time", { class: "num", datetime: iso, title: iso, "data-until": String(Date.parse(iso)) }, countdown(Date.parse(iso) - Date.now()));

function tickClocks() {
  const now = Date.now();
  for (const node of document.querySelectorAll("[data-since]")) node.textContent = duration((now - Number(node.dataset.since)) / 1000);
  for (const node of document.querySelectorAll("[data-until]")) node.textContent = countdown(Number(node.dataset.until) - now);
}

// 950, 12.4k, 310k, 4.56M, 1.20B
function amount(n) {
  if (n < 1000) return String(n);
  if (n < 1e4) return `${(n / 1e3).toFixed(1)}k`;
  if (n < 1e6) return `${Math.round(n / 1e3)}k`;
  if (n < 1e9) return `${(n / 1e6).toFixed(n < 1e7 ? 2 : 1)}M`;
  return `${(n / 1e9).toFixed(2)}B`;
}
const exact = (n) => new Intl.NumberFormat().format(n);
const modelName = (id) => id.replace(/^claude-/, "").replace(/-\d{8}$/, "");

/* ---------- Words ---------- */

const REASONS = {
  "earliest-reset": "its limit resets soonest",
  headroom: "it has the most room left",
  "saved-order": "it is the first saved account with room",
  "all-exhausted": "every other account is at its limit",
  "reset-unknown": "every other account is at its limit and none reports a reset time",
  "no-eligible-account": "no other account has fresh usage with room",
};
const STATUS = { busy: "Busy", idle: "Idle", waiting: "Waiting", unknown: "Running", ended: "Ended" };
const WATCHER_STATES = { off: "Off", armed: "Watching", waiting: "Waiting for a reset", reset_unknown: "Reset time unknown", blocked: "Blocked", switched: "Switched" };
const LAMPS = ["Signed in", "Saved", "Backup", "Armed"];
const LIT = { "no-login": 0, "save-current": 1, "need-second": 2, "pick-active": 3, ready: 3, armed: 4 };

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const nameOf = (account) => account.name || account.email;
const accountByNumber = (number) => state.accounts.find((account) => account.number === number);
const activeAccount = () => state.accounts.find((account) => account.active);
const sameEmail = (a, b) => !!a && !!b && a.toLowerCase() === b.toLowerCase();
const switchable = (account) => !!account && !account.active && !account.disabled;
const statisticsEnabled = () => state?.preferences?.statisticsEnabled !== false;
const liveSessionCount = () => statisticsEnabled() ? state?.sessions?.count || 0 : 0;
const watcherRunning = () => typeof state?.health?.watcherRunning === "boolean" ? state.health.watcherRunning : state?.desktop?.watcherRunning === true || state?.watcher?.running === true;
const desktopCommand = () => state?.desktop?.command || "sessioner desktop";

// How an account stands as a backup, in words a person would use.
function standing(account) {
  const usage = account.usage;
  if (account.disabled) return { tone: "muted", text: "Switched off", why: "Sessioner never switches to an account that is switched off." };
  if (account.sameLoginAs) return { tone: "muted", text: "Duplicate login", why: `Same login as ${account.sameLoginAs}, so it can't be a backup for itself.` };
  switch (usage.eligibilityReason) {
    case "available": return { tone: "ok", text: "Has room", why: `${Math.round(usage.headroom)}% headroom on its busiest window.` };
    case "exhausted": return { tone: "alert", text: "At limit", why: usage.recoveryAt ? "It recovers when its last limiting window resets." : "No recovery time is known." };
    case "stale": return { tone: "warn", text: "Usage out of date", why: "Not checked in the last 5 minutes. Refresh before relying on it." };
    default: return { tone: "warn", text: "Usage unknown", why: usage.note || "Its usage can't be read right now." };
  }
}

/* ---------- Actions ---------- */

// Every action goes through here: lock the controls, call, show the new state.
async function run(path, body, { onError, after } = {}) {
  if (busy || stopped) return false;
  stateVersion += 1;
  busy = true;
  let ok = false;
  document.body.setAttribute("aria-busy", "true"); // CSS locks the controls; no re-render, so focus and typing survive
  try {
    const result = await call(path, body);
    const before = state ? activeAccount()?.number : undefined;
    if (result.state) adopt(result.state, { quiet: true });
    const now = activeAccount()?.number;
    justConnected = now !== before ? now : null;
    if (justConnected != null && before !== undefined) clunk();
    toast(after ? after(result) : result.message);
    draftName = null;
    formError = null;
    renaming = null;
    renameError = null;
    ok = true;
  } catch (error) {
    if (error instanceof Offline) goOffline(error.message);
    else if (onError) onError(error.message);
    else toast(error.message, true);
  } finally {
    stateVersion += 1;
    busy = false;
    hold = null;
    aim = null;
    document.body.removeAttribute("aria-busy");
    settleView();
    render({ force: true });
  }
  return ok;
}

// The one way to change accounts, whether it came from the plug, a button, or a key.
function switchTo(number) {
  if (!state) return;
  const target = accountByNumber(number);
  if (busy) { toast("Still finishing the last change. Try again in a moment."); return; }
  if (!target) { toast("That account is no longer saved.", true); drawCable(true); return; }
  if (target.active) { toast(`Already using ${nameOf(target)}.`); drawCable(true); return; }
  if (target.disabled) { toast(`${nameOf(target)} is switched off, so it can't be used.`, true); drawCable(true); return; }
  hold = number; // the plug sits in the new jack while Sessioner confirms the login really changed
  aim = null;
  drawCable(true);
  run("/api/switch", { target: String(number) }, {
    after: (result) => `${result.message} Retry or resume in Claude.`,
  });
}

async function copyText(text, button, label) {
  try {
    await navigator.clipboard.writeText(text);
    button.textContent = "Copied";
  } catch {
    button.textContent = "Select and copy it";
  }
  setTimeout(() => { button.textContent = label; }, 1600);
}

const copyButton = (text, { small = false } = {}) => {
  const label = `Copy ${text}`;
  return el("button", { type: "button", class: `btn quiet${small ? " small" : ""}`, onclick: (event) => copyText(text, event.currentTarget, label) }, label);
};
const refreshButton = (label = "Refresh usage", extra = "quiet small") => el("button", { type: "button", class: `btn ${extra}`, "data-key": "refresh", onclick: () => run("/api/refresh", {}) }, label);

/* ---------- Plug sound (off until the person turns it on) ---------- */

function clunk() {
  if (!soundOn) return;
  try {
    audio ||= new AudioContext();
    const now = audio.currentTime;
    const noise = audio.createBuffer(1, Math.floor(audio.sampleRate * 0.05), audio.sampleRate);
    const data = noise.getChannelData(0);
    for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length);
    const click = audio.createBufferSource(); click.buffer = noise;
    const band = audio.createBiquadFilter(); band.type = "bandpass"; band.frequency.value = 1800; band.Q.value = 1.2;
    const clickGain = audio.createGain(); clickGain.gain.setValueAtTime(0.28, now); clickGain.gain.exponentialRampToValueAtTime(0.001, now + 0.05);
    click.connect(band).connect(clickGain).connect(audio.destination); click.start(now);
    const thump = audio.createOscillator(); thump.frequency.setValueAtTime(130, now); thump.frequency.exponentialRampToValueAtTime(55, now + 0.1);
    const thumpGain = audio.createGain(); thumpGain.gain.setValueAtTime(0.32, now); thumpGain.gain.exponentialRampToValueAtTime(0.001, now + 0.11);
    thump.connect(thumpGain).connect(audio.destination); thump.start(now); thump.stop(now + 0.12);
  } catch { /* no audio available: stay silent */ }
}

function syncSoundButton() {
  const button = $("sound");
  button.setAttribute("aria-pressed", String(soundOn));
  button.textContent = soundOn ? "Sound on" : "Sound off";
}

/* ---------- Navigation ---------- */

const hashOf = (v) => (v.kind === "account" ? `#account-${v.number}` : `#${v.kind}`);

function viewFromHash() {
  const hash = location.hash.slice(1);
  if (["home", "sessions", "switching"].includes(hash)) return { kind: hash };
  const match = /^account-(\d+)$/.exec(hash);
  return match ? { kind: "account", number: Number(match[1]) } : null;
}

const validView = (v) => !!v && !!state && (v.kind !== "account" || !!accountByNumber(v.number));

// Keep the page you are on valid as the state changes underneath it (an account going away).
function settleView() {
  if (state && !validView(view)) {
    view = { kind: "home" };
    history.replaceState(null, "", hashOf(view));
  }
}

function go(next, { replace = false } = {}) {
  endDrag(false);
  view = next;
  renaming = null;
  renameError = null;
  aim = null;
  history[replace ? "replaceState" : "pushState"](null, "", hashOf(next));
  render({ force: true });
  scrollTo(0, 0);
  $("view").focus({ preventScroll: true });
}

function tabs() {
  const live = liveSessionCount();
  const items = [
    [{ kind: "home" }, "Patch bay", ""],
    [{ kind: "sessions" }, "Sessions", !statisticsEnabled() ? "private" : live ? `${live} live` : ""],
    [{ kind: "switching" }, "Switching", state.automatic.blocked ? "blocked" : state.automatic.on ? "on" : "off"],
  ];
  $("tabs").replaceChildren(...items.map(([target, label, hint]) => {
    const current = view.kind === target.kind || (target.kind === "home" && view.kind === "account");
    return el("a", { class: "tab", href: hashOf(target), "data-key": `tab-${target.kind}`, "aria-current": current ? "page" : false,
      onclick: (event) => { event.preventDefault(); go(target); } }, label, hint ? el("small", {}, hint) : "");
  }));
}

/* ---------- Token report (only fetched for the pages that show it) ---------- */

async function loadTokens({ force = false } = {}) {
  if (tokensLoading || !state || !statisticsEnabled() || stopped || document.hidden) return;
  if (!force && tokens && Date.now() - tokensAt < 30000) return;
  const version = tokenVersion;
  tokensLoading = true;
  try {
    const report = (await call("/api/tokens")).tokens;
    if (version !== tokenVersion || !statisticsEnabled()) return;
    tokens = report;
    tokensError = null;
  } catch (error) {
    if (version === tokenVersion && statisticsEnabled()) tokensError = error.message;
  } finally {
    if (version === tokenVersion) {
      tokensAt = Date.now();
      tokensLoading = false;
      if (view && view.kind !== "switching") render();
    }
  }
}

function clearTokens() {
  tokenVersion += 1;
  tokens = null;
  tokensAt = 0;
  tokensLoading = false;
  tokensError = null;
}

const wantsTokens = () => statisticsEnabled() && !!view && view.kind !== "switching";

/* ---------- Home: the patch bay ---------- */

function meter(window) {
  const filled = Math.ceil(window.pct / 10);
  const tone = window.pct >= 90 ? "high" : window.pct >= 70 ? "mid" : "";
  return el("div", { class: `meter ${tone}` },
    el("span", { class: "engrave" }, window.label),
    el("span", { class: "segs", role: "img", "aria-label": `${Math.round(window.pct)}% used` },
      Array.from({ length: 10 }, (_, i) => el("span", { class: `seg${i < filled ? " on" : ""}`, style: { "--i": i } }))),
    el("span", { class: "pct" }, `${Math.round(window.pct)}%`),
    window.resetAt && window.pct >= 70 ? el("span", { class: "pct" }, "resets in ", until(window.resetAt)) : "");
}

function jack(account) {
  const spent = account.usage.windows.some((window) => window.pct >= 100);
  const standby = account.number === state.selection.nextAccount && state.automatic.on;
  const used = statisticsEnabled() ? tokens?.accounts.find((row) => sameEmail(row.email, account.email)) : null;
  return el("li", { class: `jack${account.active ? " active" : ""}${account.disabled ? " disabled" : ""}`, "data-number": account.number },
    el("span", { class: `port${spent ? " spent" : ""}${standby ? " standby" : ""}${account.number === justConnected ? " flash" : ""}`, "data-port": account.number }),
    el("div", { class: "jack-body" },
      el("span", { class: "engrave line" }, `Line ${account.number}`),
      el("div", { class: "jack-name" },
        el("a", { href: `#account-${account.number}`, onclick: (event) => { event.preventDefault(); go({ kind: "account", number: account.number }); } }, nameOf(account)),
        account.active ? el("span", { class: "chip live" }, "In use") : "",
        spent ? el("span", { class: "chip alert" }, "At limit") : "",
        !account.active && standby ? el("span", { class: "chip next" }, "Next in line") : "",
        account.disabled ? el("span", { class: "chip muted" }, "Switched off") : "",
        account.sameLoginAs ? el("span", { class: "chip muted" }, "Duplicate") : ""),
      account.name ? el("div", { class: "jack-email" }, account.email) : "",
      account.usage.windows.length
        ? el("div", { class: "meters" }, account.usage.windows.slice(0, 2).map(meter))
        : el("div", { class: "note" }, account.usage.note),
      used && used.total ? el("div", { class: "jack-tokens" }, el("b", { title: `${exact(used.total)} tokens` }, amount(used.total)), ` tokens across ${plural(used.sessions, "session")}`) : ""),
    el("div", { class: "jack-act" },
      switchable(account) ? el("button", { type: "button", class: "btn", "data-key": `switch-${account.number}`, onclick: () => switchTo(account.number) }, "Switch to this") : "",
      el("button", { type: "button", class: "btn quiet", "data-key": `details-${account.number}`, onclick: () => go({ kind: "account", number: account.number }) }, "Details")));
}

function lamps() {
  const lit = LIT[state.stage] ?? 0;
  return el("ol", { class: "lamps", "aria-label": `Setup progress: ${lit} of 4 done` },
    LAMPS.map((label, i) => el("li", { class: `lamp${i < lit ? " lit" : ""}${i === lit ? " now" : ""}`, "aria-current": i === lit ? "step" : false, style: { "--n": i } },
      el("span", { class: "bulb" }), el("span", { class: "engrave" }, label))));
}

// One card that always says the single next thing to do until setup is finished.
function setupCard() {
  const s = state;
  const card = el("section", { class: "assist" });
  const eyebrow = (text) => el("span", { class: "engrave" }, text);
  const put = (tone, ...kids) => { card.classList.add(tone); card.append(...kids); };
  const check = (label = "Check again") => el("button", { type: "button", class: "btn", "data-key": "check", onclick: () => run("/api/refresh", {}) }, label);

  switch (s.stage) {
    case "no-claude":
      put("blocked", eyebrow("Needs attention"), el("h2", {}, "Claude Code isn't available here"),
        el("p", {}, "Sessioner switches the login Claude Code uses, so Claude Code has to be installed and reachable from the terminal that started Sessioner."),
        el("div", { class: "row" }, check()));
      break;
    case "blocked":
      put("blocked", eyebrow("Needs attention"), el("h2", {}, "Claude's settings are in the way"),
        el("p", {}, s.automatic.blocked || "Claude's settings need repair before automatic switching can run."),
        el("p", {}, "Your saved accounts are safe. Repair the settings file, then check again."),
        el("div", { class: "row" }, el("code", {}, s.settingsPath), check()));
      break;
    case "no-login":
      put("attention", lamps(), el("h2", {}, "Sign in to Claude first"),
        el("p", {}, "Sessioner can't find a Claude login yet."),
        el("ol", { class: "steps" },
          el("li", {}, el("span", {}, "Open Claude Code in a terminal.")),
          el("li", {}, el("span", {}, "Type ", el("code", {}, "/login"), " and sign in.")),
          el("li", {}, el("span", {}, "Come back here and check again."))),
        el("div", { class: "row" }, copyButton("/login"), check()));
      break;
    case "save-current": {
      const input = el("input", { type: "text", id: "name", value: draftName ?? s.suggestedName ?? "", maxlength: "64", autocomplete: "off", spellcheck: "false", "aria-label": "Account name", "aria-describedby": "name-help" });
      input.addEventListener("input", () => { draftName = input.value; });
      const save = () => run("/api/add", { name: input.value }, { onError: (message) => { formError = message; focusId = "name"; } });
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") save(); });
      put("attention", lamps(),
        el("h2", {}, s.accounts.length === 0 ? "Save the login Claude is using" : "Save this new login"),
        el("p", {}, "Claude is signed in as ", el("strong", {}, s.login), ". Give it a short name you'll recognise, like work or personal. You can rename it later."),
        el("p", { id: "name-help" }, "Letters, digits, dots, dashes and underscores."),
        el("div", { class: "row" }, input, el("button", { type: "button", class: "btn", onclick: save }, "Save login")),
        el("p", { class: "field-error", hidden: !formError, role: "alert" }, formError || ""));
      break;
    }
    case "need-second":
      put("attention", lamps(), el("h2", {}, "Add a different account"),
        el("p", {}, "Automatic switching needs a second, different login to switch to."),
        s.accounts.length && s.login ? el("p", {}, "Right now Claude is still signed in as ", el("strong", {}, s.login), ", which is already saved.") : "",
        el("ol", { class: "steps" },
          el("li", {}, el("span", {}, "In Claude Code, type ", el("code", {}, "/login"), ".")),
          el("li", {}, el("span", {}, "Sign in with a ", el("strong", {}, "different"), " account.")),
          el("li", {}, el("span", {}, "Come back here and check again."))),
        el("div", { class: "row" }, copyButton("/login"), check()));
      break;
    case "pick-active":
      put("attention", lamps(), el("h2", {}, "Choose an account that's switched on"),
        el("p", {}, "The account Claude is using is switched off. Drag the plug to one of your other accounts below, or press its Switch button."));
      break;
    default:
      put("good", lamps(), el("h2", {}, "Turn on automatic switching"),
        el("p", {}, "Two different accounts are saved. When Claude reports that your usage limit is used up, Sessioner will move the plug to the other account."),
        el("div", { class: "row" }, el("button", { type: "button", class: "btn", "data-key": "arm", disabled: !s.automatic.canEnable, onclick: () => run("/api/automatic", { enabled: true }) }, "Turn on automatic switching")));
  }
  return card;
}

function recoveryNote() {
  const s = state;
  if (s.stage !== "armed" || s.backupReady) return "";
  return el("div", { class: "recovery", role: "status" },
    el("strong", {}, "Switching is on, but no backup has room right now"),
    el("p", {}, s.selection.allExhausted
      ? ["Every other account is at its limit. The earliest recovery is in ", until(s.selection.earliestResetAt), ` (${when(s.selection.earliestResetAt)}).`]
      : s.selection.resetUnknown
        ? "Every other account is at its limit and none reports a reset time. Sessioner won't guess; check /usage in Claude."
        : ["Sessioner only switches to an account whose usage was checked in the last five minutes and still has room. ", refreshButton("Refresh usage now")]));
}

function autoCard() {
  const s = state;
  const active = activeAccount();
  const next = s.selection.nextAccount != null ? accountByNumber(s.selection.nextAccount) : null;
  let plan;
  if (s.automatic.blocked) plan = s.automatic.blocked;
  else if (!s.automatic.on) plan = s.automatic.canEnable ? "Off. Accounts only change when you move the plug yourself." : (s.automatic.reason || "Finish setup to turn this on.");
  else if (next && active) plan = `If ${nameOf(active)} runs out, the plug moves to ${nameOf(next)}, because ${REASONS[s.selection.reason] || "it has room"}.`;
  else plan = "On, but no other account has room right now, so Sessioner would not switch.";
  return el("section", { class: "auto" },
    el("h2", { id: "auto-title" }, el("span", { class: `armed-lamp${s.automatic.on ? " lit" : ""}${s.automatic.blocked ? " alarm" : ""}` }), "Automatic switching"),
    el("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(s.automatic.on), "aria-labelledby": "auto-title", "data-key": "auto",
      disabled: !s.automatic.on && !s.automatic.canEnable, onclick: () => run("/api/automatic", { enabled: !s.automatic.on }) }),
    el("p", {}, plan),
    el("p", { class: "fine" }, "Sessioner only moves the login. You retry or resume in Claude. ",
      el("a", { href: "#switching", onclick: (event) => { event.preventDefault(); go({ kind: "switching" }); } }, "How switching works")));
}

// The whole product in ten seconds: a terminal hits its limit, the cord moves, you carry on.
function demoCard() {
  const line = (cls, ...kids) => el("p", { class: `t ${cls}` }, kids);
  return el("section", { class: "demo-card" },
    el("div", { class: "demo", "aria-hidden": "true" },
      el("div", { class: "term" },
        el("div", { class: "term-bar" }, el("i", {}), el("i", {}), el("i", {}), el("span", {}, "claude")),
        line("t1", el("b", {}, "> "), "fix the failing parser test"),
        line("t2", "working…"),
        line("t3", "usage limit reached"),
        line("t4", "sessioner: work → home"),
        line("t5", el("b", {}, "> "), "continue", el("em", {}, "you retry"))),
      svgNode("svg", { class: "demo-bay", viewBox: "0 0 150 150" },
        svgNode("line", { class: "d-cord", x1: 20, y1: 75, x2: 105, y2: 75 }),
        svgNode("circle", { class: "d-socket", cx: 20, cy: 75, r: 9 }),
        svgNode("circle", { class: "d-jack d-a", cx: 95, cy: 35, r: 10 }),
        svgNode("circle", { class: "d-jack d-b", cx: 95, cy: 115, r: 10 }),
        svgNode("text", { class: "d-label", x: 112, y: 39 }, "WORK"),
        svgNode("text", { class: "d-label", x: 112, y: 119 }, "HOME"))),
    el("p", { class: "fine" }, "When Claude says the limit is used up, Sessioner moves the login to an account with room. Your conversation stays in Claude; you retry there."));
}

function sessionsCard() {
  if (!statisticsEnabled()) {
    return el("section", { class: "mini-card" },
      el("h2", {}, "Account-only mode"),
      el("p", {}, "Session and token statistics are off. Your accounts, plan limits and switching still work."),
      el("div", { class: "row" }, el("button", { type: "button", class: "btn quiet small", "data-key": "privacy-settings", onclick: () => go({ kind: "switching" }) }, "Privacy settings")));
  }
  const s = state.sessions;
  const total = tokens?.totals.total;
  return el("section", { class: "mini-card" },
    el("h2", {}, s.count ? plural(s.count, "live session") : "No live sessions"),
    el("p", {}, s.count
      ? `${s.count === 1 ? "It uses" : "They all use"} ${s.activeAccount || "the current login"}. Moving the plug changes the login for ${s.count === 1 ? "it" : "all of them"}.`
      : "Start Claude Code in a terminal and it will appear here."),
    total ? el("p", { class: "fine" }, el("b", { title: `${exact(total)} tokens` }, amount(total)), ` tokens recorded across ${plural(tokens.sessionCount, "session")}.`) : "",
    el("div", { class: "row" }, el("button", { type: "button", class: "btn quiet small", "data-key": "open-sessions", onclick: () => go({ kind: "sessions" }) }, "Open sessions and token use")));
}

function homePage() {
  const s = state;
  const active = activeAccount();
  const others = s.accounts.filter(switchable).length;
  const hint = !s.accounts.length ? "Saved accounts appear here as jacks."
    : others ? "Drag the plug onto another account to switch, or press its Switch button. Number keys work too."
      : "Save a second account and you can move the plug between them.";
  return [
    s.stage !== "armed" ? setupCard() : "",
    recoveryNote(),
    el("div", { class: "home" },
      el("section", { class: "bay", "aria-label": "Accounts" },
        el("div", { class: "bay-head" }, el("span", { class: "engrave" }, "Accounts"), el("p", { class: "hint" }, hint)),
        el("div", { class: "bay-grid", id: "bay" },
          svgNode("svg", { class: "cable", id: "cable", "aria-hidden": "true" }),
          el("div", { class: "source" }, el("span", { class: "port", id: "source-port" }),
            el("div", {}, el("span", { class: "engrave" }, "Claude Code"),
              el("div", { class: "source-login" }, active ? `signed in as ${active.email}` : s.login ? `signed in as ${s.login} (not saved yet)` : "not signed in"))),
          el("ul", { class: "jacks", id: "jacks" }, s.accounts.map(jack)),
          s.accounts.length ? "" : el("p", { class: "empty" }, "No saved accounts yet. Follow the step above to save your first one."),
          el("button", { type: "button", class: "plug-handle", id: "plug-handle", "data-key": "plug", hidden: true }))),
      el("aside", { class: "rack" }, autoCard(), sessionsCard(), demoCard())),
  ];
}

/* ---------- Cable: the signature, and the control ---------- */

function center(node, within) {
  const box = node.getBoundingClientRect();
  const origin = within.getBoundingClientRect();
  return { x: box.left - origin.left + box.width / 2, y: box.top - origin.top + box.height / 2 };
}

// Down the trough, then a rounded turn into the jack. `sway` bows the run sideways.
function route(gx, sy, ex, ey, sway) {
  const drop = ey - sy;
  if (drop < 2) return `M ${gx} ${sy} H ${ex}`;
  const r = Math.min(16, drop / 2, Math.max(ex - gx, 0));
  const stop = ey - r, third = (stop - sy) / 3;
  return `M ${gx} ${sy} C ${gx + sway} ${sy + third} ${gx + sway} ${sy + 2 * third} ${gx} ${stop} Q ${gx} ${ey} ${gx + r} ${ey} H ${ex}`;
}

function plugShape(svg, x, y) {
  svg.append(svgNode("rect", { x: x - 6, y: y - 7, width: 12, height: 14, rx: 3.5, class: "plug" }));
  svg.append(svgNode("circle", { cx: x + 5, cy: y, r: 2.6, class: "plug-tip" }));
}

function paintCable(source, plugY, plugX, sway, ghost, now, reduce) {
  const svg = $("cable");
  svg.replaceChildren();
  svg.classList.toggle("aiming", aim !== null);
  if (plugX === null) {
    // Not plugged into a saved account: the cable hangs loose below its socket.
    svg.append(svgNode("path", { d: `M ${source.x} ${source.y} V ${source.y + 36}`, class: "dangling" }));
    return;
  }
  if (ghost) svg.append(svgNode("path", { d: route(source.x + 9, source.y, ghost.x - 18, ghost.y, 0), class: "ghost" }));
  const cord = svgNode("path", { d: route(source.x, source.y, plugX - 18, plugY, sway), class: "cord" });
  svg.append(cord);
  if (!reduce && aim === null) {
    // Current: light travelling from the Claude Code socket down to the seated plug.
    const length = cord.getTotalLength();
    for (let k = 0; k < 3; k++) {
      const point = cord.getPointAtLength(length * (((now / 2200) + k / 3) % 1));
      svg.append(svgNode("circle", { cx: point.x, cy: point.y, r: 2.8, class: "pulse" }));
    }
  }
  plugShape(svg, plugX - 13, plugY);
}

// While dragging, the cord hangs loose between the socket and the hand.
function paintDrag() {
  const grid = $("bay");
  if (!grid || !drag) return;
  const source = center($("source-port"), grid);
  const svg = $("cable");
  svg.replaceChildren();
  svg.classList.remove("aiming");
  const { x, y } = drag;
  const sag = Math.max(46, Math.abs(y - source.y) * 0.5);
  svg.append(svgNode("path", { d: `M ${source.x} ${source.y} C ${source.x} ${source.y + sag} ${x - 70} ${y + 34} ${x - 6} ${y}`, class: "cord loose" }));
  plugShape(svg, x, y);
}

const cable = { y: null, swayAt: null };
let frame = 0;

function placeHandle(x, y, label) {
  const handle = $("plug-handle");
  if (!handle) return;
  handle.hidden = x === null;
  if (x === null) return;
  handle.style.setProperty("left", `${x - 13 - 22}px`);
  handle.style.setProperty("top", `${y - 22}px`);
  if (label) handle.setAttribute("aria-label", label);
}

function drawCable(animate) {
  const grid = $("bay");
  cancelAnimationFrame(frame);
  if (!state || !grid || drag) return;
  const source = center($("source-port"), grid);
  const active = activeAccount();
  const number = hold ?? aim ?? active?.number ?? null;
  const port = number === null ? null : grid.querySelector(`[data-port="${number}"]`);
  if (!port) { cable.y = null; paintCable(source, 0, null, 0, null, 0, true); placeHandle(null); return; }

  const target = center(port, grid);
  const nextNumber = state.automatic.on && hold === null && aim === null ? state.selection.nextAccount : null;
  const standby = nextNumber != null && nextNumber !== number ? grid.querySelector(`[data-port="${nextNumber}"]`) : null;
  const ghost = standby ? center(standby, grid) : null;
  const reduce = reduceMotion();
  const aimed = aim !== null ? accountByNumber(aim) : null;
  const label = aimed ? `Move the plug to ${nameOf(aimed)}? Press Enter to switch, Escape to cancel.`
    : `Patch cord${active ? `, plugged into ${nameOf(active)}` : ""}. Drag it onto another account, or use the arrow keys and Enter.`;

  // First paint unspools the cable from its socket; later moves slide it between jacks.
  const from = cable.y ?? source.y;
  const tween = animate && !reduce && Math.abs(from - target.y) >= 1;
  if (tween) cable.swayAt = performance.now() + 420 * 0.75;
  const started = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);

  const tick = (now) => {
    if (!$("bay") || drag) return;
    const t = tween ? Math.min((now - started) / 420, 1) : 1;
    cable.y = tween ? from + (target.y - from) * ease(t) : target.y;
    const elapsed = cable.swayAt === null ? -1 : (now - cable.swayAt) / 1000;
    const sway = !reduce && elapsed > 0 ? 8 * Math.exp(-elapsed / 0.45) * Math.sin(elapsed * 2 * Math.PI * 2.4) : 0;
    paintCable(source, cable.y, target.x, sway, ghost, now, reduce);
    placeHandle(target.x, cable.y, label);
    if (!document.hidden && (t < 1 || !reduce)) frame = requestAnimationFrame(tick); // keeps the current flowing
  };
  if (reduce || !animate) tick(performance.now());
  else frame = requestAnimationFrame(tick);
}

/* Dragging the plug */

function jackAt(clientX, clientY) {
  for (const node of document.querySelectorAll("#jacks .jack")) {
    const box = node.getBoundingClientRect();
    if (clientX >= box.left - 60 && clientX <= box.right && clientY >= box.top - 6 && clientY <= box.bottom + 6) return Number(node.dataset.number);
  }
  return null;
}

function markDrop(number) {
  for (const node of document.querySelectorAll("#jacks .jack")) {
    const here = Number(node.dataset.number) === number;
    const account = here ? accountByNumber(number) : null;
    node.classList.toggle("drop-ok", here && switchable(account));
    node.classList.toggle("drop-no", here && !!account && !account.active && !switchable(account));
  }
}

function moveDrag(event) {
  const grid = $("bay");
  if (!drag || !grid) return;
  const box = grid.getBoundingClientRect();
  const x = Math.max(20, Math.min(event.clientX - box.left, box.width - 8));
  const y = Math.max(8, Math.min(event.clientY - box.top, box.height + 24));
  if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 5) drag.moved = true;
  drag.x = x;
  drag.y = y;
  drag.over = jackAt(event.clientX, event.clientY);
  markDrop(drag.over);
  paintDrag();
}

function startDrag(event) {
  if (event.button !== 0 || busy || !state || drag) return;
  event.preventDefault();
  const handle = event.currentTarget;
  try { handle.setPointerCapture(event.pointerId); } catch { /* the pointer is already gone */ }
  cancelAnimationFrame(frame);
  aim = null;
  drag = { id: event.pointerId, startX: event.clientX, startY: event.clientY, x: 0, y: 0, over: null, moved: false };
  document.body.classList.add("dragging");
  moveDrag(event);
}

// `drop` is false when the drag was cancelled: Escape, a lost pointer, or leaving the page.
function endDrag(drop) {
  if (!drag) return;
  const { over, moved, y, id } = drag;
  drag = null;
  document.body.classList.remove("dragging");
  markDrop(null);
  try { $("plug-handle")?.releasePointerCapture(id); } catch { /* already released */ }
  cable.y = y; // spring back, or on into the jack, from where the hand let go
  const target = over === null ? null : accountByNumber(over);
  if (dirty) { dirty = false; render({ force: true }); }
  if (drop && moved && target && !target.active) switchTo(over); // says why if it can't
  else {
    if (drop && !moved) toast("Drag the plug onto another account to switch to it.");
    drawCable(true);
  }
}

// Arrow keys on the plug choose a jack; Enter commits.
function aimWith(event) {
  if (!state || busy) return;
  const choices = state.accounts.filter(switchable).map((account) => account.number);
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    if (!choices.length) { toast("There is no other account to switch to yet."); return; }
    const at = choices.indexOf(aim);
    const step = event.key === "ArrowDown" ? 1 : -1;
    aim = choices[at === -1 ? (step === 1 ? 0 : choices.length - 1) : (at + step + choices.length) % choices.length];
    drawCable(true);
  } else if ((event.key === "Enter" || event.key === " ") && aim !== null) {
    event.preventDefault();
    switchTo(aim);
  } else if (event.key === "Escape" && aim !== null) {
    event.stopPropagation();
    aim = null;
    drawCable(true);
  }
}

function wireHandle() {
  const handle = $("plug-handle");
  if (!handle) return;
  handle.addEventListener("pointerdown", startDrag);
  handle.addEventListener("pointermove", moveDrag);
  handle.addEventListener("pointerup", () => endDrag(true));
  handle.addEventListener("pointercancel", () => endDrag(false));
  handle.addEventListener("lostpointercapture", () => endDrag(false));
  handle.addEventListener("keydown", aimWith);
  handle.addEventListener("blur", () => { if (aim !== null && !busy) { aim = null; drawCable(true); } });
}

/* ---------- One account ---------- */

function bar(pct) {
  const filled = Math.ceil(pct / (100 / 30));
  const tone = pct >= 90 ? "high" : pct >= 70 ? "mid" : "";
  return el("div", { class: `bar ${tone}`, "aria-hidden": "true" }, Array.from({ length: 30 }, (_, i) => el("i", { class: i < filled ? "on" : "", style: { "--i": i } })));
}

function gauge(window) {
  return el("article", { class: "gauge" },
    el("span", { class: "engrave" }, window.label),
    el("div", { class: "gauge-num num" }, String(Math.round(window.pct)), el("small", {}, "% used")),
    bar(window.pct),
    el("div", { class: "gauge-reset" }, window.resetAt ? ["Resets in ", until(window.resetAt)] : el("span", { class: "muted" }, "Reset time unavailable")),
    window.resetAt ? el("div", { class: "gauge-at", title: window.resetAt }, when(window.resetAt)) : "");
}

function breakdown(row) {
  return el("dl", { class: "split" },
    [["Input", row.input], ["Output", row.output], ["Cache write", row.cacheWrite], ["Cache read", row.cacheRead]].map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", { title: `${exact(value)} tokens` }, amount(value)))));
}

function modelTable(models) {
  if (!models.length) return "";
  return el("table", { class: "models" },
    el("thead", {}, el("tr", {}, ["Model", "Replies", "Input", "Output", "Cache write", "Cache read", "Total"].map((label, i) => el("th", { class: i ? "r" : "" }, label)))),
    el("tbody", {}, models.map((row) => el("tr", {},
      el("td", {}, el("code", { title: row.model }, modelName(row.model))),
      [row.messages, row.input, row.output, row.cacheWrite, row.cacheRead].map((value) => el("td", { class: "r", title: exact(value) }, amount(value))),
      el("td", { class: "r strong", title: exact(row.total) }, amount(row.total))))));
}

const modelChips = (models) => models.map((row) => el("span", { class: "chip muted", title: `${row.model}: ${exact(row.total)} tokens in ${plural(row.messages, "reply", "replies")}` }, modelName(row.model)));

function tokensPending(what) {
  if (tokensError) return el("p", { class: "note" }, `Token use couldn't be counted: ${tokensError}`);
  return el("p", { class: "muted" }, `Counting ${what}…`);
}

function sourceNote() {
  const from = tokens?.trackedSince;
  return el("p", { class: "fine" },
    "Counted from usage fields in Claude's local conversation log files. These statistics stay on this computer. ",
    from ? `Sessioner has recorded which account was active since ${when(from)}; use from before that can't be tied to an account.`
      : "Sessioner starts recording which account is active now; earlier use can't be tied to an account.");
}

function accountTokens(account) {
  if (!statisticsEnabled()) return privacyCard();
  if (!tokens) return el("section", { class: "card" }, el("h3", {}, "Tokens used"), tokensPending("tokens"));
  const mine = tokens.accounts.find((row) => sameEmail(row.email, account.email));
  const worked = tokens.sessions.filter((session) => session.accounts.some((part) => sameEmail(part.email, account.email)));
  if (!mine || !mine.total) {
    return el("section", { class: "card" }, el("h3", {}, "Tokens used"),
      el("p", { class: "muted" }, "No use recorded for this account yet. It will appear here once Claude works while this login is active."), sourceNote());
  }
  return [
    el("section", { class: "card" },
      el("h3", {}, "Tokens used by this account"),
      el("div", { class: "token-head" },
        el("div", { class: "token-big num", title: `${exact(mine.total)} tokens` }, amount(mine.total), el("small", {}, ` tokens · ${plural(mine.sessions, "session")} · ${plural(mine.messages, "reply", "replies")}`)),
        breakdown(mine)),
      modelTable(mine.models),
      sourceNote()),
    el("section", { class: "card" },
      el("h3", {}, "Sessions this account worked on"),
      el("ul", { class: "sessions compact" }, worked.slice(0, 30).map((session) => {
        const share = session.accounts.find((part) => sameEmail(part.email, account.email));
        const others = session.accounts.filter((part) => part !== share);
        return el("li", { class: "session" },
          el("div", { class: "s-status" }, el("span", { class: `led ${session.live ? "idle" : "ended"}` }), session.live ? "Live" : "Ended"),
          el("div", { class: "s-main" }, el("div", { class: "s-project" }, session.project, " ", el("code", { title: session.sessionId }, session.shortId)), el("div", { class: "s-cwd", title: session.cwd }, session.cwd)),
          el("div", { class: "s-tokens" }, el("b", { class: "num", title: `${exact(share.total)} tokens by this account` }, amount(share.total)),
            el("small", {}, others.length ? `of ${amount(session.total)} in this session` : "all of this session")),
          el("div", { class: "s-chips" }, modelChips(session.models),
            others.map((part) => el("span", { class: "chip next", title: `${exact(part.total)} tokens` }, `also ${partName(part)} ${amount(part.total)}`))),
          el("div", { class: "s-time" }, session.lastAt ? when(session.lastAt) : "", el("small", {}, "last reply")));
      })),
      worked.length > 30 ? el("p", { class: "fine" }, `Showing the 30 most recent of ${worked.length}. The Sessions page lists them all.`) : ""),
  ];
}

// Names come from the live account list, so a rename shows up everywhere at once.
function partName(part) {
  if (part.email === null) return "not attributed";
  const account = state.accounts.find((row) => sameEmail(row.email, part.email));
  return account ? nameOf(account) : part.name || part.email;
}

function renameForm(account) {
  const input = el("input", { type: "text", id: "rename", value: renameDraft, maxlength: "64", autocomplete: "off", spellcheck: "false", "aria-label": `New name for ${nameOf(account)}` });
  input.addEventListener("input", () => { renameDraft = input.value; });
  const save = () => run("/api/rename", { target: String(account.number), name: input.value }, { onError: (message) => { renameError = message; focusId = "rename"; } });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") save();
    if (event.key === "Escape") { event.stopPropagation(); renaming = null; renameError = null; render({ force: true }); }
  });
  return el("div", { class: "rename" },
    el("div", { class: "row" }, input,
      el("button", { type: "button", class: "btn small", onclick: save }, "Save name"),
      el("button", { type: "button", class: "btn quiet small", onclick: () => { renaming = null; renameError = null; render({ force: true }); } }, "Cancel")),
    el("p", { class: "fine" }, "Letters, digits, dots, dashes and underscores. Only the name changes; the login stays the same."),
    el("p", { class: "field-error", hidden: !renameError, role: "alert" }, renameError || ""));
}

function accountPage(account) {
  const s = state;
  const stand = standing(account);
  const live = liveSessionCount();
  const active = activeAccount();
  const next = s.selection.nextAccount != null ? accountByNumber(s.selection.nextAccount) : null;
  const isNext = account.number === s.selection.nextAccount;
  const rank = s.selection.ranked.indexOf(account.number) + 1;
  const age = account.usage.ageSeconds;

  const asBackup = account.active
    ? el("section", { class: "card" },
        el("h3", {}, "In use now"),
        el("p", {}, `Claude is using this login${live ? `, shared by ${plural(live, "live session")}` : ""}.`),
        el("p", { class: "fine" }, "After Sessioner switches away from it, retry or resume the conversation in Claude."))
    : el("section", { class: "card" },
        el("h3", {}, "As a backup"),
        el("p", {}, stand.why),
        isNext ? el("p", {}, `Next in line, because ${REASONS[s.selection.reason] || "it has room"}.`) : rank ? el("p", {}, `Number ${rank} in line.`) : "");

  const ifRunsOut = el("section", { class: "card" },
    el("h3", {}, "If the active login runs out"),
    !s.automatic.on
      ? el("p", {}, "Automatic switching is off, so accounts only change when you switch them yourself.")
      : next && active
        ? el("p", {}, `If ${nameOf(active)} reaches its limit, Sessioner switches to ${nameOf(next)}, because ${REASONS[s.selection.reason] || "it has room"}.`)
        : el("p", {}, "No backup has room right now, so Sessioner would not switch."),
    live ? el("p", { class: "fine" }, `Any switch changes the login for ${plural(live, "live session")}, because the login is shared by the whole profile.`) : "");

  return [
    el("a", { class: "back", href: "#home", "data-key": "back", onclick: (event) => { event.preventDefault(); go({ kind: "home" }); } }, "← Patch bay"),
    el("header", { class: "page-head" },
      el("div", {},
        el("span", { class: "engrave" }, `Line ${account.number}`),
        el("div", { class: "titlerow" }, el("h1", {}, nameOf(account)),
          account.active ? el("span", { class: "chip live" }, "In use") : "",
          isNext ? el("span", { class: "chip next" }, "Next in line") : "",
          el("span", { class: `chip ${stand.tone}` }, stand.text)),
        account.name ? el("div", { class: "jack-email" }, account.email) : ""),
      el("div", { class: "row" },
        switchable(account) ? el("button", { type: "button", class: "btn", "data-key": "switch", onclick: () => switchTo(account.number) }, "Switch to this account") : "",
        renaming === account.number ? "" : el("button", { type: "button", class: "btn quiet", "data-key": "rename", onclick: () => { renaming = account.number; renameDraft = account.name || ""; renameError = null; focusId = "rename"; render({ force: true }); } }, "Rename"))),
    renaming === account.number ? renameForm(account) : "",
    recoveryNote(),
    el("section", { class: "card" },
      el("h3", {}, "Plan limits"),
      account.usage.windows.length
        ? el("div", { class: "gauges" }, account.usage.windows.map(gauge))
        : el("p", { class: "muted" }, account.usage.note),
      el("div", { class: "row top" },
        el("span", { class: "fine" }, age == null ? "Usage not checked yet." : ["Checked ", since(receivedAt - age * 1000), " ago."]),
        refreshButton())),
    el("div", { class: "cols" }, asBackup, ifRunsOut),
    accountTokens(account),
  ];
}

/* ---------- Sessions ---------- */

function copyIcon(text, label) {
  return el("button", { type: "button", class: "btn quiet small", "aria-label": label, "data-key": `copy-${text.slice(0, 12)}`, onclick: (event) => copyText(text, event.currentTarget, "Copy") }, "Copy");
}

function sessionRow(item) {
  const used = item.tokens;
  const started = item.startedAt ? Date.parse(item.startedAt) : null;
  return el("li", { class: `session${item.live ? " live" : ""}` },
    el("div", { class: "s-status" }, el("span", { class: `led ${item.status}` }), STATUS[item.status]),
    el("div", { class: "s-main" },
      el("div", { class: "s-project" }, item.project, " ", el("code", { title: item.sessionId }, item.shortId), item.sessionId ? copyIcon(item.sessionId, `Copy full session ID ${item.shortId}`) : ""),
      el("div", { class: "s-cwd", title: item.cwd }, item.cwd)),
    el("div", { class: "s-tokens" },
      used ? [el("b", { class: "num", title: `${exact(used.total)} tokens` }, amount(used.total)),
        el("small", { title: `Input ${exact(used.input)} · output ${exact(used.output)} · cache write ${exact(used.cacheWrite)} · cache read ${exact(used.cacheRead)}` },
          `in ${amount(used.input)} · out ${amount(used.output)} · cache ${amount(used.cacheWrite + used.cacheRead)}`)]
        : el("small", {}, tokens ? "No use recorded yet" : "Counting…")),
    el("div", { class: "s-chips" },
      used ? modelChips(used.models) : "",
      used ? used.accounts.map((part) => el("span", { class: `chip ${part.email === null ? "muted" : "acct"}`, title: `${exact(part.total)} tokens, ${plural(part.messages, "reply", "replies")}${part.email ? ` · ${part.email}` : ""}` },
        `${partName(part)} ${amount(part.total)}`)) : ""),
    el("div", { class: "s-time" },
      item.live ? (started ? ["running ", since(started)] : "running") : (used?.lastAt ? when(used.lastAt) : "Unknown"),
      el("small", {}, item.live ? `PID ${item.pid} · ${item.kind}` : "last reply")));
}

function sessionsPage() {
  if (!statisticsEnabled()) {
    return [
      el("header", { class: "page-head" }, el("div", {}, el("h1", {}, "Sessions"), el("p", { class: "page-sub" }, "Session and token statistics are off."))),
      privacyCard(),
    ];
  }
  const s = state.sessions;
  const login = s.activeAccount || "no saved login";
  const byId = new Map((tokens?.sessions || []).map((row) => [row.sessionId, row]));
  const liveIds = new Set(s.items.map((item) => item.sessionId));
  const rows = s.items.map((item) => ({ ...item, live: true, tokens: byId.get(item.sessionId) || null }));
  const ended = (tokens?.sessions || []).filter((row) => !liveIds.has(row.sessionId))
    .map((row) => ({ sessionId: row.sessionId, shortId: row.shortId, project: row.project, cwd: row.cwd, live: false, status: "ended", tokens: row }));
  const list = sessionFilter === "live" ? rows : [...rows, ...ended];
  const filter = (key, label, count) => el("button", { type: "button", class: "seg-btn", "data-key": `filter-${key}`, "aria-pressed": String(sessionFilter === key),
    onclick: () => { sessionFilter = key; sessionsShown = 40; render({ force: true }); } }, label, el("small", {}, String(count)));
  const unattributed = tokens?.accounts.find((row) => row.email === null);

  return [
    el("header", { class: "page-head" },
      el("div", {}, el("h1", {}, "Sessions"), el("p", { class: "page-sub" }, "Every Claude session on this profile, live or ended, with the tokens it used, the models, and which account paid for them.")),
      el("div", { class: "seg-group", role: "group", "aria-label": "Which sessions to show" },
        filter("live", "Live", rows.length), filter("all", "All", tokens ? rows.length + ended.length : "…"))),
    el("div", { class: "bus" },
      el("span", { class: "engrave" }, "Shared login · whole profile"),
      el("strong", {}, login),
      el("p", {}, s.count > 1
        ? `All ${s.count} live sessions use this login. Switching accounts changes it for every one of them.`
        : "Every Claude session on this profile uses this login. Switching accounts changes it for all of them.")),
    tokens
      ? el("section", { class: "card totals" },
          el("div", { class: "token-head" },
            el("div", { class: "token-big num", title: `${exact(tokens.totals.total)} tokens` }, amount(tokens.totals.total), el("small", {}, ` tokens · ${plural(tokens.sessionCount, "session")}`)),
            breakdown(tokens.totals)),
          el("div", { class: "by-account" }, tokens.accounts.filter((row) => row.total || row.saved).map((row) =>
            el(row.number ? "a" : "span", { class: "acct-total", href: row.number ? `#account-${row.number}` : false,
              onclick: row.number ? (event) => { event.preventDefault(); go({ kind: "account", number: row.number }); } : false },
              el("span", { class: "engrave" }, partName(row)), el("b", { class: "num", title: `${exact(row.total)} tokens` }, amount(row.total)),
              el("small", {}, plural(row.sessions, "session"))))),
          unattributed && unattributed.total ? el("p", { class: "fine" }, "“Not attributed” is use from before Sessioner started recording which account was active.") : "",
          sourceNote())
      : el("section", { class: "card" }, tokensPending("tokens across all sessions")),
    list.length
      ? el("ul", { class: "sessions" }, list.slice(0, sessionsShown).map(sessionRow))
      : el("section", { class: "card" }, el("p", { class: "muted" }, sessionFilter === "live"
          ? "No live Claude sessions right now. Start Claude Code in a terminal and it appears here, or choose All to see past sessions."
          : "No sessions recorded on this profile yet.")),
    list.length > sessionsShown ? el("div", { class: "row" }, el("button", { type: "button", class: "btn quiet", "data-key": "more", onclick: () => { sessionsShown += 40; render({ force: true }); } }, `Show more (${list.length - sessionsShown} left)`)) : "",
    tokens && tokens.sessionCount > tokens.sessions.length ? el("p", { class: "fine" }, `Only the ${tokens.sessions.length} most recent sessions are listed; the totals above include all ${tokens.sessionCount}.`) : "",
    s.unreadable ? el("p", { class: "note" }, `${plural(s.unreadable, "session record")} couldn't be read, so ${s.unreadable === 1 ? "one live session" : "some live sessions"} may be missing above.`) : "",
  ];
}

/* ---------- Switching ---------- */

function privacyCard() {
  return el("section", { class: "card privacy-note" },
    el("h2", {}, "Account-only privacy mode"),
    el("p", {}, "Sessioner reads account and quota information only. Session records and conversation log files are not scanned while this mode is on."),
    el("p", { class: "fine" }, "Account switching, plan limits and switch history remain available."),
    el("div", { class: "row top" }, el("button", { type: "button", class: "btn quiet", "data-key": "open-privacy", onclick: () => go({ kind: "switching" }) }, "Open privacy settings")));
}

const HEALTH_LABELS = { claude: "Claude Code", accounts: "Saved accounts", active: "Active login", automatic: "Automatic switching", backup: "Backup account", watcher: "Reset watcher" };
const HEALTH_STATES = { ok: ["ok", "Ready"], attention: ["warn", "Needs attention"], off: ["muted", "Off"], unknown: ["muted", "Unknown"] };

function commandControl(command, label, key) {
  return el("div", { class: "command-control" }, el("code", {}, command),
    el("button", { type: "button", class: "btn quiet small", "data-key": key, onclick: (event) => copyText(command, event.currentTarget, label) }, label));
}

function healthFix(check) {
  const fix = check.fix;
  if (!fix) return "";
  if (fix.command) return commandControl(fix.command, fix.label || "Copy command", `fix-${check.id}`);
  return el("button", { type: "button", class: "btn quiet small", "data-key": `fix-${check.id}`,
    onclick: () => fix.action === "setup" ? go({ kind: "home" }) : run("/api/health/fix", { check: check.id }) }, fix.label || "Fix this");
}

function healthCard() {
  const health = state.health;
  const checks = health?.checks || [];
  return el("section", { class: "card health", "aria-labelledby": "health-title" },
    el("div", { class: "section-head" }, el("h2", { id: "health-title" }, "Setup health"),
      el("span", { class: `chip ${health?.ready ? "ok" : "warn"}` }, health?.ready ? "Ready" : "Check setup")),
    el("p", { class: "fine" }, "Configuration and running status, with the next step for anything that needs attention."),
    checks.length ? el("ul", { class: "health-list" }, checks.map((check) => {
      const [tone, label] = HEALTH_STATES[check.status] || HEALTH_STATES.unknown;
      return el("li", { class: "health-check" },
        el("div", { class: "health-copy" }, el("strong", {}, HEALTH_LABELS[check.id] || "Setup check"), el("p", {}, check.message)),
        el("span", { class: `chip ${tone}` }, label), healthFix(check));
    })) : el("p", { class: "muted top" }, "Setup checks are not available yet. Refresh to check again."),
    el("p", { class: "fine" }, "An installed hook still needs to be loaded by Claude. Check /hooks in your Claude terminal."));
}

const EVENT_REASONS = {
  "earliest-reset": "Its quota resets soonest.", headroom: "It has the most room left.", "saved-order": "It is the first saved account with room.",
  "quota-exhausted": "The account reached its usage limit.", "no-candidate": "No other account has fresh usage with room.",
  "no-eligible-account": "No other account has fresh usage with room.", "all-exhausted": "Every saved backup is at its limit.",
  "reset-unknown": "A reliable reset time is unavailable.", cooldown: "The account is in a cooldown after a recent switch attempt.",
  "switch-failed": "The account switch could not be completed.", "switch-unconfirmed": "The active login did not confirm the requested switch.",
  "lock-timeout": "Another account operation was still in progress. Try again.", "login-required": "Sign in again in Claude, then save the login.",
  "authentication-required": "Sign in again in Claude, then save the login.", "refresh-failed": "Usage could not be refreshed. Check the login and retry.",
  "unknown-quota": "Usage could not be confirmed, so Sessioner did not guess.",
  "manual-selection": "You chose this account.", "switch-unverified": "The active login did not confirm the requested switch.",
  "account-busy": "Another account operation was still in progress. Try again.", "no-available-account": "No other account has fresh usage with room.",
  "no-active-account": "Save the active Claude login before switching automatically.", "active-has-headroom": "The active account still has room.",
  "usage-unavailable": "Usage could not be checked for this account.", "usage-invalid": "The reported usage could not be confirmed.",
  stale: "Usage is out of date. Refresh it before switching.", disabled: "This account is switched off.",
  "token-expired": "Claude needs to renew this login.", "no-credentials": "Sign in again in Claude, then save the login.",
  "relogin-required": "Sign in again in Claude, then save the login.", "foreign-credential": "The saved login identity could not be confirmed.",
  "credentials-unavailable": "The saved login could not be read. Check the credential store and retry.",
  "quota-recovered": "Fresh usage confirms there is room again.",
};
const EVENT_SOURCES = { manual: "Manual", hook: "Limit hook", watcher: "Reset watcher", desktop: "Tray", ui: "Dashboard" };

function eventAccount(number, fallback) {
  const account = accountByNumber(number);
  return account ? nameOf(account) : Number.isInteger(number) && number > 0 ? `Line ${number}` : fallback;
}

function eventWords(item) {
  const from = eventAccount(item.from, "The active account");
  const to = eventAccount(item.to, "A backup account");
  switch (item.kind) {
    case "quota_exhausted": return ["warn", `${from} reached its usage limit.`];
    case "candidate_selected": return ["muted", `${to} selected as the backup.`];
    case "switch_confirmed": return ["ok", item.from ? `${from} → ${to}. Switch confirmed.` : `${to}. Switch confirmed.`];
    case "switch_failed": return ["alert", item.to ? `Switch to ${to} failed.` : "The account switch failed."];
    case "all_exhausted": return ["warn", "All saved backup accounts are at their limit."];
    case "quota_available": return ["ok", `${eventAccount(item.to || item.from, "An account")} has quota available again.`];
    case "login_required": return ["warn", `${eventAccount(item.to || item.from, "An account")} needs a new login.`];
    default: return null;
  }
}

function activityCard() {
  const items = (state.activity?.items || []).filter((item) => eventWords(item));
  return el("section", { class: "card activity", "aria-labelledby": "activity-title" },
    el("div", { class: "section-head" }, el("h2", { id: "activity-title" }, "Switch history"), el("span", { class: "engrave" }, "Newest first")),
    items.length ? el("ol", { class: "activity-list" }, items.slice(0, activityShown).map((item) => {
      const [tone, words] = eventWords(item);
      const reason = typeof item.reason === "string" ? EVENT_REASONS[item.reason.replaceAll("_", "-")] : null;
      const validAt = typeof item.at === "string" && Number.isFinite(Date.parse(item.at));
      return el("li", { class: `activity-item ${tone}` },
        el("span", { class: "activity-dot", "aria-hidden": "true" }),
        el("div", { class: "activity-copy" }, el("p", {}, words), reason ? el("p", { class: "fine" }, reason) : "",
          el("div", { class: "activity-meta" },
            validAt ? el("time", { datetime: item.at, title: new Date(item.at).toLocaleString() }, when(item.at)) : "Time unavailable",
            EVENT_SOURCES[item.source] ? el("span", {}, EVENT_SOURCES[item.source]) : "")));
    })) : el("p", { class: "muted activity-empty" }, "No switches recorded yet. Confirmed switches, limits and login issues will appear here."),
    items.length > activityShown ? el("div", { class: "row top" }, el("button", { type: "button", class: "btn quiet small", "data-key": "activity-more", onclick: () => { activityShown += 12; render({ force: true }); } }, `Show more (${items.length - activityShown} left)`)) : "",
    el("p", { class: "fine" }, "History contains account slots and outcomes. It does not contain conversations, credentials or provider messages."));
}

function preferencesCard() {
  const privateMode = !statisticsEnabled();
  const notifications = state.preferences?.notificationsEnabled !== false;
  const desktop = state.desktop;
  return el("section", { class: "card preferences", "aria-labelledby": "preferences-title" },
    el("h2", { id: "preferences-title" }, "Privacy and notifications"),
    el("div", { class: "preference-row" },
      el("div", {}, el("h3", { id: "privacy-title" }, "Account-only privacy mode"),
        el("p", { id: "privacy-help" }, "Skip session records and conversation log files. Hide session and token statistics; keep accounts, quota and switching.")),
      el("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(privateMode), "aria-labelledby": "privacy-title", "aria-describedby": "privacy-help", "data-key": "privacy",
        onclick: () => run("/api/preferences", { statisticsEnabled: !statisticsEnabled() }) })),
    el("p", { class: "fine preference-state" }, privateMode ? "On · session and token statistics are disabled." : "Off · Sessioner can scan Claude's local usage records for statistics."),
    el("div", { class: "preference-row" },
      el("div", {}, el("h3", { id: "notifications-title" }, "Windows notifications"),
        el("p", { id: "notifications-help" }, "Get notified about switches, exhausted accounts, available quota and logins that need renewal.")),
      el("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(notifications), "aria-labelledby": "notifications-title", "aria-describedby": "notifications-help", "data-key": "notifications",
        onclick: () => run("/api/preferences", { notificationsEnabled: !notifications }) })),
    el("p", { class: "fine preference-state" }, !notifications ? "Off · Windows notifications are disabled." : desktop?.running ? "On · delivered by the Sessioner tray app. Windows notification settings still apply." : "On · available while Sessioner desktop is running on Windows."),
    !desktop?.running && desktop?.supported !== false ? el("div", { class: "desktop-hint" }, el("p", { class: "fine" }, "Open your Sessioner desktop shortcut to keep it running in the tray. You can also start it with:"), commandControl(desktopCommand(), "Copy desktop command", "desktop-command")) : "");
}

function watcherCard() {
  const w = state.watcher;
  const running = w.enabled && watcherRunning();
  const desktop = state.desktop;
  const label = !w.enabled ? "Off" : running ? "Running" : "Enabled · stopped";
  const command = desktop?.supported !== false ? desktopCommand() : w.command;
  return el("section", { class: "card watcher" },
    el("div", { class: "watcher-head" },
      el("div", { class: "titlerow" }, el("h3", { id: "watcher-title" }, "Reset watcher"),
        el("span", { class: `chip ${!w.enabled ? "muted" : running ? "live" : "warn"}` }, label)),
      el("button", { type: "button", class: "switch", role: "switch", "aria-checked": String(w.enabled), "aria-labelledby": "watcher-title", "data-key": "watcher", onclick: () => run("/api/watcher", { enabled: !w.enabled }) })),
    el("p", {}, "Optional, and off until you turn it on. It re-checks usage and switches the saved login when your active account is used up and another has room. You retry in Claude afterward."),
    running ? el("p", { class: "fine" }, WATCHER_STATES[w.state] || "Watching", w.nextPollAt ? [" · next check in ", until(w.nextPollAt)] : "")
      : w.enabled ? el("div", { class: "watcher-start" }, el("p", { class: "note" }, "The setting is on, but no watcher is running. Open the Sessioner desktop shortcut or start it with:"), commandControl(command, "Copy start command", "watcher-start"))
        : el("p", { class: "fine" }, "Quota monitoring is off."),
    el("p", { class: "fine" }, desktop?.running ? "The tray app runs the watcher while enabled. Closing this browser keeps the tray app running; Quit stops it." : "This switch saves the setting. Open Sessioner desktop to run it without a separate terminal. Opening this browser page alone does not start it."));
}

function switchingPage() {
  const s = state;
  const active = activeAccount();
  const next = s.selection.nextAccount != null ? accountByNumber(s.selection.nextAccount) : null;
  const hook = s.automatic.blocked ? `Blocked: ${s.automatic.blocked}` : s.automatic.on ? "Installed in Claude's user settings" : "Not installed";
  const w = s.watcher;
  const order = s.selection.ranked.map((number, i) => el("span", { class: "chip muted" }, `${i + 1} · ${nameOf(accountByNumber(number))}`));
  return [
    el("header", { class: "page-head" },
      el("div", {}, el("h1", {}, "Switching"), el("p", { class: "page-sub" }, "What happens when Claude reports a usage limit, and what Sessioner will do about it."))),
    healthCard(),
    el("ol", { class: "path", "aria-label": "What happens when Claude reports a usage limit" },
      el("li", { class: "hop" }, el("b", {}, "Claude"), "reports that a usage limit was hit"),
      el("li", { class: "hop" }, el("b", {}, "Hook"), "confirms the active login is used up"),
      el("li", { class: "hop" }, el("b", {}, "Sessioner"), "picks the best backup, and tries the next one if that fails"),
      el("li", { class: "hop" }, el("b", {}, "Sessioner"), "switches the login and verifies it"),
      el("li", { class: "hop yours" }, el("b", {}, "You, in Claude"), "retry or resume. Sessioner never does")),
    el("div", { class: "cols" },
      el("div", { class: "stack" }, autoCard(),
        el("section", { class: "card" },
          el("h3", {}, "Right now"),
          el("dl", { class: "facts" },
            el("dt", {}, "Hook"), el("dd", {}, hook),
            el("dt", {}, "Applies to"), el("dd", {}, liveSessionCount() ? `The whole Claude profile, shared by ${plural(liveSessionCount(), "live session")}` : "The whole Claude profile"),
            el("dt", {}, "Active login"), el("dd", {}, active ? active.label : (s.login ? `${s.login} (not saved)` : "None")),
            el("dt", {}, "Next candidate"), el("dd", {}, next ? `${nameOf(next)}, because ${REASONS[s.selection.reason] || "it has room"}` : `None, because ${REASONS[s.selection.reason] || "no other account is ready"}`),
            el("dt", {}, "Order tried"), el("dd", {}, order.length ? el("span", { class: "order" }, order) : "No other account is ready"),
            el("dt", {}, "Last watcher switch"), el("dd", {}, w.lastSwitchTo ? `From line ${w.lastSwitchFrom} to line ${w.lastSwitchTo}` : "None yet")),
          el("div", { class: "row top" },
            refreshButton("Refresh usage", "quiet"),
            next ? el("button", { type: "button", class: "btn", "data-key": "switch-next", onclick: () => switchTo(next.number) }, `Switch to ${nameOf(next)} now`) : ""))),
      el("div", { class: "stack" }, watcherCard(), demoCard(),
        el("section", { class: "card unverified" },
          el("h3", {}, "Not yet confirmed"),
          el("p", {}, "Whether a Claude conversation that is already running picks up the new login on its own. If it doesn't, retry or resume it in Claude. In Claude, type /hooks to check that the hook is loaded.")))),
    el("div", { class: "cols" }, activityCard(), preferencesCard()),
  ];
}

/* ---------- Rendering and polling ---------- */

let signature = null;
const stable = (value) => JSON.stringify(value, (key, v) => (key === "ageSeconds" || key === "elapsedSeconds" ? undefined : v));

function render({ force = false } = {}) {
  if (!state || !view) return;
  if (drag) { dirty = true; return; } // never pull the bay out from under someone's hand
  const next = stable([view, formError, renaming, renameError, sessionFilter, sessionsShown, activityShown, tokensAt, tokensError, state]);
  if (!force && next === signature) return;
  signature = next;
  const key = document.activeElement?.dataset?.key;
  tabs();
  const nodes = view.kind === "home" ? homePage()
    : view.kind === "account" ? accountPage(accountByNumber(view.number))
      : view.kind === "sessions" ? sessionsPage() : switchingPage();
  cancelAnimationFrame(frame);
  $("view").replaceChildren(el("div", { class: `page page-${view.kind}` }, nodes));
  justConnected = null; // flash once, not on every later render
  if (view.kind === "home") { wireHandle(); drawCable(true); }
  if (key) document.querySelector(`[data-key="${key}"]`)?.focus({ preventScroll: true });
  if (focusId) { const node = $(focusId); focusId = null; node?.focus(); node?.select?.(); }
  const active = activeAccount();
  document.title = active ? `Sessioner · ${nameOf(active)}` : "Sessioner patch bay";
  $("privacy-footnote").textContent = statisticsEnabled()
    ? "Sessioner only changes which saved login Claude uses. Your conversation stays in Claude. Token statistics use usage fields in Claude's local conversation log files and stay on this computer."
    : "Account-only mode: Sessioner reads account and quota information. Session records and conversation log files are not scanned. Your conversation stays in Claude.";
  $("quit").title = state.desktop?.running ? "Quit the Sessioner tray app, dashboard and reset watcher" : "Stop this Sessioner dashboard";
  if (wantsTokens()) loadTokens();
}

function adopt(next, { quiet = false } = {}) {
  stateVersion += 1;
  const hadStatistics = statisticsEnabled();
  state = next;
  if (!statisticsEnabled()) {
    if (hadStatistics || tokens || tokensLoading || tokensError) clearTokens();
    state = { ...state, sessions: { ...state.sessions, items: [], count: 0, unreadable: 0 } };
  }
  receivedAt = Date.now();
  if (quiet) return;
  if (!view) {
    const fromHash = viewFromHash();
    view = validView(fromHash) ? fromHash : { kind: "home" };
    history.replaceState(null, "", hashOf(view));
    render({ force: true });
    return;
  }
  settleView();
  render();
}

let failures = 0;
async function poll() {
  if (busy || stopped || document.hidden || !state) return;
  const version = stateVersion;
  try {
    const result = await call("/api/state");
    if (busy || stopped || version !== stateVersion) return;
    failures = 0;
    adopt(result.state);
    if (wantsTokens()) loadTokens();
  } catch (error) {
    if (busy || stopped || version !== stateVersion) return;
    if (error instanceof Offline && ++failures >= 2) goOffline(error.message);
  }
}

/* ---------- Boot ---------- */

$("refresh").addEventListener("click", async () => { if (await run("/api/refresh", {})) loadTokens({ force: true }); });
$("brand").addEventListener("click", (event) => { event.preventDefault(); if (state) go({ kind: "home" }); });
$("quit").addEventListener("click", async () => {
  if (busy || stopped) return;
  try {
    await call("/api/quit", {});
    stopped = true;
    document.querySelector(".frame").inert = true;
    $("stopped-title").textContent = state?.desktop?.running ? "Sessioner tray app stopped" : "Sessioner stopped";
    $("stopped-message").replaceChildren(...(state?.desktop?.running
      ? ["The tray app, dashboard and reset watcher are stopped. Your saved accounts are kept. Open the Sessioner desktop shortcut to start again."]
      : ["Your saved accounts are kept. You can close this tab and run ", el("code", {}, "sessioner ui"), " to open the dashboard again."]));
    $("stopped").hidden = false;
    $("stopped-title").focus();
  } catch (error) {
    toast(error.message, true);
  }
});
addEventListener("resize", () => { if (drag) endDrag(false); else drawCable(false); });
addEventListener("blur", () => endDrag(false));
addEventListener("popstate", () => {
  const next = viewFromHash() || { kind: "home" };
  if (state && validView(next)) { endDrag(false); view = next; renaming = null; aim = null; render({ force: true }); }
});
addEventListener("keydown", (event) => {
  if (event.key === "Escape" && drag) { endDrag(false); return; }
  if (!state || busy || drag || event.ctrlKey || event.metaKey || event.altKey) return;
  if (event.target instanceof Element && event.target.closest("input, textarea")) return; // never hijack typing
  if (view.kind !== "home") return;
  const line = state.accounts.find((account) => String(account.number) === event.key);
  if (line) switchTo(line.number);
});
$("sound").addEventListener("click", () => {
  soundOn = !soundOn;
  try { localStorage.setItem("sessioner-sound", soundOn ? "on" : "off"); } catch { /* remembered only when storage allows */ }
  syncSoundButton();
  clunk();
});
document.addEventListener("visibilitychange", () => { if (document.hidden) endDrag(false); else { poll(); drawCable(false); } });
syncSoundButton();
// First paint powers the panel on: lamps and LEDs light in sequence, then the class is removed.
document.body.classList.add("boot");
setTimeout(() => document.body.classList.remove("boot"), 2600);
setInterval(tickClocks, 1000);
setInterval(poll, 10000);

call("/api/state")
  .then((result) => adopt(result.state))
  .catch((error) => { $("view").replaceChildren(el("section", { class: "card" }, el("h3", {}, "Sessioner can't be reached"), el("p", {}, error.message))); });
