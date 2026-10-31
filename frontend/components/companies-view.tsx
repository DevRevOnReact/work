import { api } from "@/lib/api";
import { countries, countryLabels, useCompanies } from "@/hooks/use-companies";
import type { WorkspaceState } from "@/hooks/use-workspace";
import { CompanyContacts } from "./company-contacts";

const signalLabels: Record<string, string> = {
  resume_fit: "Топ-100 · подборка по вашему стеку",
  referrals: "Программа рекомендаций", regional_presence: "Присутствие в регионе", cis_profile: "Публичный профиль из региона",
};
export function CompaniesView({state: s}: {state: WorkspaceState}) {
  const c = useCompanies(s.action);
  return <>
    <section className="panel padded"><div className="panel-heading"><div><h2>Откройте новых работодателей</h2>
      <p>Компании с публичными сигналами связи с СНГ и программы рекомендаций сотрудников.</p></div>
      <button className="button" disabled={s.pending} onClick={() => void s.action(() => api("companies/starter", "POST"), "Каталог обновлён")}>Добавить проверенные компании</button></div>
      <form className="discovery-form" onSubmit={e => {e.preventDefault(); void c.discover();}}>
        <label>Регион публичных профилей<select value={c.country} onChange={e => c.setCountry(e.target.value)}>
          {countries.map(country => <option key={country} value={country}>{countryLabels[country]}</option>)}</select></label>
        <button className="button primary" disabled={s.pending}>{s.pending ? "Ищем…" : `Найти новые компании${c.page > 1 ? ` · страница ${c.page}` : ""}`}</button>
      </form>
      <p className="hint">Поиск использует добровольно указанные компанию и местоположение в публичных профилях GitHub.
        Они требуют проверки и не подтверждают гражданство, занятость или готовность дать рекомендацию. Лимиты GitHub могут временно ограничить поиск.</p>
      {c.report && <p role="status">{c.report}</p>}
    </section>
    <section className="panel padded"><div className="form-grid two">
      <label>Поиск компаний<input type="search" value={c.q} onChange={e => c.setQ(e.target.value)} placeholder="Название или регион"/></label>
      <label>Сигнал<select value={c.signal} onChange={e => c.setSignal(e.target.value)}>
        <option value="">Все компании</option><option value="referrals">Программа рекомендаций</option>
        <option value="cis_profile">Публичные профили из региона</option><option value="regional_presence">Региональное присутствие</option>
        <option value="resume_fit">Топ-100 по вашему стеку</option></select></label>
    </div><p aria-live="polite">Найдено: {c.companies.length}</p>
      {c.data.isPending && <p role="status">Загружаем компании…</p>}
      {c.data.error && <p className="notice error">{c.data.error.message}</p>}
      <div className="company-grid">{c.companies.map(company => <article className="company-card" key={company.id}>
        <div className="panel-heading"><h3>{company.name}</h3><span className="company-mark">{company.name.slice(0, 2).toUpperCase()}</span></div>
        <div className="tags">{[...new Set(company.signals.map(signal => signal.kind))].map(kind => <span className="badge purple" key={kind}>{signalLabels[kind] || kind}</span>)}</div>
        {!company.signals.length && <p className="hint">Сигналы ещё не проверены</p>}
        {company.signals.map((signal, i) => <div className="company-evidence" key={i}>
          <p>{signal.rank ? `№ ${signal.rank}. ` : ""}{signal.note}</p><a href={signal.url} target="_blank" rel="noreferrer">{signal.confidence === "official" ? "Официальный источник" : signal.kind === "resume_fit" ? "Вакансия / карьера" : "Публичный профиль"} ↗</a>
          <small>{signal.location} · проверено {signal.checked_at}</small></div>)}
        <p className="hint">{company.sources.length ? `${company.sources.filter(source => source.enabled).length} активных страниц вакансий` : "Страница вакансий ещё не подключена"}</p>
        <div className="button-row">{company.careers_url && <a className="button" href={company.careers_url} target="_blank" rel="noreferrer">Карьера ↗</a>}
          <a className="button" href={`https://www.linkedin.com/search/results/people/?${new URLSearchParams({keywords: `${company.name} ${countryLabels[c.country]}`})}`} target="_blank" rel="noreferrer">Люди в LinkedIn ↗</a>
          <button className="text-button" onClick={() => {s.setSourceCompany(company.name); s.navigate("sources");}}>Подключить вакансии</button></div>
        <CompanyContacts company={company} state={s}/>
      </article>)}</div>
      {!c.data.isPending && !c.companies.length && <p className="empty-state">Компаний по этим условиям нет. Измените запрос или запустите поиск новых компаний.</p>}
    </section>
    <form className="panel padded" onSubmit={e => {e.preventDefault(); const form = e.currentTarget; const body = Object.fromEntries(new FormData(form));
      void s.action(async () => {await api("companies", "POST", body); form.reset();}, "Компания сохранена");
    }}><h2>Добавить компанию вручную</h2><fieldset disabled={s.pending}><div className="form-grid two">
      <label>Название<input name="name" required maxLength={200}/></label><label>Карьерная страница<input name="careers_url" type="url" maxLength={1000} placeholder="https://…"/></label>
    </div><button className="button">Добавить компанию</button></fieldset></form>
  </>;
}
