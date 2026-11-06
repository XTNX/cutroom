// Build the lightweight installer archive: release/Cutroom-<version>-setup.zip
// Contents: the app (Electron), backend sources + uv.lock, pinned vendor/video-use, uv.exe, installer scripts.
// Python, modules, ffmpeg and the GigaAM model are downloaded by the app on first launch (electron/setup.cjs).
const { execFileSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const name = `Cutroom-${pkg.version}`;
const stage = path.join(root, "release", name);
const zip = path.join(root, "release", `${name}-setup.zip`);
const sh = (cmd, args, opts = {}) => execFileSync(cmd, args, { stdio: "inherit", cwd: root, shell: process.platform === "win32" && cmd === "npm", ...opts });

const SKIP_DIRS = new Set([".git", "__pycache__", ".venv", ".pytest_cache", "node_modules", "edit", "video_use.egg-info"]);
function copy(src, dst) {
  const st = fs.statSync(src);
  if (st.isDirectory()) {
    if (SKIP_DIRS.has(path.basename(src))) return;
    fs.mkdirSync(dst, { recursive: true });
    for (const f of fs.readdirSync(src)) copy(path.join(src, f), path.join(dst, f));
  } else if (!/\.(pyc|log)$/.test(src)) {
    fs.copyFileSync(src, dst);
  }
}
function writeBom(file, text) { fs.writeFileSync(file, "\ufeff" + text.replace(/^\ufeff/, "")); }   // Windows PowerShell 5.1 needs the BOM for Cyrillic

console.log("1/4 сборка приложения");
// separate output: release/win-unpacked may be running (this machine's own shortcut)
const out = path.join(root, "release", "_pkg");
sh("npm", ["exec", "--", "vite", "build"]);
sh("npm", ["exec", "--", "electron-builder", "--win", "dir", `-c.directories.output=${out}`]);

console.log("2/4 сборка папки пакета");
fs.rmSync(stage, { recursive: true, force: true });
copy(path.join(out, "win-unpacked"), path.join(stage, "app"));
fs.writeFileSync(path.join(stage, "app", "resources", "cutroom-home.txt"), "AUTO");   // home = two levels above Cutroom.exe
for (const f of ["cutroom", "helpers", "prompts"]) copy(path.join(root, "backend", f), path.join(stage, "backend", f));
for (const f of ["pyproject.toml", "uv.lock"]) fs.copyFileSync(path.join(root, "backend", f), path.join(stage, "backend", f));
copy(path.join(root, "vendor", "video-use"), path.join(stage, "vendor", "video-use"));
const uvPath = execFileSync("where", ["uv"], { encoding: "utf8" }).split(/\r?\n/)[0].trim();
fs.mkdirSync(path.join(stage, "tools"), { recursive: true });
fs.copyFileSync(uvPath, path.join(stage, "tools", "uv.exe"));
fs.copyFileSync(path.join(root, "build", "icon.ico"), path.join(stage, "icon.ico"));
writeBom(path.join(stage, "install.ps1"), fs.readFileSync(path.join(root, "installer", "install.ps1"), "utf8"));
writeBom(path.join(stage, "uninstall.ps1"), fs.readFileSync(path.join(root, "installer", "uninstall.ps1"), "utf8"));
fs.writeFileSync(path.join(stage, "ПРОЧТИ.txt"), "\ufeff" + fs.readFileSync(path.join(root, "installer", "README.txt"), "utf8"));
const cmd = (script, pause) => `@echo off\r\nchcp 65001 >nul\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0${script}"\r\nif errorlevel 1 pause\r\n${pause ? "pause\r\n" : ""}`;
fs.writeFileSync(path.join(stage, "Установить Cutroom.cmd"), cmd("install.ps1", false));
fs.writeFileSync(path.join(stage, "Удалить Cutroom.cmd"), cmd("uninstall.ps1", true));
const vu = execFileSync("git", ["-C", path.join(root, "vendor", "video-use"), "rev-parse", "HEAD"], { encoding: "utf8" }).trim();
fs.writeFileSync(path.join(stage, "VERSION.txt"), `Cutroom ${pkg.version}\r\nvideo-use ${vu}\r\nсобрано ${new Date().toISOString()}\r\n`);

console.log("3/4 архив");
fs.rmSync(zip, { force: true });
const z7 = ["C:\\Program Files\\7-Zip\\7z.exe", "C:\\Program Files (x86)\\7-Zip\\7z.exe"].find((p) => fs.existsSync(p));
if (z7) execFileSync(z7, ["a", "-tzip", "-mx=7", "-mcu=on", zip, `.\\${name}\\*`], { stdio: "ignore", cwd: path.join(root, "release") });
else sh("tar", ["-a", "-cf", zip, "-C", path.join(root, "release"), name]);

const mb = (fs.statSync(zip).size / 1e6).toFixed(0);
console.log(`4/4 готово: ${zip} (${mb} МБ)`);
