import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SearchX } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ListingCard } from "../components/ListingCard";
import { Card, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import type { Finding } from "../types";

export function FindingsPage() {
  const [saved, setSaved] = useState(false);
  const findings = useQuery({ queryKey: ["findings", saved], queryFn: () => api.findings(saved) });
  return <section className="screen">
    <header className="page-header"><h1>Находки</h1><div className="segmented" aria-label="Фильтр находок"><button className={!saved ? "active" : ""} onClick={() => setSaved(false)}>Все</button><button className={saved ? "active" : ""} onClick={() => setSaved(true)}>Сохранённые</button></div></header>
    {findings.isLoading && <div className="stack"><Skeleton className="h-36" /><Skeleton className="h-36" /></div>}
    {findings.isError && <div className="error-block">Не удалось загрузить находки.</div>}
    {findings.data?.length === 0 && <Card className="empty-state"><div className="empty-state__icon"><SearchX /></div><h2>{saved ? "Нет сохранённых" : "Лента пока пуста"}</h2><p>{saved ? "Сохраняйте интересные объявления закладкой." : "Подходящие объявления из мониторинга появятся здесь."}</p></Card>}
    <div className="findings-grid">{findings.data?.map((finding) => <FindingCard key={finding.id} finding={finding} />)}</div>
  </section>;
}

function FindingCard({ finding }: { finding: Finding }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const save = useMutation({
    mutationFn: () => api.patchFinding(finding.id, { saved: !finding.saved }),
    onMutate: async () => {
      await client.cancelQueries({ queryKey: ["findings"] });
      const previous = client.getQueriesData<Finding[]>({ queryKey: ["findings"] });
      client.setQueriesData<Finding[]>({ queryKey: ["findings"] }, (items) => items?.map((item) => item.id === finding.id ? { ...item, saved: !item.saved } : item));
      return previous;
    },
    onError: (_error, _value, context) => context?.forEach(([key, data]) => client.setQueryData(key, data)),
    onSettled: () => void client.invalidateQueries({ queryKey: ["findings"] }),
  });
  return <ListingCard title={finding.title} price={formatPrice(finding)} label={valueLabel(finding.ai_value_label) ?? "Подходит"} tone={valueTone(finding.ai_value_label)} mediaId={finding.cover_media_id} city={finding.city} summary={finding.ai_summary || finding.searches.map((item) => item.name).join(" · ")} saved={finding.saved} onSave={() => save.mutate()} onOpen={() => navigate(`/findings/${finding.id}`)} />;
}

export function formatPrice(finding: Pick<Finding, "price" | "currency">) { return finding.price ? `${Number(finding.price).toLocaleString("ru-RU")} ${finding.currency === "UAH" ? "грн" : finding.currency ?? ""}` : "Цена не указана"; }
function valueLabel(value: string | null) { return ({ very_interesting: "Очень выгодно", interesting: "Интересно", ordinary: "Обычно", doubtful: "Сомнительно", unavailable: "Нет оценки" } as Record<string, string>)[value ?? ""] ?? null; }
function valueTone(value: string | null): "green" | "amber" | "danger" { return value === "very_interesting" || value === "interesting" ? "green" : value === "doubtful" ? "danger" : "amber"; }
