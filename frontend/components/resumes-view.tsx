import { api } from "@/lib/api";
import { useResource, type WorkspaceState } from "@/hooks/use-workspace";
import { AutomationSettings } from "./automation-settings";

function WorkAuthorization({profile, state: s}: {profile: Record<string, unknown>; state: WorkspaceState}) {
  const stored = profile.work_authorization;
  const declarations: Record<string, boolean> = stored && typeof stored === "object" && !Array.isArray(stored)
    ? Object.fromEntries(Object.entries(stored).filter(([, value]) => typeof value === "boolean")) : {};
  return <section className="panel padded"><h2>Право на работу</h2>
    <p>Укажите подтверждённый статус для конкретной страны. Эти сведения используются для проверки допуска и заполнения заявок.</p>
    <form onSubmit={event => {event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
      const country = String(data.get("country")).trim();
      if (!country) return;
      void s.action(() => api("profile", "PUT", {work_authorization: {...declarations, [country]: data.get("permitted") === "yes"}}), "Статус права на работу сохранён")
        .then(saved => {if (saved) form.reset();});
    }}><fieldset disabled={s.pending}><div className="form-grid two">
      <label>Страна<input name="country" required maxLength={100} placeholder="Например, Georgia"/></label>
      <label>Подтверждённый статус<select name="permitted" required defaultValue=""><option value="" disabled>Выберите статус</option>
        <option value="yes">Имею право на работу</option><option value="no">Не имею права на работу</option></select></label>
    </div><button className="button" type="submit">Сохранить статус</button></fieldset></form>
    {Object.entries(declarations).map(([country, permitted]) => <div className="resume-row" key={country}>
      <div><strong>{country}</strong><small>{permitted ? "Право на работу подтверждено" : "Право на работу отсутствует"}</small></div>
      <button className="text-button danger" disabled={s.pending} onClick={() => void s.action(() => api("profile", "PUT",
        {work_authorization: Object.fromEntries(Object.entries(declarations).filter(([key]) => key !== country))}), "Подтверждение удалено")}>Удалить подтверждение</button>
    </div>)}
  </section>;
}

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
    {profile.data && <form className="panel padded" key={JSON.stringify(profile.data)}
      onSubmit={e => {e.preventDefault(); const body = Object.fromEntries(new FormData(e.currentTarget));
        void s.action(() => api("profile", "PUT", body), "Общие условия сохранены");
      }}><h2>Общие условия</h2><p>Используются при оценке всех резюме. Загрузка другой версии сохраняет эти условия.</p>
      <fieldset disabled={s.pending}><div className="form-grid three">
        <label>Полное имя<input name="full_name" autoComplete="name" defaultValue={String(profile.data.full_name || "")}/></label>
        <label>Email для откликов<input name="email" type="email" autoComplete="email" defaultValue={String(profile.data.email || "")}/></label>
        <label>Телефон<input name="phone" type="tel" autoComplete="tel" defaultValue={String(profile.data.phone || "")}/></label>
        <label>Местоположение<input name="location" defaultValue={String(profile.data.location || "")}/></label>
        <label>Условия переезда<input name="relocation" defaultValue={String(profile.data.relocation || "")}/></label>
        <label>Ожидаемая зарплата<input name="salary_expectation" defaultValue={String(profile.data.salary_expectation || "")}/></label>
        <label>Готовность приступить к работе<input name="notice_period" defaultValue={String(profile.data.notice_period || "")}/></label>
        <label>Портфолио<input name="portfolio" type="url" defaultValue={String(profile.data.portfolio || "")}/></label>
        <label>LinkedIn<input name="linkedin" type="url" defaultValue={String(profile.data.linkedin || "")}/></label>
        <label>GitHub<input name="github" type="url" defaultValue={String(profile.data.github || "")}/></label>
      </div><button className="button">Сохранить условия</button></fieldset>
    </form>}
    {profile.data && <WorkAuthorization profile={profile.data} state={s}/>}
    <AutomationSettings/>
  </>;
}
