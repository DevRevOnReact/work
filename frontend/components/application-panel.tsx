"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ApplicationDetail } from "@/lib/types";

const labels: Record<string, string> = {
  QUEUED: "В очереди", PREPARING: "Подготовка", RESUME_GENERATED: "Резюме подготовлено",
  ANSWERING_QUESTIONS: "Подготовка ответов", FILLING_FORM: "Заполнение формы",
  AWAITING_APPROVAL: "Ожидает разрешения на отправку", SUBMITTING: "Отправка",
  SUBMITTED: "Отправка подтверждена", NEEDS_HUMAN_ACTION: "Нужна ваша помощь",
  FAILED: "Подготовка остановлена", SUBMISSION_UNCERTAIN: "Результат отправки не подтверждён",
  PAUSED: "На паузе", CANCELLED: "Отменено", APPLYING: "Заполнение и отправка",
  READY: "Готово к обработке", MATCHED: "Подходящая вакансия",
};
const running = new Set(["QUEUED", "PREPARING", "RESUME_GENERATED", "ANSWERING_QUESTIONS", "FILLING_FORM", "SUBMITTING", "APPLYING", "READY"]);
const resumable = new Set(["PAUSED", "NEEDS_HUMAN_ACTION", "FAILED"]);

function artifactUrl(path: string | null) {
  if (!path) return null;
  if (path.startsWith("/api/backend/")) return path;
  if (/^\/(?:automation|artifacts)\/[a-zA-Z0-9_./-]+$/.test(path) && !path.includes("..")) return `/api/backend${path}`;
  return null;
}

