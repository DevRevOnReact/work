"use client";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { Company } from "@/lib/types";
import { useResource, type WorkspaceState } from "./use-workspace";

export const countries = ["Armenia", "Azerbaijan", "Belarus", "Kazakhstan", "Kyrgyzstan", "Moldova",
  "Russia", "Tajikistan", "Turkmenistan", "Uzbekistan", "Georgia", "Ukraine"];
export const countryLabels: Record<string, string> = {Armenia: "Армения", Azerbaijan: "Азербайджан", Belarus: "Беларусь",
  Kazakhstan: "Казахстан", Kyrgyzstan: "Кыргызстан", Moldova: "Молдова", Russia: "Россия", Tajikistan: "Таджикистан",
  Turkmenistan: "Туркменистан", Uzbekistan: "Узбекистан", Georgia: "Грузия", Ukraine: "Украина"};
export function useCompanies(action: WorkspaceState["action"]) {
  const data = useResource<Company[]>("companies");
  const [q, setQ] = useState("");
  const [signal, setSignal] = useState("");
  const [country, setCountry] = useState("Kazakhstan");
  const [page, setPage] = useState(1);
  const [report, setReport] = useState("");
  const companies = useMemo(() => (data.data || []).filter(company => {
    const locations = company.signals.map(s => `${s.location} ${countries.filter(country => s.location.includes(country)).map(country => countryLabels[country]).join(" ")}`).join(" ");
    const text = `${company.name} ${locations}`.toLowerCase();
    return q.toLowerCase().trim().split(/\s+/).every(word => text.includes(word)) &&
      (!signal || company.signals.some(s => s.kind === signal));
  }).sort((a, b) => signal === "resume_fit"
    ? (a.signals.find(s => s.kind === "resume_fit")?.rank || 999) - (b.signals.find(s => s.kind === "resume_fit")?.rank || 999)
    : a.name.localeCompare(b.name)), [data.data, q, signal]);
  async function discover() {
    await action(async () => {
      const result = await api<{added: number; found: number; errors: string[]; next_page: number | null}>(
        "companies/discover", "POST", {country, page});
      setReport(`Найдено компаний: ${result.found}; новых: ${result.added}. ${result.errors.join("; ")}`);
      setPage(result.next_page || 1);
    }, "Поиск компаний завершён");
  }
  return {data, companies, q, setQ, signal, setSignal, country,
    setCountry: (value: string) => {setCountry(value); setPage(1); setReport("");}, page, report, discover};
}
