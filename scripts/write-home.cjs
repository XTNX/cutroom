// Before packaging: remember where this checkout lives, so Cutroom.exe can start its backend
// (backend/.venv) and find vendor/video-use. Written into resources/cutroom-home.txt.
const fs = require("node:fs");
const path = require("node:path");
const root = path.resolve(__dirname, "..");
fs.mkdirSync(path.join(root, "build"), { recursive: true });
fs.writeFileSync(path.join(root, "build", "cutroom-home.txt"), root);
console.log("cutroom-home:", root);
