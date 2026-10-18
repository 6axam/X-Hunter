import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Bot, BrainCircuit, Database, ShieldCheck } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Card, Pill, Skeleton } from "../components/ui";
import { api } from "../lib/api";

export function SettingsPage() {
  const navigate = useNavigate();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me });
  return <section className="screen"><header className="settings-header"><button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={18} />Назад</button><h1>Настройки</h1></header>{me.isLoading ? <Skeleton className="h-64" /> : me.data && <div className="settings-grid"><Card><div className="setting-row"><Bot /><div><h2>Telegram</h2><p>Подключение Mini App</p></div><Pill tone={me.data.capabilities.telegram_configured ? "green" : "amber"}>{me.data.capabilities.telegram_configured ? "Подключён" : "Нужен токен"}</Pill></div></Card><Card><div className="setting-row"><Database /><div><h2>Источник OLX</h2><p>Мониторинг выдачи</p></div><Pill tone={me.data.capabilities.source_http_enabled ? "green" : "amber"}>{me.data.capabilities.source_http_enabled ? "Включён" : "Выключен"}</Pill></div></Card><Card><div className="setting-row"><BrainCircuit /><div><h2>OpenRouter</h2><p>Планирование и оценка</p></div><Pill tone={me.data.capabilities.openrouter_configured ? "green" : "neutral"}>{me.data.capabilities.openrouter_configured ? "Подключён" : "Не настроен"}</Pill></div></Card><Card><div className="setting-row"><ShieldCheck /><div><h2>Приложение</h2><p>Часовой пояс {me.data.timezone}</p></div><Pill tone="green">Готово</Pill></div></Card></div>}</section>;
}

