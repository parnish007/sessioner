"use strict";

const TOKEN = document.querySelector('meta[name="sessioner-token"]').content;
// The one-time link carries the token; drop it from the address bar and history.
history.replaceState(null, "", "/");

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
let state = null;
let busy = false;
let cableY = null; // vertical position the cable currently reaches, for animation
let toastTimer = 0;
let draftName = null; // what the person has typed in the name field, kept across re-renders
let formError = null;
let focusName = false;
let soundOn = false;
try { soundOn = localStorage.getItem("sessioner-sound") === "on"; } catch { /* storage can be blocked */ }
let audio = null;
let justConnected = null; // slot whose lamp should flash after a switch

function el(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "style") for (const [name, v] of Object.entries(value)) node.style.setProperty(name, v); // CSSOM: the CSP forbids style attributes
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (value !== false && value != null) node.setAttribute(key, value === true ? "" : value);
  }
  for (const kid of kids.flat()) node.append(kid instanceof Node ? kid : document.createTextNode(kid ?? ""));
  return node;
}

async function call(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Sessioner-Token": TOKEN, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({ error: "Sessioner did not answer. Is it still running in the terminal?" }));
  if (!response.ok) throw new Error(data.error || "That did not work.");
  return data;
}

function toast(message, bad) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("bad", !!bad);
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), bad ? 6000 : 3200);
}

// Every action goes through here: lock the controls, call, show the new state.
async function run(path, body, { onError } = {}) {
  if (busy) return;
  busy = true;
  document.body.setAttribute("aria-busy", "true"); // CSS locks the controls; no re-render, so focus and typing survive
  try {
    const result = await call(path, body);
    const before = state?.accounts.find((account) => account.active)?.number;
    if (result.state) state = result.state;
    const after = state?.accounts.find((account) => account.active)?.number;
    justConnected = after !== before ? after : null;
    if (justConnected !== null && before !== undefined) clunk();
    if (result.message) toast(result.message);
    draftName = null;
    formError = null;
  } catch (error) {
    if (onError) onError(error.message);
    else toast(error.message, true);
  } finally {
    busy = false;
    document.body.removeAttribute("aria-busy");
    render();
  }
}

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

/* ---------- Assisted step ---------- */

const LAMPS = ["Signed in", "Saved", "Backup", "Armed"];
const LIT = { "no-login": 0, "save-current": 1, "need-second": 2, "pick-active": 3, ready: 3, armed: 4 };

// Setup really is a sequence, so progress is shown as four operator lamps.
function lamps(stage) {
  const lit = LIT[stage] ?? 0;
  return el("ol", { class: "lamps", "aria-label": `Setup progress: ${lit} of 4 done` },
    LAMPS.map((label, i) => el("li", { class: `lamp${i < lit ? " lit" : ""}${i === lit ? " now" : ""}`, "aria-current": i === lit ? "step" : false, style: { "--n": i } },
      el("span", { class: "bulb" }), el("span", { class: "engrave" }, label))));
}

function copyButton(text) {
  return el("button", {
    type: "button", class: "btn quiet",
    onclick: async (event) => {
      try { await navigator.clipboard.writeText(text); event.target.textContent = "Copied"; }
      catch { event.target.textContent = "Select and copy it"; }
    },
  }, `Copy ${text}`);
}

function checkAgain(label = "Check again") {
  return el("button", { type: "button", class: "btn", disabled: busy, onclick: () => run("/api/refresh", {}) }, label);
}

