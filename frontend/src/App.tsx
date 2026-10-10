import { useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { authenticate } from "./lib/api";
import { telegram } from "./lib/telegram";
import { FindingPage } from "./pages/FindingPage";
import { FindingsPage } from "./pages/FindingsPage";
import { SearchesPage } from "./pages/SearchesPage";
import { SettingsPage } from "./pages/SettingsPage";

export default function App() {
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  useEffect(() => {
    telegram.init();
    authenticate(telegram.app?.initData ?? "").catch((error: Error) => setAuthError(error.message)).finally(() => setReady(true));
  }, []);
  if (!ready) return <div className="splash"><span className="brand__mark">OH</span><p>Подключаем приложение…</p></div>;
  if (authError) return <div className="fatal"><span className="eyebrow">Доступ закрыт</span><h1>Не удалось войти</h1><p>{authError}</p><button className="button" onClick={() => location.reload()}>Повторить</button></div>;
  return <Routes><Route element={<Layout />}><Route index element={<Navigate to="/searches" replace />} /><Route path="searches" element={<SearchesPage />} /><Route path="findings" element={<FindingsPage />} /><Route path="findings/:id" element={<FindingPage />} /><Route path="settings" element={<SettingsPage />} /></Route></Routes>;
}

