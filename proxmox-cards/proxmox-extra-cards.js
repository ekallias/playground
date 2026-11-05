/*
 * Proxmox Cards for Home Assistant (VMs, LXC containers, storage)
 *
 *   custom:proxmox-vm-card       – one QEMU VM. Drop-in, config-compatible rewrite of
 *                                  chsegala/proxmox-cards-for-home-assistant (Apache-2.0)
 *   custom:proxmox-guest-card    – the same card for an LXC container or a VM
 *   custom:proxmox-node-card     – a Proxmox host (node): resources, guests, backups, power controls
 *   custom:proxmox-overview-card – the whole instance: problems, hosts, storage and all guests
 *   custom:proxmox-guests-card   – compact list of all (or selected) containers and VMs
 *   custom:proxmox-storage-card  – storage pools with usage bars or ring gauges
 *
 * Data source: the official Home Assistant "Proxmox VE" integration (domain `proxmoxve`).
 * The cards never talk to Proxmox directly. No dependencies, no build step.
 */
const VERSION = "0.6.0";
const DOMAIN = "proxmoxve";

/* ================================================================ registry === */

let registryPromise = null;
function loadRegistry(hass, force = false) {
  if (!registryPromise || force) {
    registryPromise = Promise.all([
      hass.callWS({ type: "config/device_registry/list" }),
      hass.callWS({ type: "config/entity_registry/list" }),
    ])
      .then(([devices, entities]) => ({ devices, entities: entities.filter((e) => e.platform === DOMAIN) }))
      .catch((err) => {
        registryPromise = null;
        throw err;
      });
  }
  return registryPromise;
}

function proxmoxId(device, re) {
  for (const [domain, id] of device.identifiers || []) {
    if (domain !== DOMAIN) continue;
    const m = re.exec(id);
    if (m) return m;
  }
  return null;
}

const GUEST_RE = /^(.+)_(vm|container)_(\d+)$/;
const STORAGE_RE = /^(.+)_storage_(.+)$/;
const deviceName = (d) => (d && (d.name_by_user || d.name)) || "";

function nodeNameFor(reg, device) {
  const parent = reg.devices.find((d) => d.id === device.via_device_id);
  if (!parent) return "";
  const m = proxmoxId(parent, /^.+_node_node\/(.+)$/);
  return m ? m[1] : deviceName(parent);
}

const GUEST_SENSORS = ["cpu", "memory", "max_memory", "memory_percentage", "disk", "max_disk", "netin", "netout", "uptime", "status"];
const GUEST_BUTTONS = ["start", "shutdown", "restart", "stop", "pause", "resume"];

/* `entities:` override roles, named exactly as in the original VM card */
const OVERRIDE_ROLES = {
  cpu: "cpu",
  memory: "memory",
  memoryTotal: "max_memory",
  memoryPercentage: "memory_percentage",
  disk: "disk",
  diskTotal: "max_disk",
  networkRx: "netin",
  networkTx: "netout",
  uptime: "uptime",
  status: "status",
  networkRxRate: "rx_rate", // optional real-time throughput sensors (e.g. a Derivative helper); any sensor
  networkTxRate: "tx_rate",
  temperature: "temperature", // optional host sensors (e.g. from Proxmox Extended Sensors); any sensor
  load: "load",
  iowait: "iowait",
  swap: "swap",
  updates: "updates",
};
const RATE_ROLES = ["networkRxRate", "networkTxRate"];
const HOST_ROLES = ["temperature", "load", "iowait", "swap", "updates"];
const FREE_ROLES = [...RATE_ROLES, ...HOST_ROLES]; // may point at any sensor, not only the Proxmox device's own

function discoverGuest(reg, deviceId, overrides = {}) {
  const device = reg.devices.find((d) => d.id === deviceId);
  if (!device) return { error: "Device not found in the Home Assistant device registry." };
  const m = proxmoxId(device, GUEST_RE);
  if (!m) return { error: "Select a container or VM device from the Proxmox VE integration." };
  const [, entry, kind, vmid] = m;
  const prefix = `${entry}_${vmid}_`;
  const keyPrefix = kind === "vm" ? "vm_" : "container_";
  const own = reg.entities.filter((e) => e.device_id === deviceId);
  const byKey = {};
  for (const e of own) {
    if (!e.unique_id.startsWith(prefix)) continue;
    byKey[`${e.entity_id.split(".")[0]}:${e.unique_id.slice(prefix.length)}`] = e;
  }
  const sensors = {};
  for (const k of GUEST_SENSORS) {
    const e = byKey[`sensor:${keyPrefix}${k}`];
    if (e) sensors[k] = e;
  }
  for (const [role, entityId] of Object.entries(overrides || {})) {
    const key = OVERRIDE_ROLES[role];
    if (!key || !entityId) continue;
    const e = own.find((x) => x.entity_id === entityId) || (FREE_ROLES.includes(role) ? { entity_id: entityId } : null);
    if (!e) return { error: `Override ${entityId} must be a sensor of this Proxmox device.` };
    sensors[key] = e;
  }
  const buttons = {};
  for (const k of GUEST_BUTTONS) {
    const e = byKey[`button:${k}`];
    if (e) buttons[k] = e;
  }
  return {
    deviceId,
    kind: kind === "vm" ? "vm" : "lxc",
    vmid: Number(vmid),
    name: deviceName(device) || `${kind === "vm" ? "VM" : "CT"} ${vmid}`,
    node: nodeNameFor(reg, device),
    sensors,
    buttons,
    running: byKey["binary_sensor:status"],
  };
}

const NODE_RE = /^(.+)_node_(node\/(.+))$/;
const NODE_SENSORS = {
  cpu: "node_cpu",
  max_cpu: "node_max_cpu",
  memory: "node_memory",
  max_memory: "node_max_memory",
  memory_percentage: "node_memory_percentage",
  disk: "node_disk",
  max_disk: "node_max_disk",
  uptime: "node_uptime",
  status: "node_status",
  backup_last: "node_backup_last_backup",
  backup_duration: "node_backup_duration",
};

function discoverNode(reg, deviceId, overrides = {}) {
  const device = reg.devices.find((d) => d.id === deviceId);
  if (!device) return { error: "Device not found in the Home Assistant device registry." };
  const m = proxmoxId(device, NODE_RE);
  if (!m) return { error: "Select a Proxmox host (node) device from the Proxmox VE integration." };
  const [, entry, nodeId, nodeName] = m;
  const prefix = `${entry}_${nodeId}_`;
  const own = reg.entities.filter((e) => e.device_id === deviceId);
  const byKey = {};
  for (const e of own) if (e.unique_id.startsWith(prefix)) byKey[`${e.entity_id.split(".")[0]}:${e.unique_id.slice(prefix.length)}`] = e;
  const sensors = {};
  for (const [k, key] of Object.entries(NODE_SENSORS)) if (byKey[`sensor:${key}`]) sensors[k] = byKey[`sensor:${key}`];
  for (const [role, entityId] of Object.entries(overrides || {})) {
    const key = OVERRIDE_ROLES[role];
    if (!key || !entityId) continue;
    const e = own.find((x) => x.entity_id === entityId) || (FREE_ROLES.includes(role) ? { entity_id: entityId } : null);
    if (!e) return { error: `Override ${entityId} must be a sensor of this Proxmox host.` };
    sensors[key] = e;
  }
  const buttons = {};
  for (const k of ["reboot", "shutdown", "start_all", "stop_all", "suspend_all"]) if (byKey[`button:${k}`]) buttons[k] = byKey[`button:${k}`];
  const guests = reg.devices
    .filter((d) => d.via_device_id === deviceId && !d.disabled_by && proxmoxId(d, GUEST_RE))
    .map((d) => discoverGuest(reg, d.id))
    .filter((g) => !g.error);
  return {
    deviceId,
    kind: "node",
    name: deviceName(device) || nodeName,
    node: nodeName,
    sensors,
    buttons,
    running: byKey["binary_sensor:status"],
    backupProblem: byKey["binary_sensor:node_backup_status"],
    guests,
  };
}

const STORAGE_SENSORS = ["storage_used", "storage_total", "storage_available", "storage_used_percentage"];
const STORAGE_BINARY = ["storage_active", "storage_enabled", "storage_shared"];

function discoverStorage(reg, deviceId) {
  const device = reg.devices.find((d) => d.id === deviceId);
  if (!device) return { error: "Device not found." };
  const m = proxmoxId(device, STORAGE_RE);
  if (!m) return { error: "Not a Proxmox storage device." };
  const storage = m[2];
  const ents = reg.entities.filter((e) => e.device_id === deviceId);
  const pick = (domain, key) =>
    ents.find((e) => e.entity_id.startsWith(domain + ".") && e.unique_id.endsWith(`_${storage}_${key}`));
  const sensors = {};
  for (const k of STORAGE_SENSORS) {
    const e = pick("sensor", k);
    if (e) sensors[k.replace("storage_", "")] = e;
  }
  for (const k of STORAGE_BINARY) {
    const e = pick("binary_sensor", k);
    if (e) sensors[k.replace("storage_", "")] = e;
  }
  return { deviceId, storage, name: (device.name_by_user || "").trim() || storage, node: nodeNameFor(reg, device), sensors };
}

function readStorage(h, p) {
  const used = readNumber(h, p.sensors.used, "bytes");
  const total = readNumber(h, p.sensors.total, "bytes");
  let free = readNumber(h, p.sensors.available, "bytes");
  let pct = readNumber(h, p.sensors.used_percentage, "pct");
  if (pct === null && used !== null && total) pct = (used / total) * 100;
  if (free === null && used !== null && total !== null) free = Math.max(0, total - used);
  const active = stateOf(h, p.sensors.active);
  const en = stateOf(h, p.sensors.enabled);
  const shared = stateOf(h, p.sensors.shared);
  const inactive = (active && active.state === "off") || (en && en.state === "off");
  return { used, total, free, pct, inactive, enabledOff: en && en.state === "off", shared: shared && shared.state === "on" };
}

function allDevices(reg, re, filter = () => true) {
  return reg.devices.filter((d) => !d.disabled_by && proxmoxId(d, re) && filter(proxmoxId(d, re))).map((d) => d.id);
}

/* ================================================================== values === */

const BYTE_UNITS = {
  B: 1, bytes: 1, bit: 1 / 8,
  kB: 1e3, KB: 1e3, MB: 1e6, GB: 1e9, TB: 1e12, PB: 1e15,
  KiB: 1024, MiB: 1024 ** 2, GiB: 1024 ** 3, TiB: 1024 ** 4, PiB: 1024 ** 5,
};
const RATE_UNITS = {
  "bit/s": 1 / 8, "kbit/s": 125, "Mbit/s": 125e3, "Gbit/s": 125e6,
  "B/s": 1, "kB/s": 1e3, "MB/s": 1e6, "GB/s": 1e9, "KiB/s": 1024, "MiB/s": 1024 ** 2, "GiB/s": 1024 ** 3,
};
const TIME_UNITS = { s: 1, min: 60, h: 3600, d: 86400 };
const BAD = new Set(["unknown", "unavailable", "", undefined]);
const enabled = (e) => !!e && !e.disabled_by;

function stateOf(hass, entity) {
  if (!enabled(entity)) return undefined;
  return hass.states[entity.entity_id];
}

function readNumber(hass, entity, kind) {
  const st = stateOf(hass, entity);
  if (!st || BAD.has(st.state)) return null;
  if (kind === "uptime" && st.attributes.device_class === "uptime") {
    const t = Date.parse(st.state);
    return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 1000) : null;
  }
  const n = Number(st.state);
  if (!Number.isFinite(n) || n < 0) return null;
  const unit = st.attributes.unit_of_measurement;
  if (kind === "bytes") return unit in BYTE_UNITS ? n * BYTE_UNITS[unit] : null;
  if (kind === "rate") return unit in RATE_UNITS ? n * RATE_UNITS[unit] : null;
  if (kind === "uptime") return n * (TIME_UNITS[unit] || 1);
  return n;
}

function fmtBytes(v, binary = true) {
  if (v === null || !Number.isFinite(v)) return "–";
  const base = binary ? 1024 : 1000;
  const units = binary ? ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] : ["B", "kB", "MB", "GB", "TB", "PB"];
  let i = 0;
  while (v >= base && i < units.length - 1) {
    v /= base;
    i++;
  }
  return `${v.toFixed(i === 0 || v >= 100 ? 0 : 1)} ${units[i]}`;
}

function fmtUptime(s) {
  if (s === null || !Number.isFinite(s)) return "";
  const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d) return `${d}d ${h % 24}h`;
  if (h) return `${h}h ${m % 60}m`;
  return m ? `${m}m` : "<1m";
}

const fmtPct = (p) => (p === null || !Number.isFinite(p) ? "–" : `${Math.round(Math.min(100, Math.max(0, p)))}%`);

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function guestState(hass, g) {
  const st = stateOf(hass, g.sensors.status);
  if (st && !BAD.has(st.state)) {
    if (["running", "stopped", "suspended"].includes(st.state)) return st.state;
    if (st.state === "paused") return "suspended";
    if (st.state === "online") return "running"; // node status
    if (st.state === "offline") return "stopped";
    return "unknown";
  }
  const bin = stateOf(hass, g.running);
  if (bin && bin.state === "on") return "running";
  if (bin && bin.state === "off") return "stopped";
  return "unavailable";
}

function guestMetrics(hass, g) {
  const s = g.sensors;
  const cpu = readNumber(hass, s.cpu, "pct");
  const memTotal = readNumber(hass, s.max_memory, "bytes");
  let mem = readNumber(hass, s.memory, "bytes");
  let memPct = readNumber(hass, s.memory_percentage, "pct");
  if (mem === null && memPct !== null && memTotal) mem = (memTotal * memPct) / 100;
  if (mem !== null && memTotal) memPct = (mem / memTotal) * 100;
  const diskTotal = readNumber(hass, s.max_disk, "bytes");
  const disk = readNumber(hass, s.disk, "bytes");
  const diskPct = disk !== null && diskTotal ? (disk / diskTotal) * 100 : null;
  return {
    cpu, mem, memTotal, memPct, disk, diskTotal, diskPct,
    netin: readNumber(hass, s.netin, "bytes"),
    netout: readNumber(hass, s.netout, "bytes"),
    rx: readNumber(hass, s.rx_rate, "rate"),
    tx: readNumber(hass, s.tx_rate, "rate"),
    uptime: readNumber(hass, s.uptime, "uptime"),
  };
}

function level(pct, warn, crit) {
  if (pct === null) return "";
  if (pct >= crit) return "crit";
  if (pct >= warn) return "warn";
  return "ok";
}

/* ======================================================= history charts === */

const toMs = (t) => (typeof t === "number" ? (t < 1e12 ? t * 1000 : t) : Date.parse(t));
const fmtRate = (bps) => `${fmtBytes(bps, false)}/s`;

/*
 * Fetch history (averages) for a set of measurement sensors from the recorder: 5-minute statistics
 * (hourly for periods over 3 days), raw history as fallback. Data sizes come back in bytes and
 * data rates in bytes per second.
 */
async function fetchSeries(hass, ids, hours) {
  const out = {};
  if (!ids.length) return out;
  const start = new Date(Date.now() - hours * 3600e3).toISOString();
  try {
    const stats = await hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: start,
      statistic_ids: ids,
      period: hours > 72 ? "hour" : "5minute",
      types: ["mean"],
      units: { data_size: "B", data_rate: "B/s" },
    });
    for (const id of ids) {
      const pts = ((stats && stats[id]) || [])
        .map((r) => [toMs(r.start), r.mean == null ? NaN : Number(r.mean)])
        .filter(([t, v]) => Number.isFinite(t) && Number.isFinite(v));
      if (pts.length > 1) out[id] = pts;
    }
  } catch (e) {
    /* statistics unavailable (recorder excluded, older HA) – fall through to history */
  }
  const missing = ids.filter((id) => !out[id]);
  if (missing.length) {
    try {
      const h = await hass.callWS({
        type: "history/history_during_period",
        start_time: start,
        entity_ids: missing,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      });
      for (const id of missing) {
        const unit = hass.states[id] && hass.states[id].attributes.unit_of_measurement;
        const f = unit in BYTE_UNITS ? BYTE_UNITS[unit] : unit in RATE_UNITS ? RATE_UNITS[unit] : 1;
        const pts = ((h && h[id]) || [])
          .map((r) => [toMs(r.lu ?? r.last_updated ?? r.last_changed), Number(r.s ?? r.state) * f])
          .filter(([t, v]) => Number.isFinite(t) && Number.isFinite(v));
        if (pts.length > 1) out[id] = pts;
      }
    } catch (e) {
      /* no history access – charts are simply omitted */
    }
  }
  return out;
}

function downsample(pts, max) {
  if (pts.length <= max) return pts;
  const step = pts.length / max;
  return Array.from({ length: max }, (_, i) => pts[Math.floor(i * step)]).concat([pts[pts.length - 1]]);
}

const CHART_FMT = { pct: (v) => `${v.toFixed(1)}%`, rate: fmtRate };

/*
 * Area/line chart. series: [{ pts: [[ms, value]], area: bool, label }]; the first series is drawn in the
 * card's accent (or warning/critical `tone`), the second in --proxmox-alt-color. `gutter` px on the right
 * holds the top/bottom scale labels; hovering shows a readout (see wireCharts).
 */
