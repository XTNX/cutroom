// First-run setup of an installed Cutroom: the archive ships only the app, the backend sources,
// uv.exe and uv.lock. On first launch this downloads what the machine needs:
//   1. Python 3.12 + backend modules — `uv sync --frozen` from the pinned uv.lock
//      (NVIDIA GPU → CUDA builds; otherwise CPU builds, ~4× smaller)
//   2. ffmpeg — pinned Gyan build, SHA-256 checked (skipped when a suitable ffmpeg is on PATH)
//   3. GigaAM speech model weights (Hugging Face mirror ai-sage/GigaAM-v3, branch e2e_rnnt)
// Everything lands inside the install folder; nothing needs admin rights.
const { spawn, execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const FFMPEG = {
  url: "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-full_build-shared.zip",
  sha256: "8d31e162f1616e37aab3fa2db991b97e1b8dbeb1c8465fd81a81be16ff91b328",
  folder: "ffmpeg-9.0.2-full_build-shared",
  size: 99899672,
};

function paths(home) {
  const rt = path.join(home, "runtime");
  return {
    home, rt,
    uv: path.join(home, "tools", "uv.exe"),
    venv: path.join(rt, "venv"),
    py: path.join(rt, "venv", "Scripts", "python.exe"),
    pyDir: path.join(rt, "python"),
    cache: path.join(rt, "uv-cache"),
    marker: path.join(rt, "ready.json"),
    lock: path.join(home, "backend", "uv.lock"),
    ffmpegBin: path.join(home, "tools", "ffmpeg", "bin"),
    models: path.join(home, "models"),
  };
}

const sha = (file) => crypto.createHash("sha256").update(fs.readFileSync(file)).digest("hex");

function hasNvidia() {
  try { execFileSync("nvidia-smi", ["-L"], { stdio: "ignore", windowsHide: true, timeout: 8000 }); return true; } catch { return false; }
}

function systemFfmpegOk() {
  try {
    const f = execFileSync("ffmpeg", ["-hide_banner", "-filters"], { windowsHide: true, timeout: 15000, encoding: "utf8" });
    execFileSync("ffprobe", ["-version"], { stdio: "ignore", windowsHide: true, timeout: 8000 });
    return f.includes("zscale");
  } catch { return false; }
}

function readMarker(p) {
  try { return JSON.parse(fs.readFileSync(p.marker, "utf8")); } catch { return null; }
}

/** Installed layout (tools/uv.exe present) whose runtime is missing or out of date. */
function needed(home) {
  const p = paths(home);
  if (!fs.existsSync(p.uv)) return false;          // a source checkout: dev setup, not ours
  const m = readMarker(p);
  return !(m && fs.existsSync(p.py) && m.lock === sha(p.lock) && m.ffmpeg && m.gigaam);
}

/** Env for the backend of an installed copy: its own ffmpeg and model folder. */
function backendEnv(home) {
  const p = paths(home);
  const env = {};
  if (fs.existsSync(p.py) && fs.existsSync(p.uv)) {
    if (fs.existsSync(p.ffmpegBin)) env.PATH = p.ffmpegBin + path.delimiter + (process.env.PATH || "");
    env.HF_HOME = p.models;
  }
  return env;
}

function killTree(proc) {
  if (!proc || proc.exitCode != null) return;
  try { execFileSync("taskkill", ["/T", "/F", "/PID", String(proc.pid)], { stdio: "ignore", windowsHide: true }); } catch { proc.kill(); }
}

let current = null; // running child, for cancel on quit

function runProc(cmd, args, opts, onLine) {
  return new Promise((resolve, reject) => {
    const proc = spawn(cmd, args, { windowsHide: true, ...opts });
    current = proc;
    let tail = [];
    const feed = (d) => d.toString().split(/\r?\n|\r/).forEach((l) => { l = l.trim(); if (l) { tail = [...tail.slice(-30), l]; onLine(l); } });
    proc.stdout.on("data", feed);
    proc.stderr.on("data", feed);
    proc.on("error", reject);
    proc.on("exit", (code) => { current = null; code === 0 ? resolve() : reject(new Error(`${path.basename(cmd)} завершился с кодом ${code}:\n${tail.slice(-6).join("\n")}`)); });
  });
}

async function download(url, file, expectSha, onProgress) {
  const r = await fetch(url, { redirect: "follow" });
  if (!r.ok) throw new Error(`Не удалось скачать ${url}: HTTP ${r.status}`);
  const total = Number(r.headers.get("content-length")) || 0;
  const out = fs.createWriteStream(file);
  const hash = crypto.createHash("sha256");
  let got = 0;
  for await (const chunk of r.body) {
    hash.update(chunk);
    got += chunk.length;
    if (!out.write(chunk)) await new Promise((res) => out.once("drain", res));
    onProgress(total ? got / total : 0, got);
  }
  await new Promise((res) => out.end(res));
  const digest = hash.digest("hex");
  if (expectSha && digest !== expectSha) { fs.rmSync(file, { force: true }); throw new Error(`Контрольная сумма не совпала (${digest.slice(0, 12)}…): файл повреждён или подменён. Повторите.`); }
}

/**
 * Run the setup. emit(state) receives {state, steps:[{id,title,status,progress,detail}], log, error}.
 * Throws on failure (state stays "error" with a human-readable message).
 */
async function run(home, emit) {
  const p = paths(home);
  const gpu = hasNvidia();
  const steps = [
    { id: "modules", title: gpu ? "Python и модули (с поддержкой видеокарты NVIDIA)" : "Python и модули (без видеокарты NVIDIA)",
      detail: gpu ? "≈ 4–5 ГБ, обычно 10–25 минут" : "≈ 1 ГБ, обычно 3–8 минут", status: "wait", progress: 0 },
    { id: "ffmpeg", title: "ffmpeg — обработка видео", detail: "≈ 100 МБ", status: "wait", progress: 0 },
    { id: "gigaam", title: "Модель распознавания речи GigaAM", detail: "≈ 450 МБ", status: "wait", progress: 0 },
  ];
  const log = [];
  const state = { state: "running", gpu, steps, log, error: null };
  const push = () => emit(JSON.parse(JSON.stringify(state)));
  const step = (id) => steps.find((s) => s.id === id);
  const line = (l) => { log.push(l); if (log.length > 60) log.shift(); };
  fs.mkdirSync(p.rt, { recursive: true });
  const prev = readMarker(p) || {};
  try {
    // 1 — Python + modules, exactly the versions in uv.lock
    let s = step("modules");
    s.status = "run"; push();
    const extras = gpu ? ["--extra", "cuda", "--extra", "gigaam-cuda"] : ["--extra", "gigaam-cpu"];
    // uv announces every download up front with its size ("Downloading torch (2.6GiB)") and
    // reports " Downloaded torch" when done: progress = finished bytes / announced bytes
    const sizes = new Map(); let doneBytes = 0;
    const toBytes = (n, u) => Number(n) * ({ B: 1, KiB: 1024, MiB: 1024 ** 2, GiB: 1024 ** 3 }[u] || 1);
    await runProc(p.uv, ["sync", "--frozen", "--no-dev", "--project", path.join(home, "backend"), "--python", "3.12.10",
      "--managed-python", "--no-progress", ...extras], {
      cwd: path.join(home, "backend"),
      env: { ...process.env, UV_PROJECT_ENVIRONMENT: p.venv, UV_PYTHON_INSTALL_DIR: p.pyDir, UV_CACHE_DIR: p.cache, NO_COLOR: "1" },
    }, (l) => {
      line(l);
      let m = l.match(/^Downloading (\S+) \(([\d.]+)(B|KiB|MiB|GiB)\)/);
      if (m) sizes.set(m[1], toBytes(m[2], m[3]));
      m = l.match(/^\s*Downloaded (\S+)/);
      if (m && sizes.has(m[1])) { doneBytes += sizes.get(m[1]); sizes.set(m[1], -sizes.get(m[1])); }
      const total = [...sizes.values()].reduce((a, v) => a + Math.abs(v), 0);
      const left = [...sizes.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
      if (left.length) s.current = `осталось: ${left.slice(0, 3).map(([k, v]) => `${k} ${(v / 1024 ** 2).toFixed(0)} МБ`).join(", ")}`;
      if (/^(Prepared|Installed) /.test(l)) { s.progress = /^Installed/.test(l) ? 0.99 : 0.92; s.current = "устанавливаю…"; }
      else if (total) s.progress = Math.min(0.9, 0.04 + 0.86 * doneBytes / total);
      push();
    });
    fs.rmSync(p.cache, { recursive: true, force: true });   // venv keeps its own copies (hardlinks)
    s.status = "done"; s.progress = 1; s.current = ""; push();

    // 2 — ffmpeg (system one if it is complete enough)
    s = step("ffmpeg");
    s.status = "run"; push();
    if (systemFfmpegOk() && !process.env.CUTROOM_FORCE_FFMPEG) {
      s.detail = "уже установлен на компьютере"; line("ffmpeg: найден в системе");
    } else if (!fs.existsSync(path.join(p.ffmpegBin, "ffmpeg.exe"))) {
      const tools = path.join(home, "tools");
      const zip = path.join(tools, "ffmpeg.zip");
      await download(FFMPEG.url, zip, FFMPEG.sha256, (k, got) => { s.progress = k * 0.9; s.current = `${(got / 1e6).toFixed(0)} из ${(FFMPEG.size / 1e6).toFixed(0)} МБ`; push(); });
      line("ffmpeg: контрольная сумма совпала, распаковываю");
      const tmp = path.join(tools, "ffmpeg-tmp");
      fs.rmSync(tmp, { recursive: true, force: true });
      fs.mkdirSync(tmp, { recursive: true });
      await runProc("tar", ["-xf", zip, "-C", tmp], {}, line);
      fs.rmSync(path.join(tools, "ffmpeg"), { recursive: true, force: true });
      fs.renameSync(path.join(tmp, FFMPEG.folder), path.join(tools, "ffmpeg"));
      fs.rmSync(tmp, { recursive: true, force: true });
      fs.rmSync(zip, { force: true });
    }
    s.status = "done"; s.progress = 1; s.current = ""; push();

    // 3 — GigaAM weights into the install folder (the CDN cert is broken; the HF mirror is used)
    s = step("gigaam");
    s.status = "run"; push();
    const code = [
      "from huggingface_hub import hf_hub_download",
      "for f in ('config.json','tokenizer.model','pytorch_model.bin'):",
      "    print('FILE', f, flush=True); hf_hub_download('ai-sage/GigaAM-v3', f, revision='e2e_rnnt')",
    ].join("\n");
    await runProc(p.py, ["-c", code], { env: { ...process.env, HF_HOME: p.models, HF_HUB_DISABLE_SYMLINKS_WARNING: "1", PYTHONUTF8: "1" } }, (l) => {
      line(l);
      const m = l.match(/(\d{1,3})%\|/);
      if (/^FILE pytorch_model/.test(l)) s.current = "веса модели";
      if (m && s.current === "веса модели") s.progress = Math.min(0.99, Number(m[1]) / 100);
      push();
    });
    s.status = "done"; s.progress = 1; s.current = ""; push();

    fs.writeFileSync(p.marker, JSON.stringify({ lock: sha(p.lock), gpu, ffmpeg: true, gigaam: true, when: new Date().toISOString(), prev: prev.when || null }, null, 1));
    state.state = "done"; push();
  } catch (e) {
    const s = steps.find((x) => x.status === "run");
    if (s) s.status = "error";
    state.state = "error";
    state.error = String(e.message || e) + "\n\nПроверьте подключение к интернету и нажмите «Повторить» — уже скачанное не загружается заново.";
    push();
    throw e;
  }
}

module.exports = { needed, run, backendEnv, paths, cancel: () => killTree(current) };
