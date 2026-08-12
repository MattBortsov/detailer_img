export function openTelegramUrl(
  url,
  {telegram, location, closeMiniApp = false},
) {
  if (!url) {
    return false;
  }
  if (telegram?.openTelegramLink) {
    telegram.openTelegramLink(url);
    if (closeMiniApp) {
      telegram.close?.();
    }
  } else {
    location.assign(url);
  }
  return true;
}
