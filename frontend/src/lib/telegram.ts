type TelegramInsets = {
  top?: number;
  bottom?: number;
  left?: number;
  right?: number;
};

type TelegramBackButton = {
  show(): void;
  hide(): void;
  onClick(callback: () => void): void;
  offClick(callback: () => void): void;
};

type TelegramEvent = "viewportChanged" | "safeAreaChanged" | "contentSafeAreaChanged" | "fullscreenChanged" | "themeChanged";

type TelegramWebApp = {
  initData: string;
  colorScheme: "light" | "dark";
  viewportHeight?: number;
  viewportStableHeight?: number;
  safeAreaInset?: TelegramInsets;
  contentSafeAreaInset?: TelegramInsets;
  isFullscreen?: boolean;
  BackButton?: TelegramBackButton;
  ready(): void;
  expand(): void;
  openLink(url: string): void;
  enableClosingConfirmation(): void;
  disableClosingConfirmation(): void;
  onEvent?(event: TelegramEvent, callback: () => void): void;
  offEvent?(event: TelegramEvent, callback: () => void): void;
};

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

const viewportEvents: TelegramEvent[] = ["viewportChanged", "safeAreaChanged", "contentSafeAreaChanged", "fullscreenChanged", "themeChanged"];

function isMiniApp(app = window.Telegram?.WebApp) {
  // telegram-web-app.js is also loaded by ordinary browsers. Its BackButton is
  // inert without signed launch data, so keep browser navigation as a fallback.
  return Boolean(app?.initData);
}

function cssPixels(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value}px` : undefined;
}

function pickInset(primary: number | undefined, fallback: number | undefined) {
  return typeof primary === "number" && Number.isFinite(primary) ? primary : fallback;
}

function setCssVariable(root: HTMLElement, name: string, value: string | undefined) {
  if (value === undefined) root.style.removeProperty(name);
  else root.style.setProperty(name, value);
}

function syncViewport(app: TelegramWebApp) {
  const root = document.documentElement;
  const safe = app.safeAreaInset ?? {};
  const contentSafe = app.contentSafeAreaInset ?? {};
  const fullscreen = Boolean(app.isFullscreen);

  // In the regular Mini App viewport Telegram's content inset already includes
  // the host header. In fullscreen only the device safe area is required.
  // Pick one source, never add them together.
  const topInset = fullscreen ? pickInset(safe.top, contentSafe.top) : pickInset(contentSafe.top, safe.top);
  const bottomInset = fullscreen ? pickInset(safe.bottom, contentSafe.bottom) : pickInset(contentSafe.bottom, safe.bottom);

  root.dataset.telegram = "true";
  root.dataset.telegramFullscreen = String(fullscreen);
  root.dataset.theme = app.colorScheme ?? "dark";
  setCssVariable(root, "--app-top-inset", cssPixels(topInset));
  setCssVariable(root, "--app-bottom-inset", cssPixels(bottomInset));
  setCssVariable(root, "--app-left-inset", cssPixels(fullscreen ? pickInset(safe.left, contentSafe.left) : pickInset(contentSafe.left, safe.left)));
  setCssVariable(root, "--app-right-inset", cssPixels(fullscreen ? pickInset(safe.right, contentSafe.right) : pickInset(contentSafe.right, safe.right)));
  setCssVariable(root, "--app-viewport-height", typeof app.viewportHeight === "number" && app.viewportHeight > 0 ? `${app.viewportHeight}px` : undefined);
  setCssVariable(root, "--app-viewport-stable-height", typeof app.viewportStableHeight === "number" && app.viewportStableHeight > 0 ? `${app.viewportStableHeight}px` : undefined);
}

export const telegram = {
  get app() {
    return window.Telegram?.WebApp;
  },
  init() {
    const app = window.Telegram?.WebApp;
    if (!app || !isMiniApp(app)) {
      document.documentElement.dataset.theme = "dark";
      return () => undefined;
    }

    app.ready();
    app.expand();
    const updateViewport = () => syncViewport(app);
    updateViewport();
    viewportEvents.forEach((event) => app.onEvent?.(event, updateViewport));

    return () => viewportEvents.forEach((event) => app.offEvent?.(event, updateViewport));
  },
  showBackButton(onClick: () => void) {
    const app = window.Telegram?.WebApp;
    const button = isMiniApp(app) ? app?.BackButton : undefined;
    if (!button) {
      document.documentElement.dataset.telegramBackButton = "false";
      return () => undefined;
    }

    button.onClick(onClick);
    button.show();
    document.documentElement.dataset.telegramBackButton = "true";
    return () => {
      button.offClick(onClick);
      button.hide();
      document.documentElement.dataset.telegramBackButton = "false";
    };
  },
  hideBackButton() {
    const app = window.Telegram?.WebApp;
    const button = isMiniApp(app) ? app?.BackButton : undefined;
    button?.hide();
    document.documentElement.dataset.telegramBackButton = "false";
  },
  open(url: string) {
    const app = window.Telegram?.WebApp;
    if (isMiniApp(app)) app?.openLink(url);
    else window.open(url, "_blank", "noopener,noreferrer");
  },
};
