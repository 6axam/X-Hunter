import { Binoculars, ListFilter, Settings2, Sparkles } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";
import { t } from "../i18n";

const items = [
  { to: "/research", label: "ИИ-поиск", icon: Sparkles },
  { to: "/searches", label: t.searches, icon: ListFilter },
  { to: "/findings", label: t.findings, icon: Binoculars },
  { to: "/settings", label: t.settings, icon: Settings2 },
];

export function Layout() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand__mark">OH</span>
          <span>{t.appName}</span>
        </div>
        <nav className="nav" aria-label="Основная навигация">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} className={({ isActive }) => cls(isActive)}>
              <Icon size={20} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
      <main className="content"><Outlet /></main>
      <nav className="bottom-nav" aria-label="Основная навигация">
        {items.map(({ to, label, icon: Icon }) => (
          <NavLink key={to} to={to} className={({ isActive }) => cls(isActive)}>
            <Icon size={21} strokeWidth={1.8} />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

function cls(active: boolean) {
  return active ? "nav__item nav__item--active" : "nav__item";
}