function buildChart({ series, lo, hi, height, gutter = 0, guides = [], tone = "", fmt = "pct" }) {
  series = series.filter((s) => s.pts.length > 1).map((s) => ({ ...s, pts: downsample(s.pts, 240) }));
  if (!series.length) return "";
  const W = 300, H = height, pad = 3;
  if (!(hi > lo)) hi = lo + 1;
  const t0 = Math.min(...series.map((s) => s.pts[0][0])), t1 = Math.max(...series.map((s) => s.pts[s.pts.length - 1][0]));
  const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * W;
  const y = (v) => H - pad - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * (H - 2 * pad);
  const paths = series
    .map((s, i) => {
      const line = s.pts.map(([t, v], j) => `${j ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join("");
      const area = s.area ? `<path class="sa s${i}" d="${line}L${x(s.pts[s.pts.length - 1][0]).toFixed(1)},${H}L${x(s.pts[0][0]).toFixed(1)},${H}Z"/>` : "";
      return `${area}<path class="sl s${i}" d="${line}"/>`;
    })
    .join("");
  const gd = guides
    .filter((g) => g.v > lo && g.v < hi)
    .map((g) => `<line class="gd ${g.cls}" x1="0" x2="${W}" y1="${y(g.v).toFixed(1)}" y2="${y(g.v).toFixed(1)}"/>`)
    .join("");
  const f = CHART_FMT[fmt];
  const axisFmt = fmt === "pct" ? (v) => `${v.toFixed(hi - lo < 10 ? 1 : 0)}%` : f;
  const data = {
    t0: t0 / 1000, t1: t1 / 1000, lo, hi, g: gutter, fmt,
    labels: series.map((s) => s.label || ""),
    s: series.map((s) => s.pts.map(([t, v]) => [Math.round(t / 1000), Math.round(v * 100) / 100])),
  };
  return `<div class="chart" style="height:${H}px;--gutter:${gutter}px" data-chart="${esc(JSON.stringify(data))}">
    <svg class="area ${tone}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${gd}${paths}</svg>
    ${gutter ? `<span class="ax top num">${axisFmt(hi)}</span><span class="ax bot num">${axisFmt(lo)}</span>` : ""}
    ${series.map((_, i) => `<i class="cur c${i}"></i>`).join("")}<span class="tip num"></span>
  </div>`;
}

function wireCharts(root) {
  root.querySelectorAll(".chart[data-chart]").forEach((ch) => {
    const d = JSON.parse(ch.dataset.chart);
    const fmt = CHART_FMT[d.fmt] || CHART_FMT.pct;
    const curs = ch.querySelectorAll(".cur"), tip = ch.querySelector(".tip");
    ch.onpointermove = (e) => {
      const box = ch.getBoundingClientRect();
      const w = box.width - d.g, h = box.height;
      const ts = d.t0 + Math.min(1, Math.max(0, (e.clientX - box.left) / w)) * (d.t1 - d.t0);
      const parts = [];
      let when = ts, px = 0;
      d.s.forEach((pts, i) => {
        let best = pts[0];
        for (const pt of pts) if (Math.abs(pt[0] - ts) < Math.abs(best[0] - ts)) best = pt;
        px = ((best[0] - d.t0) / Math.max(1, d.t1 - d.t0)) * w;
        const py = h - 3 - ((Math.min(d.hi, Math.max(d.lo, best[1])) - d.lo) / (d.hi - d.lo)) * (h - 6);
        curs[i].style.cssText = `display:block;left:${px}px;top:${py}px`;
        parts.push(`${d.labels[i] ? d.labels[i] + " " : ""}${fmt(best[1])}`);
        when = best[0];
      });
      const ago = Math.max(0, Date.now() / 1000 - when);
      tip.textContent = `${parts.join(" · ")} · ${ago < 300 ? "now" : `${fmtUptime(ago)} ago`}`;
      tip.style.display = "block";
      const tw = tip.offsetWidth;
      tip.style.left = `${Math.min(box.width - tw, Math.max(Math.min(0, box.width - tw), px - tw / 2))}px`;
    };
    ch.onpointerleave = () => {
      curs.forEach((c) => (c.style.display = "none"));
      tip.style.display = "none";
    };
  });
}

/* =========================================================== shared bits === */

const ICONS = { lxc: "mdi:package-variant-closed", vm: "mdi:desktop-tower", storage: "mdi:database", node: "mdi:server" };
const STATE_LABEL = { running: "Running", stopped: "Stopped", suspended: "Suspended", unknown: "Unknown", unavailable: "Unavailable" };

const BASE_CSS = `
  :host {
    --pxc-ok: var(--proxmox-ok-color, var(--success-color, #2e7d5b));
    --pxc-warn: var(--proxmox-warn-color, var(--warning-color, #e0a030));
    --pxc-crit: var(--proxmox-crit-color, var(--error-color, #d14b4b));
    --pxc-accent: var(--proxmox-accent-color, var(--primary-color, #3a6f8f));
    --pxc-track: var(--proxmox-track-color, color-mix(in srgb, var(--primary-text-color, #222) 10%, transparent));
    --pxc-muted: var(--secondary-text-color, #6b7480);
    --pxc-line: var(--divider-color, rgba(127,127,127,.2));
    --pxc-fill: color-mix(in srgb, var(--primary-text-color, #222) 4%, transparent);
    --pxc-alt: var(--proxmox-alt-color, #9a6fe0);
  }
  ha-card { display: block; padding: 16px; box-sizing: border-box; height: 100%; }
  .muted { color: var(--pxc-muted); }
  .head { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .badge {
    flex: none; width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center;
    background: color-mix(in srgb, var(--pxc-accent) 12%, transparent); color: var(--pxc-accent);
  }
  .badge ha-icon { --mdc-icon-size: 22px; }
  .titles { flex: 1; min-width: 0; }
  .title { font-size: 16px; font-weight: 600; line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub { font-size: 12px; color: var(--pxc-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .state { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; white-space: nowrap; flex: none; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--pxc-muted); flex: none; }
  .dot.running { background: var(--pxc-ok); box-shadow: 0 0 0 3px color-mix(in srgb, var(--pxc-ok) 22%, transparent); }
  .dot.stopped { background: var(--pxc-muted); border-radius: 2px; }
  .dot.suspended { background: var(--pxc-warn); }
  .dot.unavailable, .dot.unknown { background: transparent; border: 1.5px solid var(--pxc-muted); width: 6px; height: 6px; transform: rotate(45deg); border-radius: 1px; }
  .label { font-size: 10.5px; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; color: var(--pxc-muted); }
  .bar { height: 6px; border-radius: 3px; background: var(--pxc-track); overflow: hidden; }
  .bar > i { display: block; height: 100%; border-radius: inherit; background: var(--pxc-accent); transition: width .6s ease; }
  .bar > i.warn { background: var(--pxc-warn); }
  .bar > i.crit { background: var(--pxc-crit); }
  .sep { height: 1px; background: var(--pxc-line); margin: 14px 0; }
  .msg { padding: 8px 0; color: var(--pxc-muted); font-size: 14px; }
  .err { color: var(--pxc-crit); }
  button.btn, summary.btn {
    font: inherit; font-size: 13px; padding: 7px 6px; border-radius: 8px; cursor: pointer;
    border: 1px solid var(--pxc-line); background: var(--pxc-fill); color: var(--primary-text-color);
    flex: 1; min-width: 0; display: inline-flex; align-items: center; justify-content: center; gap: 5px; white-space: nowrap;
  }
  .btn ha-icon { --mdc-icon-size: 16px; }
  .btn:hover:not([disabled]) { background: color-mix(in srgb, var(--pxc-accent) 12%, transparent); }
  button.btn[disabled] { opacity: .5; cursor: default; }
  .btn.danger { color: var(--pxc-crit); }
  button.btn.solid { background: var(--pxc-accent); color: var(--text-primary-color, #fff); border-color: transparent; }
  button.btn.solid.danger { background: var(--pxc-crit); }
  button.link { all: unset; color: var(--pxc-accent); cursor: pointer; font-weight: 500; }
  .num { font-variant-numeric: tabular-nums; }
  .chart { position: relative; }
  .chart svg.area { position: absolute; inset: 0 var(--gutter) 0 0; width: calc(100% - var(--gutter)); height: 100%; display: block; overflow: visible; }
  .area .sl { fill: none; stroke: var(--pxc-accent); stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
  .area .sa { fill: color-mix(in srgb, var(--pxc-accent) 14%, transparent); stroke: none; }
  .area .sl.s1 { stroke: var(--pxc-alt); }
  .area .sa.s1 { fill: color-mix(in srgb, var(--pxc-alt) 12%, transparent); }
  .area.warn .sl.s0 { stroke: var(--pxc-warn); }
  .area.warn .sa.s0 { fill: color-mix(in srgb, var(--pxc-warn) 16%, transparent); }
  .area.crit .sl.s0 { stroke: var(--pxc-crit); }
  .area.crit .sa.s0 { fill: color-mix(in srgb, var(--pxc-crit) 14%, transparent); }
  .gd { stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; opacity: .7; }
  .gd.warn { stroke: var(--pxc-warn); }
  .gd.crit { stroke: var(--pxc-crit); }
  .ax { position: absolute; right: 0; width: calc(var(--gutter) - 6px); text-align: right; font-size: 10px; line-height: 1; color: var(--pxc-muted); pointer-events: none; white-space: nowrap; }
  .ax.top { top: 1px; }
  .ax.bot { bottom: 2px; }
  .cur { display: none; position: absolute; width: 7px; height: 7px; margin: -3.5px 0 0 -3.5px; border-radius: 50%;
         background: var(--ha-card-background, var(--card-background-color, #fff)); border: 2px solid var(--primary-text-color); box-sizing: border-box; pointer-events: none; }
  .cur.c1 { border-color: var(--pxc-alt); }
  .tip { display: none; position: absolute; top: 1px; z-index: 3; box-shadow: 0 2px 6px rgba(0,0,0,.2); font-size: 11px; white-space: nowrap; padding: 2px 6px; border-radius: 5px; pointer-events: none;
         background: var(--primary-text-color); color: var(--ha-card-background, var(--card-background-color, #fff)); }
  .has-chart .bar { border-radius: 0 0 3px 3px; }
`;

function fire(node, type, detail) {
  node.dispatchEvent(new CustomEvent(type, { detail, bubbles: true, composed: true }));
}
const fireMoreInfo = (node, entityId) => entityId && fire(node, "hass-more-info", { entityId });

/* Home Assistant card actions (tap_action / hold_action / double_tap_action) */
async function runCardAction(node, hass, action, fallbackEntity) {
  if (!action || action.action === "none") return;
  if (
    action.confirmation &&
    !(action.confirmation.exemptions || []).some((e) => e.user === (hass.user && hass.user.id)) &&
    !window.confirm(action.confirmation.text || "Run this action?")
  )
    return;
  switch (action.action) {
    case "more-info": {
      const id = action.entity || fallbackEntity;
      if (!id) throw new Error("Select an entity for More info.");
      return fireMoreInfo(node, id);
    }
    case "navigate": {
      const url = new URL(action.navigation_path, location.href);
      if (url.origin !== location.origin) throw new Error("Navigation must stay inside Home Assistant; use a URL action.");
      action.navigation_replace ? history.replaceState(null, "", url) : history.pushState(null, "", url);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: !!action.navigation_replace } }));
      return;
    }
    case "url":
      window.open(action.url_path, "_blank", "noopener,noreferrer");
      return;
    case "fire-dom-event":
      return fire(node, "ll-custom", action);
    case "toggle":
      return hass.callService(action.entity.split(".")[0], "toggle", {}, { entity_id: action.entity });
    case "perform-action":
    case "call-service": {
      const [domain, service] = (action.perform_action || action.service || "").split(".");
      return hass.callService(domain, service, action.data || action.service_data, action.target);
    }
  }
}

class BaseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>${BASE_CSS}${this.constructor.css || ""}</style><div id="root"></div>`;
    this._root = this.shadowRoot.getElementById("root");
    this._reg = null;
    this._error = null;
    this._lastSig = "";
  }
  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || (!this._reg && !this._loading)) this._load();
    else this._maybeRender();
  }
  get hass() {
    return this._hass;
  }
  async _load(force = false) {
    if (!this._hass) return;
    this._loading = true;
    try {
      this._reg = await loadRegistry(this._hass, force);
      this._error = null;
      this._bind();
    } catch (e) {
      this._error = `Could not read the Home Assistant registry: ${e.message || e}`;
    }
    this._loading = false;
    this._render();
  }
  _maybeRender() {
    if (this._signature() !== this._lastSig) this._render();
  }
  /* entities this card depends on – it re-renders only when one of them changes */
  _watched() {
    return [];
  }
  _signature() {
    if (!this._hass) return "";
    return this._watched()
      .map((id) => {
        const s = this._hass.states[id];
        return s ? s.last_updated + s.state : "-";
      })
      .join("|");
  }
  _render() {
    this._lastSig = this._signature();
    this._beforeRender();
    this._root.innerHTML = this._html();
    this._wire();
  }
  _beforeRender() {}
  _wire() {}
  _bind() {}
}

/* ========================================================== machine card === */
/*  One implementation behind proxmox-vm-card and proxmox-guest-card.          */

const ACTIONS = {
  start: { button: "start", label: "Start", icon: "mdi:play" },
  shutdown: { button: "shutdown", label: "Shutdown", icon: "mdi:power" },
  reboot: { button: "restart", label: "Reboot", icon: "mdi:restart" },
  "force-stop": { button: "stop", label: "Force stop", icon: "mdi:stop" },
  suspend: { button: "pause", label: "Suspend", icon: "mdi:pause" },
  resume: { button: "resume", label: "Resume", icon: "mdi:play" },
};
const ACTION_IDS = Object.keys(ACTIONS);
const NODE_ACTIONS = {
  shutdown: { button: "shutdown", label: "Shutdown", icon: "mdi:power" },
  reboot: { button: "reboot", label: "Reboot", icon: "mdi:restart" },
  start_all: { button: "start_all", label: "Start all guests", icon: "mdi:play-box-multiple-outline" },
  stop_all: { button: "stop_all", label: "Stop all guests", icon: "mdi:stop-circle-outline" },
  suspend_all: { button: "suspend_all", label: "Suspend all VMs", icon: "mdi:pause-circle-outline" },
};
const NODE_ACTION_IDS = Object.keys(NODE_ACTIONS);
const NODE_DEFAULT_CONFIRM = ["shutdown", "reboot", "stop_all", "suspend_all"];
const DEFAULT_CONFIRM = ["shutdown", "reboot", "force-stop", "suspend"];
const ACTION_ALIASES = { stop: "force-stop", restart: "reboot", pause: "suspend" };
const METRIC_KEYS = ["cpu", "memory", "disk", "network"];
const BAR_KEYS = ["cpu", "memory", "disk"];
const GESTURES = ["tap_action", "hold_action", "double_tap_action"];
const GRAPH_KEYS = ["cpu", "memory", "disk", "network"];
const GRAPH_DEFAULTS = { cpu: true, memory: true, disk: false, network: true };

function normalizeMachineConfig(raw) {
  if (!raw || typeof raw !== "object") throw new Error("Invalid configuration.");
  const c = { ...raw };
  if (c.device !== undefined && typeof c.device !== "string") throw new Error("device must be a Home Assistant device ID.");
  if (c.layout !== undefined && !["compact", "standard", "detailed"].includes(c.layout))
    throw new Error("layout must be compact, standard or detailed.");
  if (c.icon !== undefined && (typeof c.icon !== "string" || !c.icon.includes(":"))) throw new Error("icon must be an mdi: icon name.");
  for (const k of ["memory_percentage", "disk_percentage", "state_accent", "show_action_status"])
    if (c[k] !== undefined && typeof c[k] !== "boolean") throw new Error(`${k} must be true or false.`);
  for (const k of ["metrics", "actions", "entities"])
    if (c[k] !== undefined && (c[k] === null || typeof c[k] !== "object" || Array.isArray(c[k]))) throw new Error(`${k} must be a mapping.`);
  if (
    c.progress_bars !== undefined &&
    typeof c.progress_bars !== "boolean" &&
    (!c.progress_bars || typeof c.progress_bars !== "object" || Array.isArray(c.progress_bars))
  )
    throw new Error("progress_bars must be true/false or a mapping of cpu/memory/disk.");
  if (c.graphs !== undefined && typeof c.graphs !== "boolean") {
    if (!c.graphs || typeof c.graphs !== "object" || Array.isArray(c.graphs)) throw new Error("graphs must be true/false or a mapping of cpu/memory/disk/network.");
    const bad = Object.keys(c.graphs).find((k) => !GRAPH_KEYS.includes(k));
    if (bad) throw new Error(`Unknown graph "${bad}". Use: ${GRAPH_KEYS.join(", ")}.`);
  }
  for (const g of GESTURES)
    if (c[g] !== undefined && (!c[g] || typeof c[g] !== "object" || typeof c[g].action !== "string"))
      throw new Error(`${g} must be a Home Assistant action.`);
  if (c.entities)
    for (const [role, id] of Object.entries(c.entities)) {
      if (!(role in OVERRIDE_ROLES)) throw new Error(`Unknown entities role "${role}". Use: ${Object.keys(OVERRIDE_ROLES).join(", ")}.`);
      if (typeof id !== "string" || !id.startsWith("sensor.")) throw new Error(`entities.${role} must be a sensor entity ID.`);
    }
  if (c.confirm !== undefined) {
    if (!Array.isArray(c.confirm)) throw new Error("confirm must be a list of actions.");
    c.confirm = [...new Set(c.confirm.map((a) => ACTION_ALIASES[a] || a))];
    const bad = c.confirm.find((a) => !ACTIONS[a] && !NODE_ACTIONS[a]);
    if (bad) throw new Error(`Unknown action "${bad}" in confirm. Use: ${[...new Set([...ACTION_IDS, ...NODE_ACTION_IDS])].join(", ")}.`);
  }
  if (c.actions) {
    const a = {};
    for (const [k, v] of Object.entries(c.actions)) a[ACTION_ALIASES[k] || k] = v;
    c.actions = a;
  }
  /* options of the 0.1 container card */
  if (c.show_disk === false || c.show_network === false)
    c.metrics = { ...(c.show_disk === false ? { disk: false } : {}), ...(c.show_network === false ? { network: false } : {}), ...c.metrics };
  if (c.show_controls === false) c.actions = { ...Object.fromEntries(ACTION_IDS.map((a) => [a, false])), ...c.actions };
  delete c.show_disk;
  delete c.show_network;
  delete c.show_controls;
  return c;
}

class ProxmoxMachineCard extends BaseCard {
  static kindFilter = "any";
  static getStubConfig() {
    return { device: "" };
  }
  constructor() {
    super();
    this._dialog = document.createElement("dialog");
    this.shadowRoot.appendChild(this._dialog);
    this._dialog.addEventListener("close", () => (this._confirming = null));
    this._dialog.addEventListener("click", (e) => this._onDialogClick(e));
    this._setupGestures();
  }
  setConfig(raw) {
    const c = normalizeMachineConfig(raw);
    const prev = this._config;
    this._config = c;
    this._closeDialog();
    const rediscover = !prev || prev.device !== c.device || JSON.stringify(prev.entities) !== JSON.stringify(c.entities);
    const regraph = rediscover || JSON.stringify([prev.graphs, prev.graph_hours, prev.layout]) !== JSON.stringify([c.graphs, c.graph_hours, c.layout]);
    if (rediscover) {
      this._action = null;
      this._retried = false;
    }
    if (regraph) (this._hist = null), (this._histAt = 0);
    if (this._reg) {
      if (rediscover) this._bind();
      this._render();
      if (regraph) this._fetchHistory();
    }
  }
  async _load(force) {
    await super._load(force);
    this._fetchHistory();
  }
  connectedCallback() {
    clearInterval(this._histTimer);
    this._histTimer = setInterval(() => this._fetchHistory(true), 5 * 60 * 1000);
    if (this._guest) this._fetchHistory();
  }

  /* ----- history graphs */
  _graphOn(k) {
    const g = this._config.graphs;
    if (typeof g === "boolean") return g;
    return g && g[k] !== undefined ? !!g[k] : GRAPH_DEFAULTS[k];
  }
  _graphEntities() {
    const g = this._guest, s = g.sensors, ids = [];
    const add = (e) => enabled(e) && ids.push(e.entity_id);
    if (this._metricOn("cpu") && this._graphOn("cpu")) add(s.cpu);
    if (this._metricOn("memory") && this._graphOn("memory")) add(enabled(s.memory_percentage) ? s.memory_percentage : s.memory);
    if (this._metricOn("disk") && this._graphOn("disk")) add(s.disk);
    if (this._metricOn("network") && this._graphOn("network")) (add(s.rx_rate), add(s.tx_rate));
    return ids;
  }
  async _fetchHistory(force = false) {
    if (!this._hass || !this._guest || this._guest.error || this._histBusy || this._opt("layout", "standard") === "compact") return;
    if (!force && this._hist && Date.now() - (this._histAt || 0) < 4 * 60e3) return;
    const ids = this._graphEntities();
    if (!ids.length) return;
    this._histBusy = true;
    try {
      this._hist = await fetchSeries(this._hass, ids, Math.max(1, Number(this._config.graph_hours) || 24));
      this._histAt = Date.now();
    } finally {
      this._histBusy = false;
    }
    this._render();
  }
  _metricChart(key, layout, m, warn, crit) {
    if (layout === "compact" || !this._hist || !this._graphOn(key)) return "";
    const s = this._guest.sensors, H = this._hist;
    const series = (e) => (enabled(e) && H[e.entity_id]) || null;
    const asPct = (pts, total) => (pts && total ? pts.map(([t, v]) => [t, (v / total) * 100]) : null);
    let pts = null, cur = null;
    if (key === "cpu") (pts = series(s.cpu)), (cur = m.cpu);
    if (key === "memory") (pts = series(s.memory_percentage) || asPct(series(s.memory), m.memTotal)), (cur = m.memPct);
    if (key === "disk") (pts = asPct(series(s.disk), m.diskTotal)), (cur = m.diskPct);
    if (!pts || pts.length < 2) return "";
    if (cur !== null) pts = pts.concat([[Date.now(), cur]]);
    const peak = Math.max(...pts.map((x) => x[1]));
    const full = layout === "detailed";
    return buildChart({
      series: [{ pts, area: true }],
      lo: 0,
      hi: key === "cpu" ? Math.min(100, Math.max(10, peak * 1.2)) : 100, // CPU is scaled to its peak, memory/disk to 100 %
      height: full ? Math.max(24, Number(this._opt("graph_height", 44))) : 30,
      gutter: full ? 40 : 0,
      tone: level(cur, warn, crit),
      guides: key === "cpu" || !full ? [] : [{ v: warn, cls: "warn" }, { v: crit, cls: "crit" }],
    });
  }
  _netChart(m) {
    if (!this._hist || !this._graphOn("network")) return "";
    const s = this._guest.sensors, now = Date.now();
    const series = [];
    for (const [e, cur, label] of [[s.rx_rate, m.rx, "↓"], [s.tx_rate, m.tx, "↑"]]) {
      const pts = enabled(e) && this._hist[e.entity_id];
      if (pts && pts.length > 1) series.push({ pts: cur === null ? pts : pts.concat([[now, cur]]), area: label === "↓", label });
    }
    if (!series.length) return "";
    const peak = Math.max(...series.flatMap((x) => x.pts.map((p) => p[1])));
    return buildChart({ series, lo: 0, hi: Math.max(1, peak * 1.15), height: Math.max(24, Number(this._opt("graph_height", 44))), gutter: 58, fmt: "rate" });
  }
  getCardSize() {
    const l = this._config && this._config.layout;
    return l === "compact" ? 3 : l === "detailed" ? 6 : 5;
  }
  getGridOptions() {
    return { columns: 6, min_columns: 4 };
  }
  disconnectedCallback() {
    clearInterval(this._histTimer);
    this._closeDialog();
    clearTimeout(this._holdT);
    clearTimeout(this._tapT);
  }

  /* ----- config helpers */
  _opt(k, dflt) {
    return this._config[k] === undefined ? dflt : this._config[k];
  }
  _metricOn(k) {
    return !this._config.metrics || this._config.metrics[k] !== false;
  }
  _barOn(k) {
    const b = this._config.progress_bars;
    return b === undefined ? true : typeof b === "boolean" ? b : b[k] !== false;
  }
  _actionOn(id) {
    return !this._config.actions || this._config.actions[id] !== false;
  }
  _needsConfirm(id) {
    return (this._config.confirm || this._defaultConfirm()).includes(id);
  }
  /* hooks the node card overrides */
  _acts() {
    return ACTIONS;
  }
  _defaultConfirm() {
    return DEFAULT_CONFIRM;
  }
  _layoutActions(state) {
    const acts = this._visibleActions(state);
    const overflowStop = acts.includes("force-stop") && !!this._guest.buttons.shutdown; // force stop lives in ⋮ when a graceful shutdown exists
    return { inline: acts.filter((a) => !(a === "force-stop" && overflowStop)), menu: overflowStop ? ["force-stop"] : [] };
  }
  _danger(id) {
    return id === "force-stop";
  }
  _menuDesc() {
    return `Immediately stops the ${this._noun()}`;
  }
  _dialogText(id) {
    const noun = this._noun();
    return {
      "force-stop": `This immediately stops the ${noun} without a graceful shutdown and may cause data loss.`,
      shutdown: `This requests a graceful shutdown of the ${noun}.`,
      reboot: `This restarts the ${noun} and interrupts its services.`,
      suspend: `This suspends the ${noun} in memory and pauses its services.`,
      start: `This starts the ${noun}.`,
      resume: `This resumes the suspended ${noun}.`,
    }[id];
  }
  _actionDone(id, before, now, uptimeBefore, uptimeNow) {
    if (id === "start" || id === "resume") return now === "running";
    if (id === "shutdown" || id === "force-stop") return now === "stopped";
    if (id === "suspend") return now === "suspended";
    return now !== before || (uptimeBefore !== null && uptimeNow !== null && uptimeNow < uptimeBefore);
  }
  _dialogTitle(id, name) {
    return `${this._label(id)} ${name}?`;
  }
  _stateLabel(state) {
    return STATE_LABEL[state];
  }
  _pickText() {
    return `Choose a Proxmox ${this.constructor.kindFilter === "vm" ? "VM" : "container or VM"} in the card editor.`;
  }
  _subtitle(g) {
    return [g.node, `${g.kind === "vm" ? "VM" : "CT"} ${g.vmid}`];
  }
  _facts(g, state, m) {
    return [
      ["Node", g.node || "–"],
      ["ID", `${g.kind === "vm" ? "VM" : "CT"} ${g.vmid}`],
      ["State", STATE_LABEL[state]],
      ["Uptime", state === "running" && m.uptime !== null ? fmtUptime(m.uptime) : "–"],
      ["Device", g.deviceId],
    ];
  }
  _extraSections() {
    return [];
  }
  _noun() {
    return this._guest && this._guest.kind === "lxc" ? "container" : "VM";
  }
  _label(id) {
    if (id === "force-stop" && this._guest && this._guest.kind === "lxc" && !this._guest.buttons.shutdown) return "Stop";
    return this._acts()[id].label;
  }

  /* ----- discovery */
  _bind() {
    this._guest = null;
    const c = this._config;
    if (!c || !c.device) return;
    const g = discoverGuest(this._reg, c.device, c.entities);
    if (g.error && !this._retried && /not found/.test(g.error)) {
      this._retried = true;
      this._load(true);
      return;
    }
    if (!g.error && this.constructor.kindFilter !== "any" && g.kind !== this.constructor.kindFilter)
      g.error = `This is a Proxmox ${g.kind === "lxc" ? "container" : "VM"}. Use custom:proxmox-guest-card for it.`;
    this._guest = g;
  }
  _watched() {
    const g = this._guest;
    if (!g || g.error) return [];
    return [...Object.values(g.sensors), g.running].filter(Boolean).map((e) => e.entity_id);
  }
  _gestureEntity() {
    const g = this._guest;
    if (!g || g.error) return undefined;
    return (g.sensors.status || g.running || g.sensors.cpu || {}).entity_id;
  }

  /* ----- lifecycle actions */
  _visibleActions(state) {
    const allowed =
      state === "running"
        ? ["shutdown", "reboot", "suspend", "force-stop"]
        : state === "stopped"
          ? ["start"]
          : state === "suspended"
            ? ["resume", "force-stop"]
            : [];
    return allowed.filter((id) => this._actionOn(id) && enabled(this._guest.buttons[this._acts()[id].button]));
  }
  _request(id) {
    if (this._confirming || (this._action && this._action.pending)) return;
    const details = this._root.querySelector("details");
    if (details) details.open = false;
    this._menuOpen = false;
    if (this._needsConfirm(id)) this._openDialog(id);
    else this._press(id);
  }
  _openDialog(id) {
    const g = this._guest;
    const text = this._dialogText(id);
    this._confirming = { id, deviceId: g.deviceId };
    this._dialog.innerHTML = `<h2>${esc(this._dialogTitle(id, this._config.title || g.name))}</h2><p>${text}</p>
      <div class="drow"><button class="btn" data-dlg="cancel" autofocus>Cancel</button>
      <button class="btn solid ${this._danger(id) ? "danger" : ""}" data-dlg="go">${esc(this._label(id))}</button></div>`;
    try {
      this._dialog.showModal();
    } catch {
      this._dialog.setAttribute("open", "");
    }
  }
  _closeDialog() {
    if (this._dialog && this._dialog.open) this._dialog.close();
    this._confirming = null;
  }
  _onDialogClick(e) {
    if (e.target === this._dialog) return this._closeDialog(); // backdrop click
    const b = e.target.closest("[data-dlg]");
    if (!b) return;
    const pending = this._confirming;
    this._closeDialog();
    if (b.dataset.dlg === "go" && pending) this._press(pending.id, pending.deviceId);
  }
  async _press(id, expectedDevice) {
    const g = this._guest;
    const state = guestState(this._hass, g);
    if ((expectedDevice && expectedDevice !== g.deviceId) || !this._visibleActions(state).includes(id)) {
      this._action = { error: `The ${this._noun()}'s state changed. Check the card before trying again.` };
      return this._render();
    }
    const label = this._label(id);
    const uptime = guestMetrics(this._hass, g).uptime;
    this._action = { pending: true, message: `Sending ${label}…` };
    this._render();
    try {
      await this._hass.callService("button", "press", { entity_id: g.buttons[this._acts()[id].button].entity_id });
      this._action = { message: `${label} sent. Waiting for Proxmox to report the new state…`, awaiting: { id, state, uptime, at: Date.now() } };
    } catch (e) {
      this._action = { error: e && e.message ? e.message : "Home Assistant could not send the action." };
    }
    this._render();
  }
  _beforeRender() {
    const a = this._action;
    if (!a || !a.awaiting || !this._guest || this._guest.error) return;
    const { id, state, uptime, at } = a.awaiting;
    const now = guestState(this._hass, this._guest);
    const up = guestMetrics(this._hass, this._guest).uptime;
    const done = this._actionDone(id, state, now, uptime, up, at);
    if (done || Date.now() - at > 120000) this._action = null;
  }

  /* ----- disabled sensor onboarding */
  _disabledHint() {
    const g = this._guest;
    const wanted = [];
    if (this._metricOn("memory") && (!enabled(g.sensors.memory_percentage) || !enabled(g.sensors.max_memory)))
      wanted.push("memory", "max_memory", "memory_percentage");
    if (this._metricOn("disk") && (g.kind === "lxc" || g.kind === "node")) wanted.push("disk", "max_disk");
    if (this._metricOn("network")) wanted.push("netin", "netout");
    const keys = [...new Set(wanted)].filter((k) => g.sensors[k] && g.sensors[k].disabled_by);
    this._disabledEntities = keys.map((k) => g.sensors[k]);
    if (!keys.length || this._config.hide_disabled_hint) return "";
    if (this._enableState === "done") return `<div class="hint muted">Sensors enabled. Home Assistant adds them in about 30 seconds.</div>`;
    const names = { memory: "memory", max_memory: "memory size", memory_percentage: "memory %", disk: "disk", max_disk: "disk size", netin: "network in", netout: "network out" };
    const admin = this._hass.user && this._hass.user.is_admin;
    return `<div class="hint muted">${keys.length} sensor${keys.length > 1 ? "s" : ""} disabled (${esc(keys.map((k) => names[k]).join(", "))}).
      ${admin ? `<button class="link" data-enable>${this._enableState === "busy" ? "Enabling…" : "Enable"}</button>` : "Ask an administrator to enable them."}</div>`;
  }
  async _enable() {
    this._enableState = "busy";
    this._render();
    try {
      for (const e of this._disabledEntities || [])
        await this._hass.callWS({ type: "config/entity_registry/update", entity_id: e.entity_id, disabled_by: null });
      this._enableState = "done";
      this._render();
      setTimeout(() => {
        this._enableState = null;
        this._load(true);
      }, 35000);
    } catch (e) {
      this._enableState = null;
      this._action = { error: `Could not enable sensors: ${e.message || e}` };
      this._render();
    }
  }

  /* ----- rendering */
  _html() {
    const c = this._config;
    if (this._error) return `<ha-card><div class="msg err">${esc(this._error)}</div></ha-card>`;
    if (!c || !c.device)
      return `<ha-card><div class="msg">${this._pickText()}</div></ha-card>`;
    if (!this._guest) return `<ha-card><div class="msg">Loading…</div></ha-card>`;
    const g = this._guest;
    if (g.error) return `<ha-card><div class="msg err">${esc(g.error)} <button class="link" data-retry>Retry</button></div></ha-card>`;

    const hass = this._hass;
    const layout = this._opt("layout", "standard");
    const accent = this._opt("state_accent", true);
    const state = guestState(hass, g);
    const m = guestMetrics(hass, g);
    const live = state === "running" || state === "unknown";
    const warn = this._opt("warning", 75), crit = this._opt("critical", 90);
    const statusText = state === "running" && m.uptime !== null ? fmtUptime(m.uptime) : this._stateLabel(state);

    /* metric tiles */
    const tiles = [];
    const tile = (key, label, value, total, pct, showPct) => {
      const chart = this._metricChart(key, layout, m, warn, crit);
      const bar = this._barOn(key)
        ? `<div class="bar"><i class="${level(pct, warn, crit)}" style="width:${pct === null ? 0 : Math.min(100, pct)}%"></i></div>`
        : "";
      const pctText = showPct && pct !== null && key !== "cpu" ? fmtPct(pct) : "";
      if (layout === "detailed")
        return `<div class="mrow ${chart ? "has-chart" : ""}"><div class="ml"><span class="label">${label}</span>
          <span class="num"><b>${value}</b>${total ? `<span class="muted"> of ${total}</span>` : ""}${pctText ? `<span class="muted"> · ${pctText}</span>` : ""}</span></div>${chart}${bar}</div>`;
      return `<div class="tile ${chart ? "has-chart" : ""}"><div class="tl"><span class="label">${label}</span><span class="muted pct num">${pctText}</span></div>
        <div class="val num">${value}</div>${chart}${bar}${layout === "compact" ? "" : `<div class="of muted num">${total ? `of ${total}` : "&nbsp;"}</div>`}</div>`;
    };
    if (live) {
      if (this._metricOn("cpu") && enabled(g.sensors.cpu)) tiles.push(tile("cpu", "CPU", m.cpu === null ? "Unavailable" : fmtPct(m.cpu), "", m.cpu, true));
      if (this._metricOn("memory") && (enabled(g.sensors.memory) || (enabled(g.sensors.memory_percentage) && enabled(g.sensors.max_memory))))
        tiles.push(
          tile("memory", "Memory", m.mem === null ? "Unavailable" : fmtBytes(m.mem), m.memTotal !== null ? fmtBytes(m.memTotal) : "", m.memPct, this._opt("memory_percentage", true)),
        );
      /* Proxmox reports 0 disk usage for VMs without the guest agent: hide that tile unless asked for explicitly */
      if (this._metricOn("disk") && enabled(g.sensors.disk) && !(g.kind === "vm" && m.disk === 0 && !(c.metrics && c.metrics.disk === true)))
        tiles.push(
          tile("disk", "Disk", m.disk === null ? "Unavailable" : fmtBytes(m.disk), m.diskTotal !== null ? fmtBytes(m.diskTotal) : "", m.diskPct, this._opt("disk_percentage", true)),
        );
    }

    const sections = [];
    if (state === "unavailable") {
      const noun = this._noun();
      sections.push(`<div class="msg">${noun === "VM" ? noun : noun[0].toUpperCase() + noun.slice(1)} unavailable. Home Assistant can't read it right now.</div>`);
    }
    if (tiles.length)
      sections.push(layout === "detailed" ? `<div class="mrows">${tiles.join("")}</div>` : `<div class="tiles" style="--cols:${tiles.length}">${tiles.join("")}</div>`);
    sections.push(...this._extraSections(g, m, state, layout));
    const hasRate = enabled(g.sensors.rx_rate) || enabled(g.sensors.tx_rate);
    const hasTotals = enabled(g.sensors.netin) || enabled(g.sensors.netout);
    if (live && layout !== "compact" && this._metricOn("network") && hasRate)
      sections.push(`<div class="netsec">
        <div class="nh"><span class="label">Network</span><span class="nr num">
          ${enabled(g.sensors.rx_rate) ? `<span><i class="sw s0"></i>↓ ${m.rx === null ? "–" : fmtRate(m.rx)}</span>` : ""}
          ${enabled(g.sensors.tx_rate) ? `<span><i class="sw s1"></i>↑ ${m.tx === null ? "–" : fmtRate(m.tx)}</span>` : ""}</span></div>
        ${this._netChart(m)}
        ${hasTotals ? `<div class="ntot muted num">Since start: ↓ ${fmtBytes(m.netin, false)} received · ↑ ${fmtBytes(m.netout, false)} sent</div>` : ""}
      </div>`);
    else if (live && layout !== "compact" && this._metricOn("network") && hasTotals)
      sections.push(`<div class="net"><div><div class="label">↓ Received</div><div class="nv num">${fmtBytes(m.netin, false)}</div></div>
        <div><div class="label">↑ Sent</div><div class="nv num">${fmtBytes(m.netout, false)}</div></div>
        <div class="netnote muted">Cumulative totals since start</div></div>`);
    if (layout === "detailed") {
      const facts = this._facts(g, state, m);
      sections.push(`<dl>${facts.map(([k, v]) => `<dt class="label">${k}</dt><dd class="num">${esc(v)}</dd>`).join("")}</dl>`);
    }
    const hint = this._disabledHint();

    /* controls */
    const acts = this._visibleActions(state);
    const { inline, menu: menuIds } = this._layoutActions(state);
    const busy = this._action && this._action.pending;
    let controls = "";
    if (acts.length || (this._action && this._action.error)) {
      const btn = (id) =>
        `<button class="btn ${this._danger(id) ? "danger" : ""}" data-act="${id}" ${busy ? "disabled" : ""}><ha-icon icon="${this._acts()[id].icon}"></ha-icon><span>${esc(this._label(id))}</span></button>`;
      const menu = menuIds.length
        ? `<details class="more" ${this._menuOpen ? "open" : ""}><summary class="btn" aria-label="More actions">⋮</summary><div class="menu">${menuIds
            .map(
              (id) => `<button class="mi ${this._danger(id) ? "danger" : ""}" data-act="${id}" ${busy ? "disabled" : ""}><ha-icon icon="${this._acts()[id].icon}"></ha-icon>
              <span>${esc(this._label(id))}<small>${esc(this._menuDesc(id))}</small></span></button>`,
            )
            .join("")}</div></details>`
        : "";
      const a = this._action;
      const status =
        a && a.error
          ? `<div class="status err">Last error: ${esc(a.error)}</div>`
          : a && a.message && this._opt("show_action_status", true)
            ? `<div class="status muted">${esc(a.message)}</div>`
            : "";
      controls = `${acts.length ? `<div class="row">${inline.map(btn).join("")}${menu}</div>` : ""}${status}`;
    }

    const blocks = [sections.join(`<div class="sep"></div>`) + hint, controls].filter(Boolean);
    return `<ha-card class="${layout} ${accent ? "accent" : ""} ${c.embedded ? "embedded" : ""}" data-state="${state}" tabindex="0" aria-label="${esc(c.title || g.name)}">
      <div class="head">
        <div class="badge"><ha-icon icon="${esc(c.icon || ICONS[g.kind])}"></ha-icon></div>
        <div class="titles"><div class="title">${esc(c.title || g.name)}</div>
          <div class="sub">${esc(this._subtitle(g).filter(Boolean).join(" · "))}</div></div>
        <span class="state" title="${this._stateLabel(state)}"><span class="dot ${state}"></span>${esc(statusText)}</span>
      </div>
      ${this._gestureError ? `<div class="status err">${esc(this._gestureError)}</div>` : ""}
      ${blocks.map((b) => `<div class="sep"></div>${b}`).join("")}
    </ha-card>`;
  }
  _wire() {
    const r = this._root;
    r.querySelectorAll("[data-act]").forEach((el) => (el.onclick = () => this._request(el.dataset.act)));
    const en = r.querySelector("[data-enable]");
    if (en) en.onclick = () => this._enable();
    const retry = r.querySelector("[data-retry]");
    if (retry) retry.onclick = () => ((this._retried = false), this._load(true));
    const d = r.querySelector("details.more");
    if (d) d.ontoggle = () => (this._menuOpen = d.open);
    wireCharts(r);
  }

  /* ----- tap / hold / double tap on the card surface */
  _setupGestures() {
    const onSurface = (e) =>
      !e.composedPath().some((n) => n instanceof Element && n.matches("button, summary, details, dialog, a, input, select, textarea"));
    const has = (g) => {
      const a = this._config && this._config[g];
      return !!a && a.action !== "none";
    };
    this.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || !onSurface(e)) return;
      this._pt = { x: e.clientX, y: e.clientY, held: false, moved: false };
      clearTimeout(this._holdT);
      if (has("hold_action"))
        this._holdT = setTimeout(() => {
          if (this._pt) this._pt.held = true;
          this._gesture("hold");
        }, 500);
    });
    this.addEventListener("pointermove", (e) => {
      if (this._pt && Math.hypot(e.clientX - this._pt.x, e.clientY - this._pt.y) > 10) {
        this._pt.moved = true;
        clearTimeout(this._holdT);
      }
    });
    this.addEventListener("pointerup", () => clearTimeout(this._holdT));
    this.addEventListener("pointercancel", () => {
      clearTimeout(this._holdT);
      if (this._pt) this._pt.moved = true;
    });
    this.addEventListener("contextmenu", (e) => has("hold_action") && onSurface(e) && e.preventDefault());
    this.addEventListener("click", (e) => {
      if (!onSurface(e)) return;
      const pt = this._pt;
      this._pt = null;
      if (pt && (pt.held || pt.moved)) return;
      if (!has("double_tap_action")) return this._gesture("tap");
      if (this._tapT) {
        clearTimeout(this._tapT);
        this._tapT = null;
        this._gesture("double_tap");
      } else this._tapT = setTimeout(() => ((this._tapT = null), this._gesture("tap")), 250);
    });
    this.addEventListener("keydown", (e) => {
      const t = e.composedPath()[0];
      if ((e.key === "Enter" || e.key === " ") && t && t.tagName === "HA-CARD") {
        e.preventDefault();
        this._gesture("tap");
      }
    });
  }
  async _gesture(kind) {
    if (!this._hass || !this._config || this._confirming) return;
    const action = this._config[`${kind}_action`] || { action: kind === "tap" ? "more-info" : "none" };
    try {
      await runCardAction(this, this._hass, action, this._gestureEntity());
      if (this._gestureError) {
        this._gestureError = null;
        this._render();
      }
    } catch (e) {
      this._gestureError = e.message || String(e);
      this._render();
    }
  }
}
ProxmoxMachineCard.css = `
  ha-card { container-type: inline-size; cursor: pointer; outline: none; }
  ha-card.embedded { box-shadow: none; border: none; border-radius: 0; background: transparent; padding: 4px 0 2px; }
  ha-card.embedded > .head { display: none; }
  ha-card.embedded > .sep:first-of-type { display: none; }
  ha-card:focus-visible { box-shadow: 0 0 0 2px var(--pxc-accent); }
  .accent[data-state="running"] .badge { background: color-mix(in srgb, var(--pxc-ok) 14%, transparent); color: var(--pxc-ok); }
  .accent[data-state="suspended"] .badge { background: color-mix(in srgb, var(--pxc-warn) 16%, transparent); color: var(--pxc-warn); }
  .accent[data-state="stopped"] .badge, .accent[data-state="unavailable"] .badge { background: var(--pxc-fill); color: var(--pxc-muted); }
  ha-card:not(.accent) .dot { background: var(--pxc-muted); box-shadow: none; }
  .tiles { display: grid; grid-template-columns: repeat(var(--cols), minmax(0, 1fr)); gap: 16px; }
  .tile { min-width: 0; }
  .tl { display: flex; justify-content: space-between; align-items: baseline; gap: 4px; }
  .pct { font-size: 12px; }
  .val { font-size: 20px; font-weight: 600; margin: 6px 0 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .of { font-size: 11.5px; margin-top: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .mrows { display: flex; flex-direction: column; gap: 12px; }
  .ml { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 6px; font-size: 14px; }
  .ml b { font-weight: 600; }
  .mrow .bar { height: 8px; border-radius: 4px; }
  .net { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 16px; }
  .nv { font-size: 16px; font-weight: 600; margin-top: 4px; }
  .netnote { grid-column: 1 / -1; font-size: 11px; margin-top: 4px; }
  .tile .chart { margin-bottom: 0; }
  .tile.has-chart .val { margin-bottom: 4px; }
  .mrow .chart { margin-top: 2px; }
  .mrow.has-chart .bar { height: 6px; border-radius: 0 0 4px 4px; }
  .nh { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-bottom: 8px; }
  .nr { display: inline-flex; gap: 14px; font-size: 14px; font-weight: 600; }
  .nr span { display: inline-flex; align-items: center; gap: 6px; }
  .sw { display: inline-block; width: 10px; height: 3px; border-radius: 2px; background: var(--pxc-accent); }
  .sw.s1 { background: var(--pxc-alt); }
  .netsec .chart { border-bottom: 1px solid var(--pxc-line); }
  .ntot { font-size: 11.5px; margin-top: 6px; }
  dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 16px; margin: 0; font-size: 13px; align-items: baseline; }
  dd { margin: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row { display: flex; gap: 6px; }
  .status { font-size: 12px; margin-top: 8px; }
  .hint { font-size: 12px; margin-top: 12px; }
  details.more { position: relative; flex: none; display: flex; }
  details.more summary { list-style: none; width: 34px; flex: none; font-size: 16px; box-sizing: border-box; }
  details.more summary::-webkit-details-marker { display: none; }
  .menu {
    position: absolute; right: 0; bottom: calc(100% + 6px); z-index: 5; min-width: 210px; padding: 4px; border-radius: 10px;
    background: var(--ha-card-background, var(--card-background-color, #fff)); border: 1px solid var(--pxc-line);
    box-shadow: 0 8px 24px rgba(0,0,0,.18);
  }
  .mi { all: unset; box-sizing: border-box; width: 100%; display: flex; gap: 10px; align-items: flex-start; padding: 8px 10px; border-radius: 6px; cursor: pointer; font-size: 13px; }
  .mi:hover { background: var(--pxc-fill); }
  .mi.danger { color: var(--pxc-crit); }
  .mi ha-icon { --mdc-icon-size: 18px; }
  .mi small { display: block; color: var(--pxc-muted); font-size: 11.5px; margin-top: 2px; }
  .compact { padding: 12px 14px; }
  .compact .sep { margin: 10px 0; }
  .compact .val { font-size: 16px; margin: 3px 0 6px; }
  .compact .badge { width: 34px; height: 34px; border-radius: 10px; }
  .compact .title { font-size: 15px; }
  .compact .tiles { gap: 12px; }
  dialog {
    max-width: min(420px, calc(100vw - 64px)); border: 1px solid var(--pxc-line); border-radius: 14px; padding: 22px;
    background: var(--ha-card-background, var(--card-background-color, #fff)); color: var(--primary-text-color);
    font-family: inherit; box-shadow: 0 12px 40px rgba(0,0,0,.3);
  }
  dialog::backdrop { background: rgba(0,0,0,.45); }
  dialog h2 { font-size: 18px; margin: 0 0 8px; }
  dialog p { margin: 0 0 18px; line-height: 1.5; color: var(--pxc-muted); font-size: 14px; }
  dialog .drow { display: flex; gap: 8px; justify-content: flex-end; }
  dialog .btn { flex: 0 0 auto; padding: 8px 16px; }
  @container (max-width: 300px) { .tiles { grid-template-columns: 1fr 1fr; } }
`;

class ProxmoxVmCard extends ProxmoxMachineCard {
  static kindFilter = "vm";
  static getConfigElement() {
    return document.createElement("proxmox-vm-card-editor");
  }
}
class ProxmoxGuestCard extends ProxmoxMachineCard {
  static kindFilter = "any";
  static getConfigElement() {
    return document.createElement("proxmox-guest-card-editor");
  }
}

/* ============================================================== node card === */

function rawReading(hass, entity) {
  const st = stateOf(hass, entity);
  if (!st || BAD.has(st.state)) return null;
  const n = Number(st.state);
  return Number.isFinite(n) ? { n, unit: st.attributes.unit_of_measurement || "", state: st } : null;
}

class ProxmoxNodeCard extends ProxmoxMachineCard {
  static kindFilter = "node";
  static getConfigElement() {
    return document.createElement("proxmox-node-card-editor");
  }
  getCardSize() {
    return super.getCardSize() + 1;
  }
  _bind() {
    this._guest = null;
    const c = this._config;
    if (!c || !c.device) return;
    const g = discoverNode(this._reg, c.device, c.entities);
    if (g.error && !this._retried && /not found/.test(g.error)) {
      this._retried = true;
      this._load(true);
      return;
    }
    this._guest = g;
  }
  _watched() {
    const g = this._guest;
    if (!g || g.error) return [];
    return [
      ...super._watched(),
      ...[g.backupProblem].filter(Boolean).map((e) => e.entity_id),
      ...g.guests.flatMap((x) => [x.sensors.status, x.running].filter(Boolean).map((e) => e.entity_id)),
    ];
  }
  _acts() {
    return NODE_ACTIONS;
  }
  _defaultConfirm() {
    return NODE_DEFAULT_CONFIRM;
  }
  _noun() {
    return "host";
  }
  _label(id) {
    return NODE_ACTIONS[id].label;
  }
  _visibleActions(state) {
    if (state !== "running") return []; // every node action goes through the Proxmox API of the running host
    return NODE_ACTION_IDS.filter((id) => this._actionOn(id) && enabled(this._guest.buttons[NODE_ACTIONS[id].button]));
  }
  _layoutActions(state) {
    const acts = this._visibleActions(state);
    return { inline: acts.filter((a) => a === "shutdown" || a === "reboot"), menu: acts.filter((a) => a !== "shutdown" && a !== "reboot") };
  }
  _danger(id) {
    return id === "shutdown" || id === "stop_all";
  }
  _menuDesc(id) {
    return {
      start_all: "Starts the guests set to start at boot",
      stop_all: "Shuts down every VM and container",
      suspend_all: "Suspends every running VM",
    }[id] || "";
  }
  _dialogText(id) {
    const n = this._guest.guests.filter((x) => guestState(this._hass, x) === "running").length;
    const running = n ? ` ${n} running guest${n > 1 ? "s" : ""} will be affected.` : "";
    return {
      shutdown: `This shuts down the Proxmox host. Its guests are shut down first, and the host must be powered on again by hand or via Wake-on-LAN.${running}`,
      reboot: `This reboots the Proxmox host. All guests on it are interrupted while it restarts.${running}`,
      start_all: "This starts all guests on this host that are set to start at boot.",
      stop_all: `This shuts down every VM and container on this host.${running}`,
      suspend_all: "This suspends every running VM on this host.",
    }[id];
  }
  _actionDone(id, before, now, uptimeBefore, uptimeNow, at) {
    if (id === "shutdown") return now === "stopped";
    if (id === "reboot") return now !== before || (uptimeBefore !== null && uptimeNow !== null && uptimeNow < uptimeBefore);
    return Date.now() - at > 20000; // bulk guest actions: no single state to watch
  }
  _pickText() {
    return "Choose a Proxmox host (node) in the card editor.";
  }
  _stateLabel(state) {
    return state === "running" ? "Online" : state === "stopped" ? "Offline" : STATE_LABEL[state];
  }
  _dialogTitle(id, name) {
    return id === "shutdown" || id === "reboot" ? `${this._label(id)} ${name}?` : `${this._label(id)} on ${name}?`;
  }
  _cores() {
    const r = rawReading(this._hass, this._guest.sensors.max_cpu);
    return r ? r.n : null;
  }
  _subtitle(g) {
    const cores = this._cores();
    const vms = g.guests.filter((x) => x.kind === "vm").length, cts = g.guests.length - vms;
    const parts = [cores ? `${cores} CPUs` : "", [vms ? `${vms} VM${vms > 1 ? "s" : ""}` : "", cts ? `${cts} CT${cts > 1 ? "s" : ""}` : ""].filter(Boolean).join(", ")];
    return parts.some(Boolean) ? parts : ["Proxmox VE host"];
  }
  _facts(g, state, m) {
    const cores = this._cores();
    return [
      ["Node", g.node],
      ["State", this._stateLabel(state)],
      ["Uptime", state === "running" && m.uptime !== null ? fmtUptime(m.uptime) : "–"],
      ["CPUs", cores ? String(cores) : "–"],
      ["Memory", m.memTotal !== null ? fmtBytes(m.memTotal) : "–"],
      ["Device", g.deviceId],
    ];
  }
  _extraSections(g, m, state, layout) {
    if (state !== "running" && state !== "unknown") return [];
    const out = [];
    const hass = this._hass, c = this._config, s = g.sensors;

    /* optional host health sensors (e.g. Proxmox Extended Sensors) */
    const chips = [];
    const chip = (icon, text, title, lv = "") =>
      chips.push(`<span class="chip ${lv}" title="${esc(title)}"><ha-icon icon="${icon}"></ha-icon><span class="num">${esc(text)}</span></span>`);
    const t = rawReading(hass, s.temperature);
    if (t) chip("mdi:thermometer", `${t.n.toFixed(0)} ${t.unit || "°C"}`, "CPU temperature", level(t.n, this._opt("temp_warning", 70), this._opt("temp_critical", 85)));
    const ld = rawReading(hass, s.load);
    if (ld) chip("mdi:gauge", `${ld.n.toFixed(2)}`, "Load average", this._cores() ? level((ld.n / this._cores()) * 100, 80, 100) : "");
    const io = rawReading(hass, s.iowait);
    if (io) chip("mdi:timer-sand", `${io.n.toFixed(1)}% IO`, "I/O wait", level(io.n, 10, 25));
    const sw = rawReading(hass, s.swap);
    if (sw) chip("mdi:swap-horizontal", sw.unit === "%" ? `${sw.n.toFixed(0)}% swap` : `${fmtBytes(sw.n * (BYTE_UNITS[sw.unit] || 1))} swap`, "Swap usage", sw.unit === "%" ? level(sw.n, this._opt("warning", 75), this._opt("critical", 90)) : "");
    const up = rawReading(hass, s.updates);
    if (up) chip("mdi:package-up", up.n ? `${up.n.toFixed(0)} update${up.n === 1 ? "" : "s"}` : "Up to date", "Pending updates", up.n ? "info" : "");
    if (chips.length && this._opt("show_health", true)) out.push(`<div class="chips">${chips.join("")}</div>`);

    /* guests on this host */
    if (this._opt("show_guests", true) && g.guests.length) {
      const counts = { running: 0, stopped: 0, suspended: 0, other: 0 };
      for (const x of g.guests) {
        const st = guestState(hass, x);
        counts[st in counts ? st : "other"]++;
      }
      const vms = g.guests.filter((x) => x.kind === "vm").length, cts = g.guests.length - vms;
      const part = (k, label) => (counts[k] ? `<span><i class="dot ${k === "other" ? "unknown" : k}"></i>${counts[k]} ${label}</span>` : "");
      out.push(`<div class="gsum"><span class="label">Guests</span>
        <span class="gs num" title="${vms} VM${vms === 1 ? "" : "s"}, ${cts} container${cts === 1 ? "" : "s"}">${part("running", "running")}${part("suspended", "suspended")}${part("stopped", "stopped")}${part("other", "unknown")}</span></div>`);
    }

    /* last backup (sensors are disabled by default in the integration) */
    if (this._opt("show_backup", true) && (enabled(s.backup_last) || enabled(g.backupProblem))) {
      const last = stateOf(hass, s.backup_last);
      const when = last && !BAD.has(last.state) ? Date.parse(last.state) : NaN;
      const dur = readNumber(hass, s.backup_duration, "uptime");
      const prob = stateOf(hass, g.backupProblem);
      const failed = prob && prob.state === "on";
      out.push(`<div class="bk"><span class="label">Last backup</span>
        <span class="num">${Number.isFinite(when) ? `${fmtUptime(Math.max(0, (Date.now() - when) / 1000))} ago` : "–"}${dur !== null ? `<span class="muted"> · took ${fmtUptime(dur) === "<1m" ? `${Math.round(dur)}s` : fmtUptime(dur)}</span>` : ""}</span>
        ${failed ? `<span class="tag">failed</span>` : prob && prob.state === "off" ? `<span class="tag ok">OK</span>` : ""}</div>`);
    }
    return out.length ? [`<div class="hostinfo">${out.join("")}</div>`] : [];
  }
}
ProxmoxNodeCard.css =
  ProxmoxMachineCard.css +
  `
  .chips { display: flex; flex-wrap: wrap; gap: 6px; }
  .chip { display: inline-flex; align-items: center; gap: 5px; padding: 4px 9px 4px 7px; border-radius: 999px; font-size: 12.5px; background: var(--pxc-fill); border: 1px solid var(--pxc-line); }
  .chip ha-icon { --mdc-icon-size: 15px; color: var(--pxc-muted); }
  .chip.warn { color: var(--pxc-warn); border-color: color-mix(in srgb, var(--pxc-warn) 40%, transparent); }
  .chip.crit { color: var(--pxc-crit); border-color: color-mix(in srgb, var(--pxc-crit) 40%, transparent); }
  .chip.warn ha-icon, .chip.crit ha-icon { color: inherit; }
  .chip.info { color: var(--pxc-accent); }
  .chip.info ha-icon { color: inherit; }
  .gsum, .bk { display: flex; align-items: baseline; gap: 12px; font-size: 13px; flex-wrap: wrap; }
  .gs { display: inline-flex; gap: 12px; flex-wrap: wrap; }
  .gs span { display: inline-flex; align-items: center; gap: 6px; }
  .gs .dot { width: 7px; height: 7px; }
  .gs .dot.running { box-shadow: none; }
  .tag { font-size: 10.5px; padding: 1px 6px; border-radius: 6px; background: color-mix(in srgb, var(--pxc-crit) 14%, transparent); color: var(--pxc-crit); }
  .tag.ok { background: color-mix(in srgb, var(--pxc-ok) 14%, transparent); color: var(--pxc-ok); }
  .hostinfo { display: flex; flex-direction: column; gap: 10px; }
`;

/* ========================================================== overview card === */
/*  One card for a whole Proxmox instance: problems, hosts, storage, guests.     */
/*  Rows expand into the real guest/host cards (without controls when read-only). */

const OV_SECTIONS = ["problems", "hosts", "storage", "guests"];

class ProxmoxOverviewCard extends BaseCard {
  static getStubConfig() {
    return {};
  }
  static getConfigElement() {
    return document.createElement("proxmox-overview-card-editor");
  }
  constructor() {
    super();
    this._expanded = new Set();
    this._embeds = new Map();
    this._filter = "all";
    this._starting = {};
  }
  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration.");
    if (config.show !== undefined && !["all", "lxc", "vm"].includes(config.show)) throw new Error("show must be all, lxc or vm.");
    this._config = {
      title: "Proxmox",
      read_only: true,
      show: "all",
      sort: "status",
      group_by_host: false,
      storage_rows: 6,
      hide_inactive_storage: true,
      warning: 75,
      critical: 90,
      temp_warning: 70,
      temp_critical: 85,
      show_problems: true,
      show_hosts: true,
      show_storage: true,
      show_guests: true,
      ...config,
    };
    for (const el of this._embeds.values()) el.remove();
    this._embeds.clear();
    if (this._reg) {
      this._bind();
      this._render();
    }
  }
  getCardSize() {
    return 3 + (this._hosts ? this._hosts.length : 1) + Math.min(6, this._config.storage_rows || 0) / 2 + (this._guests ? this._guests.length : 4);
  }
  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }
  set hass(hass) {
    super.hass = hass;
    for (const el of this._embeds.values()) el.hass = hass;
  }
  get hass() {
    return this._hass;
  }
  connectedCallback() {
    clearInterval(this._histTimer);
    this._histTimer = setInterval(() => this._fetchHistory(true), 5 * 60 * 1000);
    if (this._hosts) this._fetchHistory();
  }
  disconnectedCallback() {
    clearInterval(this._histTimer);
  }
  async _load(force) {
    await super._load(force);
    this._fetchHistory();
  }

  /* ----- discovery */
  _bind() {
    const c = this._config;
    const reg = this._reg;
    const hostIds = c.hosts && c.hosts.length ? c.hosts : allDevices(reg, NODE_RE);
    this._hosts = hostIds.map((id) => discoverNode(reg, id, this._hostSensors(id))).filter((h) => !h.error);
    const hostNames = new Set(this._hosts.map((h) => h.node));
    const kinds = (m) => c.show === "all" || (c.show === "vm" ? m[2] === "vm" : m[2] === "container");
    this._guests = allDevices(reg, GUEST_RE, kinds)
      .map((id) => discoverGuest(reg, id))
      .filter((g) => !g.error && (!(c.hosts && c.hosts.length) || hostNames.has(g.node)));
    this._pools = allDevices(reg, STORAGE_RE)
      .map((id) => discoverStorage(reg, id))
      .filter((p) => !p.error && (!(c.hosts && c.hosts.length) || hostNames.has(p.node)));
    for (const p of this._pools) if (c.names && c.names[p.storage]) p.name = c.names[p.storage];
    if (c.exclude_storage) this._pools = this._pools.filter((p) => !c.exclude_storage.includes(p.storage));
  }
  /* optional per-host sensors: host_sensors: { pve: { temperature: sensor.x, ... } } */
  _hostSensors(deviceId) {
    const map = this._config.host_sensors;
    if (!map) return {};
    const dev = this._reg.devices.find((d) => d.id === deviceId);
    const m = dev && proxmoxId(dev, NODE_RE);
    return (m && map[m[3]]) || map[deviceId] || {};
  }
  _watched() {
    if (!this._hosts) return [];
    const ids = [];
    const add = (e) => e && ids.push(e.entity_id);
    for (const h of this._hosts) [h.sensors.status, h.running, h.sensors.cpu, h.sensors.memory_percentage, h.sensors.uptime, h.sensors.temperature, h.backupProblem].forEach(add);
    for (const g of this._guests) [g.sensors.status, g.running, g.sensors.cpu, g.sensors.memory_percentage, g.sensors.memory].forEach(add);
    for (const p of this._pools) [p.sensors.used_percentage, p.sensors.used, p.sensors.active, p.sensors.enabled].forEach(add);
    return ids;
  }
  async _fetchHistory(force = false) {
    if (!this._hass || !this._hosts || this._histBusy || !this._config.show_hosts) return;
    if (!force && this._hist && Date.now() - (this._histAt || 0) < 4 * 60e3) return;
    const ids = this._hosts.map((h) => h.sensors.cpu).filter(enabled).map((e) => e.entity_id);
    if (!ids.length) return;
    this._histBusy = true;
    try {
      this._hist = await fetchSeries(this._hass, ids, 24);
      this._histAt = Date.now();
    } finally {
      this._histBusy = false;
    }
    this._render();
  }

  /* ----- problems */
  _problems(hostRows, storRows, guestRows) {
    const c = this._config;
    const out = [];
    const add = (sev, icon, text, entity) => out.push({ sev, icon, text, entity });
    for (const { h, state, m } of hostRows) {
      const e = (h.sensors.status || h.running || {}).entity_id;
      if (state === "stopped") add("crit", "mdi:server-off", `${h.name} is offline`, e);
      else if (state === "unavailable") add("crit", "mdi:server-off", `${h.name} is unavailable`, e);
      const bp = stateOf(this._hass, h.backupProblem);
      if (bp && bp.state === "on") add("crit", "mdi:backup-restore", `Last backup failed on ${h.name}`, h.backupProblem.entity_id);
      const t = rawReading(this._hass, h.sensors.temperature);
      if (t && t.n >= c.temp_critical) add("crit", "mdi:thermometer-alert", `${h.name} is at ${t.n.toFixed(0)} ${t.unit || "°C"}`, h.sensors.temperature.entity_id);
      else if (t && t.n >= c.temp_warning) add("warn", "mdi:thermometer", `${h.name} is running warm (${t.n.toFixed(0)} ${t.unit || "°C"})`, h.sensors.temperature.entity_id);
      if (state === "running" && m.memPct !== null && m.memPct >= c.critical)
        add("warn", "mdi:memory", `${h.name} memory at ${fmtPct(m.memPct)}`, (h.sensors.memory_percentage || {}).entity_id);
    }
    for (const { p, v } of storRows) {
      if (v.inactive || v.pct === null) continue;
      const e = (p.sensors.used_percentage || p.sensors.used || {}).entity_id;
      if (v.pct >= c.critical) add("crit", "mdi:database-alert", `${p.name} is ${fmtPct(v.pct)} full`, e);
      else if (v.pct >= c.warning) add("warn", "mdi:database", `${p.name} is ${fmtPct(v.pct)} full`, e);
    }
    for (const { g, state } of guestRows)
      if (state === "unavailable") add("warn", "mdi:help-circle-outline", `${g.name} is unavailable`, (g.sensors.status || g.running || {}).entity_id);
    return out.sort((a, b) => (a.sev === b.sev ? 0 : a.sev === "crit" ? -1 : 1));
  }

  /* ----- rendering */
  _render() {
    /* never tear down an embedded card while its confirmation dialog is open */
    for (const el of this._embeds.values()) if (el._confirming) return;
    super._render();
  }
  _mini(pct) {
    const c = this._config;
    return `<div class="mini"><div class="bar"><i class="${level(pct, c.warning, c.critical)}" style="width:${pct === null ? 0 : Math.min(100, pct)}%"></i></div><span class="num">${fmtPct(pct)}</span></div>`;
  }
  _html() {
    const c = this._config;
    if (this._error) return `<ha-card><div class="msg err">${esc(this._error)}</div></ha-card>`;
    if (!this._hosts) return `<ha-card><div class="msg">Loading…</div></ha-card>`;
    const hass = this._hass;

    const hostRows = this._hosts.map((h) => ({ h, state: guestState(hass, h), m: guestMetrics(hass, h) }));
    const guestRows = this._guests.map((g) => ({ g, state: guestState(hass, g), m: guestMetrics(hass, g) }));
    let storRows = this._pools.map((p) => ({ p, v: readStorage(hass, p) }));
    if (c.hide_inactive_storage) storRows = storRows.filter((r) => !r.v.inactive);
    const problems = this._problems(hostRows, storRows, guestRows);

    const online = hostRows.filter((r) => r.state === "running").length;
    const running = guestRows.filter((r) => r.state === "running").length;
    const worst = problems.some((x) => x.sev === "crit") ? "crit" : problems.length ? "warn" : "ok";
    const pill =
      worst === "ok"
        ? `<span class="pill ok"><ha-icon icon="mdi:check-circle"></ha-icon>All good</span>`
        : `<span class="pill ${worst}"><ha-icon icon="mdi:alert"></ha-icon>${problems.length} problem${problems.length > 1 ? "s" : ""}</span>`;
    const head = `<div class="head">
        <div class="badge ${worst}"><ha-icon icon="${esc(c.icon || "mdi:server-network")}"></ha-icon></div>
        <div class="titles"><div class="title">${esc(c.title)}</div>
          <div class="sub num">${online}/${hostRows.length} host${hostRows.length === 1 ? "" : "s"} online · ${running}/${guestRows.length} guests running</div></div>
        ${c.show_problems ? pill : ""}
      </div>`;

    const blocks = {};
    if (c.show_problems && problems.length)
      blocks.problems = `<div class="probs">${problems
        .map((x) => `<button class="prob ${x.sev}" data-e="${esc(x.entity || "")}"><ha-icon icon="${x.icon}"></ha-icon><span>${esc(x.text)}</span></button>`)
        .join("")}</div>`;

    if (c.show_hosts && hostRows.length)
      blocks.hosts = `<div class="sec"><div class="sh"><span class="label">Hosts</span></div>${hostRows
        .map(({ h, state, m }) => {
          const live = state === "running";
          const pts = this._hist && enabled(h.sensors.cpu) && this._hist[h.sensors.cpu.entity_id];
          const spark =
            live && pts && pts.length > 1
              ? buildChart({
                  series: [{ pts: m.cpu === null ? pts : pts.concat([[Date.now(), m.cpu]]), area: true }],
                  lo: 0,
                  hi: Math.min(100, Math.max(10, Math.max(...pts.map((x) => x[1]), m.cpu || 0) * 1.2)),
                  height: 22,
                  tone: level(m.cpu, c.warning, c.critical),
                })
              : "";
          const t = rawReading(hass, h.sensors.temperature);
          const temp = t ? `<span class="chip ${level(t.n, c.temp_warning, c.temp_critical)}">${t.n.toFixed(0)}${esc(t.unit || "°C")}</span>` : "";
          const open = this._expanded.has(h.deviceId);
          return `<div class="orow host ${live ? "" : "off"} ${open ? "open" : ""}" data-x="${esc(h.deviceId)}">
            <span class="dot ${state}"></span>
            <div class="gn"><div class="title">${esc(h.name)}</div><div class="sub">${live ? (m.uptime !== null ? `up ${fmtUptime(m.uptime)}` : "online") : state === "stopped" ? "offline" : STATE_LABEL[state]}</div></div>
            <div class="gx">${live ? `<div class="hc"><span class="label">CPU</span><div class="hcv">${spark || this._mini(m.cpu)}${spark ? `<span class="num">${fmtPct(m.cpu)}</span>` : ""}</div></div>
              <div class="gm"><span class="label">RAM</span>${this._mini(m.memPct)}</div>${temp}` : ""}</div>
            <ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
          </div>${open ? `<div class="slot" data-slot="${esc(h.deviceId)}"></div>` : ""}`;
        })
        .join("")}</div>`;

    if (c.show_storage && storRows.length) {
      const counted = storRows.filter((r) => !r.v.inactive && !r.v.shared);
      const used = counted.reduce((a, r) => a + (r.v.used || 0), 0), total = counted.reduce((a, r) => a + (r.v.total || 0), 0);
      const sorted = storRows.slice().sort((a, b) => (b.v.pct ?? -1) - (a.v.pct ?? -1));
      const limit = Math.max(1, Number(c.storage_rows) || 6);
      blocks.storage = `<div class="sec"><div class="sh"><span class="label">Storage</span>${total ? `<span class="muted num">${fmtBytes(used)} / ${fmtBytes(total)}</span>` : ""}</div>
        <div class="stor">${sorted
          .slice(0, limit)
          .map(({ p, v }) => {
            const lv = level(v.pct, c.warning, c.critical);
            return `<div class="srow" data-e="${esc((p.sensors.used_percentage || p.sensors.used || {}).entity_id || "")}" title="${esc(`${fmtBytes(v.used)} used of ${fmtBytes(v.total)}`)}">
              <span class="sn">${esc(p.name)}</span><div class="bar"><i class="${lv}" style="width:${v.pct === null ? 0 : Math.min(100, v.pct)}%"></i></div><span class="num pv ${lv}">${v.inactive ? "–" : fmtPct(v.pct)}</span></div>`;
          })
          .join("")}</div>${sorted.length > limit ? `<div class="more muted">+${sorted.length - limit} more</div>` : ""}</div>`;
    }

    if (c.show_guests && guestRows.length) {
      const counts = { all: guestRows.length, running, stopped: guestRows.length - running };
      let rows = guestRows.filter((r) => this._filter === "all" || (this._filter === "running" ? r.state === "running" : r.state !== "running"));
      const order = { running: 0, suspended: 1, unknown: 2, unavailable: 3, stopped: 4 };
      rows.sort((a, b) =>
        c.sort === "id"
          ? a.g.vmid - b.g.vmid
          : c.sort === "cpu"
            ? (b.m.cpu ?? -1) - (a.m.cpu ?? -1)
            : c.sort === "memory"
              ? (b.m.memPct ?? -1) - (a.m.memPct ?? -1)
              : c.sort === "name"
                ? a.g.name.localeCompare(b.g.name)
                : order[a.state] - order[b.state] || a.g.name.localeCompare(b.g.name),
      );
      if (c.group_by_host) rows.sort((a, b) => a.g.node.localeCompare(b.g.node));
      let lastNode = null;
      const list = rows
        .map(({ g, state, m }) => {
          const live = state === "running";
          const open = this._expanded.has(g.deviceId);
          const starting = this._starting[g.deviceId] && Date.now() - this._starting[g.deviceId] < 60000 && !live;
          const group = c.group_by_host && g.node !== lastNode ? `<div class="grp label">${esc((lastNode = g.node))}</div>` : "";
          const canStart = !c.read_only && state === "stopped" && enabled(g.buttons.start);
          return `${group}<div class="orow guest ${live ? "" : "off"} ${open ? "open" : ""}" data-x="${esc(g.deviceId)}">
            <span class="dot ${state}"></span>
            <div class="gn"><div class="title">${esc(g.name)}</div>
              <div class="sub">${g.kind === "vm" ? "VM" : "CT"} ${g.vmid}${!c.group_by_host && this._hosts.length > 1 && g.node ? ` · ${esc(g.node)}` : ""} · ${live ? (m.uptime !== null ? fmtUptime(m.uptime) : "running") : starting ? "starting…" : STATE_LABEL[state].toLowerCase()}</div></div>
            <div class="gx">${live ? `<div class="gm"><span class="label">CPU</span>${this._mini(m.cpu)}</div><div class="gm"><span class="label">RAM</span>${this._mini(m.memPct)}</div>` : canStart && !starting ? `<button class="btn sm" data-start="${esc(g.deviceId)}"><ha-icon icon="mdi:play"></ha-icon>Start</button>` : ""}</div>
            <ha-icon class="chev" icon="mdi:chevron-down"></ha-icon>
          </div>${open ? `<div class="slot" data-slot="${esc(g.deviceId)}"></div>` : ""}`;
        })
        .join("");
      const chip = (k, label) => `<button class="fchip ${this._filter === k ? "on" : ""}" data-filter="${k}">${label} <span class="num">${counts[k]}</span></button>`;
      blocks.guests = `<div class="sec"><div class="sh"><span class="label">Guests</span>
          <span class="fchips">${chip("all", "All")}${chip("running", "Running")}${chip("stopped", "Not running")}</span></div>
        ${rows.length ? `<div class="glist">${list}</div>` : `<div class="msg">Nothing here.</div>`}</div>`;
    }

    const order = (c.sections || OV_SECTIONS).filter((k) => blocks[k]);
    const left = order.filter((k) => k === "hosts" || k === "storage").map((k) => blocks[k]).join("");
    const top = order.includes("problems") ? blocks.problems : "";
    const right = order.includes("guests") ? blocks.guests : "";
    return `<ha-card>${head}${top ? `<div class="top">${top}</div>` : ""}
      <div class="ogrid ${left && right ? "two" : ""}">${left ? `<div class="col">${left}</div>` : ""}${right ? `<div class="col">${right}</div>` : ""}</div>
    </ha-card>`;
  }
  _embedFor(id) {
    let el = this._embeds.get(id);
    if (el) return el;
    const isHost = this._hosts.some((h) => h.deviceId === id);
    const c = this._config;
    el = document.createElement(isHost ? "proxmox-node-card" : "proxmox-guest-card");
    const cfg = { device: id, embedded: true, layout: "standard" };
    if (isHost) {
      const extra = this._hostSensors(id);
      if (Object.keys(extra).length) cfg.entities = extra;
      cfg.show_guests = false;
    }
    if (c.read_only) {
      cfg.actions = Object.fromEntries((isHost ? NODE_ACTION_IDS : ACTION_IDS).map((a) => [a, false]));
      cfg.hide_disabled_hint = true;
    }
    for (const k of ["warning", "critical", "temp_warning", "temp_critical"]) cfg[k] = c[k];
    el.setConfig(cfg);
    el.hass = this._hass;
    this._embeds.set(id, el);
    return el;
  }
  _wire() {
    const r = this._root;
    r.querySelectorAll("[data-e]").forEach((el) => (el.onclick = (ev) => (ev.stopPropagation(), fireMoreInfo(this, el.dataset.e))));
    r.querySelectorAll(".orow").forEach((row) => {
      row.onclick = (ev) => {
        if (ev.composedPath().some((n) => n instanceof Element && n.matches("button"))) return;
        const id = row.dataset.x;
        this._expanded.has(id) ? this._expanded.delete(id) : this._expanded.add(id);
        this._render();
      };
    });
    r.querySelectorAll("[data-filter]").forEach((b) => (b.onclick = () => ((this._filter = b.dataset.filter), this._render())));
    r.querySelectorAll("[data-start]").forEach(
      (b) =>
        (b.onclick = async () => {
          const g = this._guests.find((x) => x.deviceId === b.dataset.start);
          if (!g || this._config.read_only) return;
          this._starting[g.deviceId] = Date.now();
          this._render();
          try {
            await this._hass.callService("button", "press", { entity_id: g.buttons.start.entity_id });
          } catch (e) {
            delete this._starting[g.deviceId];
            this._render();
          }
        }),
    );
    r.querySelectorAll(".slot").forEach((slot) => slot.appendChild(this._embedFor(slot.dataset.slot)));
    for (const [id, el] of this._embeds) if (!this._expanded.has(id)) (el.remove(), this._embeds.delete(id));
    wireCharts(r);
  }
}
ProxmoxOverviewCard.css = `
  ha-card { container-type: inline-size; }
  .badge.ok { background: color-mix(in srgb, var(--pxc-ok) 14%, transparent); color: var(--pxc-ok); }
  .badge.warn { background: color-mix(in srgb, var(--pxc-warn) 16%, transparent); color: var(--pxc-warn); }
  .badge.crit { background: color-mix(in srgb, var(--pxc-crit) 14%, transparent); color: var(--pxc-crit); }
  ha-card > .head .sub { white-space: normal; }
  .pill { display: inline-flex; align-items: center; gap: 5px; font-size: 12.5px; padding: 3px 10px 3px 7px; border-radius: 999px; flex: none; font-weight: 500; }
  .pill ha-icon { --mdc-icon-size: 16px; }
  .pill.ok { color: var(--pxc-ok); background: color-mix(in srgb, var(--pxc-ok) 12%, transparent); }
  .pill.warn { color: var(--pxc-warn); background: color-mix(in srgb, var(--pxc-warn) 14%, transparent); }
  .pill.crit { color: var(--pxc-crit); background: color-mix(in srgb, var(--pxc-crit) 12%, transparent); }
  .top { margin-top: 14px; }
  .probs { display: flex; flex-wrap: wrap; gap: 6px; }
  .prob { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; font-size: 12.5px; padding: 5px 10px 5px 8px; border-radius: 8px; }
  .prob ha-icon { --mdc-icon-size: 16px; }
  .prob.warn { color: var(--pxc-warn); background: color-mix(in srgb, var(--pxc-warn) 12%, transparent); }
  .prob.crit { color: var(--pxc-crit); background: color-mix(in srgb, var(--pxc-crit) 11%, transparent); }
  .ogrid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0 28px; }
  @container (min-width: 760px) { .ogrid.two { grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); } }
  .col { min-width: 0; }
  .sec { margin-top: 16px; padding-top: 14px; border-top: 1px solid var(--pxc-line); }
  .sh { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-bottom: 8px; font-size: 12.5px; min-height: 26px; }
  .orow { display: grid; grid-template-columns: 10px minmax(0, 1fr) auto 18px; gap: 10px; align-items: center; padding: 8px 4px; margin: 0 -4px; border-radius: 8px; cursor: pointer; }
  .gx { display: flex; align-items: center; gap: 12px; }
  .gx .gm { width: 96px; }
  .gx .hc { width: 116px; }
  .orow:hover { background: var(--pxc-fill); }
  .orow + .orow { border-top: 1px solid var(--pxc-line); border-radius: 0; }
  .orow.open { background: var(--pxc-fill); border-radius: 8px 8px 0 0; }
  .orow.off .gn .title { opacity: .6; }
  .dot.running { box-shadow: none; }
  .gn .title { font-size: 14px; font-weight: 500; }
  .gm .label, .hc .label { font-size: 9.5px; }
  .mini { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .mini .bar { flex: 1; height: 5px; }
  .mini span { width: 32px; text-align: right; }
  .hcv { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .hcv .chart { flex: 1; }
  .hcv > span { width: 32px; text-align: right; }
  .chip { font-size: 12px; padding: 2px 7px; border-radius: 999px; background: var(--pxc-fill); border: 1px solid var(--pxc-line); white-space: nowrap; }
  .chip.warn { color: var(--pxc-warn); }
  .chip.crit { color: var(--pxc-crit); }
  .chev { --mdc-icon-size: 18px; color: var(--pxc-muted); transition: transform .2s; }
  .open .chev { transform: rotate(180deg); }
  .slot { background: var(--pxc-fill); border-radius: 0 0 8px 8px; padding: 0 12px 8px; margin: 0 -4px 4px; }
  .stor { display: grid; grid-template-columns: minmax(0, 1fr); gap: 8px 20px; }
  @container (min-width: 520px) and (max-width: 759px) { .stor { grid-template-columns: 1fr 1fr; } }
  .srow { display: grid; grid-template-columns: minmax(60px, 110px) minmax(0, 1fr) 40px; gap: 10px; align-items: center; font-size: 13px; cursor: pointer; }
  .sn { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pv { text-align: right; font-weight: 600; }
  .pv.warn { color: var(--pxc-warn); }
  .pv.crit { color: var(--pxc-crit); }
  .more { font-size: 12px; margin-top: 6px; }
  .fchips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
  .fchip { all: unset; cursor: pointer; font-size: 12px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--pxc-line); color: var(--pxc-muted); }
  .fchip .num { opacity: .7; margin-left: 2px; }
  .fchip.on { background: var(--pxc-accent); border-color: transparent; color: var(--text-primary-color, #fff); }
  .grp { margin: 10px 0 2px; }
  button.btn.sm { flex: none; padding: 4px 8px; font-size: 12px; }
  button.btn.sm ha-icon { --mdc-icon-size: 14px; }
  @container (max-width: 420px) {
    .gx .gm + .gm, .host .gx .gm, .gx .chip { display: none; }
    .gx .gm, .gx .hc { width: 92px; }
  }
`;

/* ============================================================ guests list === */

class ProxmoxGuestsCard extends BaseCard {
  static getStubConfig() {
    return { show: "all" };
  }
  static getConfigElement() {
    return document.createElement("proxmox-guests-card-editor");
  }
  setConfig(config) {
    this._config = { show: "all", sort: "name", hide_stopped: false, warning: 75, critical: 90, ...config };
    if (this._reg) {
      this._bind();
      this._render();
    }
  }
  getCardSize() {
    return 1 + (this._guests ? this._guests.length : 3);
  }
  getGridOptions() {
    return { columns: 6, min_columns: 4 };
  }
  _bind() {
    const c = this._config;
    if (!c) return;
    const ids =
      c.devices && c.devices.length
        ? c.devices
        : allDevices(this._reg, GUEST_RE, (m) => c.show === "all" || (c.show === "vm" ? m[2] === "vm" : m[2] === "container"));
    this._guests = ids.map((id) => discoverGuest(this._reg, id)).filter((g) => !g.error);
  }
  _watched() {
    return (this._guests || []).flatMap((g) =>
      [g.sensors.status, g.running, g.sensors.cpu, g.sensors.memory_percentage, g.sensors.memory, g.sensors.uptime].filter(Boolean).map((e) => e.entity_id),
    );
  }
  _html() {
    if (this._error) return `<ha-card><div class="msg err">${esc(this._error)}</div></ha-card>`;
    if (!this._guests) return `<ha-card><div class="msg">Loading…</div></ha-card>`;
    const c = this._config;
    const hass = this._hass;
    let rows = this._guests.map((g) => ({ g, state: guestState(hass, g), m: guestMetrics(hass, g) }));
    if (c.hide_stopped) rows = rows.filter((r) => r.state !== "stopped");
    const order = { running: 0, suspended: 1, unknown: 2, unavailable: 3, stopped: 4 };
    rows.sort((a, b) =>
      c.sort === "id"
        ? a.g.vmid - b.g.vmid
        : c.sort === "status"
          ? order[a.state] - order[b.state] || a.g.name.localeCompare(b.g.name)
          : c.sort === "cpu"
            ? (b.m.cpu ?? -1) - (a.m.cpu ?? -1)
            : c.sort === "memory"
              ? (b.m.memPct ?? -1) - (a.m.memPct ?? -1)
              : a.g.name.localeCompare(b.g.name),
    );
    const running = rows.filter((r) => r.state === "running").length;
    const title = c.title === undefined ? "Proxmox guests" : c.title;
    const bar = (pct) =>
      `<div class="mini"><div class="bar"><i class="${level(pct, c.warning, c.critical)}" style="width:${pct === null ? 0 : Math.min(100, pct)}%"></i></div><span class="num">${fmtPct(pct)}</span></div>`;
    const list = rows
      .map(({ g, state, m }) => {
        const live = state === "running";
        return `<div class="grow ${live ? "" : "off"}" data-e="${esc((g.sensors.status || g.running || {}).entity_id || "")}">
          <ha-icon class="gi" icon="${ICONS[g.kind]}"></ha-icon>
          <div class="gn"><div class="title">${esc(g.name)}</div>
            <div class="sub"><span class="dot ${state}"></span>${g.kind === "vm" ? "VM" : "CT"} ${g.vmid}${g.node ? ` · ${esc(g.node)}` : ""}${live && m.uptime !== null ? ` · ${fmtUptime(m.uptime)}` : !live ? ` · ${STATE_LABEL[state]}` : ""}</div></div>
          ${live ? `<div class="gm"><span class="label">CPU</span>${bar(m.cpu)}</div><div class="gm"><span class="label">RAM</span>${bar(m.memPct)}</div>` : ""}
        </div>`;
      })
      .join("");
    return `<ha-card>
      ${title ? `<div class="lh"><span class="title">${esc(title)}</span><span class="muted num">${running}/${rows.length} running</span></div>` : ""}
      ${rows.length ? `<div class="list">${list}</div>` : `<div class="msg">No Proxmox guests found.</div>`}
    </ha-card>`;
  }
  _wire() {
    this._root.querySelectorAll(".grow").forEach((el) => (el.onclick = () => fireMoreInfo(this, el.dataset.e)));
  }
}
ProxmoxGuestsCard.css = `
  ha-card { container-type: inline-size; }
  .lh { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 10px; font-size: 13px; }
  .lh .title { font-size: 16px; }
  .list { display: flex; flex-direction: column; }
  .grow { display: grid; grid-template-columns: 24px minmax(0, 1fr) 84px 84px; gap: 12px; align-items: center; padding: 9px 0; cursor: pointer; border-top: 1px solid var(--pxc-line); }
  .grow:first-child { border-top: 0; }
  .grow.off .gn .title, .grow.off .gi { opacity: .55; }
  .grow.off .gn { grid-column: 2 / -1; }
  .gi { color: var(--pxc-accent); --mdc-icon-size: 20px; }
  .gn .title { font-size: 14px; font-weight: 500; }
  .gn .sub { display: flex; align-items: center; gap: 6px; }
  .gn .dot { width: 6px; height: 6px; }
  .gn .dot.running { box-shadow: none; }
  .gm .label { font-size: 9.5px; }
  .mini { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .mini .bar { flex: 1; height: 5px; }
  .mini span { width: 30px; text-align: right; }
  @container (max-width: 330px) { .grow { grid-template-columns: 24px minmax(0,1fr) 72px; } .grow .gm:last-child { display: none; } }
`;

/* =========================================================== storage card === */

class ProxmoxStorageCard extends BaseCard {
  static getStubConfig() {
    return {};
  }
  static getConfigElement() {
    return document.createElement("proxmox-storage-card-editor");
  }
  setConfig(config) {
    const prevHours = this._config && this._config.graph_hours;
    this._config = { layout: "list", sort: "name", warning: 75, critical: 90, show_free: true, hide_inactive: false, graph: "full", graph_hours: 24, graph_height: 44, ...config };
    if (this._reg) {
      this._bind();
      if (prevHours !== this._config.graph_hours) this._hist = null;
      this._render();
      this._fetchHistory();
    }
  }
  connectedCallback() {
    clearInterval(this._histTimer);
    this._histTimer = setInterval(() => this._fetchHistory(), 10 * 60 * 1000);
    if (this._pools) this._fetchHistory();
  }
  disconnectedCallback() {
    clearInterval(this._histTimer);
  }
  /* usage history for the sparklines: 5-minute / hourly recorder statistics, raw history as fallback */
  async _fetchHistory() {
    const c = this._config;
    if (!this._hass || !this._pools || !this._graphMode() || this._histBusy) return;
    const ids = this._pools.map((p) => p.sensors.used_percentage).filter(enabled).map((e) => e.entity_id);
    if (!ids.length) return;
    this._histBusy = true;
    try {
      this._hist = await fetchSeries(this._hass, ids, Math.max(1, Number(c.graph_hours) || 24));
    } finally {
      this._histBusy = false;
    }
    this._render();
  }
  _graphMode() {
    const g = this._config.graph;
    return g === false || g === "none" || this._config.layout === "grid" ? null : g === "inline" ? "inline" : "full";
  }
  /* history of one storage, downsampled, with scale and change over the period */
  _series(p, v, maxPts, minSpan) {
    const c = this._config;
    const e = p.sensors.used_percentage;
    if (!this._hist || !enabled(e) || v.inactive) return null;
    let pts = (this._hist[e.entity_id] || []).slice();
    if (v.pct !== null) pts.push([Date.now(), v.pct]);
    if (pts.length < 2) return null;
    if (pts.length > maxPts) {
      const step = pts.length / maxPts;
      pts = Array.from({ length: maxPts }, (_, i) => pts[Math.floor(i * step)]).concat([pts[pts.length - 1]]);
    }
    let lo = Math.min(...pts.map((x) => x[1])), hi = Math.max(...pts.map((x) => x[1]));
    if (hi - lo < minSpan) {
      /* keep a minimum span so tiny wiggles stay flat instead of looking dramatic */
      const mid = (hi + lo) / 2;
      lo = Math.max(0, mid - minSpan / 2);
      hi = Math.min(100, lo + minSpan);
      lo = hi - minSpan;
    }
    const delta = pts[pts.length - 1][1] - pts[0][1];
    const hours = Math.max(1, Number(c.graph_hours) || 24);
    const span = hours % 24 === 0 ? `${hours / 24}d` : `${hours}h`;
    const dText = Math.abs(delta) < 0.05 ? "±0%" : `${delta > 0 ? "+" : "−"}${Math.abs(delta).toFixed(Math.abs(delta) < 10 ? 1 : 0)}%`;
    const dCls = delta >= 0.05 ? "up" : delta <= -0.05 ? "down" : "";
    return { pts, lo, hi, delta, dText, dCls, span, lv: level(v.pct, c.warning, c.critical) };
  }
  _paths(sr, W, H, pad) {
    const t0 = sr.pts[0][0], t1 = sr.pts[sr.pts.length - 1][0];
    const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * W;
    const y = (val) => H - pad - ((val - sr.lo) / (sr.hi - sr.lo)) * (H - 2 * pad);
    const line = sr.pts.map(([t, val], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(val).toFixed(1)}`).join("");
    return { line, area: `${line}L${W},${H}L0,${H}Z`, y };
  }
  _spark(p, v) {
    const sr = this._series(p, v, 120, 2);
    if (!sr) return "";
    const W = 72, H = 20, { line, area } = this._paths(sr, W, H, 1.5);
    return `<span class="trend" title="${esc(`${sr.dText} in the last ${sr.span}`)}">
      <svg class="spark ${sr.lv}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path class="sa" d="${area}"/><path class="sl" d="${line}"/></svg>
      <span class="dl num ${sr.dCls}">${sr.dText}<small> ${sr.span}</small></span></span>`;
  }
  _chart(p, v) {
    const sr = this._series(p, v, 240, 4);
    if (!sr) return { chart: "", delta: "" };
    const c = this._config;
    return {
      delta: `<span class="dl num ${sr.dCls}" title="Change in the last ${sr.span}">${sr.dText}<small> ${sr.span}</small></span>`,
      chart: buildChart({
        series: [{ pts: sr.pts, area: true }],
        lo: sr.lo,
        hi: sr.hi,
        height: Math.max(24, Number(c.graph_height) || 44),
        gutter: 40,
        tone: sr.lv,
        guides: [{ v: c.warning, cls: "warn" }, { v: c.critical, cls: "crit" }],
      }),
    };
  }
  getCardSize() {
    const n = this._pools ? this._pools.length : 3;
    if (this._config && this._config.layout === "grid") return 1 + Math.ceil(n / 3) * 3;
    return 1 + n * (this._config && this._graphMode() === "full" ? 2 : 1);
  }
  getGridOptions() {
    return { columns: 6, min_columns: 4 };
  }
  _bind() {
    const c = this._config;
    if (!c) return;
    const ids = c.devices && c.devices.length ? c.devices : allDevices(this._reg, STORAGE_RE);
    this._pools = ids.map((id) => discoverStorage(this._reg, id)).filter((p) => !p.error);
    const names = c.names || {};
    for (const p of this._pools) if (names[p.storage]) p.name = names[p.storage];
    if (c.exclude && c.exclude.length) this._pools = this._pools.filter((p) => !c.exclude.includes(p.storage));
  }
  _watched() {
    return (this._pools || []).flatMap((p) => Object.values(p.sensors).map((e) => e.entity_id));
  }
  async _load(force) {
    await super._load(force);
    this._fetchHistory();
  }
  _read(p) {
    return readStorage(this._hass, p);
  }
  _html() {
    if (this._error) return `<ha-card><div class="msg err">${esc(this._error)}</div></ha-card>`;
    if (!this._pools) return `<ha-card><div class="msg">Loading…</div></ha-card>`;
    const c = this._config;
    let rows = this._pools.map((p) => ({ p, v: this._read(p) }));
    if (c.hide_inactive) rows = rows.filter((r) => !r.v.inactive);
    rows.sort((a, b) =>
      c.sort === "usage"
        ? (b.v.pct ?? -1) - (a.v.pct ?? -1)
        : c.sort === "size"
          ? (b.v.total ?? -1) - (a.v.total ?? -1)
          : c.sort === "none"
            ? 0
            : a.p.name.localeCompare(b.p.name),
    );
    const title = c.title === undefined ? "Storage" : c.title;
    const counted = rows.filter((r) => !r.v.inactive && !r.v.shared);
    const sumUsed = counted.reduce((s, r) => s + (r.v.used || 0), 0);
    const sumTotal = counted.reduce((s, r) => s + (r.v.total || 0), 0);
    const header = title
      ? `<div class="lh"><span class="title">${esc(title)}</span>${sumTotal ? `<span class="muted num">${fmtBytes(sumUsed)} / ${fmtBytes(sumTotal)} used</span>` : ""}</div>`
      : "";
    if (!rows.length) return `<ha-card>${header}<div class="msg">No Proxmox storage found.</div></ha-card>`;
    const tag = (v) =>
      v.enabledOff ? `<span class="tag">disabled</span>` : v.inactive ? `<span class="tag">inactive</span>` : v.shared ? `<span class="tag soft">shared</span>` : "";
    const node = (p) => (c.show_node === false || !p.node ? "" : esc(p.node));
    const eid = (p) => esc((p.sensors.used_percentage || p.sensors.used || {}).entity_id || "");

    if (c.layout === "grid") {
      const R = 34, C = 2 * Math.PI * R;
      const cells = rows
        .map(({ p, v }) => {
          const lv = level(v.pct, c.warning, c.critical);
          const frac = v.pct === null || v.inactive ? 0 : Math.min(100, v.pct) / 100;
          return `<div class="cell ${v.inactive ? "off" : ""}" data-e="${eid(p)}">
            <div class="ring"><svg viewBox="0 0 80 80"><circle class="trk" cx="40" cy="40" r="${R}"/><circle class="arc ${lv}" cx="40" cy="40" r="${R}"
              stroke-dasharray="${(C * frac).toFixed(2)} ${C.toFixed(2)}" transform="rotate(-90 40 40)"/></svg>
              <div class="rv num">${v.inactive ? "–" : fmtPct(v.pct)}</div></div>
            <div class="cn title">${esc(p.name)}</div>
            <div class="sub num">${v.inactive ? tag(v) : `${fmtBytes(v.used)} / ${fmtBytes(v.total)}`}</div>
            ${node(p) ? `<div class="sub">${node(p)}</div>` : ""}
          </div>`;
        })
        .join("");
      return `<ha-card>${header}<div class="grid">${cells}</div></ha-card>`;
    }

    const list = rows
      .map(({ p, v }) => {
        const lv = level(v.pct, c.warning, c.critical);
        const mode = this._graphMode();
        const full = mode === "full" ? this._chart(p, v) : { chart: "", delta: "" };
        return `<div class="srow ${v.inactive ? "off" : ""} ${full.chart ? "has-chart" : ""}" data-e="${eid(p)}">
          <div class="sh"><ha-icon class="si" icon="${esc((c.icons && c.icons[p.storage]) || ICONS.storage)}"></ha-icon>
            <span class="title">${esc(p.name)}</span>${tag(v)}${node(p) ? `<span class="sub">${node(p)}</span>` : ""}
            <span class="spacer"></span>${mode === "inline" ? this._spark(p, v) : full.delta}<span class="num pv ${lv}">${v.inactive ? "" : fmtPct(v.pct)}</span></div>
          ${full.chart}
          <div class="bar big"><i class="${lv}" style="width:${v.pct === null || v.inactive ? 0 : Math.min(100, v.pct)}%"></i></div>
          <div class="sf sub num">${
            v.inactive
              ? `<span>${v.enabledOff ? "Disabled in Proxmox" : "Not active on this node"}</span>`
              : `<span>${fmtBytes(v.used)} used of ${fmtBytes(v.total)}</span>${c.show_free ? `<span>${fmtBytes(v.free)} free</span>` : ""}`
          }</div>
        </div>`;
      })
      .join("");
    return `<ha-card>${header}<div class="slist">${list}</div></ha-card>`;
  }
  _wire() {
    this._root.querySelectorAll("[data-e]").forEach((el) => (el.onclick = () => fireMoreInfo(this, el.dataset.e)));
    wireCharts(this._root);
  }
}
ProxmoxStorageCard.css = `
  .lh { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 12px; font-size: 13px; }
  .lh .title { font-size: 16px; }
  .slist { display: flex; flex-direction: column; gap: 14px; }
  .srow { cursor: pointer; }
  .srow.off { opacity: .55; }
  .sh { display: flex; align-items: center; gap: 8px; min-width: 0; margin-bottom: 6px; }
  .sh .title { font-size: 14px; font-weight: 500; flex: 0 1 auto; }
  .sh .sub { flex: 0 1 auto; }
  .si { color: var(--pxc-accent); --mdc-icon-size: 18px; flex: none; }
  .spacer { flex: 1; }
  .pv { font-weight: 600; font-size: 14px; }
  .pv.warn { color: var(--pxc-warn); }
  .pv.crit { color: var(--pxc-crit); }
  .pv { min-width: 38px; text-align: right; }
  .trend { display: inline-flex; align-items: center; gap: 6px; flex: none; }
  .spark { width: 72px; height: 20px; display: block; overflow: visible; }
  .spark .sl { fill: none; stroke: var(--pxc-accent); stroke-width: 1.5; stroke-linejoin: round; stroke-linecap: round; vector-effect: non-scaling-stroke; }
  .spark .sa { fill: color-mix(in srgb, var(--pxc-accent) 16%, transparent); stroke: none; }
  .spark.warn .sl { stroke: var(--pxc-warn); }
  .spark.warn .sa { fill: color-mix(in srgb, var(--pxc-warn) 18%, transparent); }
  .spark.crit .sl { stroke: var(--pxc-crit); }
  .spark.crit .sa { fill: color-mix(in srgb, var(--pxc-crit) 16%, transparent); }
  .dl { font-size: 11px; color: var(--pxc-muted); min-width: 44px; text-align: right; white-space: nowrap; }
  .dl small { font-size: 10px; opacity: .8; }
  @container (max-width: 330px) { .trend .dl, .sh .sub { display: none; } .spark { width: 56px; } }
  .srow.has-chart .bar.big { border-radius: 0 0 4px 4px; height: 6px; }
  .srow .chart { margin-top: 2px; }
  .srow.has-chart .sh { margin-bottom: 4px; }
  ha-card { container-type: inline-size; }
  .bar.big { height: 8px; border-radius: 4px; }
  .sf { display: flex; justify-content: space-between; gap: 8px; margin-top: 5px; }
  .tag { font-size: 10.5px; padding: 1px 6px; border-radius: 6px; background: color-mix(in srgb, var(--pxc-crit) 14%, transparent); color: var(--pxc-crit); flex: none; }
  .tag.soft { background: var(--pxc-track); color: var(--pxc-muted); }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(104px, 1fr)); gap: 16px 8px; }
  .cell { display: flex; flex-direction: column; align-items: center; text-align: center; cursor: pointer; min-width: 0; }
  .cell.off { opacity: .55; }
  .cell .title { font-size: 14px; font-weight: 500; margin-top: 6px; max-width: 100%; }
  .cell .sub { max-width: 100%; }
  .ring { position: relative; width: 80px; height: 80px; }
  .ring svg { width: 100%; height: 100%; }
  .ring circle { fill: none; stroke-width: 8; }
  .ring .trk { stroke: var(--pxc-track); }
  .ring .arc { stroke: var(--pxc-accent); stroke-linecap: round; transition: stroke-dasharray .6s ease; }
  .ring .arc.warn { stroke: var(--pxc-warn); }
  .ring .arc.crit { stroke: var(--pxc-crit); }
  .rv { position: absolute; inset: 0; display: grid; place-items: center; font-size: 16px; font-weight: 600; }
`;

/* ================================================================ editors === */

class BaseEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }
  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
    else this._render();
  }
  /* form <-> config transforms; overridden where the YAML is nested */
  _toForm(c) {
    return c;
  }
  _fromForm(d) {
    const cfg = { ...d };
    for (const k of Object.keys(cfg)) if (cfg[k] === "" || cfg[k] === undefined || (Array.isArray(cfg[k]) && !cfg[k].length)) delete cfg[k];
    return cfg;
  }
  _schema() {
    return this.constructor.schema;
  }
  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => s.label || s.name;
      this._form.computeHelper = (s) => s.helper;
      this._form.addEventListener("value-changed", (ev) => {
        const cfg = { type: this._config.type, ...this._fromForm(ev.detail.value) };
        if (!cfg.type) delete cfg.type;
        this._config = cfg;
        this._form.schema = this._schema();
        this._form.data = this._toForm(cfg);
        fire(this, "config-changed", { config: cfg });
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.schema = this._schema();
    this._form.data = this._toForm(this._config);
  }
}

const thresholds = {
  type: "grid",
  name: "",
  schema: [
    { name: "warning", label: "Warning at (%)", selector: { number: { min: 1, max: 100, mode: "box" } } },
    { name: "critical", label: "Critical at (%)", selector: { number: { min: 1, max: 100, mode: "box" } } },
  ],
};
const bool = (name, label) => ({ name, label, selector: { boolean: {} } });
const formKey = (id) => `action_${id.replace("-", "_")}`;

class MachineEditor extends BaseEditor {
  _amap() {
    return ACTIONS;
  }
  _ids() {
    return Object.keys(this._amap());
  }
  _dconf() {
    return DEFAULT_CONFIRM;
  }
  _deviceField() {
    const vmOnly = this.constructor.kindFilter === "vm";
    const filter = vmOnly ? { integration: DOMAIN, model: "VM" } : [{ integration: DOMAIN, model: "Container" }, { integration: DOMAIN, model: "VM" }];
    return { name: "device", label: vmOnly ? "VM" : "Container or VM", required: true, selector: { device: { filter } } };
  }
  _extraSchema() {
    return [];
  }
  _toForm(raw) {
    let c;
    try {
      c = normalizeMachineConfig(raw);
    } catch {
      c = { ...raw };
    }
    const d = { ...c };
    for (const k of ["metrics", "actions", "progress_bars", "entities"]) delete d[k];
    for (const k of METRIC_KEYS) d[`metric_${k}`] = !c.metrics || c.metrics[k] !== false;
    const pb = c.progress_bars;
    for (const k of BAR_KEYS) d[`bar_${k}`] = pb === undefined ? true : typeof pb === "boolean" ? pb : pb[k] !== false;
    for (const id of this._ids()) d[formKey(id)] = !c.actions || c.actions[id] !== false;
    for (const role of Object.keys(OVERRIDE_ROLES)) if (c.entities && c.entities[role]) d[`entity_${role}`] = c.entities[role];
    d.layout = c.layout || "standard";
    for (const k of ["memory_percentage", "disk_percentage", "state_accent", "show_action_status"]) d[k] = c[k] !== false;
    d.confirm = c.confirm || this._dconf();
    for (const k of ["show_guests", "show_health", "show_backup"]) d[k] = c[k] !== false;
    d.warning = c.warning ?? 75;
    d.critical = c.critical ?? 90;
    delete d.graphs;
    for (const k of GRAPH_KEYS)
      d[`graph_${k}`] = typeof c.graphs === "boolean" ? c.graphs : c.graphs && c.graphs[k] !== undefined ? !!c.graphs[k] : GRAPH_DEFAULTS[k];
    d.graph_hours = c.graph_hours ?? 24;
    d.graph_height = c.graph_height ?? 44;
    return d;
  }
  _fromForm(d) {
    const c = {};
    for (const [k, v] of Object.entries(d)) {
      if (/^(metric|bar|action|entity)_/.test(k) || /^graph_(cpu|memory|disk|network)$/.test(k) || v === "" || v === undefined || v === null) continue;
      c[k] = v;
    }
    if (c.layout === "standard") delete c.layout;
    for (const k of ["memory_percentage", "disk_percentage", "state_accent", "show_action_status"]) if (c[k] === true) delete c[k];
    if (c.graph_hours === 24) delete c.graph_hours;
    if (c.graph_height === 44) delete c.graph_height;
    const graphs = Object.fromEntries(GRAPH_KEYS.filter((k) => d[`graph_${k}`] !== undefined && d[`graph_${k}`] !== GRAPH_DEFAULTS[k]).map((k) => [k, d[`graph_${k}`]]));
    if (GRAPH_KEYS.every((k) => d[`graph_${k}`] === false)) c.graphs = false;
    else if (Object.keys(graphs).length) c.graphs = graphs;
    if (c.warning === 75) delete c.warning;
    if (c.critical === 90) delete c.critical;
    const dc = this._dconf();
    if (Array.isArray(c.confirm) && c.confirm.length === dc.length && dc.every((a) => c.confirm.includes(a))) delete c.confirm;
    for (const k of ["show_guests", "show_health", "show_backup"]) if (c[k] === true || this.constructor.kindFilter !== "node") delete c[k];
    if (c.temp_warning === 70) delete c.temp_warning;
    if (c.temp_critical === 85) delete c.temp_critical;
    const metrics = Object.fromEntries(METRIC_KEYS.filter((k) => d[`metric_${k}`] === false).map((k) => [k, false]));
    if (Object.keys(metrics).length) c.metrics = metrics;
    const offBars = BAR_KEYS.filter((k) => d[`bar_${k}`] === false);
    if (offBars.length === BAR_KEYS.length) c.progress_bars = false;
    else if (offBars.length) c.progress_bars = Object.fromEntries(offBars.map((k) => [k, false]));
    const actions = Object.fromEntries(this._ids().filter((id) => d[formKey(id)] === false).map((id) => [id, false]));
    if (Object.keys(actions).length) c.actions = actions;
    const entities = Object.fromEntries(Object.keys(OVERRIDE_ROLES).filter((r) => d[`entity_${r}`]).map((r) => [r, d[`entity_${r}`]]));
    if (Object.keys(entities).length) c.entities = entities;
    return c;
  }
  _schema() {
    return [
      this._deviceField(),
      {
        type: "grid",
        name: "",
        schema: [
          { name: "title", label: "Title (optional)", selector: { text: {} } },
          { name: "icon", label: "Icon (optional)", selector: { icon: {} } },
        ],
      },
      {
        name: "layout",
        label: "Layout",
        selector: { select: { mode: "dropdown", options: ["compact", "standard", "detailed"].map((v) => ({ value: v, label: v[0].toUpperCase() + v.slice(1) })) } },
      },
      {
        type: "expandable",
        name: "",
        title: "Metrics",
        flatten: true,
        schema: [
          { type: "grid", name: "", schema: [bool("metric_cpu", "CPU"), bool("metric_memory", "Memory"), bool("metric_disk", "Disk"), bool("metric_network", "Network")] },
          { type: "grid", name: "", schema: [bool("bar_cpu", "CPU bar"), bool("bar_memory", "Memory bar"), bool("bar_disk", "Disk bar")] },
          { type: "grid", name: "", schema: [bool("memory_percentage", "Memory %"), bool("disk_percentage", "Disk %"), bool("state_accent", "Colour by state")] },
          thresholds,
        ],
      },
      {
        type: "expandable",
        name: "",
        title: "Graphs",
        flatten: true,
        schema: [
          { type: "grid", name: "", schema: [bool("graph_cpu", "CPU"), bool("graph_memory", "Memory"), bool("graph_disk", "Disk"), bool("graph_network", "Network")] },
          {
            type: "grid",
            name: "",
            schema: [
              { name: "graph_hours", label: "Period (hours)", selector: { number: { min: 1, max: 720, mode: "box" } } },
              { name: "graph_height", label: "Height in detailed layout (px)", selector: { number: { min: 24, max: 120, mode: "box" } } },
            ],
          },
          {
            name: "entity_networkRxRate",
            label: "Network receive rate sensor",
            helper: "Proxmox only reports cumulative totals. To get a live network graph, pick a throughput sensor (e.g. a Derivative helper on the network-in sensor).",
            selector: { entity: { filter: { domain: "sensor" } } },
          },
          { name: "entity_networkTxRate", label: "Network send rate sensor", selector: { entity: { filter: { domain: "sensor" } } } },
        ],
      },
      {
        type: "expandable",
        name: "",
        title: "Controls",
        flatten: true,
        schema: [
          { type: "grid", name: "", schema: this._ids().map((id) => bool(formKey(id), this._amap()[id].label)) },
          {
            name: "confirm",
            label: "Ask for confirmation before",
            selector: { select: { multiple: true, mode: "list", options: this._ids().map((id) => ({ value: id, label: this._amap()[id].label })) } },
          },
          bool("show_action_status", "Show action progress messages"),
        ],
      },
      {
        type: "expandable",
        name: "",
        title: "Interactions",
        flatten: true,
        schema: [
          { name: "tap_action", label: "Tap action", helper: "Default: more info for the status sensor", selector: { ui_action: {} } },
          { name: "hold_action", label: "Hold action", selector: { ui_action: {} } },
          { name: "double_tap_action", label: "Double tap action", selector: { ui_action: {} } },
        ],
      },
      ...this._extraSchema(),
      {
        type: "expandable",
        name: "",
        title: "Advanced: sensor overrides",
        flatten: true,
        schema: Object.keys(OVERRIDE_ROLES).filter((role) => !FREE_ROLES.includes(role)).map((role) => ({
          name: `entity_${role}`,
          label: role,
          selector: { entity: { filter: { domain: "sensor", integration: DOMAIN } } },
        })),
      },
    ];
  }
}
class VmEditor extends MachineEditor {
  static kindFilter = "vm";
}
class GuestEditor extends MachineEditor {
  static kindFilter = "any";
}
class NodeEditor extends MachineEditor {
  static kindFilter = "node";
  _amap() {
    return NODE_ACTIONS;
  }
  _dconf() {
    return NODE_DEFAULT_CONFIRM;
  }
  _toForm(raw) {
    const d = super._toForm(raw);
    d.temp_warning = (raw && raw.temp_warning) ?? 70;
    d.temp_critical = (raw && raw.temp_critical) ?? 85;
    return d;
  }
  _deviceField() {
    return { name: "device", label: "Proxmox host (node)", required: true, selector: { device: { filter: { integration: DOMAIN, model: "Node" } } } };
  }
  _extraSchema() {
    const any = (name, label, helper) => ({ name: `entity_${name}`, label, helper, selector: { entity: { filter: { domain: "sensor" } } } });
    return [
      {
        type: "expandable",
        name: "",
        title: "Host",
        flatten: true,
        schema: [
          { type: "grid", name: "", schema: [bool("show_guests", "Guest summary"), bool("show_health", "Health chips"), bool("show_backup", "Last backup")] },
          any("temperature", "CPU temperature sensor", "Optional. The official integration has no host temperatures or load; Proxmox Extended Sensors or lm-sensors via another integration can provide them."),
          any("load", "Load average sensor"),
          any("iowait", "I/O wait sensor"),
          any("swap", "Swap usage sensor"),
          any("updates", "Pending updates sensor"),
          {
            type: "grid",
            name: "",
            schema: [
              { name: "temp_warning", label: "Temperature warning", selector: { number: { min: 30, max: 120, mode: "box" } } },
              { name: "temp_critical", label: "Temperature critical", selector: { number: { min: 30, max: 120, mode: "box" } } },
            ],
          },
        ],
      },
    ];
  }
}

class GuestsEditor extends BaseEditor {}
GuestsEditor.schema = [
  { name: "title", label: "Title", selector: { text: {} } },
  {
    type: "grid",
    name: "",
    schema: [
      {
        name: "show",
        label: "Show",
        selector: { select: { mode: "dropdown", options: [{ value: "all", label: "Containers and VMs" }, { value: "lxc", label: "Containers only" }, { value: "vm", label: "VMs only" }] } },
      },
      {
        name: "sort",
        label: "Sort by",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "name", label: "Name" },
              { value: "id", label: "ID" },
              { value: "status", label: "Status" },
              { value: "cpu", label: "CPU usage" },
              { value: "memory", label: "Memory usage" },
            ],
          },
        },
      },
    ],
  },
  {
    name: "devices",
    label: "Only these guests (leave empty for all)",
    selector: { device: { multiple: true, filter: [{ integration: DOMAIN, model: "Container" }, { integration: DOMAIN, model: "VM" }] } },
  },
  bool("hide_stopped", "Hide stopped guests"),
  thresholds,
];

const OV_DEFAULTS = {
  title: "Proxmox",
  read_only: true,
  show: "all",
  sort: "status",
  group_by_host: false,
  storage_rows: 6,
  hide_inactive_storage: true,
  show_problems: true,
  show_hosts: true,
  show_storage: true,
  show_guests: true,
  warning: 75,
  critical: 90,
  temp_warning: 70,
  temp_critical: 85,
};
class OverviewEditor extends BaseEditor {
  _toForm(c) {
    return { ...OV_DEFAULTS, ...c };
  }
  _fromForm(d) {
    const c = super._fromForm(d);
    for (const [k, v] of Object.entries(OV_DEFAULTS)) if (c[k] === v) delete c[k];
    return c;
  }
}
OverviewEditor.schema = [
  {
    type: "grid",
    name: "",
    schema: [
      { name: "title", label: "Title", selector: { text: {} } },
      { name: "icon", label: "Icon (optional)", selector: { icon: {} } },
    ],
  },
  {
    name: "read_only",
    label: "Read-only (no power controls)",
    helper: "When off, expanded rows show the guest/host controls (with confirmation) and stopped guests get a Start button.",
    selector: { boolean: {} },
  },
  {
    type: "grid",
    name: "",
    schema: [bool("show_problems", "Problems"), bool("show_hosts", "Hosts"), bool("show_storage", "Storage"), bool("show_guests", "Guests")],
  },
  {
    type: "expandable",
    name: "",
    title: "Guests",
    flatten: true,
    schema: [
      {
        type: "grid",
        name: "",
        schema: [
          {
            name: "show",
            label: "Include",
            selector: { select: { mode: "dropdown", options: [{ value: "all", label: "VMs and containers" }, { value: "lxc", label: "Containers only" }, { value: "vm", label: "VMs only" }] } },
          },
          {
            name: "sort",
            label: "Sort by",
            selector: {
              select: {
                mode: "dropdown",
                options: [
                  { value: "status", label: "Status" },
                  { value: "name", label: "Name" },
                  { value: "id", label: "ID" },
                  { value: "cpu", label: "CPU usage" },
                  { value: "memory", label: "Memory usage" },
                ],
              },
            },
          },
        ],
      },
      bool("group_by_host", "Group by host"),
    ],
  },
  {
    type: "expandable",
    name: "",
    title: "Hosts & storage",
    flatten: true,
    schema: [
      { name: "hosts", label: "Only these hosts (leave empty for all)", selector: { device: { multiple: true, filter: { integration: DOMAIN, model: "Node" } } } },
      {
        type: "grid",
        name: "",
        schema: [
          { name: "storage_rows", label: "Storage rows shown", selector: { number: { min: 1, max: 30, mode: "box" } } },
          bool("hide_inactive_storage", "Hide inactive storage"),
        ],
      },
    ],
  },
  {
    type: "expandable",
    name: "",
    title: "Thresholds",
    flatten: true,
    schema: [
      thresholds,
      {
        type: "grid",
        name: "",
        schema: [
          { name: "temp_warning", label: "Temperature warning", selector: { number: { min: 30, max: 120, mode: "box" } } },
          { name: "temp_critical", label: "Temperature critical", selector: { number: { min: 30, max: 120, mode: "box" } } },
        ],
      },
    ],
  },
];

class StorageEditor extends BaseEditor {}
StorageEditor.schema = [
  { name: "title", label: "Title", selector: { text: {} } },
  {
    type: "grid",
    name: "",
    schema: [
      { name: "layout", label: "Layout", selector: { select: { mode: "dropdown", options: [{ value: "list", label: "Bars" }, { value: "grid", label: "Rings" }] } } },
      {
        name: "sort",
        label: "Sort by",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "name", label: "Name" },
              { value: "usage", label: "Usage %" },
              { value: "size", label: "Size" },
              { value: "none", label: "As selected" },
            ],
          },
        },
      },
    ],
  },
  { name: "devices", label: "Only these storages (leave empty for all)", selector: { device: { multiple: true, filter: { integration: DOMAIN, model: "Storage" } } } },
  { type: "grid", name: "", schema: [bool("show_free", "Show free space"), bool("show_node", "Show node name"), bool("hide_inactive", "Hide inactive")] },
  {
    type: "grid",
    name: "",
    schema: [
      {
        name: "graph",
        label: "Usage graph (bars layout)",
        selector: { select: { mode: "dropdown", options: [{ value: "full", label: "Full width" }, { value: "inline", label: "Small, next to %" }, { value: "none", label: "Off" }] } },
      },
      { name: "graph_hours", label: "Period (hours)", selector: { number: { min: 1, max: 720, mode: "box" } } },
      { name: "graph_height", label: "Height (px)", selector: { number: { min: 24, max: 120, mode: "box" } } },
    ],
  },
  thresholds,
];

/* =============================================================== register === */

function define(name, cls) {
  const existing = customElements.get(name);
  if (!existing) customElements.define(name, cls);
  else if (existing !== cls)
    console.warn(`[proxmox-cards] <${name}> is already defined by another resource (probably the original proxmox-vm-card). Remove that resource to use this version.`);
}
define("proxmox-vm-card", ProxmoxVmCard);
define("proxmox-guest-card", ProxmoxGuestCard);
define("proxmox-node-card", ProxmoxNodeCard);
define("proxmox-guests-card", ProxmoxGuestsCard);
define("proxmox-storage-card", ProxmoxStorageCard);
define("proxmox-overview-card", ProxmoxOverviewCard);
define("proxmox-vm-card-editor", VmEditor);
define("proxmox-guest-card-editor", GuestEditor);
define("proxmox-node-card-editor", NodeEditor);
define("proxmox-guests-card-editor", GuestsEditor);
define("proxmox-storage-card-editor", StorageEditor);
define("proxmox-overview-card-editor", OverviewEditor);

window.customCards = window.customCards || [];
for (const [type, name, description] of [
  ["proxmox-vm-card", "Proxmox VM Card", "Monitor and control one Proxmox QEMU VM."],
  ["proxmox-overview-card", "Proxmox Overview", "Your whole Proxmox instance on one card: problems, hosts, storage and all guests."],
  ["proxmox-node-card", "Proxmox Host Card", "CPU, memory, disk, guests, backups and power controls for a Proxmox host (node)."],
  ["proxmox-guest-card", "Proxmox Container Card", "Monitor and control one Proxmox LXC container (or VM)."],
  ["proxmox-guests-card", "Proxmox Guests List", "Compact list of all Proxmox containers and VMs with status, CPU and memory."],
  ["proxmox-storage-card", "Proxmox Storage Card", "Usage of Proxmox storage pools as bars or ring gauges."],
]) {
  if (!window.customCards.some((c) => c.type === type)) window.customCards.push({ type, name, description, preview: false });
}

console.info(`%c PROXMOX-CARDS %c v${VERSION} `, "background:#e57000;color:#fff;font-weight:700", "background:#444;color:#fff");
