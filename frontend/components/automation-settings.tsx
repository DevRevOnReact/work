"use client";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ApplicationDetail, AutomationSettings as Settings } from "@/lib/types";
import { ApplicationPanel } from "./application-panel";

const splitLines = (value: string) => [...new Set(value.split(/[\n,]+/).map(item => item.trim()).filter(Boolean))];

function SettingsForm({settings, onSaved}: {settings: Settings; onSaved: () => void}) {
  const client = useQueryClient();
  const [mode, setMode] = useState(settings.mode);
  const [autonomousConsent, setAutonomousConsent] = useState(settings.mode === "autonomous" && settings.auto_apply);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  return <form onSubmit={event => {event.preventDefault(); const data = new FormData(event.currentTarget);
    const authorized_domains = splitLines(String(data.get("authorized_domains"))).map(domain => domain.toLowerCase());
    if (authorized_domains.some(domain => !/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/.test(domain))) {
      setError("Укажите точные домены без https://, путей и звёздочек, например jobs.ashbyhq.com."); return;
    }
    if (mode === "autonomous" && !autonomousConsent) {setError("Подтвердите разрешение на автономную отправку откликов."); return;}
    setPending(true); setError("");
    void api("automation/settings", "PUT", {
      enabled: data.get("enabled") === "on", mode, auto_apply: mode === "autonomous" && autonomousConsent,
      min_score: Number(data.get("min_score")), daily_limit: Number(data.get("daily_limit")), authorized_domains,
      excluded_companies: splitLines(String(data.get("excluded_companies"))),
      required_approval_categories: splitLines(String(data.get("required_approval_categories"))),
    }).then(async () => {onSaved(); await client.invalidateQueries({queryKey: ["automation-settings"]});})
      .catch(error => setError(error instanceof Error ? error.message : "Не удалось сохранить настройки"))
      .finally(() => setPending(false));
  }}><fieldset disabled={pending}>
    <label className="check-label"><input type="checkbox" name="enabled" defaultChecked={settings.enabled}/>Разрешить подготовку откликов</label>
    <div className="form-grid three"><label>Режим<select name="mode" aria-label="Режим" value={mode} onChange={event => {
      setMode(event.target.value as Settings["mode"]); setAutonomousConsent(false); setError("");
    }}><option value="manual">Ручной запуск</option><option value="assisted">Подготовка с подтверждением</option><option value="autonomous">Автономная отправка</option></select></label>
      <label>Минимальная оценка<input type="number" name="min_score" min={0} max={100} required defaultValue={settings.min_score}/></label>
      <label>Максимум отправок в день<input type="number" name="daily_limit" min={1} max={100} required defaultValue={settings.daily_limit}/></label>
    </div>
    <p className="hint">{mode === "manual" ? "«Auto Apply» подготовит выбранную вакансию и запросит разрешение перед отправкой." :
      mode === "assisted" ? "Агент выбирает подходящие вакансии, готовит документы и форму; каждый отклик отправляется после вашего разрешения." :
      "Агент выбирает и отправляет подходящие отклики в пределах сохранённых разрешений. Вопросы без подтверждённых данных требуют вашего участия."}</p>
    {mode === "autonomous" && <label className="check-label"><input type="checkbox" checked={autonomousConsent} required
      onChange={event => setAutonomousConsent(event.target.checked)}/>Разрешаю JobPilot отправлять отклики от моего имени на указанные домены в пределах лимита</label>}
    <div className="form-grid two"><label>Разрешённые домены для откликов<textarea name="authorized_domains" rows={4}
      defaultValue={settings.authorized_domains.join("\n")} placeholder={"jobs.ashbyhq.com\njob-boards.greenhouse.io"}/>
      <small>Один точный домен на строку. Проверьте работодателя перед разрешением загрузки личных документов.</small></label>
      <label>Исключённые компании<textarea name="excluded_companies" rows={4} defaultValue={settings.excluded_companies.join("\n")}/><small>Одно название на строку.</small></label>
    </div>
    <label>Категории, требующие разрешения<textarea name="required_approval_categories" rows={2} defaultValue={settings.required_approval_categories.join("\n")}/>
      <small>Категории через запятую или с новой строки: legal, salary, personal. Вопросы без подтверждённых фактов всегда останавливают подготовку.</small></label>
    {error && <p className="notice error" role="alert">{error}</p>}
    <button className="button primary" type="submit">{pending ? "Сохраняем…" : "Сохранить настройки откликов"}</button>
  </fieldset></form>;
}

export function AutomationSettings() {
  const settings = useQuery<Settings>({queryKey: ["automation-settings"], queryFn: () => api("automation/settings"), retry: false});
  const history = useQuery<ApplicationDetail[]>({queryKey: ["automation-applications"], queryFn: () => api("automation/applications"), retry: false, refetchInterval: 10000});
  const [selected, setSelected] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  return <section className="panel padded"><h2>Автоматические отклики</h2>
    <p>Подготовка документов, заполнение формы и отслеживание подтверждения работодателя.</p>
    {settings.isPending && <p role="status">Загружаем настройки агента…</p>}
    {settings.error && <p className="notice error" role="alert">{settings.error.message}</p>}
    {settings.data && <><p role="status">Браузер: {settings.data.browser_available ? settings.data.browser_headless ? "подключён · снимки формы доступны в отклике" : "подключён · видимое окно на компьютере с JobPilot" : "недоступен · требуется установка браузера на сервере"}</p>
      <p className="hint">При локальном запуске агент открывает отдельное окно Chromium. Для входа или CAPTCHA используйте это окно. Форма сохраняется в браузере до 15 минут ожидания; после перезапуска подготовка выполняется заново.</p>
      {saved && <p className="notice" role="status">Параметры автоматических откликов сохранены.</p>}
      <SettingsForm key={JSON.stringify(settings.data)} settings={settings.data} onSaved={() => setSaved(true)}/></>}
    <details className="application-history"><summary>История откликов ({history.data?.length || 0})</summary>
      {history.error && <p className="notice error" role="alert">{history.error.message}</p>}
      {history.isPending && <p role="status">Загружаем историю…</p>}
      {history.data?.map(app => <div className="resume-row" key={app.id}><div><strong>{app.job.title}</strong><small>{app.job.company} · {app.state}</small></div>
        <button className="button" aria-expanded={selected === app.id} onClick={() => setSelected(value => value === app.id ? null : app.id)}>Открыть отклик</button>
        {selected === app.id && <div className="application-history-detail"><ApplicationPanel applicationId={app.id}/></div>}</div>)}
      {history.data?.length === 0 && <p>Откликов пока нет. Выберите вакансию и нажмите «Auto Apply».</p>}
    </details>
  </section>;
}
