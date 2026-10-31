import { useState } from "react";
import type { SearchDetail } from "@/lib/types";
import { activeFilters, emptyFilters } from "@/lib/search-filters";

export function SearchResults({search, filename}: {search: SearchDetail; filename: string}) {
  const [page, setPage] = useState(0);
  const size = 20;
  const jobs = search.results.slice(page * size, (page + 1) * size);
  return <section className="panel padded"><div className="panel-heading"><div>
    <h2>{search.name}</h2><p>{filename} · {search.result_count} вакансий</p></div>
    <span className="badge purple">Соответствие резюме</span></div>
    <div className="tags">{activeFilters({...emptyFilters, ...search.filters}).map(item =>
      <span className="filter-chip" key={item.key}>{item.label}</span>)}</div>
    {search.status === "failed" && <p className="notice error">Обновление не удалось. Ниже сохранены результаты предыдущего успешного запуска.</p>}
    {search.errors.map((error, i) => <p className="notice error" key={i}>{error}</p>)}
    {jobs.map(job => <details className="search-result" key={job.canonical_url}>
      <summary><div><strong>{job.title}</strong><span>{job.company} · {job.location || "Место не указано"}</span></div>
        <span className="score">{job.score}<small>/100</small></span></summary>
      <p>{job.salary || "Зарплата не указана"} · {job.provider} · {job.published_at ? new Date(job.published_at).toLocaleDateString("ru-RU") : "Дата неизвестна"}</p>
      <a href={job.canonical_url} target="_blank" rel="noreferrer">Открыть вакансию ↗</a>
      <h3>Как рассчитана оценка</h3><div className="criteria-list">{job.explanation.criteria?.map(c =>
        <div className="criterion" key={c.key}><strong>{c.label}: {c.score}/10</strong><p>{c.reason}</p></div>)}</div>
      <details><summary>Полное описание</summary><p className="description">{job.description}</p></details>
    </details>)}
    {!search.results.length && <div className="empty-state"><h3>Подходящих вакансий пока нет</h3>
      <p>Запустите поиск, проверьте подключённые компании или уберите часть фильтров. Режим «Хотя бы один навык» расширяет результаты.</p></div>}
    {search.results.length > size && <div className="pagination button-row">
      <button className="button" disabled={!page} onClick={() => setPage(v => v - 1)}>Назад</button>
      <span>{page * size + 1}–{Math.min((page + 1) * size, search.results.length)} из {search.results.length}</span>
      <button className="button" disabled={(page + 1) * size >= search.results.length} onClick={() => setPage(v => v + 1)}>Далее</button>
    </div>}
    {search.result_count > 500 && <p className="hint">Показаны первые 500 результатов выбранной сортировки. Уточните фильтры для остальных.</p>}
  </section>;
}
