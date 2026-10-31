import { api } from "@/lib/api";
import { useResource, type WorkspaceState } from "@/hooks/use-workspace";
import type { Source } from "@/lib/types";

export function SourcesView({state: s}: {state: WorkspaceState}) {
  const sources = useResource<Source[]>("sources");
  return <>
    <section className="panel padded"><h2>Подключённые страницы вакансий</h2>
      <p>Поиск по миру: страницы работодателей Greenhouse, Ashby и Lever, международная удалённая лента Remotive и компании Европы из Arbeitnow.</p>
      <p>Подключено источников: {sources.data?.length || 0}; включено: {sources.data?.filter(source => source.enabled).length || 0}. География и право на работу указаны в каждой вакансии.</p>
      <button className="button" disabled={s.pending} onClick={() => void s.action(() => api("sources/starter", "POST", {}), "Стартовые страницы подключены")}>Добавить стартовый список</button>
      <p className="hint">Стартовый список: 100 проверенных компаний и две общие ленты. Вакансии Remotive обновляются не чаще раза в 6 часов. Поиск охватывает подключённые источники; полного индекса всех компаний мира нет.</p>
      {sources.error && <p className="notice error">{sources.error.message}</p>}
      {sources.data?.map(source => <div className="resume-row" key={source.id}><div><strong>{source.company}</strong>
        <small>{source.provider} · {source.board} · {source.enabled ? "Включён" : "На паузе"}</small>
        {source.error && <small className="error">Ошибка: {source.error}</small>}</div>
        <button className="button" disabled={s.pending} onClick={() => void s.action(() => api(`sources/${source.id}/toggle`, "POST"), "Источник обновлён")}>{source.enabled ? "Приостановить" : "Включить"}</button></div>)}
      {!sources.isPending && !sources.data?.length && <p>Источники ещё не подключены. Добавьте страницу ниже или выберите стартовый список.</p>}
    </section>
    <form className="panel padded" onSubmit={e => {e.preventDefault(); const form = e.currentTarget;
      const body = Object.fromEntries(new FormData(form));
      void s.action(async () => {await api("sources", "POST", body); form.reset();}, "Источник подключён");
    }}><h2>Подключить страницу работодателя</h2><fieldset disabled={s.pending}><div className="form-grid three">
      <label>Компания<input name="company" required maxLength={200} defaultValue={s.sourceCompany}/></label>
      <label>Платформа<select name="provider"><option value="greenhouse">Greenhouse</option><option value="ashby">Ashby</option><option value="lever">Lever</option></select></label>
      <label>Идентификатор страницы<input name="board" required maxLength={120} pattern="[A-Za-z0-9_-]+" placeholder="company-slug"/></label>
    </div><p className="hint">Например, для jobs.ashbyhq.com/company-slug укажите company-slug.</p><button className="button primary">Подключить</button></fieldset></form>
  </>;
}
