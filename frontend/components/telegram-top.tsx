"use client";
import { useEffect, useState } from "react";
import type { TopVacancies } from "@/lib/types";

declare global { interface Window { Telegram?: { WebApp?: { initData: string; ready: () => void; expand: () => void } } } }
export function TelegramTop() {
  const [data, setData] = useState<TopVacancies | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  useEffect(() => {
    const app = window.Telegram?.WebApp;
    app?.ready(); app?.expand();
    if (!app?.initData) { setError("Откройте эту страницу через кнопку в вашем Telegram-боте."); return; }
    setError("");
    const controller = new AbortController();
    fetch(`/api/telegram-top?offset=${page * 20}`, {headers: {"X-Telegram-Init-Data": app.initData}, signal: controller.signal})
      .then(async response => { if (!response.ok) throw new Error("Не удалось загрузить вакансии. Проверьте, что вы открыли бота из своего аккаунта."); return response.json(); })
      .then(setData).catch(err => { if (err.name !== "AbortError") setError(err.message); });
    return () => controller.abort();
  }, [page]);
  return <main className="panel padded" style={{maxWidth: 720, margin: "0 auto"}}>
    <h1>Топ вакансий</h1><p>За последние 72 часа · оценка от 60</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {!data && !error && <p>Загружаем вакансии…</p>}
    {data && <><p>Найдено: {data.total}</p>{data.items.map(job => <article className="top-vacancy" key={job.id}>
      <h3><a href={job.canonical_url} target="_blank" rel="noreferrer">{job.title} ↗</a></h3>
      <p>{job.company} · {job.location || "Местоположение не указано"}</p>
      <p>{job.score}/100 · {job.resume_filename}</p>
      {job.salary && <p>{job.salary}</p>}
      <a className="button" href={job.canonical_url} target="_blank" rel="noreferrer">Открыть вакансию ↗</a>
    </article>)}{!data.items.length && <p>Подходящих вакансий пока нет.</p>}
    {data.total > 20 && <div className="button-row"><button className="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Назад</button><span>{page + 1}</span><button className="button" disabled={(page + 1) * 20 >= data.total} onClick={() => setPage(page + 1)}>Далее</button></div>}</>}
  </main>;
}
