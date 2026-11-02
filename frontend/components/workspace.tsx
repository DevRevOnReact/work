"use client";
import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Building2, FileText, Search, Trophy } from "lucide-react";
import { useWorkspace } from "@/hooks/use-workspace";
import type { PageName } from "@/lib/types";
import { SearchesView } from "./searches-view";
import { CompaniesView } from "./companies-view";
import { ResumesView } from "./resumes-view";
import { TopVacanciesView } from "./top-vacancies-view";

const pages = [
  {key: "search" as PageName, label: "Поиски", icon: Search, description: "Работа, которая подходит вашему опыту."},
  {key: "top" as PageName, label: "Топ за 3 дня", icon: Trophy, description: "Вакансии за последние 72 часа с соответствием резюме от 60/100."},
  {key: "companies" as PageName, label: "Компании", icon: Building2, description: "Новые работодатели и люди, с которыми можно познакомиться."},
  {key: "resumes" as PageName, label: "Резюме и профиль", icon: FileText, description: "Ваш опыт и условия — основа каждого поиска."},
];
function Workspace({initialPage}: {initialPage: PageName}) {
  const state = useWorkspace(initialPage);
  const currentPage = pages.find(page => page.key === state.page)!;
  return <div className="shell"><aside className="sidebar"><div className="brand">jobpilot<span className="brand-dot">.</span></div>
    <div className="nav-label">ВАШ СЛЕДУЮЩИЙ ШАГ</div><nav aria-label="Основная навигация">{pages.map(page =>
      <button key={page.key} className={state.page === page.key ? "active" : ""} aria-current={state.page === page.key ? "page" : undefined}
        onClick={() => state.navigate(page.key)}><page.icon size={18}/><span>{page.label}</span></button>)}</nav>
    <div className="sidebar-bottom"><span className="local-indicator"/> Локальная версия</div></aside>
    <div className="main-shell"><main><div className="page-heading"><div><div className="eyebrow">JOBPILOT / ВАША КАРЬЕРА</div>
      <h1>{currentPage.label}</h1><p>{currentPage.description}</p></div></div>
      {state.message && <div className="notice" role="status">{state.message}</div>}
      {(state.error || state.searches.error || state.resumes.error) && <div className="notice error" role="alert">
        {state.error || state.searches.error?.message || state.resumes.error?.message}</div>}
      {state.page === "search" && <SearchesView state={state}/>}
      {state.page === "top" && <TopVacanciesView state={state}/>}
      {state.page === "companies" && <CompaniesView state={state}/>}
      {state.page === "resumes" && <ResumesView state={state}/>}
    </main></div></div>;
}
export function WorkspaceApp({initialPage = "search"}: {initialPage?: PageName}) {
  const [client] = useState(() => new QueryClient({defaultOptions: {queries: {refetchOnWindowFocus: false}}}));
  return <QueryClientProvider client={client}><Workspace initialPage={initialPage}/></QueryClientProvider>;
}