function assist() {
  const s = state;
  const card = $("assist");
  card.className = "assist";
  card.replaceChildren();
  const eyebrow = (text) => el("span", { class: "engrave" }, text);
  const put = (tone, ...kids) => { if (tone) card.classList.add(tone); card.append(...kids); };

  switch (s.stage) {
    case "no-claude":
      put("blocked", eyebrow("Needs attention"), el("h2", {}, "Claude Code isn't available here"),
        el("p", {}, "Sessioner switches the login Claude Code uses, so Claude Code has to be installed and reachable from the terminal that started Sessioner."),
        el("div", { class: "row" }, checkAgain()));
      break;
    case "blocked":
      put("blocked", eyebrow("Needs attention"), el("h2", {}, "Claude's settings are in the way"),
        el("p", {}, s.automatic.blocked || "Claude's settings need repair before automatic switching can run."),
        el("p", {}, "Your saved accounts are safe. Repair the settings file, then check again."),
        el("div", { class: "row" }, el("code", {}, s.settingsPath), checkAgain()));
      break;
    case "no-login":
      put("attention", lamps(s.stage), el("h2", {}, "Sign in to Claude first"),
        el("p", {}, "Sessioner can't find a Claude login yet."),
        el("ol", { class: "steps" },
          el("li", {}, el("span", {}, "Open Claude Code in a terminal.")),
          el("li", {}, el("span", {}, "Type ", el("code", {}, "/login"), " and sign in.")),
          el("li", {}, el("span", {}, "Come back here and check again."))),
        el("div", { class: "row" }, copyButton("/login"), checkAgain()));
      break;
    case "save-current": {
      const input = el("input", { type: "text", id: "name", value: draftName ?? s.suggestedName ?? "", maxlength: "64", autocomplete: "off", spellcheck: "false", "aria-label": "Account name", "aria-describedby": "name-help" });
      input.addEventListener("input", () => { draftName = input.value; });
      const error = el("p", { class: "field-error", hidden: !formError, role: "alert" }, formError || "");
      const save = () => run("/api/add", { name: input.value }, { onError: (message) => { formError = message; focusName = true; } });
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") save(); });
      const first = s.accounts.length === 0;
      put("attention", lamps(s.stage),
        el("h2", {}, first ? "Save the login Claude is using" : "Save this new login"),
        el("p", {}, "Claude is signed in as ", el("strong", {}, s.login), ". Give it a short name you'll recognise, like work or personal."),
        el("p", { id: "name-help" }, "Letters, digits, dots, dashes and underscores."),
        el("div", { class: "row" }, input, el("button", { type: "button", class: "btn", disabled: busy, onclick: save }, "Save login")),
        error);
      break;
    }
    case "need-second": {
      const same = s.accounts.length && s.login;
      put("attention", lamps(s.stage), el("h2", {}, "Add a different account"),
        el("p", {}, "Automatic switching needs a second, different login to switch to."),
        same ? el("p", {}, "Right now Claude is still signed in as ", el("strong", {}, s.login), ", which is already saved.") : "",
        el("ol", { class: "steps" },
          el("li", {}, el("span", {}, "In Claude Code, type ", el("code", {}, "/login"), ".")),
          el("li", {}, el("span", {}, "Sign in with a ", el("strong", {}, "different"), " account.")),
          el("li", {}, el("span", {}, "Come back here and check again."))),
        el("div", { class: "row" }, copyButton("/login"), checkAgain()));
      break;
    }
    case "pick-active":
      put("attention", lamps(s.stage), el("h2", {}, "Choose an account that's switched on"),
        el("p", {}, "The account Claude is using is disabled. Switch to one of your enabled accounts below."));
      break;
    case "ready":
      put("good", lamps(s.stage), el("h2", {}, "Turn on automatic switching"),
        el("p", {}, "Two different accounts are saved. When Claude reports that your usage limit is used up, Sessioner will move to the other account."),
        el("div", { class: "row" }, el("button", { type: "button", class: "btn", disabled: busy || !s.automatic.canEnable, onclick: () => run("/api/automatic", { enabled: true }) }, "Turn on automatic switching")));
      break;
    default: {
      const stale = !s.backupReady;
      const atLimit = s.accounts.some((account) => !account.active && account.usage.windows.some((window) => window.pct >= 100));
      put(stale ? "attention" : "good", lamps(s.stage),
        el("h2", {}, !stale ? "Switching is on" : atLimit ? "Switching is on, but no backup has room" : "Switching is on, but the backup's usage isn't confirmed"),
        el("p", {}, !stale
          ? "A backup account with room is ready. If Claude hits a usage limit, Sessioner will switch the login."
          : atLimit
            ? "Your other saved account has reached its limit. Sessioner will switch once one has room again; refresh to check."
            : "Sessioner only switches to an account whose usage was checked in the last five minutes and still has room. Refresh to check now."),
        el("p", {}, "After a switch, retry or resume the conversation in Claude. Sessioner never touches the conversation itself."),
        stale ? el("div", { class: "row" }, el("button", { type: "button", class: "btn", disabled: busy, onclick: () => run("/api/refresh", {}) }, "Refresh usage")) : "");
    }
  }
}

/* ---------- Patch bay ---------- */

