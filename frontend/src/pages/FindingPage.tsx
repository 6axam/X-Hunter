import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bookmark, ExternalLink, ImageOff, MapPin } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Button, Card, Pill, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { telegram } from "../lib/telegram";
import { formatPrice } from "./FindingsPage";

const attributeLabels: Record<string, string> = {
  seller_type: "Продавец",
  seller_name: "Продавец",
  seller_rating: "Рейтинг продавца",
  seller_member_since: "На OLX",
  seller_last_seen: "Последняя активность",
};

function formatDate(value: string | null) {
  return value ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium" }).format(new Date(value)) : null;
}

export function FindingPage() {
  const { id = "" } = useParams();
  const client = useQueryClient();
  const finding = useQuery({ queryKey: ["finding", id], queryFn: () => api.finding(id) });
  const save = useMutation({ mutationFn: () => api.patchFinding(id, { saved: !finding.data?.saved }), onSuccess: () => { void client.invalidateQueries({ queryKey: ["finding", id] }); void client.invalidateQueries({ queryKey: ["findings"] }); } });
  if (finding.isLoading) return <div className="detail"><Skeleton className="h-96" /></div>;
  if (!finding.data) return <div className="error-block">Карточка не найдена или уже снята с OLX.</div>;
  const item = finding.data;
  const readyMedia = (item.media ?? []).filter((media) => media.status === "ready");
  const properties = [
    ...(item.condition ? [["Состояние", item.condition] as const] : []),
    ...Object.entries(item.attributes).filter(([key]) => key !== "seller_profile_url"),
  ];
  return <section className="detail"><Link to="/findings" className="back-link"><ArrowLeft size={18} />К находкам</Link><Card className="detail-card"><div className="detail-card__media">{readyMedia.length ? <div className="media-gallery">{readyMedia.map((media) => <img key={media.id} src={`/api/v1/media/${media.id}`} alt={item.title} />)}</div> : <div className="image-placeholder"><ImageOff size={30} /><span>{item.cover_media_id ? "Изображение загружается" : "Фото не сохранено"}</span></div>}</div><div className="detail-card__content"><div className="detail-card__meta"><Pill tone="green">Подходит</Pill>{item.city && <span><MapPin size={15} />{item.city}</span>}</div><h1>{item.title}</h1><strong className="detail-price">{formatPrice(item)}</strong>{item.published_at && <p className="detail-date">Опубликовано: {formatDate(item.published_at)}</p>}{properties.length > 0 && <dl className="finding-properties">{properties.map(([key, value]) => <div key={key}><dt>{attributeLabels[key] ?? key}</dt><dd>{value}</dd></div>)}</dl>}<p className="description">{item.description || "Полное описание не было получено."}</p><div className="detail-actions"><Button onClick={() => telegram.open(item.canonical_url)}><ExternalLink size={18} />Открыть в OLX</Button><Button className="button--secondary" onClick={() => save.mutate()}><Bookmark size={18} fill={item.saved ? "currentColor" : "none"} />{item.saved ? "Сохранено" : "Сохранить"}</Button></div></div></Card></section>;
}
