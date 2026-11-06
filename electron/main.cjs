// Cutroom — Electron main process.
// Owns the window, native menus/dialogs/notifications, and the FastAPI backend child process.
const { app, BrowserWindow, Menu, dialog, ipcMain, Notification, powerSaveBlocker, shell, session } = require("electron");
const { spawn, execFileSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");
const setup = require("./setup.cjs");

const isDev = !app.isPackaged;
const ROOT = path.resolve(__dirname, "..");
const DEV_URL = "http://127.0.0.1:5173";

let win = null;
let playerWin = null;
let backend = null; // { proc, port, token }
let blockerId = null;
let quitting = false;

// ------------------------------------------------------------------ window state

const statePath = () => path.join(app.getPath("userData"), "window.json");
function loadState() {
  try { return JSON.parse(fs.readFileSync(statePath(), "utf8")); } catch { return { width: 1440, height: 900 }; }
}
function saveState() {
  if (!win || win.isDestroyed()) return;
  const b = win.getNormalBounds();
  fs.writeFileSync(statePath(), JSON.stringify({ ...b, maximized: win.isMaximized() }));
}

// ------------------------------------------------------------------ backend

function freePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => { const { port } = srv.address(); srv.close(() => resolve(port)); });
    srv.on("error", reject);
  });
}

// Where Cutroom's backend, vendor/video-use and runtime live.
//  - dev / this machine's exe: resources/cutroom-home.txt holds the checkout path
//  - installed copy: the file says AUTO → <install>/app/Cutroom.exe, so home is two levels up
// CUTROOM_HOME overrides both.
function cutroomHome() {
  if (process.env.CUTROOM_HOME) return process.env.CUTROOM_HOME;
  if (isDev) return ROOT;
  let v = "AUTO";
  try { v = fs.readFileSync(path.join(process.resourcesPath, "cutroom-home.txt"), "utf8").trim(); } catch { /* AUTO */ }
  return v && v !== "AUTO" ? v : path.resolve(process.resourcesPath, "..", "..");
}

function backendCommand() {
  if (!isDev) {
    // packaged: PyInstaller sidecar next to the app resources
    const exe = path.join(process.resourcesPath, "backend", "cutroom-backend.exe");
    if (fs.existsSync(exe)) return { cmd: exe, args: [], cwd: path.dirname(exe) };
  }
  const home = cutroomHome();
  const installed = setup.paths(home).py;            // runtime downloaded by the first-run setup
  const py = fs.existsSync(installed) ? installed : path.join(home, "backend", ".venv", "Scripts", "python.exe");
  if (!fs.existsSync(py)) throw new Error(`Не найден Python бэкенда: ${py}
Проверьте папку Cutroom (resources/cutroom-home.txt) или задайте CUTROOM_HOME.`);
  return { cmd: py, args: ["-m", "cutroom.server"], cwd: path.join(home, "backend") };
}

