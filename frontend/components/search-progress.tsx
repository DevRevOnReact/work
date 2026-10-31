import type { SearchProgress as Progress } from "@/lib/types";
import { busy, statusLabel } from "@/lib/search-filters";

export function SearchProgress({progress, status, errors = []}: {progress?: Progress; status: string; errors?: string[]}) {
  const events = progress?.events || [];
  if (!progress?.message && !errors.length && !busy(status)) return null;
  return <div className="search-progress">
    <p role="status" aria-live="polite"><strong>{statusLabel[status] || status}</strong> · {progress?.message || "Ожидаем начала поиска…"}</p>
    {Boolean(progress?.total) && <div className="progress-count"><progress max={progress!.total} value={progress?.done || 0}
      aria-label="Прогресс поиска"/> <span>{progress?.done || 0} / {progress!.total}</span></div>}
    {progress?.updated_at && <small>Последняя активность: {new Date(progress.updated_at).toLocaleString("ru-RU")}</small>}
    {errors.map((error, i) => <p className="notice error" key={i}>{error}</p>)}
    {events.length > 0 && <details open={busy(status)}><summary>Журнал поиска · {events.length} событий</summary>
      <ol className="search-log">{events.map((event, i) => <li key={`${event.at}/${i}`} className={event.level === "error" ? "log-error" : ""}>
        <time dateTime={event.at}>{new Date(event.at).toLocaleTimeString("ru-RU")}</time> {event.message}
      </li>)}</ol></details>}
  </div>;
}