function meter(window) {
  const filled = Math.ceil(window.pct / 10);
  const tone = window.pct >= 90 ? "high" : window.pct >= 70 ? "mid" : "";
  const segs = el("span", { class: "segs", "aria-hidden": "true" },
    Array.from({ length: 10 }, (_, i) => el("span", { class: `seg${i < filled ? " on" : ""}`, style: { "--i": i } })));
  return el("div", { class: `meter ${tone}` }, el("span", { class: "engrave" }, window.label), segs,
    el("span", { class: "pct" }, `${Math.round(window.pct)}% used`));
}

function jack(account) {
  const canSwitch = !account.active && !busy;
  const spent = account.usage.windows.some((window) => window.pct >= 100);
  const meters = account.usage.windows.length
    ? el("div", { class: "meters" }, account.usage.windows.map(meter))
    : el("p", { class: "note meters" }, account.usage.note);
  return el("li", { class: `jack${account.active ? " active" : ""}${account.disabled ? " disabled" : ""}`, "data-number": account.number },
    el("span", { class: `port${spent ? " spent" : ""}${account.number === state.nextBackup && state.automatic.on ? " standby" : ""}${account.number === justConnected ? " flash" : ""}`, "data-port": account.number }),
    el("div", {},
      el("span", { class: "engrave line" }, `Line ${account.number}`),
      el("div", { class: "jack-name" }, account.name || account.email,
        account.active ? el("span", { class: "chip live" }, "In use") : "",
        spent ? el("span", { class: "chip alert" }, "Limit reached") : "",
        !account.active && account.number === state.nextBackup && state.automatic.on ? el("span", { class: "chip next" }, "Next in line") : "",
        account.disabled ? el("span", { class: "chip muted" }, "Switched off") : "",
        account.sameLoginAs ? el("span", { class: "chip muted" }, `Same login as ${account.sameLoginAs}`) : ""),
      account.name ? el("div", { class: "jack-email" }, account.email) : ""),
    account.active ? "" : el("button", { type: "button", class: "btn quiet jack-act", disabled: !canSwitch, "aria-label": `Switch to ${account.label}`, onclick: () => run("/api/switch", { target: String(account.number) }) }, "Switch to this"),
    meters);
}

function bay() {
  const list = $("jacks");
  list.replaceChildren(...state.accounts.map(jack));
  $("empty").hidden = state.accounts.length > 0;
  requestAnimationFrame(() => drawCable(true));
}

/* ---------- Cable: the signature ---------- */

function center(node, within) {
  const box = node.getBoundingClientRect();
  const origin = within.getBoundingClientRect();
  return { x: box.left - origin.left + box.width / 2, y: box.top - origin.top + box.height / 2 };
}

function svgNode(tag, attrs) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

// Down the trough, then a rounded turn into the jack. `sway` bows the run sideways.
function route(gx, sy, ex, ey, sway) {
  const drop = ey - sy;
  if (drop < 2) return `M ${gx} ${sy} H ${ex}`;
  const r = Math.min(18, drop / 2, Math.max(ex - gx, 0));
  const stop = ey - r, third = (stop - sy) / 3;
  return `M ${gx} ${sy} C ${gx + sway} ${sy + third} ${gx + sway} ${sy + 2 * third} ${gx} ${stop} Q ${gx} ${ey} ${gx + r} ${ey} H ${ex}`;
}

function paintCable(source, plugY, plugX, sway, ghost, now, reduce) {
  const svg = $("cable");
  svg.replaceChildren();
  if (plugX === null) {
    // Not plugged into a saved account: the cable hangs loose below its socket.
    svg.append(svgNode("path", { d: `M ${source.x} ${source.y} V ${source.y + 40}`, class: "dangling" }));
    return;
  }
  if (ghost) svg.append(svgNode("path", { d: route(source.x + 11, source.y, ghost.x - 20, ghost.y, 0), class: "ghost" }));
  const cord = svgNode("path", { d: route(source.x, source.y, plugX - 20, plugY, sway) });
  svg.append(cord);
  if (!reduce) {
    // Current: light travelling from the Claude Code socket down to the seated plug.
    const length = cord.getTotalLength();
    for (let k = 0; k < 3; k++) {
      const point = cord.getPointAtLength(length * (((now / 2200) + k / 3) % 1));
      svg.append(svgNode("circle", { cx: point.x, cy: point.y, r: 3, class: "pulse" }));
    }
  }
  svg.append(svgNode("rect", { x: plugX - 21, y: plugY - 8, width: 14, height: 16, rx: 4, class: "plug" }));
  svg.append(svgNode("circle", { cx: plugX - 9, cy: plugY, r: 3, class: "plug-tip" }));
}