async function startBackend() {
  const port = await freePort();
  const token = crypto.randomBytes(24).toString("hex");
  const { cmd, args, cwd } = backendCommand();
  const proc = spawn(cmd, [...args, "--port", String(port), "--token", token], {
    cwd,
    env: { ...process.env, ...setup.backendEnv(cutroomHome()), PYTHONUTF8: "1", CUTROOM_PARENT_WATCH: "1" },
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend = { proc, port, token };
  return new Promise((resolve, reject) => {
    let out = "";
    const timer = setTimeout(() => reject(new Error("бэкенд не стартовал за 60 с:\n" + out.slice(-2000))), 60000);
    const onData = (d) => {
      const s = d.toString();
      out += s;
      if (isDev) process.stdout.write("[backend] " + s);
      if (s.includes("CUTROOM_READY")) { clearTimeout(timer); resolve(backend); }
    };
    proc.stdout.on("data", onData);
    proc.stderr.on("data", (d) => { out += d.toString(); if (isDev) process.stderr.write("[backend] " + d); });
    proc.on("exit", (code) => {
      clearTimeout(timer);
      if (!quitting) {
        reject(new Error(`бэкенд завершился (код ${code}):\n` + out.slice(-2000)));
        if (win && !win.isDestroyed()) dialog.showErrorBox("Cutroom: бэкенд остановился", `Код ${code}. Последние строки:\n${out.slice(-1200)}`);
      }
    });
  });
}

async function api(pathname, method = "GET") {
  if (!backend) return null;
  const r = await fetch(`http://127.0.0.1:${backend.port}${pathname}`, { method, headers: { "X-Cutroom-Token": backend.token } });
  return r.json();
}

function killBackend() {
  if (!backend?.proc || backend.proc.exitCode != null) return;
  try {
    // the whole tree: python → ffmpeg / transcribe helpers
    execFileSync("taskkill", ["/T", "/F", "/PID", String(backend.proc.pid)], { windowsHide: true, stdio: "ignore" });
  } catch { backend.proc.kill(); }
}

// ------------------------------------------------------------------ windows

function createWindow() {
  const st = loadState();
  win = new BrowserWindow({
    width: st.width, height: st.height, x: st.x, y: st.y,
    minWidth: 720, minHeight: 520,
    backgroundColor: "#0b0c10",
    title: "Cutroom",
    icon: isDev ? path.join(ROOT, "build", "icon.ico") : path.join(process.resourcesPath, "icon.ico"),
    titleBarStyle: "hidden",
    titleBarOverlay: { color: "#12141a", symbolColor: "#8c93a3", height: 40 },
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false, // program playback swaps segments on timers; keep them exact when unfocused
    },
  });
  if (st.maximized) win.maximize();
  win.once("ready-to-show", () => win.show());
  for (const ev of ["resize", "move", "close"]) win.on(ev, saveState);

  // the renderer never navigates away or opens arbitrary windows
  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (e, url) => { if (!url.startsWith(DEV_URL) && !url.startsWith("file://")) e.preventDefault(); });

  win.on("close", async (e) => {
    if (quitting) return;
    e.preventDefault();
    let running = 0;
    try { running = (await api("/api/jobs/summary"))?.running ?? 0; } catch { /* backend gone */ }
    if (running) {
      const { response } = await dialog.showMessageBox(win, {
        type: "warning", buttons: ["Остановить и выйти", "Остаться"], defaultId: 1, cancelId: 1,
        title: "Cutroom", message: `Идёт задач: ${running}.`, detail: "Выход остановит ffmpeg и расшифровку. Готовые результаты и кэш сохранятся.",
      });
      if (response !== 0) return;
    }
    quitting = true;
    try { await api("/api/shutdown", "POST"); } catch { /* ignore */ }
    setTimeout(() => { killBackend(); app.quit(); }, 400);
  });

  if (isDev) win.loadURL(DEV_URL);
  else win.loadFile(path.join(ROOT, "dist", "index.html"));
}

function openPlayerWindow() {
  if (playerWin && !playerWin.isDestroyed()) return playerWin.focus();
  playerWin = new BrowserWindow({
    width: 1280, height: 760, backgroundColor: "#000", title: "Cutroom — плеер", autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, nodeIntegration: false, sandbox: true },
  });
  const q = "?player=1";
  if (isDev) playerWin.loadURL(DEV_URL + "/" + q);
  else playerWin.loadFile(path.join(ROOT, "dist", "index.html"), { search: q });
}

// ------------------------------------------------------------------ menu

function send(cmd) { win?.webContents.send("menu", cmd); }
function buildMenu() {
  const t = [
    { label: "Файл", submenu: [
      { label: "Открыть папку…", accelerator: "Ctrl+O", click: () => send("open-folder") },
      { label: "Подготовить все файлы", click: () => send("ingest") },
      { type: "separator" },
      { label: "Закрыть проект", click: () => send("close-project") },
      { type: "separator" },
      { role: "quit", label: "Выход" },
    ] },
    { label: "Правка", submenu: [
      { label: "Отменить", accelerator: "Ctrl+Z", click: () => send("undo") },
      { label: "Повторить", accelerator: "Ctrl+Shift+Z", click: () => send("redo") },
      { type: "separator" },
      { role: "cut", label: "Вырезать" }, { role: "copy", label: "Копировать" }, { role: "paste", label: "Вставить" },
    ] },
    { label: "Вид", submenu: [
      { label: "Команды", accelerator: "Ctrl+K", click: () => send("palette") },
      { label: "Очередь задач", click: () => send("queue") },
      { label: "Система", click: () => send("system") },
      { label: "Плеер в отдельном окне", accelerator: "Ctrl+Shift+P", click: openPlayerWindow },
      { label: "Тема", click: () => send("theme") },
      { type: "separator" },
      { role: "togglefullscreen", label: "Полный экран" },
      ...(isDev ? [{ role: "toggleDevTools", label: "DevTools" }, { role: "reload", label: "Перезагрузить" }] : []),
    ] },
    { label: "Проект", submenu: [
      { label: "Предложить стратегию", accelerator: "Ctrl+Enter", click: () => send("draft") },
      { label: "Показать папку edit/", click: () => send("reveal-edit") },
    ] },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(t));
}

