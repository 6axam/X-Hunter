import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Pause, Play, RotateCcw, Sparkles, StopCircle } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Card, Pill, Skeleton, Button } from "../components/ui";
import { api, ApiError } from "../lib/api";
import type { ResearchItem, ResearchSession } from "../types";

export function ResearchPage() {
  const client = useQueryClient();
  const navigate = useNavigate();
  const { id } = useParams();
  const [query, setQuery] = useState("");
  const [model, setModel] = useState("");
  const markets = useQuery({ queryKey: ["marketplaces"], queryFn: api.marketplaces });
  const sessions = useQuery({ queryKey: ["research-sessions"], queryFn: api.researchSessions, refetchInterval: 3000 });
  const created = useMutation({
    mutationFn: () => api.createResearch({ query, marketplaces: ["olx"], max_items: 15, budget_usd: "0.10", model: model || undefined }),
    onSuccess: (session) => { void client.invalidateQueries({ queryKey: ["research-sessions"] }); navigate(`/research/${session.id}`); },
  });
  const selected = useQuery({ queryKey: ["research", id], queryFn: () => api.research(id!), enabled: Boolean(id), refetchInterval: 2500 });
  const items = useQuery({ queryKey: ["research-items", id], queryFn: () => api.researchItems(id!), enabled: Boolean(id), refetchInterval: 2500 });
  const events = useQuery({ queryKey: ["research-events", id], queryFn: () => api.researchEvents(id!), enabled: Boolean(id), refetchInterval: 2500 });
  const action = useMutation({ mutationFn: (value: "pause" | "resume" | "cancel") => api.researchAction(id!, value), onSuccess: () => void client.invalidateQueries({ queryKey: ["research"] }) });

  if (id) return <ResearchDetail session={selected.data} items={items.data ?? []} events={events.data ?? []} loading={selected.isLoading} onAction={(value) => action.mutate(value)} />;
  return <section>
    <header className="page-header"><div><span className="eyebrow">Автономный агент</span><h1>ИИ-исследование</h1></div><Sparkles color="var(--accent)" /></header>
    <Card className="research-form"><h2>Что ищем?</h2><p>Агент последовательно проверит объявления, сохранит причины и составит рейтинг.</p><textarea value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Например: электровелосипед до 30 000 грн, для города, без ремонта" /><label>Model ID OpenRouter (необязательно, если задан в .env)<input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Выберите существующий ID из каталога" /></label><div className="market-list">{markets.data?.marketplaces.map((market) => <span key={market.id} className={market.available ? "market market--active" : "market"}>{market.label}{!market.available && " · скоро"}</span>)}</div>{created.error && <div className="error-block">{created.error instanceof ApiError ? created.error.message : "Не удалось создать исследование."}</div>}<Button onClick={() => created.mutate()} disabled={query.trim().length < 3 || created.isPending}>{created.isPending ? "Создаю…" : "Начать исследование"}<Play size={17} /></Button></Card>
    <h2 className="section-title">История</h2>{sessions.isLoading ? <Skeleton className="h-36" /> : <div className="stack">{sessions.data?.length ? sessions.data.map((session) => <SessionCard key={session.id} session={session} />) : <Card className="empty-inline"><p>Пока нет ИИ-исследований.</p></Card>}</div>}
  </section>;
}

function SessionCard({ session }: { session: ResearchSession }) { return <Link to={`/research/${session.id}`}><Card className="research-session"><div><Pill>{statusLabel(session.status)}</Pill><h2>{session.query}</h2><p>{session.processed_count}/{session.max_items} объявлений · {session.actual_cost_usd}$ из {session.budget_usd}$</p></div><strong>{session.accepted_count} лидеров</strong></Card></Link>; }

function ResearchDetail({ session, items, events, loading, onAction }: { session?: ResearchSession; items: ResearchItem[]; events: { id: string; message: string; created_at: string }[]; loading: boolean; onAction: (value: "pause" | "resume" | "cancel") => void }) {
  if (loading || !session) return <Skeleton className="h-96" />;
  const active = session.status === "running" || session.status === "queued";
  return <section className="research-detail"><Link className="back-link" to="/research">← К исследованиям</Link><header className="page-header"><div><span className="eyebrow">{statusLabel(session.status)}</span><h1>{session.query}</h1></div></header><Card className="research-summary"><div><b>Этап:</b> {session.stage}</div><div><b>Прогресс:</b> {session.processed_count}/{session.max_items}</div><div><b>Расход:</b> {session.actual_cost_usd}$ факт · {session.estimated_cost_usd}$ оценка</div><div className="research-actions">{active && <Button className="button--secondary" onClick={() => onAction("pause")}><Pause size={16} />Пауза</Button>}{session.status === "paused" && <Button onClick={() => onAction("resume")}><RotateCcw size={16} />Продолжить</Button>}{active && <Button className="button--ghost" onClick={() => onAction("cancel")}><StopCircle size={16} />Стоп</Button>}</div></Card><h2 className="section-title">Рейтинг</h2><div className="stack">{items.length ? items.map((item) => <ResearchItemCard key={item.id} item={item} />) : <Card className="empty-inline"><p>Лоты появятся здесь по мере последовательной оценки.</p></Card>}</div><h2 className="section-title">Ход работы</h2><Card className="research-events">{events.map((event) => <p key={event.id}>{event.message}</p>)}</Card></section>;
}

function ResearchItemCard({ item }: { item: ResearchItem }) { const a = item.analysis; return <Card className="research-item"><div className="research-item__top"><Pill>{item.score ?? "…"}/100</Pill><span>{item.recommendation === "shortlist" ? "В лидерах" : item.recommendation === "reject" ? "Отклонено" : "Нужно уточнить"}</span></div><h2>{item.listing_snapshot.title ?? "Объявление"}</h2><strong>{item.listing_snapshot.price} {item.listing_snapshot.currency}</strong><p>{a.summary ?? (item.status === "error" ? "Не удалось получить оценку модели." : "Ожидает оценки.")}</p>{a.risks?.length ? <p className="research-risk">Риски: {a.risks.join("; ")}</p> : null}<div className="research-item__footer"><span>Уверенность: {item.confidence ?? "—"}</span>{item.listing_snapshot.url && <a href={item.listing_snapshot.url} target="_blank" rel="noreferrer">OLX <ExternalLink size={14} /></a>}</div></Card>; }
function statusLabel(status: string) { return ({ queued: "В очереди", running: "Исследуем", completed: "Готово", paused: "Пауза", waiting_configuration: "Нужна настройка", budget_exhausted: "Лимит исчерпан", failed: "Ошибка", cancelled: "Остановлено" } as Record<string, string>)[status] ?? status; }
