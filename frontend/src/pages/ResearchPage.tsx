import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight, CircleDollarSign, ExternalLink, Pause, Play, RotateCcw, SlidersHorizontal, StopCircle, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Button, Card, Pill, Skeleton } from "../components/ui";
import { api, ApiError } from "../lib/api";
import { telegram } from "../lib/telegram";
import type { Marketplace, ResearchEvent, ResearchItem, ResearchSession } from "../types";

const activeStatuses = new Set(["queued", "running"]);
const recommendationLabels: Record<string, string> = {
  shortlist: "Подходят",
  needs_details: "Нужно уточнить",
  reject: "Отклонены",
};

export function ResearchPage() {
  const { id } = useParams();
  const [draftQuery, setDraftQuery] = useState("");
  if (id) return <ResearchDetail />;
  return <ResearchHome draftQuery={draftQuery} onDraftQuery={setDraftQuery} />;
}

function ResearchHome({ draftQuery, onDraftQuery }: { draftQuery: string; onDraftQuery: (value: string) => void }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [marketplaces, setMarketplaces] = useState<string[]>(["olx"]);
  const [maxItems, setMaxItems] = useState(15);
  const [budget, setBudget] = useState("0.10");
  const [model, setModel] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const markets = useQuery({ queryKey: ["marketplaces"], queryFn: api.marketplaces });
  const sessions = useQuery({ queryKey: ["research-sessions"], queryFn: api.researchSessions, refetchInterval: (query) => query.state.data?.some((item) => activeStatuses.has(item.status)) ? 3000 : false });
  const created = useMutation({
    mutationFn: () => api.createResearch({ query: draftQuery.trim(), marketplaces, max_items: maxItems, budget_usd: budget, model: model.trim() || undefined }),
    onSuccess: (session) => {
      void client.invalidateQueries({ queryKey: ["research-sessions"] });
      navigate(`/research/${session.id}`);
    },
  });
  const toggleMarket = (market: Marketplace) => {
    if (!market.available) return;
    setMarketplaces((current) => current.includes(market.id) ? current.filter((id) => id !== market.id) : [...current, market.id]);
  };

  return <section className="screen research-home">
    <header className="screen-heading"><div><h1>ИИ-поиск</h1><p>Запрос и условия для поиска на площадках.</p></div></header>
    <Card className="composer">
      <label className="composer__query"><span className="sr-only">Что ищем</span><textarea value={draftQuery} onChange={(event) => onDraftQuery(event.target.value)} placeholder="Например: ThinkPad до 5 000 грн для Docker и Python" rows={3} /></label>
      <div className="source-chips" aria-label="Маркетплейсы">
        {markets.data?.marketplaces.map((market) => <button type="button" key={market.id} className={`source-chip ${marketplaces.includes(market.id) ? "source-chip--selected" : ""}`} disabled={!market.available} onClick={() => toggleMarket(market)}>{market.label}{!market.available && " · скоро"}</button>)}
      </div>
      <button type="button" className="composer__advanced" onClick={() => setAdvancedOpen((value) => !value)} aria-expanded={advancedOpen}><SlidersHorizontal size={16} />Параметры <ChevronRight size={16} /></button>
      {advancedOpen && <div className="composer__controls">
        <label>Объявлений<input type="number" min="1" max="100" value={maxItems} onChange={(event) => setMaxItems(Math.max(1, Number(event.target.value) || 1))} /></label>
        <label>Лимит, $<input inputMode="decimal" value={budget} onChange={(event) => setBudget(event.target.value)} /></label>
        <label className="composer__model">Модель OpenRouter <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Из .env, если пусто" /></label>
      </div>}
      {created.error && <div className="error-block">{created.error instanceof ApiError ? created.error.message : "Не удалось создать исследование."}</div>}
      <Button className="composer__submit" onClick={() => created.mutate()} disabled={draftQuery.trim().length < 3 || marketplaces.length === 0 || created.isPending}>{created.isPending ? "Создаю…" : "Искать"}<Play size={17} /></Button>
    </Card>
    <div className="section-heading"><h2>Исследования</h2><span>{sessions.data?.length ?? 0}</span></div>
    {sessions.isLoading ? <div className="stack"><Skeleton className="h-36" /><Skeleton className="h-36" /></div> : <div className="stack">{sessions.data?.length ? sessions.data.map((session) => <ResearchSessionCard key={session.id} session={session} />) : <EmptyResearches />}</div>}
  </section>;
}

function ResearchSessionCard({ session }: { session: ResearchSession }) {
  return <Link className="research-session-card" to={`/research/${session.id}`}>
    <div className="research-session-card__top"><Pill tone={statusTone(session.status)}>{statusLabel(session.status)}</Pill><time>{relativeDate(session.updated_at)}</time></div>
    <h2>{session.query}</h2>
    <div className="research-session-card__meta"><span>{progressLabel(session)}</span><span><CircleDollarSign size={14} />{formatUsd(session.actual_cost_usd)}</span><ChevronRight size={18} /></div>
  </Link>;
}

