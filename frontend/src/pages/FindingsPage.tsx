import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, ExternalLink, ImageOff, MapPin, SearchX } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Button, Card, Pill, Skeleton } from "../components/ui";
import { t } from "../i18n";
import { api } from "../lib/api";
import type { Finding } from "../types";

export function FindingsPage() {
  const [saved, setSaved] = useState(false);
  const findings = useQuery({ queryKey: ["findings", saved], queryFn: () => api.findings(saved) });
  return <section>
    <header className="page-header"><div><span className="eyebrow">Ваш архив</span><h1>{t.findings}</h1></div><div className="segmented"><button className={!saved ? "active" : ""} onClick={() => setSaved(false)}>{t.all}</button><button className={saved ? "active" : ""} onClick={() => setSaved(true)}>{t.saved}</button></div></header>
    {findings.isLoading && <div className="findings-grid"><Skeleton className="h-80" /><Skeleton className="h-80" /></div>}
    {findings.isError && <div className="error-block">Не удалось загрузить находки.</div>}
    {findings.data?.length === 0 && <Card className="empty-state"><div className="empty-state__icon"><SearchX /></div><h2>{saved ? "Нет сохранённых" : "Лента пока пуста"}</h2><p>{saved ? "Сохраняйте интересные карточки закладкой." : t.emptyFindings}</p></Card>}
    <div className="findings-grid">{findings.data?.map((finding) => <FindingCard key={finding.id} finding={finding} />)}</div>
  </section>;
}

function FindingCard({ finding }: { finding: Finding }) {
  const client = useQueryClient();
  const save = useMutation({ mutationFn: () => api.patchFinding(finding.id, { saved: !finding.saved }), onMutate: async () => { await client.cancelQueries({ queryKey: ["findings"] }); const previous = client.getQueriesData<Finding[]>({ queryKey: ["findings"] }); client.setQueriesData<Finding[]>({ queryKey: ["findings"] }, (items) => items?.map((item) => item.id === finding.id ? { ...item, saved: !item.saved } : item)); return previous; }, onError: (_error, _value, context) => context?.forEach(([key, data]) => client.setQueryData(key, data)), onSettled: () => void client.invalidateQueries({ queryKey: ["findings"] }) });
  return <Card className="finding-card">
    <Link to={`/findings/${finding.id}`} className="finding-card__image" aria-label={`Открыть ${finding.title}`}>
      {finding.cover_media_id ? <img src={`/api/v1/media/${finding.cover_media_id}`} alt="" /> : <div className="image-placeholder"><ImageOff size={26} /><span>Фото ожидает загрузки</span></div>}
      <Pill tone="green">{valueLabel(finding.ai_value_label) ?? "Подходит"}</Pill>
    </Link>
    <div className="finding-card__body"><div className="finding-card__heading"><Link to={`/findings/${finding.id}`}><h2>{finding.title}</h2></Link><button className={`bookmark ${finding.saved ? "bookmark--saved" : ""}`} aria-label={finding.saved ? "Убрать из сохранённых" : "Сохранить"} onClick={() => save.mutate()}><Bookmark size={21} fill={finding.saved ? "currentColor" : "none"} /></button></div><strong>{formatPrice(finding)}</strong>{finding.city && <p className="location"><MapPin size={15} />{finding.city}</p>}<p className="reason">{finding.ai_summary || finding.searches.map((item) => item.name).join(" · ")}</p><Button className="button--secondary" onClick={() => window.open(finding.canonical_url, "_blank", "noopener,noreferrer")}><ExternalLink size={17} />Открыть в OLX</Button></div>
  </Card>;
}

export function formatPrice(finding: Finding) { return finding.price ? `${Number(finding.price).toLocaleString("ru-RU")} ${finding.currency === "UAH" ? "грн" : finding.currency ?? ""}` : "Цена не указана"; }
function valueLabel(value: string | null) { return ({ very_interesting: "Очень выгодно", interesting: "Интересно", ordinary: "Обычно", doubtful: "Сомнительно", unavailable: "Нет оценки" } as Record<string, string>)[value ?? ""] ?? null; }
