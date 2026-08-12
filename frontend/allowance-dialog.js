export function syncAllowanceDialog(
  {dialog, message, action},
  {visible, text},
) {
  if (visible) {
    message.textContent = text;
    if (!dialog.open) {
      dialog.showModal();
      action.focus();
      return true;
    }
    return false;
  }

  if (dialog.open) {
    dialog.close();
    return true;
  }
  return false;
}
