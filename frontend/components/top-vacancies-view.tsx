"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { busy } from "@/lib/search-filters";
import type { TopVacancies } from "@/lib/types";
import type { WorkspaceState } from "@/hooks/use-workspace";
import { AutoApplyButton } from "./application-panel";

export function TopVacanciesView({state: s}: {state: WorkspaceState}) {
  const [page, setPage] = useState(0);
  const [provider, setProvider] = useState("");
  const refreshing = s.searches.data?.some(search => busy(search.status));
  const data = useQuery<TopVacancies>({queryKey: ["top-vacancies", page, provider],
    queryFn: () => api(`top-vacancies?offset=${page * 20}&limit=20&provider=${provider}`), retry: false,
    refetchInterval: refreshing ? 2000 : 15000});
  const notification = useQuery<{telegram: boolean; telegram_counts: Record<string, number>; last_error: string | null}>({
    queryKey: ["notifications/status"], queryFn: () => api("notifications/status"), retry: false, refetchInterval: 15000});
  return <section className="panel padded">
    <div className="panel-heading"><div><h2>Лучшие совпадения · от 60/100</h2>
      <p>Сортировка по оценке. Учитываются дата публикации и фильтры ваших поисков; одинаковые ссылки объединены с лучшей оценкой.</p></div>
      <button className="button primary" disabled={s.pending || refreshing || !s.searches.data?.some(search => search.enabled)}
        onClick={() => void s.action(() => api("discover", "POST"), "Обновление подборки запущено")}>{refreshing ? "Подборка обновляется…" : "Обновить подборку"}</button></div>
    <p className="hint">Новые подходящие вакансии отправляются в Telegram отдельными сообщениями с полной ссылкой. Одна вакансия не дублируется между поисками.</p>
    <label>Источник вакансий<select value={provider} onChange={event => {setProvider(event.target.value); setPage(0);}}>
      <option value="">Все источники</option><option value="linkedin">LinkedIn</option></select></label>
    {provider === "linkedin" && <p className="hint">Публичная выдача LinkedIn может быть неполной. В топ попадают только вакансии с подтверждённой датой публикации и оценкой по вашему резюме от 60. В Telegram добавляется краткое описание.</p>}
    {notification.data && <p role="status">Telegram: {notification.data.telegram ? `подключён · отправлено ${notification.data.telegram_counts?.sent || 0} · в очереди ${notification.data.telegram_counts?.pending || 0}` : "не настроен"}</p>}
    {notification.error && <p className="notice error">Не удалось проверить состояние Telegram: {notification.error.message}</p>}
    {notification.data?.last_error && <p className="notice error">Ошибка уведомлений: {notification.data.last_error}</p>}
    {refreshing && <p role="status">Проверяем источники и обновляем оценки. Подборка появится автоматически; подробные этапы доступны в «Поиски».</p>}
    {data.isPending && <p role="status">Загружаем топ вакансий…</p>}
    {data.error && <p className="notice error" role="alert">{data.error.message}</p>}
    {data.data && <><p>Найдено: {data.data.total} · последние {data.data.window_hours} часа · оценка ≥ {data.data.min_score}</p>
      <div className="top-vacancies">{data.data.items.map(job => <article className="top-vacancy" key={job.id}>
        <div className="panel-heading"><div><h3><a href={job.canonical_url} target="_blank" rel="noreferrer">{job.title} ↗</a></h3>
          <p>{job.company} · {job.location || "Местоположение не указано"}</p></div><span className="score">{job.score}<small>/100</small></span></div>
        <small>Опубликовано {job.published_at ? new Date(job.published_at).toLocaleString("ru-RU", {timeZone: "Europe/Moscow"}) : "—"} (Москва) · {job.provider}</small>
        <small>Оценка по резюме: {job.resume_filename} · {job.search_name}</small>
        {job.eligibility === "rejected" && <p className="notice error">Есть ограничения допуска — проверьте условия вакансии.</p>}
        {job.salary && <p>{job.salary}</p>}
        <a className="button" href={job.canonical_url} target="_blank" rel="noreferrer">Открыть вакансию ↗</a>
        <AutoApplyButton canonicalUrl={job.canonical_url}/>
      </article>)}</div>
      {!data.data.items.length && <p className="empty-state">Пока нет вакансий с оценкой от 60 за последние 72 часа. Запустите обновление подборки или дождитесь ближайшего автопоиска.</p>}
      {data.data.total > 20 && <div className="pagination button-row">
        <button className="button" disabled={page === 0} onClick={() => setPage(value => value - 1)}>Назад</button>
        <span>{page * 20 + 1}–{Math.min((page + 1) * 20, data.data.total)} из {data.data.total}</span>
        <button className="button" disabled={(page + 1) * 20 >= data.data.total} onClick={() => setPage(value => value + 1)}>Далее</button>
      </div>}</>}
  </section>;
}