export function ApplicationPanel({applicationId}: {applicationId: string}) {
  const client = useQueryClient();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const query = useQuery<ApplicationDetail>({queryKey: ["automation-application", applicationId],
    queryFn: () => api(`automation/applications/${applicationId}`), retry: false,
    refetchInterval: state => running.has(state.state.data?.state || "") ? 2000 : 10000});
  const app = query.data;
  const destinationUrl = app?.application_url || app?.job.canonical_url;
  async function perform(action: string) {
    if (!app) return;
    setPending(true); setError(""); setMessage("");
    try {
      const updated = await api<ApplicationDetail>(`automation/applications/${app.id}/action`, "POST",
        {action, ...(action === "approve" ? {approval_token: app.approval_token} : {})});
      client.setQueryData(["automation-application", app.id], updated);
      await client.invalidateQueries({queryKey: ["automation-applications"]});
      await query.refetch();
    } catch (e) {setError(e instanceof Error ? e.message : "Не удалось выполнить действие");}
    finally {setPending(false);}
  }
  if (!app) return <div className="application-panel">{query.error ? <p className="notice error" role="alert">{query.error.message}</p> : <p role="status">Загружаем отклик…</p>}</div>;
  const resume = artifactUrl(app.generated_resume_url);
  const screenshot = artifactUrl(app.screenshot_url);
  const question = app.progress?.question || app.progress?.questions?.[0] || "";
  const uncertain = app.state === "SUBMISSION_UNCERTAIN";
  let employerUrl: string | null = null;
  try {
    const destination = new URL(destinationUrl || "");
    if (destination.protocol === "https:") employerUrl = destination.href;
  } catch { /* Do not render invalid URLs. */ }
  return <section className="application-panel" aria-label={`Отклик: ${app.job.title}`}>
    <div className="panel-heading"><h3>Автоматический отклик</h3><span className="badge purple" role="status">{labels[app.state] || app.state}</span></div>
    <p aria-live="polite">{app.progress?.message || app.progress?.step || "Ожидаем обновления агента"}</p>
    {app.progress?.resume_filename && <small>Подобрано для вакансии: {app.progress.resume_filename}</small>}
    {app.progress?.browser_status && <small>Браузер агента: {({active: "выполняет действие", waiting: "ожидает вашего действия", closed: "сессия завершена"} as Record<string, string>)[app.progress.browser_status] || app.progress.browser_status}</small>}
    {app.progress?.blocked_host && <p className="notice error">Сайт использует проверку или обращается к неподдерживаемому сервису ({app.progress.blocked_host}). Откройте вакансию и продолжите отклик на сайте работодателя.</p>}
    {app.attention && <p className="notice error" role="alert">{app.attention}</p>}
    {app.state === "NEEDS_HUMAN_ACTION" && <p className="hint">Откройте вакансию и завершите отклик на сайте работодателя. Это отдельная сессия: проверка на сайте не продолжит работу браузера агента автоматически.</p>}
    {(error || query.error) && <p className="notice error" role="alert">{error || query.error?.message}</p>}
    {message && <p className="notice" role="status">{message}</p>}
    {app.state === "SUBMITTED" && <p className="notice">Сайт подтвердил отправку{app.submitted_at ? ` · ${new Date(app.submitted_at).toLocaleString("ru-RU", {timeZone: "Europe/Moscow"})} (Москва)` : ""}.</p>}
    {uncertain && <p className="hint">Запрос мог дойти до работодателя. Агент проверит подтверждение; повторная отправка заблокирована.</p>}
    <div className="button-row">
      {app.state === "NEEDS_HUMAN_ACTION" && employerUrl && <a className="button primary" href={employerUrl}>Перейти к вакансии ↗</a>}
      {resume && <a className="button" href={resume} target="_blank" rel="noreferrer">Открыть подготовленное резюме ↗</a>}
      {app.resume_id && <a href={`/api/backend/resumes/${encodeURIComponent(app.resume_id)}/file`} target="_blank" rel="noreferrer">Исходное резюме ↗</a>}
      {app.state !== "NEEDS_HUMAN_ACTION" && employerUrl && <a href={employerUrl} target="_blank" rel="noreferrer">Форма отклика работодателя ↗</a>}
    </div>
    {app.answers?.length > 0 && <details open={app.state === "AWAITING_APPROVAL"}><summary>Подготовленные ответы ({app.answers.length})</summary>
      <dl className="application-answers">{app.answers.map((answer, i) => <div key={`${answer.question}-${i}`}><dt>{answer.question}</dt><dd>{answer.answer}</dd></div>)}</dl></details>}
    {screenshot && <details><summary>Последнее состояние браузера</summary><a href={screenshot} target="_blank" rel="noreferrer">
      <img className="application-screenshot" src={screenshot} alt="Последний снимок формы отклика" loading="lazy"/></a></details>}
    {app.state === "NEEDS_HUMAN_ACTION" && question && <details open><summary>Подтвердить ответ для этой и будущих заявок</summary>
      <form key={question} onSubmit={e => {e.preventDefault(); const form = e.currentTarget; const body = new FormData(form);
        setPending(true); setError(""); setMessage("");
        void api("answers", "POST", {question: String(body.get("question")).trim(), answer: String(body.get("answer")).trim()})
          .then(() => {setMessage("Ответ сохранён. Нажмите «Продолжить подготовку», чтобы агент проверил форму снова."); form.reset();})
          .catch(e => setError(e instanceof Error ? e.message : "Не удалось сохранить ответ"))
          .finally(() => setPending(false));
      }}><fieldset disabled={pending}><label>Точный вопрос работодателя<textarea name="question" defaultValue={question} required maxLength={2000} rows={2}/></label>
        <label>Ваш подтверждённый ответ<textarea name="answer" required maxLength={10000} rows={3}/></label>
        <p className="hint">Сохранённый ответ будет использоваться повторно для такого же вопроса.</p>
        <button className="button" type="submit">Сохранить подтверждённый ответ</button></fieldset></form>
    </details>}
    {app.state === "AWAITING_APPROVAL" && <p className="hint">Проверьте резюме, ответы и снимок формы. Разрешение относится к текущей подготовленной версии отклика.</p>}
    <div className="button-row application-actions">
      {app.state === "AWAITING_APPROVAL" && <button className="button primary" disabled={pending || !app.approval_token}
        onClick={() => void perform("approve")}>Разрешить отправку работодателю</button>}
      {(running.has(app.state) || app.state === "AWAITING_APPROVAL") && !app.submit_intent_at && <button className="button" disabled={pending} onClick={() => void perform("pause")}>Пауза</button>}
      {resumable.has(app.state) && !app.submit_intent_at && <button className="button" disabled={pending} onClick={() => void perform("resume")}>Продолжить подготовку</button>}
      {uncertain && <button className="button" disabled={pending} onClick={() => void perform("verify")}>Проверить подтверждение</button>}
      {!["SUBMITTED", "CANCELLED"].includes(app.state) && !app.submit_intent_at && <button className="button danger" disabled={pending} onClick={() => void perform("cancel")}>Отменить отклик</button>}
      <button className="text-button" disabled={pending || query.isFetching} onClick={() => void query.refetch()}>Обновить состояние</button>
    </div>
    {app.progress?.events?.length ? <details><summary>Журнал действий агента</summary><ol className="search-log">{app.progress.events.map((event, i) =>
      <li key={i} className={event.level === "error" ? "log-error" : undefined}>{event.at && <time>{new Date(event.at).toLocaleTimeString("ru-RU")} · </time>}{event.message}</li>)}</ol></details> : null}
  </section>;
}

export function AutoApplyButton({canonicalUrl}: {canonicalUrl: string}) {
  const [applicationId, setApplicationId] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  async function start() {
    if (applicationId) {setOpen(value => !value); return;}
    setPending(true); setError("");
    try {
      const app = await api<ApplicationDetail>("automation/applications", "POST", {canonical_url: canonicalUrl});
      setApplicationId(app.id); setOpen(true);
    } catch (e) {setError(e instanceof Error ? e.message : "Не удалось подготовить отклик");}
    finally {setPending(false);}
  }
  return <div className="auto-apply"><button className="button primary" disabled={pending} aria-expanded={open}
    onClick={() => void start()}>{pending ? "Запускаем агента…" : applicationId ? open ? "Скрыть отклик" : "Открыть отклик" : "Auto Apply"}</button>
    {error && <p className="notice error" role="alert">{error}</p>}
    {open && applicationId && <ApplicationPanel applicationId={applicationId}/>}</div>;
}
