function currentWebApp(telegram, browserWindow) {
  return browserWindow?.Telegram?.WebApp ?? telegram;
}

export function closeTelegramMiniApp({telegram, browserWindow}) {
  const webApp = currentWebApp(telegram, browserWindow);
  if (typeof webApp?.close !== "function") {
    return false;
  }

  webApp.disableClosingConfirmation?.();
  webApp.close();
  return true;
}

export function returnToTelegramChat(
  url,
  {telegram, browserWindow, location},
) {
  if (closeTelegramMiniApp({telegram, browserWindow})) {
    return true;
  }
  if (!url) {
    return false;
  }

  location.assign(url);
  return true;
}