// ------------------------------------------------------------------ IPC (narrow bridge)

let projectDir = null;
const inside = (p, root) => {
  if (!root) return false;
  const rel = path.relative(path.resolve(root), path.resolve(p));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
};

let backendReady = null; // promise: the window opens at once and waits for the backend here
let setupState = null;    // first-run download progress (null = not needed)
let retrySetup = null;
ipcMain.handle("setup-status", () => setupState ?? { state: setup.needed(cutroomHome()) ? "starting" : "none" });
ipcMain.on("setup-retry", () => { if (retrySetup) { const r = retrySetup; retrySetup = null; r(); } });
ipcMain.handle("backend", async () => {
  const b = await backendReady;
  return { url: `http://127.0.0.1:${b.port}`, token: b.token };
});
ipcMain.handle("pick-folder", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory"], title: "Папка с исходниками" });
  if (r.canceled) return null;
  projectDir = r.filePaths[0];
  return projectDir;
});
ipcMain.handle("pick-files", async () => {
  const r = await dialog.showOpenDialog(win, {
    properties: ["openFile", "multiSelections"], title: "Исходники",
    filters: [{ name: "Видео и аудио", extensions: ["mp4", "mov", "mxf", "mkv", "m4v", "mts", "wav", "mp3", "aac", "m4a", "flac"] }],
  });
  return r.canceled ? [] : r.filePaths;
});
ipcMain.on("project-dir", (_e, dir) => { projectDir = dir; });
ipcMain.on("reveal", (_e, p) => {
  // only inside the open project (sources and edit/) or the music shelf under the user's Music folder
  if (inside(p, projectDir) || inside(p, app.getPath("music"))) shell.showItemInFolder(p);
});
// music libraries open in the system browser — allow-listed hosts only
const MUSIC_HOSTS = ["mixkit.co", "pixabay.com", "studio.youtube.com", "freemusicarchive.org", "uppbeat.io"];
ipcMain.on("open-external", (_e, url) => {
  try {
    const u = new URL(url);
    if (u.protocol === "https:" && MUSIC_HOSTS.some((h) => u.hostname === h || u.hostname.endsWith("." + h))) shell.openExternal(u.toString());
  } catch { /* not a url */ }
});
ipcMain.on("busy", (_e, busy) => {
  if (busy && blockerId == null) blockerId = powerSaveBlocker.start("prevent-app-suspension");
  if (!busy && blockerId != null) { powerSaveBlocker.stop(blockerId); blockerId = null; }
});
ipcMain.on("notify", (_e, { title, body }) => {
  if (Notification.isSupported()) new Notification({ title, body, silent: false }).show();
  shell.beep();
});

// ------------------------------------------------------------------ lifecycle

app.setAppUserModelId("Cutroom");
const single = app.requestSingleInstanceLock();
if (!single) app.quit();
app.on("second-instance", () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

app.whenReady().then(async () => {
  if (!isDev) {
    session.defaultSession.webRequest.onHeadersReceived((d, cb) => cb({
      responseHeaders: {
        ...d.responseHeaders,
        "Content-Security-Policy": ["default-src 'self'; connect-src 'self' http://127.0.0.1:* ws://127.0.0.1:*; img-src 'self' data: blob: http://127.0.0.1:*; media-src 'self' blob: http://127.0.0.1:*; style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self'"],
      },
    }));
  }
  buildMenu();
  backendReady = (async () => {
    // first launch of an installed copy: download Python, modules, ffmpeg, GigaAM (with progress)
    const home = cutroomHome();
    while (setup.needed(home)) {
      try { await setup.run(home, (st) => { setupState = st; win?.webContents.send("setup", st); }); }
      catch { await new Promise((res) => { retrySetup = res; }); }   // the window shows the error and «Повторить»
    }
    return startBackend();
  })();
  backendReady.catch((e) => {
    dialog.showErrorBox("Cutroom не запустился", String(e.message || e));
    app.exit(1);
  });
  createWindow();
});

app.on("before-quit", () => { quitting = true; });
app.on("will-quit", () => { if (blockerId != null) powerSaveBlocker.stop(blockerId); setup.cancel(); killBackend(); });
app.on("window-all-closed", () => app.quit());
