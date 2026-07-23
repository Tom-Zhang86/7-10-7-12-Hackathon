let sendTimer = null;
let lastHash = "";

function visibleText(element) {
  if (!element || !element.innerText) return "";
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden") return "";
  return element.innerText.trim().replace(/\s+/g, " ").slice(0, 160);
}

function collectMedia() {
  const media = [...document.querySelectorAll("video, audio")];
  const selected = media.find((item) => !item.paused) || media[0];
  if (!selected) return null;
  return {
    kind: selected.tagName.toLowerCase(),
    playing: !selected.paused && !selected.ended,
    duration: Number.isFinite(selected.duration) ? Math.round(selected.duration) : null,
    current_time: Number.isFinite(selected.currentTime) ? Math.round(selected.currentTime) : null,
  };
}

function collectContext() {
  const safeUrl = `${location.origin}${location.pathname}`;
  const description = document
    .querySelector('meta[name="description"]')
    ?.getAttribute("content")
    ?.trim()
    .slice(0, 300) || "";
  const headings = [...document.querySelectorAll("h1, h2")]
    .map(visibleText)
    .filter(Boolean)
    .slice(0, 12);
  return {
    url: safeUrl.slice(0, 1000),
    hostname: location.hostname,
    page_title: document.title.slice(0, 300),
    description,
    headings,
    language: document.documentElement.lang || "",
    media: collectMedia(),
  };
}

function lightweightHash(value) {
  const text = JSON.stringify(value);
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return String(hash >>> 0);
}

function sendContext() {
  sendTimer = null;
  if (document.visibilityState !== "visible") return;
  const data = collectContext();
  const hash = lightweightHash(data);
  if (hash === lastHash) return;
  lastHash = hash;
  chrome.runtime.sendMessage({type: "ai-desk-page-context", data});
}

function scheduleSend(delay = 1200) {
  clearTimeout(sendTimer);
  sendTimer = setTimeout(sendContext, delay);
}

new MutationObserver(() => scheduleSend()).observe(document.documentElement, {
  childList: true,
  subtree: true,
});
document.addEventListener("play", () => scheduleSend(100), true);
document.addEventListener("pause", () => scheduleSend(100), true);
document.addEventListener("visibilitychange", () => scheduleSend(100));
scheduleSend(100);
setInterval(() => {
  lastHash = "";
  scheduleSend(100);
}, 10000);
