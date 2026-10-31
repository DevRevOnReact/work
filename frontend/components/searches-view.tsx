import { api } from "@/lib/api";
import { busy, statusLabel } from "@/lib/search-filters";
import type { SavedSearch } from "@/lib/types";
import type { WorkspaceState } from "@/hooks/use-workspace";
import { SearchEditor } from "./search-editor";
import { SearchResults } from "./search-results";

export function SearchesView({state: s}: {state: WorkspaceState}) {
  return <>
    <section className="panel padded"><div className="panel-heading"><div><h2>Ваши поиски</h2>
      <p>Своё резюме, фильтры и ежедневное обновление для каждого поиска.</p></div>
      <button className="button primary" onClick={() => {s.setSelected(null); s.setEditing(true);}}>Новый поиск</button></div>
      <button className="text-button" disabled={s.pending || !s.resumes.data?.length} onClick={() => void s.action(
        () => api("searches/setup", "POST"), "Поиски для резюме настроены. Существующие параметры сохранены.")}>Настроить поиски для всех резюме</button>
      {s.searches.isPending && <p role="status">Загружаем поиски…</p>}
      {s.searches.data?.map(search => <div className={`resume-row search-row ${s.current?.id === search.id ? "selected" : ""}`} key={search.id}>
        <div><button className="text-button" aria-pressed={s.current?.id === search.id} onClick={() => {s.setSelected(search.id); s.setEditing(false);}}>{search.name}</button>
          <small>{s.resumes.data?.find(r => r.id === search.resume_id)?.filename} · {search.enabled ? "Ежедневно" : "Ручной запуск"} · {statusLabel[search.status] || search.status} · {search.result_count} вакансий</small>
          {search.completed_at && <small>Обновлён {new Date(search.completed_at).toLocaleString("ru-RU")}</small>}</div>
        <div className="button-row"><button className="button" disabled={s.pending || busy(search.status)} onClick={() => void s.action(
          () => api(`searches/${search.id}/run`, "POST"), "Поиск поставлен в очередь")}>{busy(search.status) ? statusLabel[search.status] : "Запустить"}</button>
          <button className="button" disabled={s.pending || busy(search.status)} onClick={() => {s.setSelected(search.id); s.setEditing(true);}}>Настроить</button>
          <button className="text-button danger" disabled={s.pending || busy(search.status)} onClick={() => void s.action(async () => {
            await api(`searches/${search.id}`, "DELETE"); if(s.selected === search.id) s.setSelected(null);
          }, "Поиск удалён")}>Удалить</button></div>
      </div>)}
      {!s.searches.isPending && !s.searches.data?.length && <div className="empty-state"><h3>Начните с резюме</h3>
        <p>После загрузки автоматически появится поиск с навыками из вашего файла.</p><button className="button" onClick={() => s.navigate("resumes")}>Загрузить резюме</button></div>}
    </section>
    {s.editing && <SearchEditor key={s.current?.id || "new"} selected={s.current} resumes={s.resumes.data || []} pending={s.pending}
      onCancel={() => s.setEditing(false)} onSave={async body => {await s.action(async () => {
        const saved = await api<SavedSearch>(s.current ? `searches/${s.current.id}` : "searches", s.current ? "PUT" : "POST", body);
        s.setSelected(saved.id); s.setEditing(false);
      }, "Настройки сохранены. Запустите поиск для обновления результатов.");}}/>}
    {!s.editing && s.current && busy(s.current.status) && <div className="panel padded" role="status">Поиск выполняется. Результаты появятся здесь автоматически.</div>}
    {!s.editing && !busy(s.current?.status || "") && s.detail.isPending && s.current && <p role="status">Загружаем результаты…</p>}
    {s.detail.error && <p className="notice error" role="alert">{s.detail.error.message}</p>}
    {!s.editing && !busy(s.current?.status || "") && s.detail.data && <SearchResults key={`${s.detail.data.id}/${s.detail.data.completed_at}`} search={s.detail.data}
      filename={s.resumes.data?.find(r => r.id === s.current?.resume_id)?.filename || "Резюме"}/>}
  </>;
}
