import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CirclePause, ExternalLink, Play, Plus, Radar, X } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { t } from "../i18n";
import { api, ApiError } from "../lib/api";
import type { Draft, Search } from "../types";
import { Button, Card, Pill, Skeleton } from "../components/ui";

const importSchema = z.object({ url: z.url("Вставьте корректную ссылку") });
type ImportForm = z.infer<typeof importSchema>;

export function SearchesPage() {
  const queryClient = useQueryClient();
  const [showImport, setShowImport] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const searches = useQuery({ queryKey: ["searches"], queryFn: api.searches });
  const form = useForm<ImportForm>({ resolver: zodResolver(importSchema) });
  const importer = useMutation({
    mutationFn: ({ url }: ImportForm) => api.importSearch(url),
    onSuccess: (value) => setDraft(value),
  });
  const creator = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("draft missing");
      const name = (document.querySelector<HTMLInputElement>("#draft-name")?.value ?? draft.name).trim();
      const max = document.querySelector<HTMLInputElement>("#draft-price")?.value;
      const required = document.querySelector<HTMLInputElement>("#draft-required")?.value ?? "";
      const parsed = { ...draft.parsed_spec, price_to: max || null };
      const rules = {
        required_groups: required.trim() ? [{ values: required.split(",").map((x) => x.trim()), scope: "both" }] : [],
        excluded: [],
      };
      const saved = await api.patchDraft(draft.id, { name, parsed_spec: parsed, rules });
      return api.createSearch(saved.id);
    },
    onSuccess: () => {
      setDraft(null);
      setShowImport(false);
      void queryClient.invalidateQueries({ queryKey: ["searches"] });
    },
  });

  return (
    <section>
      <header className="page-header">
        <div><span className="eyebrow">Мониторинг</span><h1>{t.searches}</h1></div>
        <Button onClick={() => setShowImport(true)}><Plus size={18} />{t.addSearch}</Button>
      </header>
      {notice && <div className="status-banner">{notice}<button onClick={() => setNotice(null)}><X size={16} /></button></div>}
      {searches.isLoading && <div className="stack"><Skeleton className="h-36" /><Skeleton className="h-36" /></div>}
      {searches.isError && <ErrorBlock error={searches.error} />}
      {searches.data?.length === 0 && <EmptySearches onAdd={() => setShowImport(true)} />}
      <div className="search-grid">
        {searches.data?.map((search) => (
          <SearchCard key={search.id} search={search} onNotice={setNotice} />
        ))}
      </div>
      {(showImport || draft) && (
        <div className="sheet-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setShowImport(false)}>
          <Card className="sheet">
            <div className="sheet__header"><div><span className="eyebrow">Новый поиск</span><h2>{draft ? "Настройте условия" : "Импорт ссылки"}</h2></div><button className="icon-button" onClick={() => { setDraft(null); setShowImport(false); }}><X /></button></div>
            {!draft ? (
              <form onSubmit={form.handleSubmit((value) => importer.mutate(value))} className="form-stack">
                <label>Ссылка на выдачу OLX<input autoFocus placeholder="https://www.olx.ua/uk/..." {...form.register("url")} /></label>
                {form.formState.errors.url && <p className="field-error">{form.formState.errors.url.message}</p>}
                {importer.error && <ErrorBlock error={importer.error} />}
                <Button type="submit" disabled={importer.isPending}>{importer.isPending ? "Распознаю…" : t.import}<ExternalLink size={17} /></Button>
              </form>
            ) : draft.page_type === "listing" ? (
              <div className="empty-inline"><p>Это ссылка отдельного объявления. Она не запускает мониторинг выдачи.</p></div>
            ) : (
              <div className="form-stack">
                <label>Название<input id="draft-name" defaultValue={draft.name} /></label>
                <div className="field-row"><label>Цена до, грн<input id="draft-price" inputMode="decimal" defaultValue={String(draft.parsed_spec.price_to ?? "")} /></label><label>Интервал<input disabled value="15 минут" /></label></div>
                <label>Нужные слова, через запятую<input id="draft-required" placeholder="steam deck, стим дек" /></label>
                <div className="callout"><Radar size={19} /><span>Первый запуск наполнит ленту без сообщений по старым объявлениям.</span></div>
                {creator.error && <ErrorBlock error={creator.error} />}
                <Button onClick={() => creator.mutate()} disabled={creator.isPending}>{creator.isPending ? "Сохраняю…" : "Сохранить и запустить"}<Play size={17} /></Button>
              </div>
            )}
          </Card>
        </div>
      )}
    </section>
  );
}

function SearchCard({ search, onNotice }: { search: Search; onNotice: (value: string) => void }) {
  const client = useQueryClient();
  const toggle = useMutation({ mutationFn: () => api.patchSearch(search, { active: !search.active }), onSuccess: () => void client.invalidateQueries({ queryKey: ["searches"] }) });
  const scan = useMutation({ mutationFn: () => api.scan(search.id), onSuccess: (run) => onNotice(run.status === "not_configured" ? t.sourceOff : `Проверка: ${run.status}`) });
  const price = search.specification.price_to ? `до ${search.specification.price_to} грн` : "любая цена";
  return <Card className="search-card">
    <div className="search-card__top"><div className="search-card__icon"><Radar size={21} /></div><button role="switch" aria-checked={search.active} className={`switch ${search.active ? "switch--on" : ""}`} onClick={() => toggle.mutate()}><span /></button></div>
    <div><h2>{search.name}</h2><p>{String(price)} · каждые {search.interval_minutes} мин</p></div>
    <div className="search-card__footer"><Pill tone={search.active ? "green" : "neutral"}>{search.active ? "Активен" : "На паузе"}</Pill><Button className="button--ghost" onClick={() => scan.mutate()} disabled={scan.isPending}>{scan.isPending ? <CirclePause size={17} /> : <Play size={17} />}Проверить</Button></div>
  </Card>;
}

function EmptySearches({ onAdd }: { onAdd: () => void }) { return <Card className="empty-state"><div className="empty-state__icon"><Radar /></div><h2>Пока нет поисков</h2><p>{t.emptySearches}</p><Button onClick={onAdd}><Plus size={18} />{t.addSearch}</Button></Card>; }
function ErrorBlock({ error }: { error: unknown }) { return <div className="error-block">{error instanceof ApiError ? error.message : "Не удалось загрузить данные."}</div>; }

