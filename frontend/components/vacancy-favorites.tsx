"use client";
import { Heart } from "lucide-react";

export function VacancyTabs({favorites, onChange}: {favorites: boolean; onChange: (value: boolean) => void}) {
  return <div className="button-row" role="tablist" aria-label="Подборки вакансий">
    {[false, true].map(value => <button key={String(value)} type="button" role="tab"
      className={`button${favorites === value ? " primary" : ""}`} aria-selected={favorites === value}
      tabIndex={favorites === value ? 0 : -1} onClick={() => onChange(value)}
      onKeyDown={event => {
        if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
          event.preventDefault();
          const next = event.key === "Home" ? false : event.key === "End" ? true : !favorites;
          onChange(next);
          (event.currentTarget.parentElement?.children[Number(next)] as HTMLButtonElement)?.focus();
        }
      }}>{value ? "Избранное" : "Все вакансии"}</button>)}
  </div>;
}

export function FavoriteButton({favorite, disabled, onClick}: {favorite: boolean; disabled: boolean; onClick: () => void}) {
  return <button type="button" className={`button favorite-button${favorite ? " is-favorite" : ""}`}
    aria-label={favorite ? "Удалить из избранного" : "Добавить в избранное"}
    title={favorite ? "Удалить из избранного" : "Добавить в избранное"}
    aria-pressed={favorite} disabled={disabled} onClick={onClick}>
    <Heart size={20} aria-hidden="true" fill={favorite ? "currentColor" : "none"}/>
  </button>;
}
