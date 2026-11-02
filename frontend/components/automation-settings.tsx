"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ApplicationDetail, AutomationSettings as Settings } from "@/lib/types";
import { ApplicationPanel } from "./application-panel";

const splitLines = (value: string) => [...new Set(value.split(/[\n,]+/).map(item => item.trim()).filter(Boolean))];

function SettingsForm({settings, onSaved}: {settings: Settings; onSaved: () => void}) {
  const client = useQueryClient();
  const [automatic, setAutomatic] = useState(settings.mode === "autonomous" && settings.auto_apply && settings.enabled);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  return <form onSubmit={event => {event.preventDefault(); const data = new FormData(event.currentTarget);
    const domains = splitLines(String(data.get("authorized_domains") || "")).map(v => v.toLowerCase());
    setPending(true); setError("");
    void api("automation/settings", "PUT", {
      enabled: true, mode: automatic ? "autonomous" : "manual", auto_apply: automatic,
      use_supported_sites: true, authorized_domains: domains,
      min_score: Number(data.get("min_score")), daily_limit: Number(data.get("daily_limit")),
      excluded_companies: splitLines(String(data.get("excluded_companies") || "")),
      required_approval_categories: data.getAll("review"),
    }).then(async () => {onSaved(); await client.invalidateQueries({queryKey: ["automation-settings"]});})
      .catch(e => setError(e instanceof Error ? e.message : "Не удалось сохранить настройки"))
      .finally(() => setPending(false));
  }}><fieldset disabled={pending}>
    <div className="automation-choice">
      <label className="check-label"><input type="checkbox" role="switch" checked={automatic}
        onChange={e => {setAutomatic(e.target.checked); setError("");}}/><strong>Отправлять подходящие отклики автоматически</strong></label>
      <p>{automatic ? "JobPilot подберёт резюме под вакансию и отправит отклик без подтверждения каждого раза." :
        "Автоотправка выключена. Вы сами выбираете вакансию и подтверждаете отправку."}</p>
    </div>
    <p className="hint">Учитываются содержание загруженных резюме, требования вакансии и данные профиля. Если сведений не хватает или сайт требует участия человека, отклик остановится и попросит вашей помощи.</p>
    {automatic && <div className="form-grid two">
      <label>Не больше откликов в день<input name="daily_limit" type="number" min={1} max={100} required defaultValue={settings.daily_limit}/></label>
      <label>Минимальное совпадение<input name="min_score" type="number" min={0} max={100} required defaultValue={settings.min_score}/><small>От 0 до 100 баллов</small></label>
    </div>}
    {!automatic && <><input type="hidden" name="daily_limit" value={settings.daily_limit}/><input type="hidden" name="min_score" value={settings.min_score}/></>}
    <details><summary>Дополнительные настройки</summary>
      <p className="hint">Поддерживаемые сайты вакансий доступны автоматически. Другие сайты можно добавить по точному домену.</p>
      <label>Дополнительные сайты<textarea name="authorized_domains" rows={2} defaultValue={settings.authorized_domains.filter(domain => !settings.supported_domains.includes(domain)).join("\n")}/><small>Например, careers.example.com; по одному домену на строку.</small></label>
      <label>Не откликаться в эти компании<textarea name="excluded_companies" rows={2} defaultValue={settings.excluded_companies.join("\n")}/></label>
      <p>Спрашивать перед отправкой, если встречаются:</p>
      {[["legal", "Право на работу и согласия"], ["salary", "Зарплатные ожидания"], ["personal", "Личные сведения"]].map(([value, label]) =>
        <label className="check-label" key={value}><input type="checkbox" name="review" value={value}
          defaultChecked={settings.required_approval_categories.includes(value)}/>{label}</label>)}
      {settings.required_approval_categories.includes("all") && <input type="hidden" name="review" value="all"/>}
      <p className="hint">Неподтверждённые сведения всегда требуют вашего ответа.</p>
    </details>
    {error && <p className="notice error" role="alert">{error}</p>}
    <button className="button primary" type="submit" disabled={pending || (automatic && !settings.browser_available)}>
      {pending ? "Сохраняем…" : automatic ? (settings.auto_apply ? "Сохранить изменения" : "Включить автоотправку") : "Сохранить ручной режим"}</button>
  </fieldset></form>;
}

export function AutomationSettings() {
  const settings = useQuery<Settings>({queryKey: ["automation-settings"], queryFn: () => api("automation/settings"), retry: false});
  const history = useQuery<ApplicationDetail[]>({queryKey: ["automation-applications"], queryFn: () => api("automation/applications"), retry: false, refetchInterval: 10000});
  const [selected, setSelected] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  return <section className="panel padded automation-settings"><h2>Отклики за вас</h2>
    <p>Подбор резюме, заполнение формы и отправка откликов.</p>
    {settings.isPending && <p role="status">Загружаем настройки агента…</p>}
    {settings.error && <p className="notice error" role="alert">{settings.error.message}</p>}
    {settings.data && <>
      <p className="automation-status" role="status">{settings.data.enabled && settings.data.auto_apply ? "● Автоотправка включена" : "○ Ручной режим"}</p>
      {!settings.data.browser_available && <p className="notice error">Агент временно недоступен. Автоотправку можно включить после восстановления соединения.</p>}
      {saved && <p className="notice" role="status">Параметры автоматических откликов сохранены.</p>}
      <SettingsForm key={JSON.stringify(settings.data)} settings={settings.data} onSaved={() => setSaved(true)}/></>}
    <details className="application-history"><summary>История откликов ({history.data?.length || 0})</summary>
      {history.error && <p className="notice error" role="alert">{history.error.message}</p>}
      {history.isPending && <p role="status">Загружаем историю…</p>}
      {history.data?.map(app => <div className="resume-row" key={app.id}><div><strong>{app.job.title}</strong><small>{app.job.company} · {app.state}</small></div>
        <button className="button" aria-expanded={selected === app.id} onClick={() => setSelected(value => value === app.id ? null : app.id)}>Открыть отклик</button>
        {selected === app.id && <div className="application-history-detail"><ApplicationPanel applicationId={app.id}/></div>}</div>)}
      {history.data?.length === 0 && <p>Откликов пока нет. Выберите вакансию, чтобы подготовить первый отклик.</p>}
    </details>
  </section>;
}