function EmptyResearches() {
  return <Card className="empty-inline"><strong>Исследований пока нет</strong><p>Сформулируйте запрос выше — история появится здесь.</p></Card>;
}

function ResearchDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [filter, setFilter] = useState<"shortlist" | "needs_details" | "rejected">("shortlist");
  const [selectedItem, setSelectedItem] = useState<ResearchItem | null>(null);
  const selected = useQuery({ queryKey: ["research", id], queryFn: () => api.research(id), refetchInterval: (query) => query.state.data && activeStatuses.has(query.state.data.status) ? 2500 : false });
  const items = useQuery({ queryKey: ["research-items", id], queryFn: () => api.researchItems(id), enabled: Boolean(id), refetchInterval: () => selected.data && activeStatuses.has(selected.data.status) ? 2500 : false });
  const events = useQuery({ queryKey: ["research-events", id], queryFn: () => api.researchEvents(id), enabled: Boolean(id), refetchInterval: () => selected.data && activeStatuses.has(selected.data.status) ? 2500 : false });
  const action = useMutation({ mutationFn: (value: "pause" | "resume" | "cancel") => api.researchAction(id, value), onSuccess: () => void Promise.all([client.invalidateQueries({ queryKey: ["research", id] }), client.invalidateQueries({ queryKey: ["research-sessions"] })]) });
  const visibleItems = useMemo(() => (items.data ?? []).filter((item) => {
    if (filter === "rejected") return item.recommendation === "reject" || item.status === "error";
    return item.recommendation === filter;
  }).sort((a, b) => (b.score ?? -1) - (a.score ?? -1)), [filter, items.data]);

  if (selected.isLoading || !selected.data) return <section className="screen"><Skeleton className="h-96" /></section>;
  const session = selected.data;
  return <section className="screen research-detail-screen">
    <button className="back-link" onClick={() => navigate(-1)}><ArrowLeft size={18} />Назад</button>
    <header className="detail-heading"><div><Pill tone={statusTone(session.status)}>{statusLabel(session.status)}</Pill><h1>{session.query}</h1></div></header>
    <ResearchStatistics session={session} />
    <ResearchActions session={session} pending={action.isPending} onAction={(value) => action.mutate(value)} />
    <ResearchPlan session={session} />
    <div className="section-heading"><h2>Результаты</h2><span>{items.data?.length ?? 0}</span></div>
    <div className="result-filters" role="tablist" aria-label="Фильтр результатов">
      <button className={filter === "shortlist" ? "active" : ""} onClick={() => setFilter("shortlist")}>Подходят</button>
      <button className={filter === "needs_details" ? "active" : ""} onClick={() => setFilter("needs_details")}>Уточнить</button>
      <button className={filter === "rejected" ? "active" : ""} onClick={() => setFilter("rejected")}>Отклонены</button>
    </div>
    <div className="stack research-results">{visibleItems.length ? visibleItems.map((item) => <ResearchListingCard key={item.id} item={item} onOpen={() => setSelectedItem(item)} />) : <Card className="empty-inline"><p>{filter === "shortlist" ? "Подходящих предложений пока нет." : "В этой категории пока пусто."}</p></Card>}</div>
    <ResearchEvents events={events.data ?? []} />
    {selectedItem && <ResearchItemDetail item={selectedItem} onClose={() => setSelectedItem(null)} />}
  </section>;
}

function ResearchStatistics({ session }: { session: ResearchSession }) {
  return <div className="research-statistics"><div><span>Найдено</span><strong>{session.collected_count}</strong></div><div><span>Оценено</span><strong>{session.processed_count}</strong></div><div><span>Расход</span><strong>{formatUsd(session.actual_cost_usd)}</strong></div></div>;
}

function ResearchActions({ session, pending, onAction }: { session: ResearchSession; pending: boolean; onAction: (value: "pause" | "resume" | "cancel") => void }) {
  if (session.status === "paused") return <div className="research-actions"><Button onClick={() => onAction("resume")} disabled={pending}><RotateCcw size={16} />Продолжить</Button><Button className="button--ghost" onClick={() => onAction("cancel")} disabled={pending}>Остановить</Button></div>;
  if (!activeStatuses.has(session.status)) return null;
  return <div className="research-actions"><Button className="button--secondary" onClick={() => onAction("pause")} disabled={pending}><Pause size={16} />Пауза</Button><Button className="button--ghost" onClick={() => onAction("cancel")} disabled={pending}><StopCircle size={16} />Остановить</Button></div>;
}

function ResearchPlan({ session }: { session: ResearchSession }) {
  const plan = session.search_plan;
  if (!plan.product_type) return null;
  return <details className="research-plan"><summary>Параметры поиска <ChevronRight size={17} /></summary><div className="research-plan__content">
    <p><strong>{[plan.brand, plan.model_family, plan.product_type].filter(Boolean).join(" · ")}</strong>{plan.max_price ? ` · до ${plan.max_price} ${plan.currency ?? ""}` : ""}</p>
    {plan.purpose && <p>{plan.purpose}</p>}
    {plan.hard_requirements?.length ? <PlanRow label="Обязательно" values={plan.hard_requirements} /> : null}
    {plan.soft_requirements?.length ? <PlanRow label="Условия" values={plan.soft_requirements} /> : null}
    <PlanRow label="OLX" values={plan.search_queries ?? []} />
    {plan.clarification_question && <p className="research-risk">Нужно уточнить: {plan.clarification_question}</p>}
  </div></details>;
}

