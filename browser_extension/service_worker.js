const HOST_NAME = "com.ai_desk.activity";
let nativePort = null;

function getNativePort() {
  if (nativePort) return nativePort;
  nativePort = chrome.runtime.connectNative(HOST_NAME);
  nativePort.onDisconnect.addListener(() => {
    nativePort = null;
  });
  return nativePort;
}

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== "ai-desk-page-context" || !sender.tab) return;
  const data = {
    ...message.data,
    audible: Boolean(sender.tab.audible),
    incognito: Boolean(sender.tab.incognito),
  };
  try {
    getNativePort().postMessage({
      version: 1,
      type: "web.semantic",
      timestamp: new Date().toISOString(),
      data,
    });
  } catch (_error) {
    nativePort = null;
  }
});
