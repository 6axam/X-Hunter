type TelegramWebApp = {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  openLink(url: string): void;
  enableClosingConfirmation(): void;
  disableClosingConfirmation(): void;
};

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

export const telegram = {
  get app() {
    return window.Telegram?.WebApp;
  },
  init() {
    const app = window.Telegram?.WebApp;
    app?.ready();
    app?.expand();
    // The Mini App keeps its graphite UI when opened outside Telegram as well.
    document.documentElement.dataset.theme = app?.colorScheme ?? "dark";
  },
  open(url: string) {
    if (window.Telegram?.WebApp) window.Telegram.WebApp.openLink(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  },
};

