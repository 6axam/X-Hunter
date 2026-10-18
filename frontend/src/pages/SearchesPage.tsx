import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CirclePause, ExternalLink, Play, Plus, Radar, X } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
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
  const [editing, setEditing] = useState<Search | null>(null);
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
      const excluded = document.querySelector<HTMLInputElement>("#draft-excluded")?.value ?? "";
      const aiModel = document.querySelector<HTMLInputElement>("#draft-ai-model")?.value.trim() ?? "";
      const aiInstruction = document.querySelector<HTMLTextAreaElement>("#draft-ai-instruction")?.value.trim() ?? "";
      const parsed = { ...draft.parsed_spec, price_to: max || null };
      const rules = {
        required_groups: required.trim() ? [{ values: required.split(",").map((x) => x.trim()), scope: "both" }] : [],
        excluded: excluded.trim() ? excluded.split(",").map((value) => ({ value: value.trim(), scope: "both" })) : [],
      };
      const ai_settings = { enabled: Boolean(aiModel && aiInstruction), model: aiModel || null, instruction: aiInstruction, daily_budget_usd: "0.10" };
      const saved = await api.patchDraft(draft.id, { name, parsed_spec: parsed, rules, ai_settings });
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
          <SearchCard key={search.id} search={search} onNotice={setNotice} onOpen={() => setEditing(search)} />
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
                <label>Запрещённые слова, через запятую<input id="draft-excluded" placeholder="чехол, запчасти" /></label>
                <details className="form-details"><summary>ИИ-анализ через OpenRouter</summary><div className="form-stack"><label>Model ID<input id="draft-ai-model" placeholder="provider/model" /></label><label>Что считать выгодным<textarea id="draft-ai-instruction" placeholder="Например: полный комплект, без ремонта, хорошая цена для моего бюджета" /></label><p>ИИ включится только если заданы модель и инструкция. Лимит поиска — $0.10 в сутки.</p></div></details>
                <div className="callout"><Radar size={19} /><span>Первый запуск наполнит ленту без сообщений по старым объявлениям.</span></div>
                {creator.error && <ErrorBlock error={creator.error} />}
                <Button onClick={() => creator.mutate()} disabled={creator.isPending}>{creator.isPending ? "Сохраняю…" : "Сохранить и запустить"}<Play size={17} /></Button>
              </div>
            )}
          </Card>
        </div>
      )}
      {editing && <SearchSettingsSheet search={editing} onClose={() => setEditing(null)} />}
    </section>
  );
}

function SearchCard({ search, onNotice, onOpen }: { search: Search; onNotice: (value: string) => void; onOpen: () => void }) {
  const client = useQueryClient();
  const toggle = useMutation({ mutationFn: () => api.patchSearch(search, { active: !search.active }), onSuccess: () => void client.invalidateQueries({ queryKey: ["searches"] }) });
  const scan = useMutation({ mutationFn: () => api.scan(search.id), onSuccess: (run) => onNotice(run.status === "not_configured" ? t.sourceOff : `Проверка: ${run.status}`) });
  const discounts = useMutation({ mutationFn: () => api.patchSearch(search, { event_settings: { ...search.event_settings, price_drop_enabled: !search.event_settings.price_drop_enabled, price_drop_percent: search.event_settings.price_drop_percent ?? 5 } }), onSuccess: () => void client.invalidateQueries({ queryKey: ["searches"] }) });
  const price = search.specification.price_to ? `до ${search.specification.price_to} грн` : "любая цена";
  const openFromKeyboard = (event: KeyboardEvent<HTMLElement>) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } };
  return <Card className="search-card search-card--interactive" role="link" tabIndex={0} onClick={onOpen} onKeyDown={openFromKeyboard}>
    <div className="search-card__top"><div className="search-card__icon"><Radar size={21} /></div><button role="switch" aria-checked={search.active} className={`switch ${search.active ? "switch--on" : ""}`} onClick={(event) => { event.stopPropagation(); toggle.mutate(); }}><span /></button></div>
    <div><h2>{search.name}</h2><p>{String(price)} · каждые {search.interval_minutes} мин</p></div>
    <div className="search-card__footer"><button className="text-action" onClick={(event) => { event.stopPropagation(); discounts.mutate(); }}>Скидки {search.event_settings.price_drop_enabled ? "вкл" : "выкл"}</button><Button className="button--ghost" onClick={(event) => { event.stopPropagation(); scan.mutate(); }} disabled={scan.isPending}>{scan.isPending ? <CirclePause size={17} /> : <Play size={17} />}Проверить</Button></div>
  </Card>;
}

function SearchSettingsSheet({ search, onClose }: { search: Search; onClose: () => void }) {
  const client = useQueryClient();
  const [name, setName] = useState(search.name);
  const [interval, setInterval] = useState(search.interval_minutes);
  const saved = useMutation({ mutationFn: () => api.patchSearch(search, { name: name.trim() || search.name, interval_minutes: interval }), onSuccess: () => { void client.invalidateQueries({ queryKey: ["searches"] }); onClose(); } });
  return <div className="sheet-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}><Card className="sheet"><div className="sheet__header"><div><span className="eyebrow">Мониторинг</span><h2>Настройки поиска</h2></div><button className="icon-button" onClick={onClose}><X /></button></div><div className="form-stack"><label>Название<input value={name} onChange={(event) => setName(event.target.value)} /></label><label>Период проверки, минут<input type="number" min="10" max="10080" value={interval} onChange={(event) => setInterval(Math.max(10, Number(event.target.value) || 10))} /></label><p className="form-note">Фильтры и URL сохранены из импорта. Для другой OLX-выдачи создайте новый мониторинг.</p>{saved.error && <ErrorBlock error={saved.error} />}<Button onClick={() => saved.mutate()} disabled={saved.isPending}>{saved.isPending ? "Сохраняю…" : "Сохранить"}</Button></div></Card></div>;
}

function EmptySearches({ onAdd }: { onAdd: () => void }) { return <Card className="empty-state"><div className="empty-state__icon"><Radar /></div><h2>Пока нет поисков</h2><p>{t.emptySearches}</p><Button onClick={onAdd}><Plus size={18} />{t.addSearch}</Button></Card>; }
function ErrorBlock({ error }: { error: unknown }) { return <div className="error-block">{error instanceof ApiError ? error.message : "Не удалось загрузить данные."}</div>; }
