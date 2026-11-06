"use client";
import { useEffect, useState } from "react";
import type { TopVacancies } from "@/lib/types";
import { FavoriteButton, VacancyTabs } from "./vacancy-favorites";

declare global { interface Window { Telegram?: { WebApp?: { initData: string; ready: () => void; expand: () => void } } } }
export function TelegramTop() {
  const [data, setData] = useState<TopVacancies | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(0);
  const [favorites, setFavorites] = useState(false);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [favoriteError, setFavoriteError] = useState("");
  useEffect(() => {
    const app = window.Telegram?.WebApp;
    app?.ready(); app?.expand();
    if (!app?.initData) { setError("Откройте эту страницу через кнопку в вашем Telegram-боте."); return; }
    setError("");
    const controller = new AbortController();
    fetch(`/api/telegram-top?offset=${page * 20}&favorites=${favorites}`, {headers: {"X-Telegram-Init-Data": app.initData}, signal: controller.signal})
      .then(async response => { if (!response.ok) throw new Error("Не удалось загрузить вакансии. Проверьте, что вы открыли бота из своего аккаунта."); return response.json(); })
      .then(result => {
        setData(result);
        if (page > 0 && page * 20 >= result.total) setPage(Math.max(0, Math.ceil(result.total / 20) - 1));
      }).catch(err => { if (err.name !== "AbortError") setError(err.message); });
    return () => controller.abort();
  }, [page, favorites, revision]);
  async function saveFavorite(id: string, favorite: boolean) {
    setSaving(true); setFavoriteError("");
    try {
      const response = await fetch(`/api/telegram-top?id=${encodeURIComponent(id)}`, {method: "PUT",
        headers: {"Content-Type": "application/json", "X-Telegram-Init-Data": window.Telegram?.WebApp?.initData || ""},
        body: JSON.stringify({favorite})});
      if (!response.ok) throw new Error("Не удалось сохранить избранное");
      setRevision(value => value + 1);
    } catch (error) {setFavoriteError(error instanceof Error ? error.message : "Не удалось сохранить избранное");}
    finally {setSaving(false);}
  }
  return <main className="panel padded" style={{maxWidth: 720, margin: "0 auto"}}>
    <h1>Топ вакансий</h1><p>{favorites ? "Избранное без ограничения срока" : "За последние 72 часа и избранное"} · оценка от 60</p>
    <VacancyTabs favorites={favorites} onChange={value => {setFavorites(value); setPage(0); setData(null);}}/>
    <p className="hint">Вакансии с сердечком сохраняются после трёх дней.</p>
    {favoriteError && <p className="notice error" role="alert">{favoriteError}</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
    {!data && !error && <p>Загружаем вакансии…</p>}
    {data && <><p>Найдено: {data.total}</p>{data.items.map(job => <article className="top-vacancy" key={job.id}>
      <div className="panel-heading"><h3><a href={job.canonical_url} target="_blank" rel="noreferrer">{job.title} ↗</a></h3>
        <FavoriteButton favorite={job.favorite} disabled={saving} onClick={() => void saveFavorite(job.id, !job.favorite)}/></div>
      <p>{job.company} · {job.location || "Местоположение не указано"}</p>
      <p>{job.score}/100 · {job.resume_filename}</p>
      {job.salary && <p>{job.salary}</p>}
      <a className="button" href={job.canonical_url} target="_blank" rel="noreferrer">Открыть вакансию ↗</a>
    </article>)}{!data.items.length && <p>{favorites ? "В избранном пока пусто. Добавьте вакансию сердечком." : "Подходящих вакансий пока нет."}</p>}
    {data.total > 20 && <div className="button-row"><button className="button" disabled={page === 0} onClick={() => setPage(page - 1)}>Назад</button><span>{page + 1}</span><button className="button" disabled={(page + 1) * 20 >= data.total} onClick={() => setPage(page + 1)}>Далее</button></div>}</>}
  </main>;
}
