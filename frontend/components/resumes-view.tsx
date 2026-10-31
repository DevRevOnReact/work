import { api } from "@/lib/api";
import { useResource, type WorkspaceState } from "@/hooks/use-workspace";

export function ResumesView({state: s}: {state: WorkspaceState}) {
  const profile = useResource<Record<string, unknown>>("profile");
  const notifications = useResource<{telegram: boolean; email: boolean; recipient: string; counts: Record<string, number>; last_error: string | null}>("notifications/status");
  return <>
    <section className="panel padded"><h2>Уведомления о вакансиях</h2>
      <p>Сводка новых вакансий после успешного поиска. Повторные вакансии не отправляются.</p>
      {notifications.data && <><p>Telegram: {notifications.data.telegram ? "подключён" : "не настроен"} · Почта: {notifications.data.email ? notifications.data.recipient : "не настроена"}</p>
        <p>Отправлено: {notifications.data.counts.sent || 0} · В очереди: {notifications.data.counts.pending || 0} · Ошибок: {notifications.data.counts.failed || 0}</p>
        {notifications.data.last_error && <p className="notice error">Доставка: {notifications.data.last_error}</p>}
        <button className="button" disabled={s.pending || (!notifications.data.telegram && !notifications.data.email)}
          onClick={() => void s.action(() => api("notifications/test", "POST"), "Проверочное уведомление поставлено в очередь")}>Проверить уведомления</button></>}
      {notifications.error && <p className="notice error">{notifications.error.message}</p>}
    </section>
    <section className="panel padded"><h2>Ваши резюме</h2><p>Для каждого нового резюме автоматически создаётся отдельный поиск.
      Навыки берутся из этого файла; существующие поиски сохраняют все параметры.</p>
      <form onSubmit={e => {e.preventDefault(); const form = e.currentTarget; const body = new FormData(form);
        void s.action(async () => {await api("resumes", "POST", body); form.reset();}, "Резюме загружено, поиск настроен");
      }}><fieldset disabled={s.pending}><div className="form-grid two">
        <label>PDF, DOCX или TXT · до 10 МБ<input type="file" name="file" accept=".pdf,.docx,.txt" required/></label>
        <label>Вариант резюме<select name="variant"><option value="general">Общее</option><option value="frontend">Frontend</option>
          <option value="full-stack">Full Stack</option><option value="react-python-ai">React / Python / AI</option></select></label>
      </div><button className="button primary">{s.pending ? "Загружаем…" : "Загрузить резюме"}</button></fieldset></form>
      {s.resumes.data?.map(resume => <div className="resume-row" key={resume.id}><div>
        <a href={`/api/backend/resumes/${resume.id}/file`} target="_blank" rel="noreferrer">{resume.filename} ↗</a>
        <small>{resume.variant} · {resume.text_length} символов извлечено</small>
        {resume.parse_warnings?.map((warning, i) => <small key={i}>{warning}</small>)}</div>
        <button className="button" onClick={() => {const search = s.searches.data?.find(search => search.resume_id === resume.id);
          s.setSelected(search?.id || null); s.setEditing(Boolean(search)); s.navigate("search");}}>Настроить поиск</button></div>)}
    </section>
    {profile.error && <p className="notice error">{profile.error.message}</p>}
    {profile.data && <form className="panel padded" key={JSON.stringify([profile.data.location, profile.data.relocation, profile.data.salary_expectation])}
      onSubmit={e => {e.preventDefault(); const body = Object.fromEntries(new FormData(e.currentTarget));
        void s.action(() => api("profile", "PUT", body), "Общие условия сохранены");
      }}><h2>Общие условия</h2><p>Используются при оценке всех резюме. Загрузка другой версии сохраняет эти условия.</p>
      <fieldset disabled={s.pending}><div className="form-grid three">
        <label>Местоположение<input name="location" defaultValue={String(profile.data.location || "")}/></label>
        <label>Условия переезда<input name="relocation" defaultValue={String(profile.data.relocation || "")}/></label>
        <label>Ожидаемая зарплата<input name="salary_expectation" defaultValue={String(profile.data.salary_expectation || "")}/></label>
      </div><button className="button">Сохранить условия</button></fieldset>
    </form>}
  </>;
}
