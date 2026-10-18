import { Bookmark, ImageOff, MapPin } from "lucide-react";
import type { KeyboardEvent, ReactNode } from "react";

type ListingCardProps = {
  title: string;
  price: string;
  label: string;
  tone?: "neutral" | "green" | "amber" | "danger";
  summary?: string | null;
  risk?: string;
  city?: string | null;
  mediaId?: string | null;
  score?: number | null;
  saved?: boolean;
  onOpen: () => void;
  onSave?: () => void;
  action?: ReactNode;
};

export function ListingCard({ title, price, label, tone = "neutral", summary, risk, city, mediaId, score, saved, onOpen, onSave, action }: ListingCardProps) {
  const openFromKeyboard = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onOpen();
    }
  };
  return <article className="listing-card" role="link" tabIndex={0} onClick={onOpen} onKeyDown={openFromKeyboard}>
    <div className={`listing-card__media${score !== undefined ? " listing-card__media--score" : ""}`}>
      {mediaId ? <img src={`/api/v1/media/${mediaId}`} alt="" /> : score !== undefined ? <><strong>{score ?? "—"}</strong><small>/100</small></> : <><ImageOff size={20} /><small>Нет фото</small></>}
    </div>
    <div className="listing-card__body">
      <div className="listing-card__top"><span className={`pill pill--${tone}`}>{label}</span>{onSave && <button className={`bookmark ${saved ? "bookmark--saved" : ""}`} aria-label={saved ? "Убрать из сохранённых" : "Сохранить"} onClick={(event) => { event.stopPropagation(); onSave(); }}><Bookmark size={18} fill={saved ? "currentColor" : "none"} /></button>}</div>
      <h3>{title}</h3><strong>{price}</strong>
      {city && <span className="listing-card__location"><MapPin size={13} />{city}</span>}
      {summary && <p>{summary}</p>}{risk && <span className="risk-tag">{risk}</span>}{action}
    </div>
  </article>;
}
