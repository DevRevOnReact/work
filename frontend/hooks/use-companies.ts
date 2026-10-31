"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { busy } from "@/lib/search-filters";
import type { Company, CompanyDiscovery } from "@/lib/types";
import { useResource, type WorkspaceState } from "./use-workspace";

export function useCompanies(action: WorkspaceState["action"]) {
  const query = useQueryClient();
  const data = useResource<Company[]>("companies");
  const regions = useResource<Record<string, string>>("companies/regions");
  const countryLabels = regions.data || {Worldwide: "Весь мир · IT-компании"};
  const countries = Object.keys(countryLabels);
  const [q, setQ] = useState("");
  const [signal, setSignal] = useState("");
  const [country, setCountry] = useState("Worldwide");
  const [page, setPage] = useState(1);
  const handled = useRef(new Set<string>());
  const discoveries = useQuery<CompanyDiscovery[]>({
    queryKey: ["companies/discoveries"], queryFn: () => api("companies/discoveries"), retry: false,
    refetchInterval: state => state.state.data?.some(job => busy(job.status)) ? 1500 : 10000,
  });
  const job = discoveries.data?.[0];
  const discovering = Boolean(discoveries.data?.some(job => busy(job.status)));
  useEffect(() => {
    if (!job || busy(job.status) || handled.current.has(job.id)) return;
    handled.current.add(job.id);
    void query.invalidateQueries({queryKey: ["companies"]});
    if (job.country === country && job.status === "complete") setPage(job.result.next_page || 1);
  }, [job, country, query]);
  const companies = useMemo(() => (data.data || []).filter(company => {
    const locations = company.signals.map(s => `${s.location} ${s.country || ""} ${countryLabels[s.country || ""] || ""} ${Object.keys(countryLabels).filter(country => s.location.includes(country)).map(country => countryLabels[country]).join(" ")}`).join(" ");
    const text = `${company.name} ${locations}`.toLowerCase();
    return q.toLowerCase().trim().split(/\s+/).every(word => text.includes(word)) &&
      (!signal || company.signals.some(s => s.kind === signal || signal === "github_profile" && s.kind === "cis_profile"));
  }).sort((a, b) => signal === "resume_fit"
    ? (a.signals.find(s => s.kind === "resume_fit")?.rank || 999) - (b.signals.find(s => s.kind === "resume_fit")?.rank || 999)
    : a.name.localeCompare(b.name)), [data.data, q, signal, countryLabels]);
  async function discover() {
    await action(async () => {
      await api<CompanyDiscovery>("companies/discover/start", "POST", {country, page});
    }, "Поиск компаний запущен. Этапы и результат появятся ниже.");
  }
  return {data, regions, companies, q, setQ, signal, setSignal, country, countryLabels, countries,
    setCountry: (value: string) => {setCountry(value); setPage(1);}, page, job, discoveries, discovering, discover};
}