const cable = { y: null, swayAt: null };
let frame = 0;

function drawCable(animate) {
  if (!state) return;
  const grid = $("bay");
  const source = center($("source-port"), grid);
  const active = state.accounts.find((account) => account.active);
  cancelAnimationFrame(frame);
  if (!active) { cable.y = null; paintCable(source, 0, null, 0, null, 0, true); return; }

  const target = center(document.querySelector(`[data-port="${active.number}"]`), grid);
  const standby = state.automatic.on && state.nextBackup != null && state.nextBackup !== active.number
    ? document.querySelector(`[data-port="${state.nextBackup}"]`) : null;
  const ghost = standby ? center(standby, grid) : null;
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // First paint unspools the cable from its socket; later moves slide it between jacks.
  const from = cable.y ?? source.y;
  const tween = animate && !reduce && Math.abs(from - target.y) >= 1;
  if (tween) cable.swayAt = performance.now() + 520 * 0.75;
  const started = performance.now();
  const ease = (t) => 1 - Math.pow(1 - t, 3);

  const tick = (now) => {
    const t = tween ? Math.min((now - started) / 520, 1) : 1;
    cable.y = tween ? from + (target.y - from) * ease(t) : target.y;
    const since = cable.swayAt === null ? -1 : (now - cable.swayAt) / 1000;
    const sway = !reduce && since > 0 ? 9 * Math.exp(-since / 0.45) * Math.sin(since * 2 * Math.PI * 2.4) : 0;
    paintCable(source, cable.y, target.x, sway, ghost, now, reduce);
    if (t < 1 || !reduce) frame = requestAnimationFrame(tick); // keeps the current flowing
  };
  frame = requestAnimationFrame(tick);
}

/* ---------- Automatic switching ---------- */

function auto() {
  const s = state;
  const on = s.automatic.on;
  const disabled = busy || (!on && !s.automatic.canEnable);
  const toggle = el("button", {
    type: "button", class: "switch", role: "switch", "aria-checked": String(on), "aria-labelledby": "auto-title",
    disabled, title: !on && s.automatic.reason ? s.automatic.reason : false,
    onclick: () => run("/api/automatic", { enabled: !on }),
  });
  $("auto").replaceChildren(
    el("div", {},
      el("h2", { id: "auto-title" }, el("span", { class: `armed-lamp${on ? " lit" : ""}`, "aria-hidden": "true" }), "Automatic switching"),
      el("p", {}, on ? "On. Sessioner watches for Claude's usage-limit error and moves to a backup that has room."
                     : (s.automatic.reason || "Off. Turn it on to let Sessioner move to a backup when a usage limit is hit."))),
    toggle,
    el("p", { class: "fine" }, "Not yet confirmed: whether a Claude conversation that is already running picks up the new login on its own. If it doesn't, retry or resume it in Claude. In Claude, type /hooks to check that the hook is loaded."));
}

function render() {
  if (!state) return;
  assist();
  bay();
  justConnected = null; // flash once, not on every later render
  auto();
  if (focusName) { focusName = false; $("name")?.focus(); }
}

/* ---------- Boot ---------- */

$("refresh").addEventListener("click", () => run("/api/refresh", {}));
$("quit").addEventListener("click", async () => {
  try { const r = await call("/api/quit", {}); toast(r.message); document.body.setAttribute("aria-busy", "true"); } catch (e) { toast(e.message, true); }
});
addEventListener("resize", () => drawCable(false));
addEventListener("keydown", (event) => {
  if (busy || !state || event.ctrlKey || event.metaKey || event.altKey || event.target.closest("input, textarea")) return;
  const line = state.accounts.find((account) => String(account.number) === event.key && !account.active);
  if (line) run("/api/switch", { target: String(line.number) });
});
$("sound").addEventListener("click", () => {
  soundOn = !soundOn;
  try { localStorage.setItem("sessioner-sound", soundOn ? "on" : "off"); } catch { /* remembered only when storage allows */ }
  syncSoundButton();
  clunk();
});
syncSoundButton();
// First paint powers the panel on: lamps and LEDs light in sequence, then the class is removed.
document.body.classList.add("boot");
setTimeout(() => document.body.classList.remove("boot"), 2600);

call("/api/state").then((result) => { state = result.state; render(); })
  .catch((error) => { $("assist").replaceChildren(el("h2", {}, "Sessioner can't be reached"), el("p", {}, error.message)); });