function PlanRow({ label, values }: { label: string; values: string[] }) { return values.length ? <div className="plan-row"><span>{label}</span><div>{values.map((value) => <Pill key={value}>{value}</Pill>)}</div></div> : null; }

function ResearchListingCard({ item, onOpen }: { item: ResearchItem; onOpen: () => void }) {
  const analysis = item.analysis;
  return <button className="listing-card research-listing-card" onClick={onOpen}>
    <div className="listing-card__placeholder">{item.score ?? "—"}<small>/100</small></div>
    <div className="listing-card__body"><div className="listing-card__top"><Pill tone={item.recommendation === "shortlist" ? "green" : item.recommendation === "reject" ? "danger" : "amber"}>{recommendationLabels[item.recommendation ?? ""] ?? "В обработке"}</Pill><span>{item.confidence ? `${Math.round(Number(item.confidence) * 100)}%` : ""}</span></div><h3>{item.listing_snapshot.title ?? "Объявление"}</h3><strong>{priceText(item)}</strong><p>{analysis.summary ?? (item.status === "error" ? "Не удалось получить оценку." : "Ожидает оценки.")}</p>{analysis.risks?.[0] && <span className="risk-tag">{analysis.risks[0]}</span>}</div>
  </button>;
}

function ResearchItemDetail({ item, onClose }: { item: ResearchItem; onClose: () => void }) {
  const analysis = item.analysis;
  return <div className="detail-overlay" role="dialog" aria-modal="true" aria-label="Объявление" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><article className="listing-detail-sheet">
    <button className="icon-button listing-detail-sheet__close" onClick={onClose} aria-label="Закрыть"><X /></button>
    <div className="listing-detail-sheet__score">{item.score ?? "—"}<span>/100</span></div>
    <Pill tone={item.recommendation === "shortlist" ? "green" : "amber"}>{recommendationLabels[item.recommendation ?? ""] ?? "В обработке"}</Pill>
    <h2>{item.listing_snapshot.title ?? "Объявление"}</h2><strong className="detail-price">{priceText(item)}</strong>
    {analysis.summary && <p className="detail-summary">{analysis.summary}</p>}
    <AnalysisGroup title="Преимущества" values={analysis.strengths} /><AnalysisGroup title="Риски" values={analysis.risks} danger /><AnalysisGroup title="Нужно уточнить" values={analysis.missing_information} />
    {analysis.market_assessment && <section><h3>Обоснование оценки</h3><p>{analysis.market_assessment}</p></section>}
    {analysis.evidence?.length ? <section><h3>Подтверждения</h3>{analysis.evidence.map((item, index) => <blockquote key={`${item.quote}-${index}`}>{item.quote}</blockquote>)}</section> : null}
    <div className="detail-actions">{item.listing_snapshot.url && <Button onClick={() => telegram.open(item.listing_snapshot.url!)}><ExternalLink size={18} />Открыть на OLX</Button>}</div>
  </article></div>;
}

function AnalysisGroup({ title, values, danger = false }: { title: string; values?: string[]; danger?: boolean }) { return values?.length ? <section className={danger ? "analysis-group analysis-group--danger" : "analysis-group"}><h3>{title}</h3><ul>{values.map((value) => <li key={value}>{value}</li>)}</ul></section> : null; }

function ResearchEvents({ events }: { events: ResearchEvent[] }) { return <details className="research-events"><summary>Журнал работы <ChevronRight size={17} /></summary><div>{events.length ? events.map((event) => <p key={event.id}><time>{new Date(event.created_at).toLocaleString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</time>{event.message}</p>) : <p>Событий пока нет.</p>}</div></details>; }

function statusLabel(status: string) { return ({ queued: "В очереди", running: "Идёт поиск", completed: "Готово", paused: "Пауза", waiting_configuration: "Нужна настройка", needs_clarification: "Нужно уточнение", budget_exhausted: "Лимит исчерпан", failed: "Ошибка", cancelled: "Остановлено" } as Record<string, string>)[status] ?? status; }
function statusTone(status: string) { return ({ running: "green", completed: "green", failed: "danger", budget_exhausted: "amber", needs_clarification: "amber", paused: "amber" } as Record<string, string>)[status] ?? "neutral"; }
function progressLabel(session: ResearchSession) { return activeStatuses.has(session.status) ? `${session.processed_count}/${session.max_items} оценено` : `${session.processed_count} оценено`; }
function formatUsd(value: string) { return `$${Number(value).toFixed(4)}`; }
function priceText(item: ResearchItem) { return item.listing_snapshot.price ? `${Number(item.listing_snapshot.price).toLocaleString("ru-RU")} ${item.listing_snapshot.currency === "UAH" ? "грн" : item.listing_snapshot.currency ?? ""}` : "Цена не указана"; }
function relativeDate(value: string) { return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short" }).format(new Date(value)); }
