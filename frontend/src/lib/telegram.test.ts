import { afterEach, describe, expect, it, vi } from "vitest";
import { telegram } from "./telegram";

type Listener = () => void;

function installTelegram() {
  const events = new Map<string, Listener>();
  const backClicks = new Set<Listener>();
  const app = {
    initData: "signed-init-data",
    colorScheme: "dark" as const,
    viewportHeight: 720,
    viewportStableHeight: 680,
    safeAreaInset: { top: 24, bottom: 16, left: 0, right: 0 },
    contentSafeAreaInset: { top: 58, bottom: 16, left: 0, right: 0 },
    isFullscreen: false,
    BackButton: {
      show: vi.fn(),
      hide: vi.fn(),
      onClick: vi.fn((callback: Listener) => backClicks.add(callback)),
      offClick: vi.fn((callback: Listener) => backClicks.delete(callback)),
    },
    ready: vi.fn(),
    expand: vi.fn(),
    openLink: vi.fn(),
    enableClosingConfirmation: vi.fn(),
    disableClosingConfirmation: vi.fn(),
    onEvent: vi.fn((event: string, callback: Listener) => events.set(event, callback)),
    offEvent: vi.fn((event: string) => events.delete(event)),
  };
  window.Telegram = { WebApp: app };
  return { app, events, backClicks };
}

afterEach(() => {
  delete window.Telegram;
  document.documentElement.removeAttribute("data-telegram");
  document.documentElement.removeAttribute("data-telegram-fullscreen");
  document.documentElement.removeAttribute("data-telegram-back-button");
  ["--app-top-inset", "--app-bottom-inset", "--app-left-inset", "--app-right-inset", "--app-viewport-height", "--app-viewport-stable-height"].forEach((property) => document.documentElement.style.removeProperty(property));
});

describe("Telegram viewport integration", () => {
  it("uses the content safe area in a standard Mini App without adding the device inset", () => {
    const { app, events } = installTelegram();
    const stop = telegram.init();

    expect(app.ready).toHaveBeenCalledOnce();
    expect(app.expand).toHaveBeenCalledOnce();
    expect(document.documentElement.style.getPropertyValue("--app-top-inset")).toBe("58px");
    expect(document.documentElement.style.getPropertyValue("--app-viewport-height")).toBe("720px");

    app.isFullscreen = true;
    events.get("fullscreenChanged")?.();
    expect(document.documentElement.style.getPropertyValue("--app-top-inset")).toBe("24px");
    stop();
  });

  it("uses the native BackButton and unregisters it when leaving a nested screen", () => {
    const { app, backClicks } = installTelegram();
    const onBack = vi.fn();
    const stop = telegram.showBackButton(onBack);

    expect(app.BackButton.show).toHaveBeenCalledOnce();
    expect(document.documentElement.dataset.telegramBackButton).toBe("true");
    [...backClicks].forEach((callback) => callback());
    expect(onBack).toHaveBeenCalledOnce();

    stop();
    expect(app.BackButton.hide).toHaveBeenCalledOnce();
    expect(backClicks).toHaveLength(0);
  });

  it("keeps the browser fallback when the SDK has no signed launch data", () => {
    const { app } = installTelegram();
    app.initData = "";
    const stop = telegram.showBackButton(vi.fn());

    expect(app.BackButton.show).not.toHaveBeenCalled();
    expect(document.documentElement.dataset.telegramBackButton).toBe("false");
    stop();
  });
});
