import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bookmark, ExternalLink, ImageOff, MapPin } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Button, Card, Pill, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import { telegram } from "../lib/telegram";
import { formatPrice } from "./FindingsPage";

export function FindingPage() {
  const { id = "" } = useParams();
  const client = useQueryClient();
  const finding = useQuery({ queryKey: ["finding", id], queryFn: () => api.finding(id) });
  const save = useMutation({ mutationFn: () => api.patchFinding(id, { saved: !finding.data?.saved }), onSuccess: () => { void client.invalidateQueries({ queryKey: ["finding", id] }); void client.invalidateQueries({ queryKey: ["findings"] }); } });
  if (finding.isLoading) return <div className="detail"><Skeleton className="h-96" /></div>;
  if (!finding.data) return <div className="error-block">Карточка не найдена или уже снята с OLX.</div>;
  const item = finding.data;
  return <section className="detail"><Link to="/findings" className="back-link"><ArrowLeft size={18} />К находкам</Link><Card className="detail-card"><div className="detail-card__media"><div className="image-placeholder"><ImageOff size={30} /><span>{item.cover_media_id ? "Изображение загружается" : "Фото не сохранено"}</span></div></div><div className="detail-card__content"><div className="detail-card__meta"><Pill tone="green">Подходит</Pill>{item.city && <span><MapPin size={15} />{item.city}</span>}</div><h1>{item.title}</h1><strong className="detail-price">{formatPrice(item)}</strong><p className="description">{item.description || "Полное описание не было получено."}</p><div className="detail-actions"><Button onClick={() => telegram.open(item.canonical_url)}><ExternalLink size={18} />Открыть в OLX</Button><Button className="button--secondary" onClick={() => save.mutate()}><Bookmark size={18} fill={item.saved ? "currentColor" : "none"} />{item.saved ? "Сохранено" : "Сохранить"}</Button></div></div></Card></section>;
}

