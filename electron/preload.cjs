// Narrow bridge: the renderer gets no Node, no fs — only these calls.
const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("cutroom", {
  isElectron: true,
  backend: () => ipcRenderer.invoke("backend"),
  pickFolder: () => ipcRenderer.invoke("pick-folder"),
  pickFiles: () => ipcRenderer.invoke("pick-files"),
  pathForFile: (file) => webUtils.getPathForFile(file),
  setProjectDir: (dir) => ipcRenderer.send("project-dir", dir),
  revealInFolder: (p) => ipcRenderer.send("reveal", p),
  openExternal: (url) => ipcRenderer.send("open-external", url),
  setBusy: (busy) => ipcRenderer.send("busy", !!busy),
  setupStatus: () => ipcRenderer.invoke("setup-status"),
  setupRetry: () => ipcRenderer.send("setup-retry"),
  onSetup: (cb) => {
    const h = (_e, st) => cb(st);
    ipcRenderer.on("setup", h);
    return () => ipcRenderer.removeListener("setup", h);
  },
  notify: (title, body) => ipcRenderer.send("notify", { title, body }),
  onMenu: (cb) => {
    const h = (_e, cmd) => cb(cmd);
    ipcRenderer.on("menu", h);
    return () => ipcRenderer.removeListener("menu", h);
  },
});
