import { api } from "@/lib/api";
import { useCompanies } from "@/hooks/use-companies";
import type { WorkspaceState } from "@/hooks/use-workspace";
import { CompanyContacts } from "./company-contacts";
import { SearchProgress } from "./search-progress";
import { busy, emptyFilters } from "@/lib/search-filters";
import type { Company, SavedSearch } from "@/lib/types";

const signalLabels: Record<string, string> = {
  resume_fit: "Топ-100 · подборка по вашему стеку",
  referrals: "Программа рекомендаций", regional_presence: "Присутствие в регионе", cis_profile: "Публичный профиль из региона",
  github_profile: "Публичный профиль GitHub", it_catalog: "Мировой IT-каталог",
};
export function CompaniesView({state: s}: {state: WorkspaceState}) {
  const c = useCompanies(s.action);
  async function findJobs(company: Company) {
    await s.action(async () => {
      const resumeId = s.current?.resume_id || s.resumes.data?.[0]?.id;
      if (!resumeId) throw new Error("Сначала загрузите резюме для подбора вакансий");
      let search = s.searches.data?.find(item => item.resume_id === resumeId && item.filters.company === company.name);
      if (!search) search = await api<SavedSearch>("searches", "POST", {
        name: `${company.name} · вакансии`.slice(0, 120), resume_id: resumeId, enabled: true,
        filters: {...emptyFilters, ...s.current?.filters, company: company.name, provider: ""},
      });
      if (!busy(search.status)) await api(`searches/${search.id}/run`, "POST");
      s.setSelected(search.id); s.setEditing(false); s.navigate("search");
    }, "Поиск вакансий компании запущен");
  }
  return <>
    <section className="panel padded"><div className="panel-heading"><div><h2>Откройте новых работодателей</h2>
      <p>IT-работодатели по всему миру, программы рекомендаций и публичные профили потенциальных контактов.</p></div>
      <button className="button" disabled={s.pending} onClick={() => void s.action(() => api("companies/starter", "POST"), "Каталог обновлён")}>Добавить проверенные компании</button></div>
      <form className="discovery-form" onSubmit={e => {e.preventDefault(); void c.discover();}}>
        <label>Регион поиска<select value={c.country} disabled={c.discovering} onChange={e => c.setCountry(e.target.value)}>
          {c.countries.map(country => <option key={country} value={country}>{c.countryLabels[country]}</option>)}</select></label>
        <button className="button primary" disabled={s.pending || c.discovering}>{c.discovering ? "Ищем компании…" : `Найти новые компании${c.country !== "Worldwide" && c.page > 1 ? ` · страница ${c.page}` : ""}`}</button>
      </form>
      <p className="hint">«Весь мир» подключает каталог IT-работодателей и их страницы вакансий. Выбор страны ищет компании через публичные профили GitHub.
        Они требуют проверки и не подтверждают гражданство, занятость или готовность дать рекомендацию. Лимиты GitHub могут временно ограничить поиск.</p>
      {c.regions?.error && <p className="notice error">Не удалось загрузить регионы поиска</p>}
      {c.discoveries.error && <p className="notice error">{c.discoveries.error.message}</p>}
      {c.job && <><p>{c.countryLabels[c.job.country] || c.job.country} · запуск {new Date(c.job.started_at).toLocaleString("ru-RU")}</p>
        <SearchProgress progress={c.job.progress} status={c.job.status} errors={c.job.result.errors || []}/></>}
    </section>
    <section className="panel padded"><div className="form-grid two">
      <label>Поиск компаний<input type="search" value={c.q} onChange={e => c.setQ(e.target.value)} placeholder="Название или регион"/></label>
      <label>Сигнал<select value={c.signal} onChange={e => c.setSignal(e.target.value)}>
        <option value="">Все компании</option><option value="referrals">Программа рекомендаций</option>
        <option value="github_profile">Публичные профили из всех регионов</option><option value="regional_presence">Региональное присутствие</option>
        <option value="it_catalog">Мировой IT-каталог</option>
        <option value="resume_fit">Топ-100 по вашему стеку</option></select></label>
    </div><p aria-live="polite">Найдено: {c.companies.length}</p>
      {c.data.isPending && <p role="status">Загружаем компании…</p>}
      {c.data.error && <p className="notice error">{c.data.error.message}</p>}
      <div className="company-grid">{c.companies.map(company => <article className="company-card" key={company.id}>
        <div className="panel-heading"><h3>{company.name}</h3><span className="company-mark">{company.name.slice(0, 2).toUpperCase()}</span></div>
        <div className="tags">{[...new Set(company.signals.map(signal => signal.kind))].map(kind => <span className="badge purple" key={kind}>{signalLabels[kind] || kind}</span>)}</div>
        {!company.signals.length && <p className="hint">Сигналы ещё не проверены</p>}
        {company.signals.map((signal, i) => <div className="company-evidence" key={i}>
          <p>{signal.rank ? `№ ${signal.rank}. ` : ""}{signal.note}</p><a href={signal.url} target="_blank" rel="noreferrer">{signal.confidence === "official" ? "Официальный источник" : ["resume_fit", "it_catalog"].includes(signal.kind) ? "Вакансия / карьера" : "Публичный профиль"} ↗</a>
          <small>{signal.location} · проверено {signal.checked_at}</small></div>)}
        <p className="hint">{company.sources.length ? `${company.sources.filter(source => source.enabled).length} активных страниц вакансий` : "Страница вакансий ещё не подключена"}</p>
        <div className="button-row">{company.careers_url && <a className="button" href={company.careers_url} target="_blank" rel="noreferrer">Карьера ↗</a>}
          <a className="button" href={`https://www.linkedin.com/search/results/people/?${new URLSearchParams({keywords: `${company.name}${c.country === "Worldwide" ? "" : ` ${c.country}`}`})}`} target="_blank" rel="noreferrer">Люди в LinkedIn ↗</a>
          {company.sources.some(source => source.enabled) && <button className="button primary" disabled={s.pending || !s.resumes.data?.length}
            onClick={() => void findJobs(company)}>Найти вакансии</button>}
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
