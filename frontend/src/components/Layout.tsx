import { useCallback, useEffect } from "react";
import { Bookmark, Radar, Settings, Sparkles } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { telegram } from "../lib/telegram";

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const isDetail = /^\/(research|findings)\/[^/]+/.test(location.pathname);
  const usesBackNavigation = isDetail || location.pathname === "/settings";
  const fallbackRoute = location.pathname.startsWith("/findings/") ? "/findings" : "/research";
  const goBack = useCallback(() => {
    if (window.history.length > 1) navigate(-1);
    else navigate(fallbackRoute, { replace: true });
  }, [fallbackRoute, navigate]);

  useEffect(() => {
    if (!usesBackNavigation) {
      telegram.hideBackButton();
      return;
    }
    return telegram.showBackButton(goBack);
  }, [goBack, usesBackNavigation]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink to="/research" className="brand" aria-label="X-Hunter: ИИ-поиск"><span className="brand__mark">X</span><span>X-Hunter</span></NavLink>
        <Navigation className="nav nav--desktop" />
        <NavLink to="/settings" className="sidebar-settings"><Settings size={18} />Настройки</NavLink>
      </aside>
      <main className={`content${isDetail ? " content--detail" : ""}`}>
        {!isDetail && <header className="app-bar"><NavLink to="/research" className="app-bar__brand">X-Hunter</NavLink>{location.pathname !== "/settings" && <NavLink to="/settings" className="app-bar__settings" aria-label="Настройки"><Settings size={20} /></NavLink>}</header>}
        <Outlet />
      </main>
      {!isDetail && <Navigation className="bottom-nav" />}
    </div>
  );
}

function Navigation({ className }: { className: string }) {
  return <nav className={className} aria-label="Основная навигация">
    {items.map(({ to, label, icon: Icon }) => <NavLink key={to} to={to} className={({ isActive }) => isActive ? "nav__item nav__item--active" : "nav__item"}>
      <Icon size={20} strokeWidth={1.9} /><span>{label}</span>
    </NavLink>)}
  </nav>;
}

const items = [
  { to: "/research", label: "ИИ-поиск", icon: Sparkles },
  { to: "/searches", label: "Мониторинг", icon: Radar },
  { to: "/findings", label: "Находки", icon: Bookmark },
];
